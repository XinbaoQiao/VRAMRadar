"""Click-to-open: launch an AI desktop app, or bring its window to the front.

Targets come from the background detection (``state["launch"]``): never
resolved on the UI thread.  Launching runs on a short worker thread; failures
are logged quietly (type only) and never raised into the UI.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

LOG = logging.getLogger("vram_radar")
LAUNCH_DEBOUNCE_SECONDS = 1.5      # repeated clicks on one app within this window are ignored
HOVER_LEAVE_GRACE_MS = 250         # strip -> hover card: time allowed to cross the gap
KINDS = ("exe", "aumid", "bundle", "app")


@dataclass(frozen=True)
class LaunchTarget:
    provider_id: str
    kind: str          # "exe" | "aumid" (MSIX shell:AppsFolder) | "bundle" (macOS id) | "app" (macOS path)
    target: str
    folder: str = ""   # processes whose image lives here count as the app's windows


def target_from_state(provider_id: str, state) -> LaunchTarget | None:
    """Launch target from a provider snapshot; None when not installed/undetected."""
    if not isinstance(state, dict) or not state.get("installed"):
        return None
    launch = state.get("launch")
    if not isinstance(launch, dict):
        return None
    kind, target = launch.get("kind"), launch.get("target")
    if kind not in KINDS or not isinstance(target, str) or not target.strip():
        return None
    folder = launch.get("folder") if isinstance(launch.get("folder"), str) else ""
    return LaunchTarget(provider_id, kind, target.strip(), folder)


def launch_targets(provider_states, selected) -> dict[str, LaunchTarget]:
    states = provider_states if isinstance(provider_states, dict) else {}
    result = {}
    for pid in selected or ():
        target = target_from_state(pid, states.get(pid))
        if target is not None:
            result[pid] = target
    return result


def click_action(provider_id: str | None, targets: dict) -> str:
    """Strip single-click: 'open_app' on a detected model, 'nothing' on an
    undetected model, 'home' outside any model (previous behaviour)."""
    if provider_id is None:
        return "home"
    return "open_app" if provider_id in targets else "nothing"


def hit_model(cells, point) -> str | None:
    """Provider id whose (left, top, right, bottom) rect contains ``point``."""
    x, y = point
    for provider_id, (left, top, right, bottom) in cells or ():
        if left <= x < right and top <= y < bottom:
            return provider_id
    return None


def open_app_menu_entries(selected, provider_states, language: str = "zh-CN", names=None) -> list[tuple[str, str]]:
    """macOS menu-bar items: ("Open Kimi" | "打开 Kimi", provider id) per detected app."""
    names = names or {}
    english = language == "en"
    entries = []
    for pid in launch_targets(provider_states, selected):
        name = names.get(pid) or pid
        entries.append((f"Open {name}" if english else f"打开 {name}", pid))
    return entries


# -- Windows window lookup / focus --------------------------------------------

def _under(path: str, folder: str) -> bool:
    if not path or not folder:
        return False
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def find_app_window(target: LaunchTarget, *, windows=None, image_path=None) -> int:
    """Top-level visible, titled, non-tool window owned by the app; 0 if none."""
    if windows is None:
        windows = list_top_windows()
    if image_path is None:
        from .providers.base import process_path as image_path
    exe_name = os.path.basename(target.target).lower() if target.kind == "exe" else ""
    cache: dict[int, str] = {}
    for hwnd, pid in windows:
        if pid not in cache:
            cache[pid] = image_path(pid) or ""
        path = cache[pid]
        if _under(path, target.folder) or (exe_name and os.path.basename(path).lower() == exe_name):
            return hwnd
    return 0


def list_top_windows() -> list[tuple[int, int]]:
    """(hwnd, pid) for visible, unowned, titled, non-tool top-level windows."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[tuple[int, int]] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowLongW.restype = ctypes.c_long

    def visit(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER
                return True
            if user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x00000080:                  # WS_EX_TOOLWINDOW
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((int(hwnd), int(pid.value)))
        except Exception:
            pass
        return len(found) < 4096

    try:
        user32.EnumWindows(proc(visit), 0)
    except Exception:
        return []
    return found


def focus_window(hwnd: int) -> bool:
    """Restore if minimized and bring to front.  Only ever called right after
    the user's own click on VRAM Radar (so Windows allows the switch)."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)      # SW_SHOW
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def start_target(target: LaunchTarget) -> None:
    """Open the app through the shell (same path as the Start menu)."""
    if sys.platform == "darwin":
        command = ["open", "-b", target.target] if target.kind == "bundle" else ["open", target.target]
        subprocess.run(command, check=True, timeout=10, capture_output=True)
        return
    if sys.platform != "win32":
        raise OSError("unsupported platform")
    import ctypes
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY: let the new app come forward
    except Exception:
        pass
    if target.kind == "aumid":
        os.startfile("shell:AppsFolder\\" + target.target)
    elif target.kind == "exe":
        folder = os.path.dirname(target.target) or None
        os.startfile(target.target, cwd=folder) if folder else os.startfile(target.target)
    else:
        raise OSError("unsupported launch kind")


class AppLauncher:
    """Open-or-focus with per-app debounce.  All side effects injectable."""

    def __init__(self, *, start: Callable[[LaunchTarget], None] = start_target,
                 find: Callable[[LaunchTarget], int] = find_app_window,
                 focus: Callable[[int], bool] = focus_window,
                 clock: Callable[[], float] = time.monotonic, platform: str = sys.platform,
                 background: bool = True):
        self.start, self.find, self.focus, self.clock = start, find, focus, clock
        self.platform = platform
        self.background = background
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def open(self, target: LaunchTarget | None) -> str:
        """'focused' | 'launched' | 'debounced' | 'failed' | 'none'."""
        if target is None:
            return "none"
        now = self.clock()
        with self._lock:
            last = self._last.get(target.provider_id)
            if last is not None and now - last < LAUNCH_DEBOUNCE_SECONDS:
                return "debounced"
            self._last[target.provider_id] = now
        if self.platform == "win32":
            try:
                hwnd = self.find(target)
            except Exception as exc:
                LOG.info("app window lookup failed for %s (%s)", target.provider_id, type(exc).__name__)
                hwnd = 0
            if hwnd:
                if self.focus(hwnd):
                    return "focused"
                LOG.info("app focus refused for %s; launching instead", target.provider_id)
        return self._launch(target)

    def _launch(self, target: LaunchTarget) -> str:
        def run():
            try:
                self.start(target)
            except Exception as exc:
                LOG.info("app launch failed for %s (%s)", target.provider_id, type(exc).__name__)
        if self.background:
            threading.Thread(target=run, daemon=True, name=f"open-{target.provider_id}").start()
            return "launched"
        try:
            self.start(target)
            return "launched"
        except Exception as exc:
            LOG.info("app launch failed for %s (%s)", target.provider_id, type(exc).__name__)
            return "failed"