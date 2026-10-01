"""Read a usage figure that an app is currently *showing on screen*.

Grok Bot keeps its weekly usage only in memory (fetched with an encrypted
sign-in token, never written to disk), but it renders it in its own account
menu (avatar > usage row: "<label>  NN%", with a reset hint).  While that
menu is open, Windows UI Automation exposes the same text to any assistive
client.  This module reads it, read-only:

* only while the app's own window is the foreground window (the user is
  looking at it), at most every ``interval`` seconds;
* only inside UI Automation ``Menu`` elements (never chat text, documents or
  input fields), and only a percentage plus a short reset phrase;
* nothing is clicked, typed, focused or invoked; nothing is logged or saved.

The last reading is kept in memory with the time it was seen, so the strip
can say "Grok 23% (read 14:05)".  If the menu is never opened, no number is
known and the strip keeps an honest sign-in label.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time

LOG = logging.getLogger("vram_radar")

_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:[.,]\d{1,2})?)\s*%")
_KEYWORDS = re.compile(r"usage|limit|quota|plan|week|grok|heavy|credit|用量|使用|限额|额度|配额|本周|每周|套餐|方案",
                       re.IGNORECASE)
_RESET = re.compile(r"(resets?\b[^|\n%]{0,40}|[^\s|%]{0,16}重置[^\s|%]{0,16}|[^\s|%]{0,12}刷新[^\s|%]{0,12})",
                    re.IGNORECASE)


def parse_menu_texts(items) -> dict | None:
    """``items`` = list of text lists, one per menu item (its name followed by
    its descendants' names).  Returns {"percent_used", "reset_text"} or None.

    A percentage counts only on a menu item that also names usage/limit/plan
    (or carries a reset hint), so e.g. a zoom "100%" item is ignored.
    """
    best = None
    for texts in items:
        texts = [t.strip() for t in texts if isinstance(t, str) and t.strip()][:12]
        joined = " | ".join(t[:120] for t in texts)
        match = _PERCENT.search(joined)
        if not match:
            continue
        reset = _RESET.search(joined)
        if not (_KEYWORDS.search(joined) or reset):
            continue
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if not 0 <= value <= 100:
            continue
        candidate = {"percent_used": value,
                     "reset_text": reset.group(1).strip(" ·|,")[:48] if reset else ""}
        # Prefer the row that also carries its reset hint (the detail row).
        if best is None or (candidate["reset_text"] and not best["reset_text"]):
            best = candidate
    return best


class ScreenUsageWatcher:
    """Background, foreground-gated UI Automation reader for one app."""

    def __init__(self, process_names, *, interval: float = 3.0):
        self.process_names = {name.lower() for name in process_names}
        self.interval = interval
        self.lock = threading.Lock()
        self.reading: dict | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._uia = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if sys.platform != "win32" or os.environ.get("VRAM_RADAR_NO_SCREEN_READ"):
            return
        with self.lock:
            if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
                return
            # Each worker owns its stop event: a stop() quickly followed by
            # start() (Grok unticked and re-ticked) used to clear the event
            # while the old worker was exiting -- and no reader ran at all.
            self._stop = stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(stop,), daemon=True, name="screen-usage-reader")
            self._thread.start()

    def stop(self) -> None:
        with self.lock:
            self._stop.set()

    def latest(self) -> dict | None:
        with self.lock:
            return dict(self.reading) if self.reading else None

    # -- internals -------------------------------------------------------
    def _run(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        while not stop.wait(self.interval):
            try:
                handle = self._foreground_window()
                if handle:
                    found = self.scan(handle)
                    if found:
                        found["seen_at"] = time.time()
                        with self.lock:
                            self.reading = found
            except Exception as exc:  # never let UI Automation errors escape
                LOG.info("screen usage read failed (%s)", type(exc).__name__)

    def _foreground_window(self) -> int:
        """Foreground HWND if it belongs to one of our process names."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = user32.GetForegroundWindow()
        if not handle:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not process:
            return 0
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return 0
        finally:
            kernel32.CloseHandle(process)
        return int(handle) if os.path.basename(buffer.value).lower() in self.process_names else 0

    def scan(self, handle: int) -> dict | None:
        if self._uia is None:
            try:
                from System.Reflection import Assembly
                for name in ("UIAutomationClient", "UIAutomationTypes"):
                    Assembly.Load(name + ", Version=4.0.0.0, Culture=neutral, PublicKeyToken=" "31bf3856ad364e35")
                self._uia = True
            except Exception:
                self._uia = False
        if not self._uia:
            return None
        from System import IntPtr
        from System.Windows.Automation import (AutomationElement, ControlType, PropertyCondition,
                                               TreeScope)
        root = AutomationElement.FromHandle(IntPtr(int(handle)))
        menus = root.FindAll(TreeScope.Descendants,
                             PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Menu))
        items = []
        for menu in list(menus)[:6]:
            entries = menu.FindAll(TreeScope.Descendants,
                                   PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.MenuItem))
            for entry in list(entries)[:40]:
                texts = [entry.Current.Name]
                for child in list(entry.FindAll(TreeScope.Descendants,
                                                PropertyCondition(AutomationElement.ControlTypeProperty,
                                                                  ControlType.Text)))[:8]:
                    texts.append(child.Current.Name)
                items.append(texts)
        return parse_menu_texts(items)
