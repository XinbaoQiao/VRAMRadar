"""Small native quota surface, independent of the main WebView's visibility."""
from __future__ import annotations

import logging
import math
import re
import sys
import threading
import time
from typing import Callable
import os

# Alpha of the strip's hit-test window: WinForms maps Opacity to a byte
# (int(opacity * 255)), so this is alpha 1/255 -- invisible, yet hit-testable
# (only alpha 0 / colour-keyed pixels pass clicks through).
CATCHER_OPACITY = 0.004
# VRAM_RADAR_PERF=1 logs the strip's UI-thread tick cost once a minute.
PERF_LOG = os.environ.get("VRAM_RADAR_PERF") == "1"

_DLLS: dict = {}


def _dll(name: str):
    """Private WinDLL instances.  Setting ``argtypes`` on the process-wide
    ``ctypes.windll.user32`` leaked into tray.py (which passes its own RECT
    structure) and broke off-screen window recovery."""
    lib = _DLLS.get(name)
    if lib is None:
        import ctypes
        from ctypes import wintypes
        lib = ctypes.WinDLL(name, use_last_error=True)
        if name == "user32":
            lib.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            lib.FindWindowW.restype = wintypes.HWND
            lib.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
            lib.FindWindowExW.restype = wintypes.HWND
            lib.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _DLLS[name] = lib
    return lib


def quota_lines(state: dict, language: str = "zh-CN", *, now: float | None = None) -> list[dict]:
    """One compact line per actual window; never invent a reset or a quota."""
    english = language == "en"
    now = time.time() if now is None else now
    if not state.get("enabled"):
        return []
    rows = []
    for window in state.get("windows", []):
        minutes = window.get("window_minutes")
        duration = "?"
        if isinstance(minutes, (int, float)) and minutes > 0:
            duration = f"{minutes / 1440:g}d" if minutes % 1440 == 0 else (
                f"{minutes / 60:g}h" if minutes % 60 == 0 else f"{minutes:g}m")
        reset = window.get("resets_at")
        valid_reset = isinstance(reset, (int, float)) and math.isfinite(reset)
        expired = valid_reset and reset <= now
        left = max(0, math.ceil((reset - now) / 60)) if valid_reset else None
        if left is None:
            countdown = "Reset unknown" if english else "重置时间未知"
        elif expired:
            countdown = "Updating…" if english else "等待更新"
        else:
            countdown = f"{(reset-now) / 3600:.1f}h" if reset-now >= 360 else "<0.1h"
        value = window.get("remaining_percent")
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and not expired and not state.get("stale")
                 and state.get("state") in {"ready", "loading"})
        percent = f"{max(0, min(100, value)):.0f}%" if valid else "—"
        rows.append({"label": duration, "value": percent, "countdown": countdown,
                     "low": valid and value <= 10,
                     "detail": f"{window.get('name') or 'Codex'} · {duration} · {percent} · {countdown}"})
    if rows:
        return rows
    code = state.get("code")
    if state.get("state") == "loading":
        message = "Reading…" if english else "正在读取…"
    elif code in {"login_required", "unsupported_account"}:
        message = "Sign in to Codex" if english else "请登录 Codex"
    elif code in {"not_installed", "invalid_executable", "start_failed"}:
        message = "Check Codex setup" if english else "请设置 Codex"
    else:
        message = "Usage unavailable" if english else "额度暂不可用"
    return [{"label": "Codex", "value": "—", "countdown": message, "low": False, "detail": message}]


def strip_bounds(work_area: tuple[int, int, int, int], size: tuple[int, int],
                 position: tuple[int, int] | None = None, margin: int = 8) -> tuple[int, int]:
    """Anchor above the taskbar, or clamp a dragged strip after display changes."""
    left, top, right, bottom = work_area
    width, height = size
    x, y = position if position is not None else (right - width - margin, bottom - height - margin)
    return (max(left, min(x, right - width)), max(top, min(y, bottom - height)))


def windows_taskbar_geometry():
    """Read Explorer's taskbar and notification area without changing either."""
    import ctypes
    from ctypes import wintypes
    user32 = _dll("user32")
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    tray = user32.FindWindowExW(taskbar, None, "TrayNotifyWnd", None) if taskbar else None
    rectangles = []
    for handle in (taskbar, tray):
        rect = wintypes.RECT()
        if not handle or not user32.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        rectangles.append((rect.left, rect.top, rect.right, rect.bottom))
    bar, tray = rectangles
    return (bar, tray) if bar[2]-bar[0] > bar[3]-bar[1] > 8 else None


def taskbar_anchor(bar, tray, size):
    """Place directly before the notification area, centered in the taskbar."""
    width, height = size
    gap = 1
    return max(bar[0], tray[0]-width-gap), bar[1]+(bar[3]-bar[1]-height)//2


# Taskbar elements that bound the empty "left" area on Windows 11.  Explorer
# exposes them through UI Automation with stable AutomationIds; coordinates
# are physical pixels for a per-monitor-DPI-aware caller.
LEFT_BOUND_IDS = ("WidgetsButton",)
RIGHT_BOUND_IDS = ("StartButton", "SearchButton", "TaskViewButton")


def left_slot(bar, elements, size, margin=6):
    """Choose a spot in the empty area right of the Widgets/weather button and
    left of Start/Search (centered taskbar) or of the first pinned app.

    ``elements`` maps AutomationId -> (left, top, right, bottom).  Returns
    ``(x, y)`` or ``None`` when the gap cannot hold ``size`` without overlap
    (e.g. left-aligned icons with Widgets next to Start); callers then fall
    back to ``taskbar_anchor``.  Vertical taskbars are not supported here.
    """
    width, height = size
    gap = left_gap(bar, elements, margin)
    if gap is None or gap[1] - gap[0] < width:
        return None
    y = bar[1] + (bar[3] - bar[1] - height) // 2
    return gap[0], y


def left_gap(bar, elements, margin=6):
    """(left, right) of the empty taskbar area used by ``left_slot``, or None."""
    left_edge = bar[0] + margin
    for key in LEFT_BOUND_IDS:
        rect = elements.get(key)
        if rect and rect[2] > rect[0] and bar[0] <= rect[0] < bar[2]:
            left_edge = max(left_edge, rect[2] + margin)
    candidates = [rect[0] for key, rect in elements.items()
                  if (key in RIGHT_BOUND_IDS or key == "first_app") and rect and rect[2] > rect[0]
                  and rect[0] >= left_edge - margin and rect[0] <= bar[2]]
    widgets = elements.get("WidgetsButton")
    if widgets and widgets[0] > (bar[0] + bar[2]) / 2:
        return None  # Widgets on the right (left-aligned layout); no left gap
    right_edge = min(candidates) - margin if candidates else None
    if right_edge is None or right_edge <= left_edge:
        return None
    return left_edge, right_edge


def content_right(pixels, width, height, threshold=60, min_hits=2):
    """Column just past the right-most visible content in a BGRA capture.

    Each column's background is taken from its own top/bottom rows (the
    taskbar is translucent and the hover highlight is a rounded rect), and a
    column counts as content when at least ``min_hits`` of its middle rows
    differ from that background by more than ``threshold`` (sum of |dRGB|).
    Returns an offset in ``0..width`` (0 = nothing visible) or None for bad
    input.
    """
    if width <= 0 or height < 8 or len(pixels) < width * height * 4:
        return None
    def px(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2], pixels[i + 1], pixels[i]
    top_rows = (1, 2, 3)
    bottom_rows = (height - 4, height - 3, height - 2)
    middle = range(int(height * 0.2), int(height * 0.8) + 1)
    last = 0
    for x in range(width):
        refs = [px(x, y) for y in (*top_rows, *bottom_rows)]
        ref = tuple(sorted(c[k] for c in refs)[len(refs) // 2] for k in range(3))
        hits = 0
        for y in middle:
            r, g, b = px(x, y)
            if abs(r - ref[0]) + abs(g - ref[1]) + abs(b - ref[2]) > threshold:
                hits += 1
                if hits >= min_hits:
                    last = x + 1
                    break
    return last


def capture_screen(rect):
    """BGRA bytes of a physical-pixel screen rect, or None."""
    try:
        import ctypes
        from ctypes import wintypes
        left, top, right, bottom = (int(v) for v in rect)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0 or width * height > 4_000_000:
            return None
        user32, gdi32 = _dll("user32"), _dll("gdi32")
        vp, ci = ctypes.c_void_p, ctypes.c_int
        user32.GetDC.restype, user32.GetDC.argtypes = vp, [vp]
        user32.ReleaseDC.argtypes = [vp, vp]
        gdi32.CreateCompatibleDC.restype, gdi32.CreateCompatibleDC.argtypes = vp, [vp]
        gdi32.CreateCompatibleBitmap.restype, gdi32.CreateCompatibleBitmap.argtypes = vp, [vp, ci, ci]
        gdi32.SelectObject.restype, gdi32.SelectObject.argtypes = vp, [vp, vp]
        gdi32.BitBlt.argtypes = [vp, ci, ci, ci, ci, vp, ci, ci, wintypes.DWORD]
        gdi32.DeleteObject.argtypes = [vp]
        gdi32.DeleteDC.argtypes = [vp]

        class Header(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        gdi32.GetDIBits.argtypes = [vp, vp, wintypes.UINT, wintypes.UINT, vp, ctypes.POINTER(Header), wintypes.UINT]
        screen = user32.GetDC(None)
        if not screen:
            return None
        memory = gdi32.CreateCompatibleDC(screen)
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        old = gdi32.SelectObject(memory, bitmap)
        try:
            if not gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x00CC0020):
                return None
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            buffer = (ctypes.c_ubyte * (width * height * 4))()
            gdi32.SelectObject(memory, old)
            if gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
                return None
            return bytes(buffer)
        finally:
            gdi32.SelectObject(memory, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)
    except Exception:
        return None


def trim_widgets(elements, own=None, capture=capture_screen, min_width=24):
    """Shrink the Widgets button to its visible content (icon + weather text).

    Explorer reports the weather button much wider than what it draws
    (228 px for "23°C / 局部多云" at 150 %, text ending near 150 px), which
    made the empty gap look ~100 px narrower than it is.  The part covered by
    our own strip is never scanned, so the strip cannot push itself away.
    """
    widgets = elements.get("WidgetsButton") if elements else None
    if not widgets or widgets[2] - widgets[0] < min_width:
        return elements
    left, top, right, bottom = widgets
    if own and own[0] < right and own[2] > left and own[1] < bottom and own[3] > top:
        right = max(left, min(right, own[0]))
    if right - left < min_width:
        return elements
    pixels = capture((left, top, right, bottom))
    edge = content_right(pixels, right - left, bottom - top) if pixels else None
    if not edge or edge < min_width // 2:
        return elements
    trimmed = dict(elements)
    trimmed["WidgetsButton"] = (left, top, min(widgets[2], left + edge), bottom)
    return trimmed


class TaskbarLayout:
    """Cached UI Automation reader for the primary taskbar's buttons.

    Lookups are cheap (~15 ms) but are throttled anyway and invalidated when
    the taskbar window handle or size changes (Explorer restart, DPI/scale or
    alignment change).  Any failure returns an empty mapping so callers fall
    back to the classic tray anchor; nothing here can raise into the UI loop.
    """

    def __init__(self, interval: float = 5.0, retry: float = 1.0, trim=trim_widgets, background=False):
        self.interval = interval
        # background: after the first good reading, refresh on a worker
        # thread (UIA FindAll + widget pixel scan took 70-300 ms on the UI
        # thread) and serve the cached reading meanwhile.
        self.background = background
        self._worker = None
        self.trim = trim
        self.retry = min(retry, interval)
        self._key = None
        self._at = 0.0
        self._elements: dict = {}
        self._ready = None
        # Last usable reading per (taskbar handle, rect).  UI Automation
        # lookups fail transiently (Explorer busy, Start menu animating);
        # returning {} then made the strip jump to the tray fallback.
        self._good_key = None
        self._good: dict = {}
        self.failures = 0

    def _load(self):
        if self._ready is None:
            try:
                from System.Reflection import Assembly
                for name in ("UIAutomationClient", "UIAutomationTypes"):
                    Assembly.Load(name + ", Version=4.0.0.0, Culture=neutral, PublicKeyToken=" "31bf3856ad364e35")
                self._ready = True
            except Exception:
                logging.getLogger("vram_radar").info("taskbar UI Automation unavailable")
                self._ready = False
        return self._ready

    def elements(self, taskbar_handle: int, bar, exclude=None) -> dict:
        now = time.monotonic()
        key = (int(taskbar_handle or 0), tuple(bar))
        wait = self.retry if self.failures else self.interval
        if key == self._key and now - self._at < wait:
            return self._elements
        self._key, self._at = key, now
        if self.background and self._good_key == key and self._ready:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._refresh, args=(key, taskbar_handle, exclude),
                                                daemon=True, name="taskbar-layout")
                self._worker.start()
            return self._elements
        return self._refresh(key, taskbar_handle, exclude)

    def _refresh(self, key, taskbar_handle, exclude) -> dict:
        found = self._read(taskbar_handle) if taskbar_handle and self._load() else {}
        found = exclude_rect(found, exclude)
        if usable_layout(found) and self.trim is not None:
            try:
                found = self.trim(found, exclude)
            except Exception as exc:  # worker thread: never die silently mid-update
                logging.getLogger("vram_radar").info("widget trim failed (%s)", type(exc).__name__)
        if usable_layout(found):
            self._good_key, self._good, self.failures = key, found, 0
            self._elements = found
        else:
            self.failures += 1
            # Same bar rect (also across an Explorer restart, whose new
            # taskbar needs a few seconds before UIA exposes Start): the
            # buttons have not moved, so the last good reading still holds.
            same_bar = self._good_key is not None and self._good_key[1] == key[1]
            self._elements = self._good if same_bar else {}
        return self._elements

    def _read(self, taskbar_handle) -> dict:
        try:
            from System import IntPtr
            from System.Windows.Automation import (AutomationElement, OrCondition, PropertyCondition,
                                                   TreeScope, ControlType)
            root = AutomationElement.FromHandle(IntPtr(int(taskbar_handle)))
            conditions = [PropertyCondition(AutomationElement.AutomationIdProperty, value)
                          for value in (*LEFT_BOUND_IDS, *RIGHT_BOUND_IDS)]
            found = {}
            for element in root.FindAll(TreeScope.Descendants, OrCondition(*conditions)):
                rect = element.Current.BoundingRectangle
                if rect.IsEmpty or rect.Width <= 0:
                    continue
                found[element.Current.AutomationId] = (int(rect.Left), int(rect.Top),
                                                       int(rect.Right), int(rect.Bottom))
            if not any(k in found for k in RIGHT_BOUND_IDS):
                # Start hidden by policy/tools: use the first task button.
                buttons = root.FindAll(TreeScope.Descendants, PropertyCondition(
                    AutomationElement.ControlTypeProperty, ControlType.Button))
                lefts = [(int(b.Current.BoundingRectangle.Left), int(b.Current.BoundingRectangle.Top),
                          int(b.Current.BoundingRectangle.Right), int(b.Current.BoundingRectangle.Bottom))
                         for b in buttons if b.Current.AutomationId not in LEFT_BOUND_IDS
                         and not b.Current.BoundingRectangle.IsEmpty]
                if lefts:
                    found["first_app"] = min(lefts)
            return found
        except Exception as exc:
            logging.getLogger("vram_radar").info("taskbar layout read failed (%s)", type(exc).__name__)
            return {}

    def invalidate(self):
        self._key = None


def exclude_rect(elements, own):
    """Drop readings that lie inside our own strip window.

    The strip floats over the taskbar; an element reported at our own rect
    (hit-testing through the overlay, a stale fallback button) would make the
    gap look occupied and push the strip to the tray side.
    """
    if not elements or not own:
        return elements or {}
    l, t, r, b = own
    return {k: v for k, v in elements.items()
            if not (v and v[0] >= l and v[2] <= r and v[1] >= t and v[3] <= b)}


def usable_layout(elements) -> bool:
    """A reading that can bound the left slot (Start/Search/first app found)."""
    return bool(elements) and any(elements.get(k) for k in (*RIGHT_BOUND_IDS, "first_app"))


def docked_target(bar, tray, elements, size, margin=6, widgets_gap=4):
    """``(slot, anchor_x, y)`` for a docked strip.

    ``slot`` is "left" (anchor = left edge of the strip) or "tray"
    (anchor = right edge the strip must stay left of).  Anchors rather than
    final x keep a width change from looking like a side switch.
    """
    width, height = size
    y = bar[1] + (bar[3] - bar[1] - height) // 2
    # Centered taskbar: the empty area left of Start is authoritative.  A
    # strip wider than the gap is compacted/clipped by the caller; it never
    # switches to the tray side (which has even less room and covers pinned
    # icons).  Only a left-aligned layout (no left gap) uses the tray anchor.
    gap = left_gap(bar, elements, margin) if elements else None
    if gap is not None:
        return ("left", gap[0], y)
    right = tray[0]
    widgets = elements.get("WidgetsButton") if elements else None
    if widgets and widgets[0] > (bar[0] + bar[2]) / 2 and widgets[0] < tray[0]:
        right = widgets[0] - widgets_gap
    return ("tray", right, y)


def docked_point(bar, target, width):
    slot, anchor, y = target
    return (anchor, y) if slot == "left" else (max(bar[0], anchor - width - 1), y)


class PlacementDebouncer:
    """Commit a new docked target only after ``confirm`` identical readings.

    The first target is applied at once; afterwards a different side or
    anchor must be seen ``confirm`` ticks in a row, so one odd reading can
    neither move the strip nor flip it to the other side.
    """

    def __init__(self, confirm: int = 3, switch_confirm: int = 5, tolerance: int = 2):
        self.confirm = max(1, int(confirm))
        self.switch_confirm = max(self.confirm, int(switch_confirm))
        self.tolerance = max(0, int(tolerance))
        self.current = None
        self._pending = None
        self._count = 0

    def reset(self):
        self.current, self._pending, self._count = None, None, 0

    def propose(self, target):
        if self.current is None:
            self.current = target
        elif self._near(target, self.current):
            self._pending, self._count = None, 0
        else:
            if self._pending is not None and self._near(target, self._pending):
                self._count += 1
            else:
                self._pending, self._count = target, 1
            needed = self.switch_confirm if target[0] != self.current[0] else self.confirm
            if self._count >= needed:
                self.current, self._pending, self._count = target, None, 0
        return self.current

    def _near(self, a, b):
        return a[0] == b[0] and all(abs(int(x) - int(y)) <= self.tolerance for x, y in zip(a[1:], b[1:]))


def settle_color(previous, sampled, threshold: int = 12):
    """Ignore small pixel noise (Mica, hover glow beside the strip)."""
    if sampled is None:
        return previous
    if previous is None:
        return tuple(sampled)
    return tuple(previous) if max(abs(int(a) - int(b)) for a, b in zip(previous, sampled)) <= threshold \
        else tuple(sampled)


def windows_needs_topmost(handle) -> bool:
    """True when our strip lost topmost or Explorer's taskbar sits above it."""
    try:
        import ctypes
        from ctypes import wintypes
        u = _dll("user32")
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        if not (u.GetWindowLongW(handle, -20) & 0x8):
            return True
        bar = u.FindWindowW("Shell_TrayWnd", None)
        current = handle
        for _ in range(128):
            current = u.GetWindow(current, 3)  # GW_HWNDPREV
            if not current:
                return False
            if bar and current == bar:
                return True
        return False
    except Exception:
        return False


# Shell overlays that cover the screen briefly (Alt+Tab, Task View, Start,
# notification centre).  They are not fullscreen applications.
SHELL_OVERLAY_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow", "ForegroundStaging", "Windows.UI.Core.CoreWindow",
    "TaskListThumbnailWnd", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "LockScreenBackstopFrame", "Windows.UI.Input.InputSite.WindowClass",
})


def taskbar_scale(dpi, geometry):
    preferred = max(0.6, dpi / 96 * 0.85)
    return min(preferred, max(0.6, (geometry[0][3]-geometry[0][1]-6)/40)) if geometry else preferred


def windows_taskbar_dpi(fallback=96):
    import ctypes
    from ctypes import wintypes
    user32 = _dll("user32")
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    return user32.GetDpiForWindow(user32.FindWindowW("Shell_TrayWnd", None)) or fallback


def theme_settings():
    """Personalization flags; missing values fall back to the Windows defaults."""
    values = {"SystemUsesLightTheme": 0, "AppsUseLightTheme": 1, "EnableTransparency": 1, "ColorPrevalence": 0}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            for name in values:
                try:
                    values[name] = int(winreg.QueryValueEx(key, name)[0])
                except (OSError, ValueError, TypeError):
                    pass
    except OSError:
        pass
    return values


def _luminance(rgb):
    return sum(v*w for v, w in zip(rgb, (.2126, .7152, .0722)))


def sample_taskbar_color(bar, exclude=None, near=None, capture=None):
    """Median colour of a few taskbar pixels away from icons/text, or None.

    Reads the composited screen (works with Mica/acrylic/transparency, light
    or dark, accent colour on) instead of guessing.  ``exclude`` is our own
    window rectangle so we never sample ourselves.
    """
    # Per-pixel GetPixel on the screen DC costs ~15 ms each (a DWM readback);
    # ~15 of them stalled the UI thread for 200-260 ms every 5 s.  Grab the
    # bounding rows once (one BitBlt per row band) and index into them.
    try:
        capture = capture or capture_screen
        left, top, right, bottom = bar
        height = bottom - top
        if near:
            # Translucent (Mica/acrylic) taskbars vary along their length:
            # match the pixels immediately beside the widget.
            points = [(x, int(top + (height - 1) * fy)) for x in (near[0] - 3, near[2] + 3, near[2] + 8)
                      for fy in (0.08, 0.3, 0.5, 0.7, 0.92) if left <= x < right]
        else:
            points = [(int(left + (right - left - 1) * fx), int(top + (height - 1) * fy))
                      for fx in (0.005, 0.25, 0.5, 0.62, 0.75, 0.995) for fy in (0.06, 0.94)]
        points = [(x, y) for x, y in points
                  if not (exclude and exclude[0] <= x < exclude[2] and exclude[1] <= y < exclude[3])]
        samples = []
        if points:
            x0, y0 = min(x for x, _ in points), min(y for _, y in points)
            x1, y1 = max(x for x, _ in points) + 1, max(y for _, y in points) + 1
            rows = sorted({y for _, y in points}) if not near else [None]
            for row in rows:
                ry0, ry1 = (y0, y1) if row is None else (row, row + 1)
                pixels = capture((x0, ry0, x1, ry1))
                if not pixels:
                    continue
                width = x1 - x0
                for x, y in points:
                    if row is not None and y != row:
                        continue
                    offset = ((y - ry0) * width + (x - x0)) * 4
                    if offset + 2 < len(pixels):
                        samples.append((pixels[offset + 2], pixels[offset + 1], pixels[offset]))
        if len(samples) < 4:
            return None
        samples.sort(key=_luminance)
        return samples[len(samples) // 2]
    except Exception:
        return None


def taskbar_palette(settings, sampled=None, accent=None):
    """Background/foreground/hover/bright tuple for a taskbar strip.

    ``sampled`` (actual taskbar pixels) wins; otherwise the Windows defaults
    for the system theme (accent colour when ColorPrevalence is on, dark mode).
    Foreground is chosen for contrast against the final background.
    """
    light = bool(settings.get("SystemUsesLightTheme"))
    if sampled is not None:
        background = tuple(int(v) for v in sampled)
    elif accent is not None and settings.get("ColorPrevalence") and not light:
        background = tuple(int(v) for v in accent)
    else:
        background = (238, 238, 238) if light else (28, 28, 28)
    bright = _luminance(background) > 140
    foreground = (26, 26, 26) if bright else (245, 245, 245)
    hover = tuple(max(0, v-18) if bright else min(255, v+24) for v in background)
    return background, foreground, hover, bright


def windows_taskbar_palette(bar=None, exclude=None, near=None):
    """Follow the shell theme, sampling the live taskbar when possible."""
    settings = theme_settings()
    accent = None
    if settings.get("ColorPrevalence"):
        try:
            import ctypes
            color, opaque = ctypes.c_uint(), ctypes.c_int()
            if _dll("dwmapi").DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
                accent = ((color.value >> 16) & 255, (color.value >> 8) & 255, color.value & 255)
        except OSError:
            pass
    sampled = sample_taskbar_color(bar, exclude, near) if bar else None
    return taskbar_palette(settings, sampled, accent)


# (font factor, compaction level) tried in order until the strip fits the
# left gap.  Type never goes below 90 % of the normal strip font (12/13 px at
# 100 %, i.e. the original two-app layout); after that content is compacted,
# and as a last resort the strip is clipped to the gap.
FIT_PLAN = ((1.0, 0), (1.0, 1), (0.95, 1), (0.9, 1), (0.9, 2))
FIT_FACTORS = tuple(dict.fromkeys(factor for factor, _ in FIT_PLAN))


def compact_value(value: str, level: int) -> str:
    """Shorter strip value: 1 drops a trailing countdown ("0% 5.1h" -> "0%"),
    2 also drops unit words ("已用 15%" -> "15%", "¥6.00" -> "¥6")."""
    text = str(value or "")
    if level >= 1:
        text = re.sub(r"\s+<?\d+(?:\.\d)?h$", "", text)
    if level >= 2:
        text = re.sub(r"^(?:已用|Used)\s*(\d+(?:\.\d+)?%)$", r"\1", text)
        text = re.sub(r"(\d+)\.00(?!\d)", r"\1", text)
    return text


def fit_choice(widths, available):
    """Index into FIT_PLAN of the first fitting width, else the last one."""
    for index, width in enumerate(widths):
        if available is None or width <= available:
            return index
    return len(widths) - 1

# Strip background styles (persisted as Profile.usage_background).
BACKGROUND_STYLES = ("transparent", "match", "dark", "light", "accent")
BACKGROUND_LABELS = {
    "transparent": ("透明（无背景）", "Transparent (no background)"),
    "match": ("与任务栏同色", "Match taskbar"),
    "dark": ("深色半透明胶囊", "Subtle dark pill"),
    "light": ("浅色胶囊", "Subtle light pill"),
    "accent": ("主题色调", "Accent tint"),
}


def _mix(a, b, t):
    return tuple(max(0, min(255, round(x*(1-t) + y*t))) for x, y in zip(a, b))


def strip_look(style, base, accent=None):
    """Colours for one background style over the taskbar colour ``base``.

    ``fill`` is the pill colour (None = no pill).  ``keyed`` means the form's
    own background is made fully transparent (colour key = ``base``), so only
    the pill and the text are drawn and everything else shows the real
    taskbar.  Text colour is chosen for contrast against what is under it.
    """
    style = style if style in BACKGROUND_STYLES else "transparent"
    base = tuple(int(v) for v in base)
    dark_bar = _luminance(base) <= 140
    fill = None
    if style == "dark":
        fill = _mix(base, (0, 0, 0), 0.45 if dark_bar else 0.16)
    elif style == "light":
        fill = _mix(base, (255, 255, 255), 0.17 if dark_bar else 0.8)
    elif style == "accent":
        fill = _mix(base, tuple(accent or (0, 120, 215)), 0.55 if dark_bar else 0.35)
    surface = fill or base
    bright = _luminance(surface) > 140
    foreground = (26, 26, 26) if bright else (245, 245, 245)
    hover = tuple(max(0, v-18) if bright else min(255, v+24) for v in surface)
    return {"style": style, "base": base, "fill": fill, "surface": surface, "foreground": foreground,
            "hover": hover, "bright": bright, "keyed": style != "match"}


def windows_accent_color():
    """The user's accent colour (Settings > Personalization > Colors)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value = int(winreg.QueryValueEx(key, "AccentColor")[0]) & 0xFFFFFFFF
            return (value & 255, (value >> 8) & 255, (value >> 16) & 255)  # ABGR
    except (OSError, ValueError, TypeError):
        pass
    try:
        import ctypes
        color, opaque = ctypes.c_uint(), ctypes.c_int()
        if _dll("dwmapi").DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
            return ((color.value >> 16) & 255, (color.value >> 8) & 255, color.value & 255)
    except (OSError, AttributeError):
        pass
    return None


def windows_surface_obscured(widget, docked=True):
    """Yield to fullscreen applications and to a hidden/restarting taskbar."""
    import ctypes
    from ctypes import wintypes
    u = _dll("user32")
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    def bounds(handle):
        rect, info = wintypes.RECT(), MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not u.GetWindowRect(handle, ctypes.byref(rect)) or not u.GetMonitorInfoW(
                u.MonitorFromWindow(handle, 2), ctypes.byref(info)):
            return None
        return rect, info.monitor
    if docked:
        bar = u.FindWindowW("Shell_TrayWnd", None)
        pair = bounds(bar) if bar else None
        if not pair or not u.IsWindowVisible(bar):
            return True
        rect, screen = pair
        if min(rect.right, screen.right)-max(rect.left, screen.left) <= 4 or min(
                rect.bottom, screen.bottom)-max(rect.top, screen.top) <= 4:
            return True
    foreground = u.GetForegroundWindow()
    if not foreground or foreground == widget or u.IsZoomed(foreground):
        return False
    owner, own = wintypes.DWORD(), wintypes.DWORD()
    u.GetWindowThreadProcessId(foreground, ctypes.byref(owner))
    u.GetWindowThreadProcessId(widget, ctypes.byref(own))
    if owner.value == own.value:
        return False
    name = ctypes.create_unicode_buffer(80)
    u.GetClassNameW(foreground, name, len(name))
    if name.value in SHELL_OVERLAY_CLASSES:
        return False
    pair = bounds(foreground)
    if not pair:
        return False
    rect, screen = pair
    same_monitor = u.MonitorFromWindow(foreground, 2) == u.MonitorFromWindow(widget, 2)
    return bool(same_monitor and rect.left <= screen.left and rect.top <= screen.top
                and rect.right >= screen.right and rect.bottom >= screen.bottom)


def usage_color(value, *, bright=False, waiting=False):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return (112, 120, 125) if bright else (160, 168, 173)
    # Berry, coral, amber, jade and ocean blue. Dark/light variants preserve
    # legibility; interpolation makes these anchors a continuous scale.
    colors = ((175, 76, 109), (184, 108, 84), (158, 130, 67),
              (51, 139, 120), (55, 125, 163)) if bright else (
              (227, 143, 163), (230, 166, 135), (218, 190, 119),
              (105, 195, 173), (112, 184, 220))
    value = max(0, value)
    # A continuous proximity curve, without freezing all waits above 72 hours.
    # 18h is the midpoint; longer waits gradually approach the cool endpoint.
    fraction = 1 - 64800/(value+64800) if waiting else min(1, value/100)
    position = fraction*(len(colors)-1)
    index = min(len(colors)-2, int(position))
    t = position-index
    # Interpolate light rather than gamma-encoded bytes. No per-segment easing:
    # it previously stalled around each anchor and looked like discrete tiers.
    def linear(channel):
        channel /= 255
        return channel/12.92 if channel <= .04045 else ((channel+.055)/1.055)**2.4
    def encoded(channel):
        channel = 12.92*channel if channel <= .0031308 else 1.055*channel**(1/2.4)-.055
        return max(0, min(255, round(channel*255)))
    return tuple(encoded(linear(a)*(1-t)+linear(b)*t)
                 for a, b in zip(colors[index], colors[index+1]))


def widget_reading(state: dict, index: int = 0, language: str = "zh-CN", *, now: float | None = None,
                   time_format: str = "decimal") -> dict:
    """Codex strip text: remaining quota and time left in the window.

    Layout/time notation adapted from Amygdala42/CodexUsage (MIT),
    WidgetRenderer.cs at cd72934ef01960c221aa8461b06ab657cf2e803d.
    """
    now = time.time() if now is None else now
    rows = quota_lines(state, language, now=now)
    row = rows[min(index, len(rows)-1)] if rows else {"value": "—", "low": False}
    windows = state.get("windows", [])
    window = windows[index] if 0 <= index < len(windows) else {}
    percent = window.get("remaining_percent") if row["value"] != "—" else None
    reset = window.get("resets_at")
    left = reset - now if isinstance(reset, (int, float)) and math.isfinite(reset) else None
    english = language == "en"
    warning = False
    if state.get("state") == "error":
        countdown = ("Login" if english else "登录") if state.get("code") in {
            "login_required", "unsupported_account"} else ("Retry" if english else "重试")
        warning = True
    elif state.get("stale"):
        countdown, warning = ("Stale" if english else "过期"), True
    elif left is not None and left <= 0:
        countdown = "Wait" if english else "待更新"
    elif state.get("state") == "loading" and not window:
        countdown = "Sync" if english else "更新中"
    elif left is None:
        countdown = "—"
    else:
        # Legacy saved format values remain readable but no longer alter display.
        countdown = f"{left / 3600:.1f}h" if left >= 360 else "<0.1h"
    return {"value": row["value"], "percent": percent, "countdown": countdown,
            "remaining_seconds": left if not warning and left is not None and left > 0 else None,
            "warning": warning, "low": row["low"]}


def _provider_status(state: dict | None, english: bool) -> str:
    if not state:
        return "Scanning" if english else "检测中"
    if state.get("state") == "error":
        return "Probe failed" if english else "检测失败"
    if state.get("installed"):
        if state.get("running"):
            return "Running" if english else "运行中"
        return "Installed" if english else "已安装"
    if state.get("code") == "leftover_data":
        return "Leftover data only" if english else "仅有旧数据"
    return "Not found" if english else "未检测到"


def provider_reading(state: dict | None, spec: dict, language: str = "zh-CN", *, now: float | None = None) -> dict:
    """One compact column for a non-Codex provider.  Only facts the provider
    actually verified are shown; missing data reads as a status, never a number."""
    english = language == "en"
    key = "en" if english else "zh"
    now = time.time() if now is None else now
    name, short = spec.get("name") or spec.get("id", "?"), spec.get("short") or spec.get("id", "?")
    if not state:
        status = _provider_status(None, english)
        return {"name": short, "value": "…", "countdown": status, "low": False, "warning": False,
                "detail": f"{name} · {status}", "brief": f"{name}  {status}"}
    def local(value, fallback=""):
        return value.get(key) or fallback if isinstance(value, dict) else fallback
    headline = local(state.get("headline"), "—")
    subline = local(state.get("subline"))
    lines = [f"{name} · {headline}" + (f" · {subline}" if subline else "")]
    facts = []
    if state.get("installed"):
        version = state.get("version")
        facts.append((f"Installed {version}" if english else f"已安装 {version}").strip())
    else:
        facts.append(_provider_status(state, english))
    signed = state.get("signed_in")
    if signed is True:
        facts.append("Signed in" if english else "已登录")
    elif signed is False:
        facts.append("Not signed in / no key" if english else "未登录或未配置")
    last = state.get("last_used")
    if isinstance(last, (int, float)) and math.isfinite(last) and 0 < last <= now + 86400:
        stamp = time.strftime("%m-%d %H:%M", time.localtime(last))
        facts.append(("Last activity " if english else "最近活动 ") + stamp)
    lines.append("  " + " · ".join(facts))
    for fact in state.get("facts", [])[:8]:
        value = local(fact)
        if value:
            lines.append("  " + value)
    warning = state.get("state") in {"error", "not_installed"} or bool(state.get("stale")) or bool(state.get("session_relogin"))
    # One short tooltip line: name + key quota/balance + reset (if any).
    brief = local(state.get("brief")) or headline
    reset = state.get("reset_at")
    if (isinstance(reset, (int, float)) and math.isfinite(reset) and reset > now
            and not state.get("stale")):
        brief += (" · resets in " if english else " · ") + _hours(reset - now) + ("" if english else " 后重置")
    elif subline and re.match(r"^(记录|As of|旧记录|Old data)", subline):
        brief += f" ({subline})" if english else f"（{subline}）"
    return {"name": short, "value": headline, "countdown": subline or _provider_status(state, english),
            "low": bool(state.get("low")), "warning": warning, "detail": "\n".join(lines),
            "brief": f"{name}  {brief}"}


def _hours(seconds):
    return f"{seconds / 3600:.1f}h" if seconds >= 360 else "<0.1h"


def codex_brief(rows, language: str = "zh-CN") -> str:
    """Codex in one line: each window's remaining quota and its reset."""
    english = language == "en"
    if not rows:
        return "Codex"
    if len(rows) == 1 and rows[0].get("label") == "Codex":
        return f"Codex  {rows[0].get('countdown') or rows[0].get('value')}"
    parts = []
    for row in rows:
        part = f"{row['label']} {row['value']}"
        countdown = row.get("countdown") or ""
        if re.fullmatch(r"<?\d+(?:\.\d)?h", countdown):
            part += (f" · resets in {countdown}" if english else f" · {countdown} 后重置")
        elif countdown:
            part += f" · {countdown}"
        parts.append(part)
    return "Codex  " + (" | ".join(parts))


def concise_tooltip(codex_rows, provider_rows, language: str = "zh-CN") -> str:
    """Hover text: one short line per selected app, no diagnostics."""
    lines = [codex_brief(codex_rows, language)] if codex_rows else []
    lines += [info.get("brief") or info.get("name", "") for info in provider_rows]
    return "\n".join(line for line in lines if line)


# Seconds from 允许 to the value on the strip, as measured on the user's PC
# (immediate fetch, see ProviderMonitor.fetch_now); stated in the dialog.
CONSENT_ETA_SECONDS = 5   # measured 1.0-1.8 s (Grok/Kimi, 10-02); margin for slow networks
# The strip shows "查询中" for a just-allowed provider until its first
# session result arrives (or this many seconds pass).
PENDING_TIMEOUT = 60


# Menu footers stay shorter than the rows they explain: a dropdown is as wide
# as its widest item, so a long footer stretched every row (empty gap between
# "Grok" and its state).
# One short line (a multi-line item makes WinForms raise every row height;
# a long line set the width).  Rows are ~8 chars wide.
AUTO_READ_FOOTER = ("只读，不保存登录", "Read-only, no login kept")


def menu_tier(state: dict | None, chosen: bool) -> int:
    """Row tier in the provider menus: 0 chosen (ticked / auto-read on),
    1 detected (installed, running, needs login, or not probed yet),
    2 not detected (greyed, at the bottom)."""
    if chosen:
        return 0
    if isinstance(state, dict) and not state.get("installed"):
        return 2
    return 1


def tiered(ids, chosen, states) -> list[tuple[int, str]]:
    """(tier, id) in tier order; the fixed registry order is kept inside a
    tier (stable sort)."""
    rows = [(menu_tier(states.get(pid) if isinstance(states, dict) else None, pid in chosen), pid) for pid in ids]
    return sorted(rows, key=lambda row: row[0])


def limit_menu_text(limit: int, language: str = "zh-CN") -> str:
    return f"Up to {limit} shown" if language == "en" else f"最多显示 {limit} 个"


def limit_hint_text(limit: int, language: str = "zh-CN") -> str:
    """Hint shown when a further model is ticked past ``MAX_SELECTED``."""
    return (f"Up to {limit} shown · untick one first" if language == "en"
            else f"最多同时显示 {limit} 个，请先取消一个")


def auto_read_status(state: dict | None, consented: bool, english: bool = False) -> tuple[str, bool]:
    """(state label, clickable) of one app in the 自动读取额度 menu."""
    if consented:
        if isinstance(state, dict) and state.get("session_relogin"):
            return ("On · sign in again" if english else "已开启 · 需重新登录"), True
        return ("On ✓" if english else "已开启 ✓"), True
    if isinstance(state, dict) and state.get("installed") is False:
        return ("Not installed" if english else "未安装"), False
    if isinstance(state, dict) and state.get("signed_in") is False:
        return ("Sign in first" if english else "需登录"), True
    return ("Off" if english else "未开启"), True


def still_pending(state: dict | None, granted_at: float, now: float, timeout: float = PENDING_TIMEOUT) -> bool:
    """True while a just-allowed provider has no session result yet."""
    if now - granted_at > timeout:
        return False
    return not (isinstance(state, dict) and state.get("session_quota")
                and float(state.get("checked_at") or 0) >= granted_at)


def consent_precheck(state: dict | None, app: str) -> str | None:
    """Why the consent dialog must not be offered yet, or None.

    Session reading needs the app installed and signed in on this PC; asking
    for consent before that would promise something we cannot do.  An
    unknown state (first probe not finished) does not block the dialog.
    """
    if not isinstance(state, dict):
        return None
    if state.get("installed") is False:
        return f"未检测到 {app}。请先在本机安装并登录 {app}，再开启自动读取额度。"
    if state.get("signed_in") is False:
        return f"{app} 尚未登录。请先在 {app} 中登录，再开启自动读取额度。"
    return None


def windows_dpi_scale(fallback: float = 1.0) -> float:
    try:
        dpi = _dll("user32").GetDpiForSystem()
        return max(1.0, dpi / 96) if dpi else fallback
    except Exception:
        return fallback


def windows_consent_dialog(app: str, provider: str, scale: float | None = None, *,
                           icon_path: str | None = None) -> bool:
    """Modal 允许/暂不 (UI thread only). True only on 允许."""
    from . import ui_dialogs
    spec = ui_dialogs.consent_spec(app, app, CONSENT_ETA_SECONDS)
    return ui_dialogs.show_dialog(spec, scale=scale, icon_path=icon_path) == "allow"


def windows_revoke_dialog(name: str, scale: float | None = None, *, icon_path: str | None = None) -> bool:
    from . import ui_dialogs
    return ui_dialogs.show_dialog(ui_dialogs.revoke_spec(name), scale=scale, icon_path=icon_path) == "revoke"


def windows_notice_dialog(text: str, scale: float | None = None, *, name: str = "",
                          icon_path: str | None = None) -> None:
    from . import ui_dialogs
    ui_dialogs.show_dialog(ui_dialogs.notice_spec(text, name), scale=scale, icon_path=icon_path)


class CodexUsageSurface:
    def __init__(self, window, snapshot: Callable[[], dict], *, language: Callable[[], str],
                 open_settings: Callable[[], object], refresh: Callable[[], object],
                 disable: Callable[[], object], quit_application: Callable[[], object],
                 open_home: Callable[[], object] | None = None,
                 display_options: Callable[[], dict] | None = None,
                 save_display: Callable[[str, object], dict] | None = None,
                 providers: Callable[[], dict] | None = None,
                 save_providers: Callable[[list], dict] | None = None,
                 rescan: Callable[[], object] | None = None,
                 save_consent: Callable[[str, bool], dict] | None = None,
                 confirm_consent: Callable[[str, str], bool] | None = None):
        self.window, self.snapshot, self.language = window, snapshot, language
        # Without a provider source the strip is exactly the Codex-only widget.
        self.providers = providers or (lambda: {"enabled": True, "selected": ["codex"], "providers": {}})
        self.save_providers = save_providers
        self.rescan = rescan or (lambda: None)
        # Session-based quota reading consent: dialog injectable for tests.
        self.save_consent = save_consent
        # One design for every popup (ui_dialogs); the icon is the app's own.
        self._dialog_icon = None
        self._dialog_name = ""
        self.confirm_consent = confirm_consent or (
            lambda app, provider: windows_consent_dialog(app, provider, self._dialog_scale(),
                                                         icon_path=self._dialog_icon))
        self.confirm_revoke = lambda name: windows_revoke_dialog(name, self._dialog_scale(),
                                                                 icon_path=self._dialog_icon)
        self.notify_blocked = lambda text: windows_notice_dialog(text, self._dialog_scale(),
                                                                 name=self._dialog_name, icon_path=self._dialog_icon)
        self.notify_toast = self._show_toast
        self._pending: dict = {}
        self._extra_columns: list = []
        self.open_settings, self.refresh = open_settings, refresh
        self.open_home = open_home or open_settings
        self.display_options = display_options or (lambda: {})
        self.save_display = save_display
        self._display_error = False
        self.disable, self.quit_application = disable, quit_application
        self.active = False
        self.closed = False
        self.started = False
        self.form = self.timer = self.status_item = None
        self._delegate = None
        self._dispatch = None
        self._last_signature = None

    def _action(self, callback) -> None:
        # RPC/settings work and window restore must never block a native UI loop.
        def invoke():
            try:
                callback()
            except Exception:
                logging.getLogger("vram_radar").warning("Codex surface action failed")
        threading.Thread(target=invoke, daemon=True, name="codex-surface-action").start()

    @staticmethod
    def _consent_spec(provider_id):
        from .providers import PROVIDERS
        return next((spec for spec in PROVIDERS if spec.id == provider_id and spec.needs_session_consent), None)

    def has_session_consent(self, provider_id) -> bool:
        try:
            return provider_id in (self.providers().get("session_consent") or ())
        except Exception:
            return False

    def request_session_consent(self, provider_id) -> bool:
        """UI thread. Ask a capable, not yet consented provider's consent.
        确认 persists it; 取消 changes nothing, so a just-ticked provider stays
        shown with its local-only status."""
        spec = self._consent_spec(provider_id)
        if spec is None or self.save_consent is None or self.has_session_consent(provider_id):
            return False
        try:
            states = (self.providers() or {}).get("providers") or {}
        except Exception:
            states = {}
        state = states.get(provider_id) if isinstance(states, dict) else None
        self._dialog_icon = (state or {}).get("install_path") if isinstance(state, dict) else None
        self._dialog_name = spec.name
        blocked = consent_precheck(state, spec.session_app or spec.name)
        if blocked:
            try:
                self.notify_blocked(blocked)
            except Exception:
                logging.getLogger("vram_radar").warning("session consent notice failed")
            return False
        try:
            accepted = self.confirm_consent(spec.session_app or spec.name, spec.session_server or spec.name) is True
        except Exception:
            logging.getLogger("vram_radar").warning("session consent dialog failed")
            accepted = False
        if accepted:
            # Strip shows 查询中 until the immediate fetch (save_consent
            # triggers it) delivers the first value.
            self._pending[provider_id] = time.time()
            self._action(lambda: self._store_consent(provider_id, True))
            self._toast(f"正在读取 {spec.name} 额度…", f"约 {CONSENT_ETA_SECONDS} 秒内显示在任务栏", spec.name)
        return accepted

    def revoke_session_consent(self, provider_id) -> None:
        if self.save_consent is not None and self._consent_spec(provider_id) is not None:
            self._pending.pop(provider_id, None)
            self._action(lambda: self._store_consent(provider_id, False))

    def _consent_clicked(self, provider_id) -> None:
        if self.has_session_consent(provider_id):
            spec = self._consent_spec(provider_id)
            if spec is None:
                return
            try:
                state = ((self.providers() or {}).get("providers") or {}).get(provider_id)
            except Exception:
                state = None
            self._dialog_icon = state.get("install_path") if isinstance(state, dict) else None
            try:
                confirmed = self.confirm_revoke(spec.name) is True
            except Exception:
                logging.getLogger("vram_radar").warning("revoke dialog failed")
                confirmed = False
            if confirmed:
                self.revoke_session_consent(provider_id)
                self._toast(f"已关闭 {spec.name} 的自动读取", "可随时在同一菜单重新开启", spec.name)
        else:
            self.request_session_consent(provider_id)

    def _dialog_scale(self):
        """Monitor DPI of the strip (dialogs follow the screen, not the
        taskbar-fitted strip scale); None -> system DPI."""
        try:
            # Same source as the menu (DeviceDpi stays 96 under system DPI awareness).
            return max(1.0, windows_taskbar_dpi(windows_dpi_scale() * 96) / 96)
        except Exception:
            return None

    def _toast(self, headline, line="", name="") -> None:
        try:
            self.notify_toast(headline, line, name)
        except Exception as exc:
            logging.getLogger("vram_radar").info("notice unavailable (%s)", type(exc).__name__)

    def _show_toast(self, headline, line="", name="") -> None:
        from . import ui_dialogs
        form = self.form
        anchor = (form.Left, form.Top, form.Right, form.Bottom) if form is not None and form.Visible else None
        icon = self._dialog_icon if name and name == self._dialog_name else None
        if not name:
            icon = sys.executable if getattr(sys, "frozen", False) else None
        ui_dialogs.show_toast(ui_dialogs.toast_spec(headline, line, name or "显存雷达"), anchor,
                              scale=self._dialog_scale(), icon_path=icon)

    def _store_consent(self, provider_id, granted: bool) -> None:
        try:
            self._display_error = not bool(self.save_consent(provider_id, granted).get("ok"))
        except Exception:
            self._display_error = True

    def _disable(self) -> None:
        # Keep settings reachable when disabling the only macOS menu-bar entry.
        self.disable()
        self.open_settings()

    def start(self) -> None:
        if self.started or self.closed:
            return
        self.started = True
        try:
            if sys.platform == "win32":
                from System import Action
                self._dispatch = lambda callback: self.window.native.Invoke(Action(callback))
                self._dispatch(self._start_windows)
            elif sys.platform == "darwin":
                from PyObjCTools.AppHelper import callAfter
                self._dispatch = callAfter
                callAfter(self._start_macos)
        except Exception:
            self.started = False
            logging.getLogger("vram_radar").warning("Codex native usage display could not start")

    def _start_windows(self) -> None:
        if self.closed:
            return
        from System.Drawing import Color, ContentAlignment, Font, FontStyle, GraphicsUnit, Pen, Point, Region, Size, SolidBrush
        from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
        from System.Windows.Forms import (AutoScaleMode, ContextMenuStrip, Cursor, Form,
            FormBorderStyle, FormStartPosition, Label, MouseButtons, Screen, Timer, ToolTip,
            Padding, ToolStripProfessionalRenderer, ToolStripSeparator, SystemInformation)

        # CodexUsage's two-disk layout, with a tighter number column and Radar colors.
        form = Form()
        self.form = form
        form.Text = "Codex · VRAM Radar"
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.AutoScaleMode = getattr(AutoScaleMode, "None")
        form.BackColor = Color.FromArgb(23, 31, 35)
        self._scale = max(1, float(self.window.native.DeviceDpi) / 96)
        geometry = windows_taskbar_geometry()
        self._scale = taskbar_scale(windows_taskbar_dpi(float(self.window.native.DeviceDpi)), geometry)
        scale = lambda value: round(value * self._scale)
        # Before handle creation, WinForms applies the system's minimum tracked
        # size even to borderless forms. Create it first to retain the compact card.
        _ = form.Handle
        form.ClientSize = Size(scale(78), scale(40))
        quota_color = Color.FromArgb(67, 220, 55)
        time_color = Color.FromArgb(143, 208, 248)
        track_color = Color.FromArgb(43, 56, 63)
        warning_color = Color.FromArgb(224, 176, 100)
        self._reading = widget_reading({})
        self._selected_id = None
        self._hovered = False

        def rounded_path(inset=0):
            path = GraphicsPath()
            diameter, width, height = scale(16), form.Width-1-inset, form.Height-1-inset
            for x, y, angle in [(inset, inset, 180), (width-diameter, inset, 270),
                                 (width-diameter, height-diameter, 0), (inset, height-diameter, 90)]:
                path.AddArc(x, y, diameter, diameter, angle, 90)
            path.CloseFigure()
            return path

        def reshape(*_):
            path = rounded_path()
            old_region = form.Region
            form.Region = Region(path)
            path.Dispose()
            if old_region is not None:
                old_region.Dispose()
            form.Invalidate()

        form.SizeChanged += reshape
        reshape()

        def paint(_sender, event):
            graphics = event.Graphics
            graphics.SmoothingMode = SmoothingMode.AntiAlias
            path = rounded_path()
            pen = Pen(track_color, 1)
            try:
                fill = getattr(self, "_fill", None)
                if fill is not None:
                    pill = rounded_path(1)
                    brush = SolidBrush(fill)
                    try:
                        graphics.FillPath(brush, pill)
                    finally:
                        brush.Dispose()
                        pill.Dispose()
                if self._hovered:
                    graphics.DrawPath(pen, path)
            finally:
                pen.Dispose()
                path.Dispose()

        form.Paint += paint
        # Showing the strip must not interrupt typing in another application.
        import ctypes
        from ctypes import wintypes
        user32 = _dll("user32")
        get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
        set_style = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        handle = int(form.Handle.ToInt64())
        set_style(handle, -20, get_style(handle, -20) | 0x08000000 | 0x00000080)
        tooltip = ToolTip()
        self._tooltip = tooltip
        self._position = None
        self._placement = "taskbar"
        self._drag = None
        self._drag_origin = None
        self._drag_moved = False
        self._labels, self._countdowns, self._captions = [], [], []
        self._fonts = [Font("Segoe UI", scale(14), FontStyle.Bold, GraphicsUnit.Pixel),
                       Font("Segoe UI", scale(14), FontStyle.Bold, GraphicsUnit.Pixel)]
        for y, collection in [(0, self._labels), (20, self._countdowns)]:
            label = Label()
            label.AutoSize = False
            label.Font = self._fonts[0 if y == 0 else 1]
            label.TextAlign = ContentAlignment.MiddleRight
            label.ForeColor = Color.FromArgb(231, 238, 242) if y == 0 else time_color
            # Transparent labels show what the form paints (pill or nothing),
            # so rounded pills keep their corners under the text.
            label.BackColor = Color.Transparent
            label.Location = Point(scale(5), scale(y))
            label.Size = Size(scale(47), scale(20))
            form.Controls.Add(label)
            collection.append(label)
        text_controls = [*self._labels, *self._countdowns]
        def update_hover(*_):
            hovered = form.Visible and form.Bounds.Contains(Cursor.Position)
            if hovered != self._hovered:
                self._hovered = hovered
                form.Invalidate()
        for control in [form, *text_controls]:
            control.MouseEnter += update_hover
            control.MouseLeave += update_hover

        self._layout = TaskbarLayout(background=True)
        self._slot = "tray"
        self._placer = PlacementDebouncer(confirm=3, switch_confirm=5)
        self._bar_handle = None
        self._obscured_ticks = 0
        self._inactive_ticks = 0
        self._last_overview = {}
        self._fit_key = None
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND

        def position():
            geometry = windows_taskbar_geometry()
            if self._placement == "taskbar" and geometry:
                bar, tray = geometry
                bar_handle = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
                if bar_handle != self._bar_handle:
                    # First run or Explorer restart: re-read the layout now but
                    # keep the committed spot until new readings confirm a move.
                    self._bar_handle = bar_handle
                    self._layout.invalidate()
                size = (form.Width, form.Height)
                own = (form.Left, form.Top, form.Right, form.Bottom) if form.Visible else None
                elements = self._layout.elements(bar_handle, bar, own)
                target = self._placer.propose(docked_target(bar, tray, elements, size, scale(6), scale(4)))
                self._slot = target[0]
                x, y = docked_point(bar, target, size[0])
                if (form.Left, form.Top) != (x, y):
                    form.Location = Point(x, y)
                return
            if self._placement == "taskbar" and self._placer.current is not None:
                # Taskbar momentarily unreadable (Explorer restarting, DPI
                # change): stay put instead of jumping to free placement.
                return
            self._placer.reset()
            screen = Screen.FromPoint(Point(*self._position)) if self._position else Screen.PrimaryScreen
            area = screen.Bounds if self._position else screen.WorkingArea
            x, y = strip_bounds((area.Left, area.Top, area.Right, area.Bottom),
                                (form.Width, form.Height), self._position, scale(8))
            form.Location = Point(x, y)

        def mouse_down(sender, event):
            if event.Button == MouseButtons.Right:
                cancel_click()
            if event.Button == MouseButtons.Left:
                self._drag = (Cursor.Position.X - form.Left, Cursor.Position.Y - form.Top)
                self._drag_origin = (Cursor.Position.X, Cursor.Position.Y)
                self._drag_moved = False
                sender.Capture = True

        def mouse_move(sender, event):
            if self._drag is not None and self._placement == "free":
                if max(abs(Cursor.Position.X-self._drag_origin[0]), abs(Cursor.Position.Y-self._drag_origin[1])) > scale(3):
                    self._drag_moved = True
                if self._drag_moved:
                    click_timer.Stop()
                    self._last_click = None
                    self._placement = "free"
                    self._position = (Cursor.Position.X-self._drag[0], Cursor.Position.Y-self._drag[1])
                    position()

        def mouse_up(sender, event):
            clicked = self._drag is not None and not self._drag_moved and event.Button == MouseButtons.Left
            self._drag = None
            sender.Capture = False
            if clicked:
                point = (Cursor.Position.X, Cursor.Position.Y)
                if click_timer.Enabled and self._last_click is not None and max(
                        abs(point[i]-self._last_click[i]) for i in (0, 1)) <= SystemInformation.DoubleClickSize.Width:
                    click_timer.Stop()
                    self._last_click = None
                    self._action(self.open_settings)
                else:
                    if click_timer.Enabled:
                        self._action(self.open_home)
                    self._last_click = point
                    click_timer.Stop()
                    click_timer.Start()

        click_timer = Timer()
        self._click_timer = click_timer
        self._last_click = None
        click_timer.Interval = SystemInformation.DoubleClickTime
        def single_click(*_):
            click_timer.Stop()
            self._last_click = None
            self._action(self.open_home)
        click_timer.Tick += single_click
        def cancel_click(*_):
            click_timer.Stop()
            self._last_click = None
        self._cancel_click = cancel_click
        self._up_handler = mouse_up
        self._single_click_handler = single_click

        self._move_handler = mouse_move

        for control in [form, *text_controls]:
            control.MouseDown += mouse_down
            control.MouseMove += mouse_move
            control.MouseUp += mouse_up
        menu = ContextMenuStrip()
        self._menu = menu
        menu.Opening += cancel_click
        menu.AutoClose = True
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        def activate_menu_owner(*_):
            # A tray-style non-activating owner must become foreground on the
            # explicit right-click so native outside-click dismissal works.
            user32.SetForegroundWindow(handle)
        menu.Opening += activate_menu_owner
        def menu_closed(*_):
            user32.PostMessageW(handle, 0, 0, 0)
            if getattr(self, "_tick_deferred", False):
                self._tick_deferred = False
                self._tick()
        menu.Closed += menu_closed
        self._menu_back = form.BackColor
        self._fill = None
        menu.BackColor = form.BackColor
        menu.ForeColor = Color.FromArgb(231, 238, 242)
        menu.Font = Font("Microsoft YaHei UI", 9, FontStyle.Regular)
        self._menu_font = menu.Font
        menu.Padding = Padding(6)
        menu.ShowImageMargin = True
        menu.ShowCheckMargin = False
        renderer = ToolStripProfessionalRenderer()
        renderer.RoundedEdges = False
        def menu_path(width, height, inset=0, radius=8):
            path = GraphicsPath()
            d = radius*2
            for x, y, angle in [(inset, inset, 180), (width-inset-d, inset, 270),
                                (width-inset-d, height-inset-d, 0), (inset, height-inset-d, 90)]:
                path.AddArc(x, y, d, d, angle, 90)
            path.CloseFigure()
            return path
        def fill_background(_sender, event):
            brush = SolidBrush(self._menu_back)
            try:
                event.Graphics.FillRectangle(brush, event.AffectedBounds)
            finally:
                brush.Dispose()
        def fill_item(_sender, event):
            brush = SolidBrush(self._menu_back)
            try:
                event.Graphics.FillRectangle(brush, 0, 0, event.Item.Width, event.Item.Height)
                if event.Item.Selected:
                    path = menu_path(event.Item.Width, event.Item.Height, 1, 5)
                    highlight = SolidBrush(self._menu_hover)
                    try:
                        event.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                        event.Graphics.FillPath(highlight, path)
                    finally:
                        highlight.Dispose()
                        path.Dispose()
            finally:
                brush.Dispose()
        def draw_border(_sender, event):
            pen = Pen(self._menu_hover)
            try:
                path = menu_path(event.ToolStrip.Width-1, event.ToolStrip.Height-1)
                try:
                    event.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                    event.Graphics.DrawPath(pen, path)
                finally:
                    path.Dispose()
            finally:
                pen.Dispose()
        renderer.RenderToolStripBackground += fill_background
        renderer.RenderImageMargin += fill_background
        renderer.RenderMenuItemBackground += fill_item
        renderer.RenderToolStripBorder += draw_border
        def draw_arrow(_sender, event):
            rect = event.ArrowRectangle
            brush = SolidBrush(self._menu_back)
            pen = Pen(menu.ForeColor, 1.5)
            try:
                event.Graphics.FillRectangle(brush, rect)
                x, y = rect.Left+rect.Width//2, rect.Top+rect.Height//2
                event.Graphics.DrawLine(pen, x-2, y-3, x+1, y)
                event.Graphics.DrawLine(pen, x+1, y, x-2, y+3)
            finally:
                brush.Dispose()
                pen.Dispose()
        renderer.RenderArrow += draw_arrow
        def draw_check(_sender, event):
            rect = event.ImageRectangle
            brush = SolidBrush(self._menu_hover if event.Item.Selected else self._menu_back)
            pen = Pen(menu.ForeColor, 1.6)
            try:
                event.Graphics.FillRectangle(brush, rect.Left-2, 0, rect.Width+4, event.Item.Height)
                x, y = rect.Left+rect.Width//2, event.Item.Height//2
                event.Graphics.SmoothingMode = SmoothingMode.AntiAlias
                event.Graphics.DrawLine(pen, x-4, y, x-1, y+3)
                event.Graphics.DrawLine(pen, x-1, y+3, x+5, y-4)
            finally:
                brush.Dispose()
                pen.Dispose()
        def draw_separator(_sender, event):
            brush = SolidBrush(self._menu_back)
            pen = Pen(self._menu_hover)
            try:
                event.Graphics.FillRectangle(brush, 0, 0, event.Item.Width, event.Item.Height)
                y = event.Item.Height//2
                event.Graphics.DrawLine(pen, 10, y, event.Item.Width-10, y)
            finally:
                brush.Dispose()
                pen.Dispose()
        renderer.RenderItemCheck += draw_check
        renderer.RenderSeparator += draw_separator
        menu.Renderer = renderer
        self._menu_renderer = renderer
        self._menu_hover = Color.FromArgb(52, 52, 52)
        def round_menu(sender, _event):
            if sender.Width < 20 or sender.Height < 20:
                return
            path = menu_path(sender.Width, sender.Height)
            previous = sender.Region
            sender.Region = Region(path)
            path.Dispose()
            if previous is not None:
                previous.Dispose()
        menu.SizeChanged += round_menu
        actions = [("刷新额度", "Refresh usage", self.refresh),
                   ("隐藏额度条", "Hide usage strip", self._disable),
                   ("退出 VRAM Radar", "Quit VRAM Radar", self.quit_application)]
        action_items = []
        for zh, en, action in actions:
            item = menu.Items.Add(zh)
            item.Padding = Padding(4, 4, 8, 4)
            item.Click += lambda _sender, _event, callback=action: self._action(callback)
            action_items.append(item)
        menu.Items.Remove(action_items[-1])
        menu.Items.Add(ToolStripSeparator())
        def place(mode):
            self._placement, self._position = mode, None
            tick()
        self._dock_item = menu.Items.Add("放回任务栏")
        self._dock_item.Click += lambda *_: place("free" if self._placement == "taskbar" else "taskbar")
        self._window_menu = menu.Items.Add("显示周期")
        menu.Items.Remove(self._window_menu)
        self._window_menu.DropDown.BackColor = menu.BackColor
        self._window_menu.DropDown.ForeColor = menu.ForeColor
        self._window_menu.DropDown.Renderer = renderer
        self._window_menu.DropDown.Font = menu.Font
        self._window_menu.DropDown.SizeChanged += round_menu
        self._display_menu = menu.Items.Add("显示设置")
        self._display_menu.DropDown.Renderer = renderer
        self._display_menu.DropDown.SizeChanged += round_menu
        self._display_choices = []
        def save_choice(key, value):
            def save():
                try:
                    self._display_error = not bool(self.save_display and self.save_display(key, value).get("ok"))
                except Exception:
                    self._display_error = True
            self._action(save)
        self._background_title = self._display_menu.DropDownItems.Add("背景")
        self._background_title.Enabled = False
        for style in BACKGROUND_STYLES:
            zh, en = BACKGROUND_LABELS[style]
            item = self._display_menu.DropDownItems.Add(zh)
            item.Click += lambda _s, _e, v=style: save_choice("usage_background", v)
            self._display_choices.append((item, "usage_background", style, zh, en))
        for item in (self._dock_item, self._window_menu):
            item.Padding = Padding(4, 4, 8, 4)
        # Multi-provider picker (multi-select, persisted in the Profile).
        from .providers import PROVIDERS
        self._models_menu = menu.Items.Add("显示模型")
        self._models_menu.DropDown.Renderer = renderer
        self._models_menu.DropDown.SizeChanged += round_menu
        self._model_items = {}
        def show_limit_hint():
            from .providers import MAX_SELECTED
            try:
                text = limit_hint_text(MAX_SELECTED, self.language())
                self._limit_item.Visible = True
                self._limit_item.Text = limit_menu_text(MAX_SELECTED, self.language())
                if form.Visible:
                    self._toast(text, "", "")
            except Exception:
                logging.getLogger("vram_radar").info("model limit hint failed")

        def toggle_model(provider_id):
            try:
                current = list(self.providers().get("selected") or ["codex"])
            except Exception:
                current = ["codex"]
            from .providers import MAX_SELECTED
            if provider_id not in current and len(current) >= MAX_SELECTED:
                show_limit_hint()  # the strip lays out at most MAX_SELECTED apps legibly
                return
            chosen = [x for x in current if x != provider_id] if provider_id in current else current + [provider_id]
            if not chosen:
                return  # keep at least one; the strip (and this menu) must stay reachable
            def save():
                try:
                    self._display_error = not bool(self.save_providers and self.save_providers(chosen).get("ok"))
                except Exception:
                    self._display_error = True
            self._action(save)
            if provider_id not in current:
                self.request_session_consent(provider_id)  # 取消 keeps it ticked, local-only
        for spec in PROVIDERS:
            item = self._models_menu.DropDownItems.Add(spec.name)
            item.Click += lambda _s, _e, pid=spec.id: toggle_model(pid)
            self._model_items[spec.id] = (item, spec)
        self._limit_item = self._models_menu.DropDownItems.Add("最多同时显示 4 个")
        self._limit_item.Enabled = False
        self._models_tail_sep = ToolStripSeparator()
        self._models_menu.DropDownItems.Add(self._models_tail_sep)
        self._rescan_item = self._models_menu.DropDownItems.Add("重新检测")
        self._rescan_item.Click += lambda *_: self._action(self.rescan)
        self._menu_order, self._menu_separators = {}, {}
        # 自动读取额度: one row per capable app -- its icon, name and a plain
        # state on the right (已开启 ✓ / 未开启 / 需登录 / 未安装, greyed).
        self._consent_menu = menu.Items.Add("自动读取额度")
        self._consent_menu.DropDown.Renderer = renderer
        self._consent_menu.DropDown.SizeChanged += round_menu
        self._consent_items = {}
        for spec in PROVIDERS:
            if spec.needs_session_consent:
                item = self._consent_menu.DropDownItems.Add(spec.name)
                item.ShowShortcutKeys = True
                item.Click += lambda _s, _e, pid=spec.id: self._consent_clicked(pid)
                self._consent_items[spec.id] = (item, spec)
        self._consent_tail_sep = ToolStripSeparator()
        self._consent_menu.DropDownItems.Add(self._consent_tail_sep)
        self._consent_rescan = self._consent_menu.DropDownItems.Add("重新检测")
        self._consent_rescan.Click += lambda *_: self._action(self.rescan)
        self._consent_hint = self._consent_menu.DropDownItems.Add(AUTO_READ_FOOTER[0])
        self._consent_hint.Enabled = False
        self._consent_menu.Visible = bool(self._consent_items)
        self._item_icons = {}
        menu.Items.Add(ToolStripSeparator())
        menu.Items.Add(action_items[-1])
        menu_scale = windows_taskbar_dpi()/96
        self._menu_icon_font = Font("Segoe UI Symbol", 12, FontStyle.Regular)
        descriptions = {
            action_items[0]: ("↻", "立即同步最新额度", "Fetch the latest usage"),
            action_items[1]: ("−", "可在设置中重新开启", "Restore from Settings"),
            self._dock_item: ("✓", "固定显示在展开箭头旁", "Keep beside the system tray"),
            self._display_menu: ("☷", "显示设置", "Display options"),
            self._models_menu: ("◉", "显示模型", "Models"),
            self._consent_menu: ("✦", "自动读取额度", "Read quota automatically"),
            action_items[-1]: ("×", "关闭软件与额度显示", "Close Radar and its widget"),
        }
        for item in descriptions:
            item.AutoSize = False
            item.Size = Size(round(205*menu_scale), round(34*menu_scale))
        def scale_menu(dpi):
            nonlocal menu_scale
            menu_scale = dpi/96
            self._menu_dpi = dpi
            old_font, old_icon = self._menu_font, self._menu_icon_font
            self._menu_font = Font("Microsoft YaHei UI", round(12*menu_scale), FontStyle.Regular, GraphicsUnit.Pixel)
            self._menu_icon_font = Font("Segoe UI Symbol", round(16*menu_scale), FontStyle.Regular, GraphicsUnit.Pixel)
            menu.Font = self._menu_font
            self._window_menu.DropDown.Font = menu.Font
            self._display_menu.DropDown.Font = menu.Font
            self._models_menu.DropDown.Font = menu.Font
            self._consent_menu.DropDown.Font = menu.Font
            for drop in (self._models_menu.DropDown, self._consent_menu.DropDown):
                drop.ImageScalingSize = Size(round(18*menu_scale), round(18*menu_scale))
            self._item_icons.clear()
            menu.Padding = Padding(round(6*menu_scale))
            for item in descriptions:
                item.Size = Size(round(205*menu_scale), round(34*menu_scale))
            old_font.Dispose()
            old_icon.Dispose()
        scale_menu(windows_taskbar_dpi())
        def align_menu_items(_sender, _event):
            # Native submenu placement uses the item's bounds, not the popup's
            # bounds. Match owner-drawn rows to the final auto-sized popup.
            for item in descriptions:
                item.Width = menu.ClientSize.Width - item.Bounds.Left
        menu.Opened += align_menu_items
        def draw_item_text(_sender, event):
            if event.Item not in descriptions:
                return
            fill_item(_sender, event)
            icon, zh, en = descriptions[event.Item]
            foreground = SolidBrush(menu.ForeColor)
            accent = SolidBrush(menu.ForeColor)
            try:
                if event.Item == self._dock_item and not self._dock_item.Checked:
                    icon = "↗"
                event.Graphics.DrawString(icon, self._menu_icon_font, accent, float(8*menu_scale), float(7*menu_scale))
                event.Graphics.DrawString(event.Item.Text, menu.Font, foreground, float(34*menu_scale), float(8*menu_scale))
            finally:
                foreground.Dispose()
                accent.Dispose()
        renderer.RenderItemText += draw_item_text
        self._window_menu_signature = None
        form.ContextMenuStrip = menu
        for control in text_controls:
            control.ContextMenuStrip = menu

        # Colour-keyed pixels (the whole "transparent" background, and the
        # area around a pill) are click-through: right-clicks between letters
        # fell to the taskbar.  A practically invisible (alpha 1/255) layered
        # window directly under the strip takes every click in its rectangle.
        catcher = Form()
        self._catcher = catcher
        catcher.Text = "VRAM Radar strip hit area"
        catcher.FormBorderStyle = getattr(FormBorderStyle, "None")
        catcher.StartPosition = FormStartPosition.Manual
        catcher.ShowInTaskbar = False
        catcher.TopMost = True
        catcher.AutoScaleMode = getattr(AutoScaleMode, "None")
        catcher.BackColor = Color.FromArgb(0, 0, 0)
        catcher.Opacity = CATCHER_OPACITY
        catcher_handle = int(catcher.Handle.ToInt64())
        set_style(catcher_handle, -20, get_style(catcher_handle, -20) | 0x08000000 | 0x00000080)
        catcher.ContextMenuStrip = menu
        catcher.MouseDown += mouse_down
        catcher.MouseMove += mouse_move
        catcher.MouseUp += mouse_up
        catcher.MouseEnter += update_hover
        catcher.MouseLeave += update_hover
        self._catcher_state = None
        def sync_catcher(*_):
            if self.closed:
                return
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
            state = (form.Left, form.Top, form.Width, form.Height) if form.Visible else None
            if state is None:
                if self._catcher_state is not None:
                    user32.SetWindowPos(catcher_handle, None, 0, 0, 0, 0, 0x0080 | 0x0010 | 0x0013)
                self._catcher_state = None
                return
            # Directly below the strip (insert-after = strip), never activated.
            user32.SetWindowPos(catcher_handle, handle, *state, 0x0040 | 0x0010)
            self._catcher_state = state
        self._sync_catcher = sync_catcher
        form.LocationChanged += sync_catcher
        form.SizeChanged += sync_catcher
        form.VisibleChanged += sync_catcher

        self._name_font = Font("Segoe UI", scale(12), FontStyle.Regular, GraphicsUnit.Pixel)
        self._value_font = Font("Segoe UI", scale(13), FontStyle.Bold, GraphicsUnit.Pixel)
        self._fit_fonts = {}

        def make_column():
            pair = []
            for font in (self._name_font, self._value_font):
                label = Label()
                label.AutoSize = False
                label.Font = font
                label.TextAlign = ContentAlignment.MiddleLeft
                label.BackColor = Color.Transparent
                label.ForeColor = menu.ForeColor
                label.Location = Point(scale(5), 0)
                label.Size = Size(scale(47), scale(20))
                label.ContextMenuStrip = menu
                label.MouseEnter += update_hover
                label.MouseLeave += update_hover
                label.MouseDown += mouse_down
                label.MouseMove += mouse_move
                label.MouseUp += mouse_up
                form.Controls.Add(label)
                pair.append(label)
            self._extra_columns.append(tuple(pair))
            return tuple(pair)

        def choose(identity):
            self._selected_id = identity
            tick()

        def tick(*_):
            nonlocal quota_color, time_color, track_color, warning_color
            if self.closed:
                return
            if menu.Visible:
                # Menu open: keep the UI thread free for it; refresh on close.
                self._tick_deferred = True
                return
            perf_start = time.perf_counter()
            try:
                tick_body()
            finally:
                note_tick((time.perf_counter() - perf_start) * 1000)

        def note_tick(elapsed):
            self._tick_count = getattr(self, "_tick_count", 0) + 1
            self._tick_total = getattr(self, "_tick_total", 0.0) + elapsed
            self._tick_max = max(getattr(self, "_tick_max", 0.0), elapsed)
            if PERF_LOG and self._tick_count % 60 == 0:
                logging.getLogger("vram_radar").info("surface tick avg %.1f ms max %.1f ms over %d",
                                                     self._tick_total / self._tick_count, self._tick_max, self._tick_count)
                self._tick_count, self._tick_total, self._tick_max = 0, 0.0, 0.0

        def tick_body():
            nonlocal quota_color, time_color, track_color, warning_color
            state = self.snapshot()
            language = self.language()
            options = self.display_options()
            try:
                overview = self.providers() or {}
                self._last_overview = overview
            except Exception:
                overview = self._last_overview
            selected = [x for x in (overview.get("selected") or ["codex"]) if isinstance(x, str)] or ["codex"]
            provider_states = overview.get("providers") if isinstance(overview.get("providers"), dict) else {}
            master_enabled = overview.get("enabled", True) is not False
            others = [spec for pid, (item, spec) in self._model_items.items()
                      if pid in selected and pid != "codex"] if master_enabled else []
            multi = bool(others)
            english = language == "en"
            self._models_menu.Text = "Models" if english else "显示模型"
            self._rescan_item.Text = "Detect again" if english else "重新检测"
            self._consent_menu.Text = "Read quota automatically" if english else "自动读取额度"
            self._consent_hint.Text = AUTO_READ_FOOTER[1 if english else 0]
            consented = overview.get("session_consent") or ()
            icon_px = round(18*menu_scale)
            def set_icon(item, spec, pstate):
                path = pstate.get("install_path") if isinstance(pstate, dict) else None
                key = (path, icon_px)
                if self._item_icons.get(item) != key:
                    self._item_icons[item] = key
                    try:
                        from .ui_dialogs import provider_icon
                        item.Image = provider_icon(path, icon_px, spec.name)
                    except Exception:
                        item.Image = None
            for pid, (item, spec) in self._consent_items.items():
                pstate = provider_states.get(pid)
                label, clickable = auto_read_status(pstate, pid in consented, english)
                item.Text = spec.name
                item.ShortcutKeyDisplayString = label
                item.Enabled = clickable
                set_icon(item, spec, pstate)
            from .providers import MAX_SELECTED
            full = len(selected) >= MAX_SELECTED
            for pid, (item, spec) in self._model_items.items():
                pstate = provider_states.get(pid)
                item.Text = spec.name
                item.ShowShortcutKeys = True
                item.ShortcutKeyDisplayString = _provider_status(pstate, english)
                set_icon(item, spec, pstate)
                item.Checked = pid in selected
                # Keep unticked items clickable at the limit so a 5th tick can
                # explain itself (hint) instead of silently doing nothing.
                item.Enabled = pid in selected or pstate is None or bool(pstate.get("installed"))
            # Rows in tiers (chosen, detected, not detected), fixed order
            # inside a tier.  Re-arranged only when the tiers change; ticks
            # pause while the menu is open, so rows never jump under the mouse.
            def arrange(name, items, rows, tail):
                key = tuple(rows)
                if self._menu_order.get(name) == key:
                    return
                self._menu_order[name] = key
                pool = self._menu_separators.setdefault(name, [])
                collection = items
                collection.Clear()
                used, last = 0, None
                for tier, item in rows:
                    if last is not None and tier != last:
                        if used >= len(pool):
                            pool.append(ToolStripSeparator())
                        collection.Add(pool[used])
                        used += 1
                    collection.Add(item)
                    last = tier
                for item in tail:
                    collection.Add(item)
            model_rows = [(tier, self._model_items[pid][0]) for tier, pid in
                          tiered(list(self._model_items), set(selected), provider_states)]
            arrange("models", self._models_menu.DropDownItems, model_rows,
                    [self._limit_item, self._models_tail_sep, self._rescan_item])
            consent_rows = [(tier, self._consent_items[pid][0]) for tier, pid in
                            tiered(list(self._consent_items), set(consented), provider_states)]
            arrange("consent", self._consent_menu.DropDownItems, consent_rows,
                    [self._consent_tail_sep, self._consent_rescan, self._consent_hint])
            for rescan_item in (self._rescan_item, self._consent_rescan):
                rescan_item.Text = "Detect again" if english else "重新检测"
                fore = menu.ForeColor
                glyph_key = ("↻", icon_px, int(fore.R), int(fore.G), int(fore.B))
                if self._item_icons.get(rescan_item) != glyph_key:
                    self._item_icons[rescan_item] = glyph_key
                    try:
                        from .ui_dialogs import glyph_icon
                        rescan_item.Image = glyph_icon("↻", icon_px, (fore.R, fore.G, fore.B))
                    except Exception:
                        rescan_item.Image = None
            self._limit_item.Visible = full
            self._limit_item.Text = limit_menu_text(MAX_SELECTED, language)
            self._background_title.Text = "Background" if english else "背景"
            self._display_menu.Text = ("Display options" if language == "en" else "显示设置") + (
                (" · Save failed" if language == "en" else " · 保存失败") if self._display_error else "")
            style = options.get("usage_background")
            style = style if style in BACKGROUND_STYLES else "transparent"
            for item, key, value, zh, en in self._display_choices:
                item.Text = en if language == "en" else zh
                item.Checked = style == value
            now_mono = time.monotonic()
            theme = theme_settings()
            if (theme != getattr(self, "_theme", None) or style != getattr(self, "_style", None)
                    or now_mono - getattr(self, "_palette_at", 0) >= 5):
                self._theme, self._style, self._palette_at = theme, style, now_mono
                bar_geometry = windows_taskbar_geometry()
                own = (form.Left, form.Top, form.Right, form.Bottom) if form.Visible else None
                docked = self._placement == "taskbar" and own is not None
                base = windows_taskbar_palette(bar_geometry[0] if bar_geometry else None, own,
                                               own if docked else None)[0]
                theme_changed = getattr(self, "_base_key", None) != (theme, style, docked)
                self._base_key = (theme, style, docked)
                base = tuple(base) if theme_changed else settle_color(getattr(self, "_base", None), base)
                self._base = base
                # A colour-keyed strip off the taskbar (free placement) sits on
                # arbitrary windows; give it a readable surface there.
                effective = style if docked or style != "transparent" else "dark"
                self._next_look = strip_look(effective, base, windows_accent_color())
            look = getattr(self, "_next_look", None) or strip_look(style, windows_taskbar_palette()[0])
            if look != getattr(self, "_look", None):
                self._look = look
                background, foreground, hover, bright = look["base"], look["foreground"], look["hover"], look["bright"]
                self._palette = (look["surface"], foreground, hover, bright)
                form.BackColor = Color.FromArgb(*background)
                # Colour key = the taskbar colour itself: anti-aliased text and
                # pill edges blend toward what is really behind them.
                form.TransparencyKey = form.BackColor if look["keyed"] else Color.Empty
                set_style(handle, -20, get_style(handle, -20) | 0x08000000 | 0x00000080)
                self._fill = Color.FromArgb(*look["fill"]) if look["fill"] else None
                self._menu_back = Color.FromArgb(*look["surface"])
                menu.BackColor, menu.ForeColor = self._menu_back, Color.FromArgb(*foreground)
                self._menu_hover = Color.FromArgb(*hover)
                quota_color = Color.FromArgb(*( (25, 160, 20) if bright else (67, 220, 55) ))
                time_color = Color.FromArgb(*( (0, 98, 150) if bright else (143, 208, 248) ))
                warning_color = Color.FromArgb(*( (139, 85, 0) if bright else (224, 176, 100) ))
                self._labels[0].ForeColor = quota_color
                track_color = Color.FromArgb(*tuple(max(0, v-28) if bright else min(255, v+40) for v in look["surface"]))
                for drop in (self._models_menu.DropDown, self._window_menu.DropDown, self._display_menu.DropDown):
                    drop.BackColor, drop.ForeColor = self._menu_back, menu.ForeColor
                for item in menu.Items:
                    item.ForeColor = menu.ForeColor
                form.Invalidate()
                menu.Invalidate()
            geometry = windows_taskbar_geometry()
            current_dpi = windows_taskbar_dpi(float(form.DeviceDpi))
            if current_dpi != self._menu_dpi:
                scale_menu(current_dpi)
            new_scale = taskbar_scale(current_dpi, geometry)
            if new_scale != self._scale:
                self._scale = new_scale
                font = Font("Segoe UI", scale(14), FontStyle.Bold, GraphicsUnit.Pixel)
                old_fonts, self._fonts = self._fonts, [font, Font("Segoe UI", scale(14), FontStyle.Bold, GraphicsUnit.Pixel)]
                for y, label in zip((0, 20), text_controls):
                    label.Font = self._fonts[0 if y == 0 else 1]
                    label.Location = Point(scale(5), scale(y))
                old_cell_fonts = (self._name_font, self._value_font)
                self._name_font = Font("Segoe UI", scale(12), FontStyle.Regular, GraphicsUnit.Pixel)
                self._value_font = Font("Segoe UI", scale(13), FontStyle.Bold, GraphicsUnit.Pixel)
                for top, bottom in self._extra_columns:
                    top.Font, bottom.Font = self._name_font, self._value_font
                old_fit = [font for pair in self._fit_fonts.values() for font in pair]
                self._fit_fonts = {}
                for old_font in (*old_fonts, *old_cell_fonts, *old_fit):
                    old_font.Dispose()
            rows = quota_lines(state, language) if "codex" in selected else []
            self.active = bool(rows or others)
            if not self.active:
                # A single empty snapshot (reload, provider rescan) must not
                # blink the strip; hide only when it stays empty.
                self._inactive_ticks += 1
                if self._inactive_ticks >= 3 or not form.Visible:
                    click_timer.Stop()
                    self._last_click = None
                    if form.Visible:
                        form.Hide()
                return
            self._inactive_ticks = 0
            windows = state.get("windows", []) if rows else []
            identities = [window.get("id") or f"{window.get('name')}:{window.get('window_minutes')}:{index}"
                          for index, window in enumerate(windows)]
            index = identities.index(self._selected_id) if self._selected_id in identities else 0
            signature = (language, tuple(identities))
            if signature != self._window_menu_signature:
                self._window_menu.DropDownItems.Clear()
                for identity, row in zip(identities, rows):
                    item = self._window_menu.DropDownItems.Add(f"{row['detail'].split(' · ')[0]} · {row['label']}")
                    item.Click += lambda _sender, _event, key=identity: choose(key)
                self._window_menu_signature = signature
            self._window_menu.Text = "Window" if language == "en" else "显示周期"
            self._dock_item.Text = (("Move freely" if language == "en" else "切换为自由移动")
                                    if self._placement == "taskbar" else
                                    ("Lock to taskbar" if language == "en" else "固定到任务栏"))
            self._dock_item.Checked = self._placement == "taskbar"
            self._window_menu.Enabled = bool(windows)
            for i in range(self._window_menu.DropDownItems.Count):
                self._window_menu.DropDownItems[i].Checked = i == index
            bright = self._palette[3]
            if rows:
                reading = widget_reading(state, index, language)
                quota_color = Color.FromArgb(*usage_color(reading["percent"], bright=bright))
                time_color = Color.FromArgb(*usage_color(reading["remaining_seconds"], bright=bright, waiting=True))
                self._labels[0].ForeColor = quota_color
                self._labels[0].Text = reading["value"]
                self._countdowns[0].Text = reading["countdown"]
                self._countdowns[0].ForeColor = time_color
            else:
                reading = widget_reading({})
            for label in text_controls:
                label.Visible = bool(rows) and not multi
            cells = []
            if rows and multi:
                countdown = reading["countdown"]
                cells.append(("Codex", f"{reading['value']} {countdown}".strip() if countdown != "—" else reading["value"],
                              quota_color))
            provider_rows = []
            fg = menu.ForeColor
            for spec in others:
                info = provider_reading(provider_states.get(spec.id),
                                        {"id": spec.id, "name": spec.name, "short": spec.short}, language)
                granted = self._pending.get(spec.id)
                if granted is not None:
                    pstate = provider_states.get(spec.id)
                    if still_pending(pstate, granted, time.time()):
                        info = {**info, "value": "Reading" if english else "查询中", "countdown": "",
                                "low": False, "warning": False}
                    else:
                        self._pending.pop(spec.id, None)
                        logging.getLogger("vram_radar").info(
                            "auto read %s: first result %.1f s after consent", spec.id, time.time() - granted)
                provider_rows.append(info)
                value = info["value"]
                if re.fullmatch(r"<?\d+(?:\.\d)?h", info["countdown"] or ""):
                    value = f"{value} {info['countdown']}"
                color = (Color.FromArgb(*usage_color(0, bright=bright)) if info["low"] else
                         warning_color if info["warning"] else fg)
                cells.append((info["name"], value, color))
            while len(self._extra_columns) < len(cells):
                make_column()
            muted = Color.FromArgb(*(round(f*0.68 + b*0.32) for f, b in zip(
                (fg.R, fg.G, fg.B), self._palette[0])))
            for (name_label, value_label), (name, value, color) in zip(self._extra_columns, cells):
                name_label.Text, value_label.Text = name, compact_value(value, getattr(self, "_fit_level", 0))
                name_label.ForeColor, value_label.ForeColor = muted, color
                name_label.Visible = value_label.Visible = True
            for name_label, value_label in self._extra_columns[len(cells):]:
                name_label.Visible = value_label.Visible = False
            if not multi:
                self._fit_key = None
                self._fit_level = 0
                # Keep the gap tight without shrinking type or clipping longer
                # countdowns (for example 168.0h) and localized status messages.
                text_width = max(scale(47), *(label.GetPreferredSize(Size(0, 0)).Width for label in text_controls))
                for label in text_controls:
                    label.Size = Size(text_width, scale(20))
                left_padding = 5
                for y, label in zip((0, 20), text_controls):
                    label.Location = Point(scale(left_padding), scale(y))
                form.ClientSize = Size(scale(left_padding) + text_width + scale(2), scale(40))
            else:
                # Two provider rows per column: "Name  value", full names.  Up
                # to four apps (two columns) fit the empty taskbar area left of
                # Start at the normal font; compact values before clipping.
                used = self._extra_columns[:len(cells)]
                available = None
                if self._placement == "taskbar" and geometry:
                    elements = self._layout.elements(int(user32.FindWindowW("Shell_TrayWnd", None) or 0), geometry[0],
                                                     (form.Left, form.Top, form.Right, form.Bottom) if form.Visible else None)
                    gap_area = left_gap(geometry[0], elements, scale(6)) if elements else None
                    available = gap_area[1] - gap_area[0] if gap_area else None
                fit_key = (tuple((n, v) for n, v, _ in cells), available, self._scale)
                if fit_key != self._fit_key:
                    self._fit_key = fit_key
                    for factor, level in FIT_PLAN:
                        self._fit_level = level
                        for (_, value_label), (_, value, _) in zip(used, cells):
                            value_label.Text = compact_value(value, level)
                        fonts = self._fit_fonts.get(factor)
                        if fonts is None:
                            fonts = self._fit_fonts[factor] = (
                                Font("Segoe UI", max(1, round(scale(12)*factor)), FontStyle.Regular, GraphicsUnit.Pixel),
                                Font("Segoe UI", max(1, round(scale(13)*factor)), FontStyle.Bold, GraphicsUnit.Pixel))
                        for top, bottom in used:
                            if top.Font is not fonts[0]:
                                top.Font = fonts[0]
                            if bottom.Font is not fonts[1]:
                                bottom.Font = fonts[1]
                        x, gap, inner = scale(7*factor), scale(10*factor), scale(4*factor)
                        for start in range(0, len(used), 2):
                            group = used[start:start+2]
                            name_w = max(n.GetPreferredSize(Size(0, 0)).Width for n, _ in group)
                            value_w = max(v.GetPreferredSize(Size(0, 0)).Width for _, v in group)
                            for row, (name_label, value_label) in enumerate(group):
                                y = scale(10) if len(group) == 1 else scale(20)*row
                                name_label.Size = Size(name_w, scale(20))
                                value_label.Size = Size(value_w, scale(20))
                                name_label.Location = Point(x, y)
                                value_label.Location = Point(x + name_w + inner, y)
                            x += name_w + inner + value_w + gap
                        width = x - gap + scale(7*factor)
                        if available is None or width <= available:
                            break
                    if available is not None and width > available:
                        width = max(scale(40), int(available))  # clip, never move sides
                    form.ClientSize = Size(width, scale(40))
            if reading != self._reading:
                self._reading = reading
                form.Invalidate()
            hint = "Click: GPU home · Double-click: quota details" if language == "en" else "单击打开 GPU 主页 · 双击查看额度详情"
            tip = concise_tooltip(rows, provider_rows, language)
            form.AccessibleName = ("Codex · " + rows[index]["label"]) if rows and not multi else (
                "AI usage" if english else "AI 用量")
            form.AccessibleDescription = tip + "\n" + hint
            tip_controls = [form, catcher, *text_controls, *(c for pair in self._extra_columns for c in pair)]
            if (tip, len(tip_controls)) != getattr(self, "_tip_key", None):
                self._tip_key = (tip, len(tip_controls))
                for control in tip_controls:
                    tooltip.SetToolTip(control, tip)
            for i, (zh, en, _) in enumerate(actions):
                action_items[i].Text = en if language == "en" else zh
            position()
            update_hover()
            obscured = windows_surface_obscured(handle, self._placement == "taskbar") and not menu.Visible
            self._obscured_ticks = self._obscured_ticks + 1 if obscured else 0
            if obscured and (self._obscured_ticks >= 2 or not form.Visible):
                cancel_click()
                if form.Visible:
                    form.Hide()
                return
            shown = False
            if not form.Visible:
                form.Show()
                shown = True
            raise_topmost(force=shown)

        def raise_topmost(force=False):
            # Never re-assert z-order while the menu is open: it would push
            # the strip above the popup's owner chain and close/steal it.
            if self.closed or not form.Visible or menu.Visible:
                return
            if force or windows_needs_topmost(handle):
                user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
                user32.SetWindowPos(handle, -1, 0, 0, 0, 0, 0x0013)
                sync_catcher()

        timer = Timer()
        self.timer = timer
        timer.Interval = 1000
        timer.Tick += tick
        self._tick = tick
        # Explorer raises its taskbar above other topmost windows when it is
        # clicked; re-assert quickly (cheap z-order walk, no move/resize).
        z_timer = Timer()
        self._z_timer = z_timer
        z_timer.Interval = 250
        z_timer.Tick += lambda *_: raise_topmost()
        tick()
        timer.Start()
        z_timer.Start()

    def _start_macos(self) -> None:
        if self.closed:
            return
        try:
            from AppKit import NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength
            from Foundation import NSObject, NSTimer
            surface = self

            class RadarCodexMenuTarget(NSObject):
                def tick_(self, _):
                    surface._mac_tick()

                def settings_(self, _):
                    surface._action(surface.open_settings)

                def refresh_(self, _):
                    surface._action(surface.refresh)

                def disable_(self, _):
                    surface._action(surface._disable)

                def quit_(self, _):
                    surface._action(surface.quit_application)

            self._delegate = RadarCodexMenuTarget.alloc().init()
            self._mac_classes = (NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self._delegate, "tick:", None, True)
            self._mac_tick()
        except Exception:
            logging.getLogger("vram_radar").warning("Codex menu-bar display could not start")

    def _mac_tick(self) -> None:
        if self.closed:
            return
        state = self.snapshot()
        language = self.language()
        rows = quota_lines(state, language)
        NSMenu, NSMenuItem, NSStatusBar, length = self._mac_classes
        self.active = bool(rows)
        if not rows:
            if self.status_item is not None:
                NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
                self.status_item = None
            self._last_signature = None
            return
        if self.status_item is None:
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(length)
        signature = (language, str(rows))
        if self._last_signature == signature:
            return
        self._last_signature = signature
        title = "C  " + " · ".join(f"{row['label']} {row['value']} ({row['countdown']})" for row in rows[:2])
        self.status_item.button().setTitle_(title)
        self.status_item.button().setToolTip_("\n".join(["Codex", *(row["detail"] for row in rows)]))
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for row in rows:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(row["detail"], None, "")
            item.setEnabled_(False)
            menu.addItem_(item)
        menu.addItem_(NSMenuItem.separatorItem())
        for zh, en, selector in [("额度设置", "Usage settings", "settings:"),
                                 ("刷新额度", "Refresh usage", "refresh:"),
                                 ("关闭额度显示", "Disable usage display", "disable:"),
                                 ("退出 VRAM Radar", "Quit VRAM Radar", "quit:")]:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(en if language == "en" else zh, selector, "")
            item.setTarget_(self._delegate)
            menu.addItem_(item)
        self.status_item.setMenu_(menu)

    def stop(self) -> None:
        self.closed = True
        self.active = False
        def cleanup():
            if sys.platform == "win32":
                if self.timer is not None:
                    self.timer.Stop()
                    self.timer.Dispose()
                if getattr(self, "_click_timer", None) is not None:
                    self._click_timer.Stop()
                    self._click_timer.Dispose()
                if getattr(self, "_z_timer", None) is not None:
                    self._z_timer.Stop()
                    self._z_timer.Dispose()
                if self.form is not None:
                    self.form.Close()
                    self.form.Dispose()
                if getattr(self, "_catcher", None) is not None:
                    self._catcher.Close()
                    self._catcher.Dispose()
                try:
                    from .ui_dialogs import close_toast
                    close_toast()
                except Exception:
                    pass
                for resource in (getattr(self, "_tooltip", None), getattr(self, "_menu", None), getattr(self, "_window_menu", None)):
                    if resource is not None:
                        resource.Dispose()
                for font in getattr(self, "_fonts", []):
                    font.Dispose()
                if getattr(self, "_menu_font", None) is not None:
                    self._menu_font.Dispose()
                for name in ("_menu_detail_font", "_menu_icon_font"):
                    font = getattr(self, name, None)
                    if font is not None:
                        font.Dispose()
            elif sys.platform == "darwin":
                if self.timer is not None:
                    self.timer.invalidate()
                if self.status_item is not None:
                    self._mac_classes[2].systemStatusBar().removeStatusItem_(self.status_item)
                    self.status_item = None
        if self._dispatch:
            try:
                self._dispatch(cleanup)
            except Exception:
                logging.getLogger("vram_radar").warning("Codex native display cleanup unavailable")
