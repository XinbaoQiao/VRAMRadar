"""Fallback outside-click dismissal for the strip's context menu.

WinForms closes a ContextMenuStrip when this thread's active window changes.
That needs the tray-style SetForegroundWindow(owner) on right-click to have
been granted; if Windows refuses it, a click elsewhere would leave the menu
open.  While (and only while) the menu is open this watcher polls the mouse
button state with GetAsyncKeyState on a UI-thread timer and closes the menu
when a button goes down outside the menu, its open submenus and the strip.

Passive by construction: it only reads button state and the cursor position
(no hook, no input, no foreground change), so the user's click always goes
through to whatever they clicked.
"""
from __future__ import annotations

import logging

BUTTONS = (0x01, 0x02, 0x04, 0x05, 0x06)   # left, right, middle, X1, X2
POLL_MS = 40


def contains(rect, point) -> bool:
    left, top, right, bottom = rect
    return left <= point[0] < right and top <= point[1] < bottom


def outside_press(was_down, now_down, cursor, inside_rects) -> bool:
    """True when a button newly went down with the cursor outside every rect."""
    pressed = any(now and not was for was, now in zip(was_down, now_down))
    return pressed and cursor is not None and not any(contains(r, cursor) for r in inside_rects)


class OutsideClickWatch:
    """start() on menu open, stop() on close; tick() runs on the UI timer.

    ``timer`` needs Start/Stop/Dispose and a Tick event (WinForms Timer);
    ``buttons()`` -> tuple of bools, ``cursor()`` -> (x, y) or None,
    ``inside()`` -> rects (left, top, right, bottom), ``close()`` closes the menu,
    ``is_open()`` -> whether the menu is still shown.
    """

    def __init__(self, timer, buttons, cursor, inside, close, is_open):
        self._timer, self._buttons, self._cursor = timer, buttons, cursor
        self._inside, self._close, self._is_open = inside, close, is_open
        self._last = None
        self.running = False
        self._timer.Interval = POLL_MS
        self._timer.Tick += self.tick

    def start(self, *_):
        try:
            self._last = tuple(self._buttons())   # a held right button from the opening click is not a new press
        except Exception:
            self._last = None
        self.running = True
        self._timer.Start()

    def stop(self, *_):
        self.running = False
        self._last = None
        try:
            self._timer.Stop()
        except Exception:
            pass

    def dispose(self):
        self.stop()
        try:
            self._timer.Dispose()
        except Exception:
            pass

    def tick(self, *_):
        if not self.running:
            self.stop()
            return
        try:
            if not self._is_open():
                self.stop()
                return
            now = tuple(self._buttons())
            last, self._last = self._last, now
            if last is not None and outside_press(last, now, self._cursor(), self._inside()):
                self.stop()
                self._close()
        except Exception:
            # Never keep polling after a failure.
            self.stop()
            logging.getLogger("vram_radar").debug("menu outside-click watch stopped", exc_info=True)


def _user32():
    """A private WinDLL (argtypes never leak to other modules)."""
    import ctypes
    from ctypes import wintypes
    lib = ctypes.WinDLL("user32")
    lib.GetAsyncKeyState.argtypes, lib.GetAsyncKeyState.restype = [ctypes.c_int], ctypes.c_short
    lib.GetCursorPos.argtypes, lib.GetCursorPos.restype = [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL
    return lib


def win32_buttons(user32=None):
    user32 = user32 or _user32()
    def read():
        return tuple(bool(user32.GetAsyncKeyState(vk) & 0x8000) for vk in BUTTONS)
    return read


def win32_cursor(user32=None):
    user32 = user32 or _user32()
    import ctypes
    from ctypes import wintypes

    def read():
        point = wintypes.POINT()
        return (point.x, point.y) if user32.GetCursorPos(ctypes.byref(point)) else None
    return read


def menu_rects(menu, *extra):
    """Bounds of the menu, every open submenu (recursively) and ``extra``
    controls (the strip), as (left, top, right, bottom)."""
    rects = []
    def add(control):
        if control is not None and control.Visible:
            b = control.Bounds
            rects.append((b.Left, b.Top, b.Right, b.Bottom))
    def walk(drop):
        add(drop)
        for i in range(drop.Items.Count):
            sub = getattr(drop.Items[i], "DropDown", None)
            if sub is not None and getattr(drop.Items[i], "HasDropDownItems", False) and sub.Visible:
                walk(sub)
    walk(menu)
    for control in extra:
        add(control)
    return rects