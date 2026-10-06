"""Hover-card click-to-open: launch an AI desktop app, or bring its window forward.

Only rows of the hover detail card open apps; the taskbar strip itself always
opens VRAM Radar.  Targets come from the background detection
(``state["launch"]``): never resolved on the UI thread.  Launching runs on a
short worker thread; failures are logged quietly (type only) and never raised
into the UI.
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
FEEDBACK_FLASH_MS = 200            # confirmation flash after a row opened its app
FEEDBACK_HIDE_MS = 1500            # card hides at the latest this long after a row click
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


class RowFeedback:
    """Visual state of hover-card rows (pure; the card renders ``highlight()``).

    hot: clickable row under the pointer.  pressed: left button went down on a
    clickable row.  result: brief confirmation after the click ("flash" when the
    app was opened or focused, "failed" for a neutral grey state).  Rows of
    undetected apps never get a state and keep the arrow cursor.
    """

    def __init__(self) -> None:
        self.hot: str | None = None
        self.pressed: str | None = None
        self.result: tuple[str, str] | None = None

    def highlight(self) -> tuple[str, str] | None:
        """(provider id, level) with level 'flash' | 'failed' | 'pressed' | 'hover'."""
        if self.result is not None:
            return self.result[0], self.result[1]
        if self.pressed is not None:
            return self.pressed, "pressed"
        if self.hot is not None:
            return self.hot, "hover"
        return None

    def _change(self, update) -> bool:
        before = self.highlight()
        update()
        return self.highlight() != before

    def move(self, provider_id: str | None, clickable: bool) -> bool:
        def update():
            self.hot = provider_id if provider_id and clickable else None
            if self.pressed is not None and self.pressed != self.hot:
                self.pressed = None          # dragged off the pressed row: no click
        return self._change(update)

    def leave(self) -> bool:
        def update():
            self.hot = self.pressed = None
        return self._change(update)

    def down(self, provider_id: str | None, clickable: bool) -> bool:
        def update():
            self.pressed = provider_id if provider_id and clickable and self.result is None else None
            if self.pressed:
                self.hot = self.pressed
        return self._change(update)

    def up(self, provider_id: str | None) -> str | None:
        """Row to open: only when released on the row that was pressed."""
        fire = self.pressed if self.pressed and self.pressed == provider_id else None
        self.pressed = None
        return fire

    def finish(self, provider_id: str, outcome: str) -> str | None:
        """Record the open result; returns the level shown ('flash' | 'failed' | None)."""
        level = {"focused": "flash", "launched": "flash", "failed": "failed"}.get(outcome)
        self.result = (provider_id, level) if level else None
        return level

    def reset(self) -> None:
        self.hot = self.pressed = None
        self.result = None


def row_cursor(provider_id: str | None, clickable) -> str:
    """'hand' over a clickable (detected) row, else 'arrow'."""
    try:
        return "hand" if provider_id and callable(clickable) and clickable(provider_id) else "arrow"
    except Exception:
        return "arrow"


# -- Windows window lookup / focus --------------------------------------------

def _under(path: str, folder: str) -> bool:
    if not path or not folder:
        return False
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _shared_roots() -> set[str]:
    """Folders that hold many unrelated programs; never 'the app's own folder'."""
    roots = set()
    for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot", "USERPROFILE",
                 "LOCALAPPDATA", "APPDATA", "PUBLIC"):
        value = os.environ.get(name)
        if value:
            roots.add(os.path.normcase(os.path.abspath(value)))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.add(os.path.normcase(os.path.abspath(os.path.join(local, "Programs"))))
    home = os.environ.get("USERPROFILE")
    if home:
        for sub in ("Desktop", "Downloads", "Documents"):
            roots.add(os.path.normcase(os.path.abspath(os.path.join(home, sub))))
    return roots


def own_folder(folder: str) -> bool:
    """True when ``folder`` can identify one app (not a drive root or a shared root)."""
    if not folder:
        return False
    full = os.path.normcase(os.path.abspath(folder))
    if os.path.dirname(full.rstrip("\\/")) in ("", full.rstrip("\\/")) or full.rstrip("\\/").endswith(":"):
        return False
    return full.rstrip("\\/") not in {root.rstrip("\\/") for root in _shared_roots()}


def find_app_window(target: LaunchTarget, *, windows=None, image_path=None, own_pid: int | None = None) -> int:
    """Top-level visible, titled, non-tool window owned by the app; 0 if none.

    Matches the app's executable name, or any process under the app's own
    folder -- but never VRAM Radar's own windows and never via a broad shared
    folder (a drive root, Program Files, Downloads ...), which would focus an
    unrelated program."""
    if windows is None:
        windows = list_top_windows()
    if image_path is None:
        from .providers.base import process_path as image_path
    own_pid = os.getpid() if own_pid is None else own_pid
    exe_name = os.path.basename(target.target).lower() if target.kind == "exe" else ""
    folder = target.folder if own_folder(target.folder) else ""
    cache: dict[int, str] = {}
    for hwnd, pid in windows:
        if pid == own_pid:
            continue
        if pid not in cache:
            cache[pid] = image_path(pid) or ""
        path = cache[pid]
        if _under(path, folder) or (exe_name and os.path.basename(path).lower() == exe_name):
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

    def open(self, target: LaunchTarget | None, on_done: Callable[[str], None] | None = None) -> str:
        """'focused' | 'launched' | 'debounced' | 'failed' | 'none'.

        ``on_done(outcome)`` reports the final result: 'focused' at once, or
        'launched' / 'failed' after the shell call returns (worker thread when
        ``background``).  Never called for 'debounced' / 'none'.
        """
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
                    _report(on_done, "focused")
                    return "focused"
                LOG.info("app focus refused for %s; launching instead", target.provider_id)
        return self._launch(target, on_done)

    def _launch(self, target: LaunchTarget, on_done=None) -> str:
        def run() -> str:
            try:
                self.start(target)
                outcome = "launched"
            except Exception as exc:
                LOG.info("app launch failed for %s (%s)", target.provider_id, type(exc).__name__)
                outcome = "failed"
            _report(on_done, outcome)
            return outcome
        if self.background:
            threading.Thread(target=run, daemon=True, name=f"open-{target.provider_id}").start()
            return "launched"
        return run()


def _report(callback, outcome: str) -> None:
    if callback is None:
        return
    try:
        callback(outcome)
    except Exception as exc:
        LOG.info("app open result handler failed (%s)", type(exc).__name__)