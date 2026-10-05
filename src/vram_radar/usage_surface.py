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

from .reset_format import RESET_RE, reset_full, reset_short, strip_parts, valid_epoch
from .quota_colors import quota_color as quota_rule_color, usage_color

# Alpha of the strip's hit-test window: WinForms maps Opacity to a byte
# (int(opacity * 255)), so this is alpha 1/255 -- invisible, yet hit-testable
# (only alpha 0 / colour-keyed pixels pass clicks through).
CATCHER_OPACITY = 0.004
# VRAM_RADAR_PERF=1 logs the strip's UI-thread tick cost once a minute.
PERF_LOG = os.environ.get("VRAM_RADAR_PERF") == "1"

_DLLS: dict = {}
_STRUCTS: dict = {}


def _struct(name: str):
    """Win32 structures, defined once.  A ctypes class created inside a
    function and passed to ``ctypes.POINTER`` is cached by ctypes forever
    (~5 KB each): the 1 s strip tick leaked ~20 MB/h that way."""
    cls = _STRUCTS.get(name)
    if cls is not None:
        return cls
    import ctypes
    from ctypes import wintypes
    if name == "MonitorInfo":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        cls = MonitorInfo
    elif name == "Header":
        class Header(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        cls = Header
    else:
        raise KeyError(name)
    _STRUCTS[name] = cls
    return cls


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
            countdown = reset_short(reset - now, english)
        value = window.get("remaining_percent")
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and not expired and not state.get("stale")
                 and state.get("state") in {"ready", "loading"})
        percent = f"{max(0, min(100, value)):.0f}%" if valid else "—"
        rows.append({"label": duration, "value": percent, "countdown": countdown,
                     "reset_at": reset if valid_reset and not expired else None,
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
# Minimum clear gap (px at 100 %) between the strip and the weather text on
# its left / Start (or Search) on its right.
STRIP_MARGIN = 12
# The strip draws at 85 % of the taskbar's DPI scale (``taskbar_scale``).
STRIP_DENSITY = 0.85


def strip_margin(strip_scale: float) -> int:
    """STRIP_MARGIN in physical px at the real display scale (18 px at 150 %)."""
    return max(1, round(STRIP_MARGIN * strip_scale / STRIP_DENSITY))
RIGHT_BOUND_IDS = ("StartButton", "SearchButton", "TaskViewButton")


# 图标 mode: icon size (px at the strip's 100 % scale) and the clear gap
# between an icon and its value in px at 100 % *display* scale.
ICON_PX = 15
ICON_GAP = 5   # 10-02 19:18: tighter icon -> value (was 8)


def strip_icon_px(strip_scale: float, factor: float = 1.0) -> int:
    """15 px at 1x: fits a 20 px row with even padding."""
    return max(10, round(round(ICON_PX * strip_scale) * factor))


def icon_gap_px(strip_scale: float, factor: float = 1.0) -> int:
    import math
    return max(1, math.ceil(ICON_GAP * strip_scale / STRIP_DENSITY * factor))


def set_name_cell(name_label, image, name: str, strip_scale: float, factor: float) -> None:
    """Name cell: the app icon (图标) or the name text (文字 / no icon)."""
    from System.Drawing import ContentAlignment
    from System.Windows.Forms import Padding
    if name_label.Image is not image:
        name_label.Image = image
        name_label.ImageAlign = ContentAlignment.MiddleLeft
    # 1 px lower: the regular-weight name (and the icon) then share the bold
    # value's baseline / optical centre instead of sitting ~1 px high.
    nudge = Padding(0, max(1, round(round(1.5 * strip_scale) * factor)), 0, 0)
    if name_label.Padding != nudge:
        name_label.Padding = nudge
    name_label.Text = "" if image is not None else name


def strip_reset_text(text: str) -> str:
    """Strip display of a countdown: a hair space inside '1d\u200a6h' (4-5 px
    at 150 % instead of 7, so it stays tighter than the quota -> reset gap;
    tooltip/web keep '1d 6h')."""
    return (text or "").replace(" ", "\u200a")


SINGLE_FIT_FACTORS = (1.0, 0.95, 0.9)  # same 90 % floor as FIT_PLAN


def place_single(labels, fonts_for, pick, strip_scale: float, available=None) -> tuple[int, float]:
    """Lay out the Codex-only strip (value over countdown); returns (width, factor).

    ``fonts_for(factor)`` gives the (top, bottom) base fonts at that size and
    ``pick(text, base)`` the script-appropriate face (see pick_value_font).  As
    in the multi-app layout, the type shrinks step by step until the strip fits
    ``available`` (the empty taskbar gap left of Start); if even the smallest
    size does not fit, the width is clipped to the gap, so the strip never
    reaches Start.  Without a gap (free placement, tray side) nothing shrinks.
    """
    from System.Drawing import Point, Size
    scale = lambda v: round(v * strip_scale)
    left, right, minimum = scale(5), scale(2), scale(47)
    width, factor = 1, SINGLE_FIT_FACTORS[0]
    for factor in SINGLE_FIT_FACTORS:
        for label, base in zip(labels, fonts_for(factor)):
            want = pick(label.Text, base)
            if label.Font is not want:
                label.Font = want
        natural = left + max(label.GetPreferredSize(Size(0, 0)).Width for label in labels) + right
        width = max(natural, left + minimum + right)
        if available is None:
            break
        if natural <= available:
            width = min(width, int(available))
            break
    if available is not None and width > available:
        width = max(1, int(available))  # clip, never move sides or cover Start
    for y, label in zip((0, 20), labels):
        label.Size = Size(max(1, width - left - right), scale(20))
        label.Location = Point(left, scale(y))
    return width, factor


def place_columns(used, strip_scale: float, factor: float) -> int:
    """Lay out (name, value[, reset]) label cells, two rows per column;
    returns the client width.  Icon cells reserve exactly the icon width and
    keep ``icon_gap_px`` clear before the value; text cells use the tight
    gap.  A shown reset label sits right after its own value, a small gap
    apart (quota only / reset only / both)."""
    from System.Drawing import Point, Size
    scale = lambda value: round(value * strip_scale)
    icon_px = strip_icon_px(strip_scale, factor)
    # Label padding already reads as a space: the reset label overlaps the
    # value's empty right padding by rgap (ink stays >= 3 px apart, checked by
    # tools/check_strip_pixels.py); columns 7 px apart (was 10).
    x, gap, inner, rgap = scale(7*factor), scale(7*factor), scale(4*factor), -max(0, scale(2*factor) - 2)
    preferred = lambda label: label.GetPreferredSize(Size(0, 0)).Width

    def widths(cell):
        value, reset = cell[1], (cell[2] if len(cell) > 2 and cell[2].Text else None)
        vw = preferred(value) if value.Text or reset is None else 0
        rw = preferred(reset) if reset is not None else 0
        return vw, rw

    for start in range(0, len(used), 2):
        group = used[start:start+2]
        has_icon = any(c[0].Image is not None and not c[0].Text for c in group)
        name_w = max(icon_px if (c[0].Image is not None and not c[0].Text) else preferred(c[0]) for c in group)
        between = max(inner, icon_gap_px(strip_scale, factor)) if has_icon else inner
        sizes = [widths(c) for c in group]
        content_w = max(vw + (rgap if vw and rw else 0) + rw for vw, rw in sizes)
        for row, (cell, (vw, rw)) in enumerate(zip(group, sizes)):
            name_label, value_label = cell[0], cell[1]
            # Rows split the strip height exactly (scale(20)*2 can be 1 px
            # taller than scale(40), which clipped the lower row).
            total, top_h = scale(40), scale(40) // 2
            if len(group) == 1:
                y, row_h = (total - scale(20)) // 2, scale(20)
            else:
                y, row_h = (0, top_h) if row == 0 else (top_h, total - top_h)
            vx = x + name_w + between
            name_label.Size = Size(name_w, row_h)
            name_label.Location = Point(x, y)
            value_label.Size = Size(vw if rw else content_w, row_h)
            value_label.Location = Point(vx, y)
            if len(cell) > 2:
                cell[2].Size = Size(rw, row_h)
                cell[2].Location = Point(vx + vw + (rgap if vw else 0), y)
        x += name_w + between + content_w + gap
    return x - gap + scale(7*factor)


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


def content_right(pixels, width, height, threshold=60, min_hits=2, max_gap=None):
    """Column just past the right-most visible content in a BGRA capture.

    Each column's background is taken from its own top/bottom rows (the
    taskbar is translucent and the hover highlight is a rounded rect), and a
    column counts as content when at least ``min_hits`` of its middle rows
    differ from that background by more than ``threshold`` (sum of |dRGB|).
    Returns an offset in ``0..width`` (0 = nothing visible) or None for bad
    input.  With ``max_gap`` only the first cluster counts: content after a
    blank run wider than ``max_gap`` columns (e.g. our own strip, drawn
    there while a background scan used a stale strip rect) is ignored.
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
        if max_gap is not None and last and x + 1 - last > max_gap:
            break
    return last


def bar_plausible(pixels, width, height, threshold=60, max_share=0.25):
    """False when a capture does not look like taskbar background around content.

    On the taskbar a column's top and bottom rows show the same background;
    on the lock-screen photo (or a full-screen overlay) they differ in most
    columns, and every column would read as "content".
    """
    if width <= 0 or height < 8 or len(pixels) < width * height * 4:
        return False
    def px(x, y):
        i = (y * width + x) * 4
        return pixels[i + 2], pixels[i + 1], pixels[i]
    # Skip the taskbar's top border line (1-2 px darker at 150 %, 10-02: it
    # made every live capture "implausible").
    a = max(3, height // 12)
    rows = (a, a + 1, a + 2, height - a - 3, height - a - 2, height - a - 1)
    bad = 0
    for x in range(0, width, 2):
        refs = [px(x, y) for y in rows]
        spread = sum(max(c[k] for c in refs) - min(c[k] for c in refs) for k in range(3))
        bad += spread > threshold
    return bad <= max_share * ((width + 1) // 2)


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

        Header = _struct("Header")
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


LATIN_VALUE_FAMILY = "Segoe UI"
# Segoe UI has no CJK glyphs; GDI's font-link fallback draws them noticeably
# lighter than Segoe UI Bold (10-02 v8: '\u672a\u8fd0\u884c' next to bold '0%').
# Microsoft YaHei UI Bold matches the weight (its Latin glyphs are Segoe's).
CJK_VALUE_FAMILY = "Microsoft YaHei UI"
_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")


def value_font_family(text) -> str:
    """Font family for a strip value/reset/state text (same weight and size)."""
    return CJK_VALUE_FAMILY if _CJK.search(text or "") else LATIN_VALUE_FAMILY


def pick_value_font(text, base, cache: dict, make):
    """``base`` (Segoe UI Bold) for Latin text, else the same size/style in
    ``value_font_family(text)``; ``make(family, base)`` builds it once."""
    family = value_font_family(text)
    if family == LATIN_VALUE_FAMILY:
        return base
    key = (family, float(base.Size), int(base.Style))
    if key not in cache:
        cache[key] = make(family, base)
    return cache[key]


# Weather scan tuning (fractions of the taskbar height; see trim_widgets).
WEATHER_INK_THRESHOLD = 45     # sum |dRGB|: grey line-2 text of a news item
WEATHER_BLANK_RUN = 0.5
# Must stay below the strip's clear gap minus the placement tolerance at every
# scale: the gap is 0.25 x height (12 px of a 48 px bar at 100 %) and
# PlacementDebouncer ignores moves of <= 2 px, so 1-2 px wider text leaves a
# 10 px gap.  0.2 x (10 px) read that as "under the strip" and jumped the
# strip one bar height right and back (10-03 review).
WEATHER_OCCLUDED_SLACK = 0.15


def weather_scan_limit(elements, own=None):
    """Right end of the area scanned for weather content.

    Not the Widgets button's UIA rect: Explorer does not always widen that
    rect when the weather text grows (10-02: text drawn past its 237 px
    right edge while the strip sat 18 px right of the rect -> overlap).  The
    scan runs to Start/Search/first app instead, stopping early at our own
    strip (never scanned) -- the blank-run rule in ``content_right`` ends it
    right after the text anyway.
    """
    left, top, right, bottom = elements["WidgetsButton"]
    bounds = [r[0] for k, r in elements.items()
              if k in (*RIGHT_BOUND_IDS, "first_app") and r and r[0] > left]
    limit = max(right, min(bounds)) if bounds else right
    if own and own[1] < bottom and own[3] > top and own[2] > left and own[0] < limit:
        limit = max(left, own[0])
    return limit


def scan_bound(elements):
    """Left edge of Start/Search/first app right of the Widgets button (or None)."""
    left = elements["WidgetsButton"][0]
    bounds = [r[0] for k, r in elements.items()
              if k in (*RIGHT_BOUND_IDS, "first_app") and r and r[0] > left]
    return min(bounds) if bounds else None


LOCK_PROCESSES = ("lockapp.exe", "logonui.exe")


def lock_state(foreground, process, input_default, session_flags=None) -> bool:
    """Pure decision behind ``screen_locked``.

    ``session_flags``: WTSINFOEX SessionFlags of our session (0 = locked,
    1 = unlocked, None/-1 = unknown) -- authoritative when known.
    LockApp stays the foreground window after an unlock until another
    window is activated; reading that as "locked" froze the weather
    measurement while the desktop was in plain view (10-03 03:02: a 2-line
    news widget grew under the strip and the step-right logic never ran).
    Without session flags: a lock/logon window in the foreground, or no
    foreground window while the input desktop is not "Default", is locked.
    """
    if session_flags == 0:
        return True
    if session_flags == 1:
        return False
    if input_default is False:
        return True       # Winlogon/secure desktop has the input
    if not foreground:
        return input_default is None
    return (process or "").lower() in LOCK_PROCESSES


def session_lock_flags():
    """SessionFlags of the current session (0 locked, 1 unlocked) or None."""
    try:
        import ctypes
        from ctypes import wintypes
        wts = ctypes.windll.wtsapi32
        wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        buf, size = ctypes.c_void_p(), wintypes.DWORD()
        # WTS_CURRENT_SESSION, WTSSessionInfoEx
        if not wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, ctypes.byref(buf), ctypes.byref(size)) or not buf.value:
            return None
        try:
            # WTSINFOEXW: DWORD Level; union (8-aligned) WTSINFOEX_LEVEL1_W:
            # SessionId, SessionState, SessionFlags.
            if size.value < 20 or ctypes.c_uint32.from_address(buf.value).value != 1:
                return None
            flags = ctypes.c_int32.from_address(buf.value + 16).value
        finally:
            wts.WTSFreeMemory(buf)
        return flags if flags in (0, 1) else None
    except Exception:
        return None


def _input_desktop_default():
    """True/False when the input desktop is/is not "Default"; None if unknown."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        desk = user32.OpenInputDesktop(0, False, 0x0001)   # DESKTOP_READOBJECTS
        if not desk:
            return False  # access denied: the secure (Winlogon) desktop has the input
        try:
            buf = ctypes.create_unicode_buffer(64)
            need = wintypes.DWORD()
            if not user32.GetUserObjectInformationW(desk, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):
                return None
            return buf.value.lower() == "default"
        finally:
            user32.CloseDesktop(desk)
    except Exception:
        return None


def screen_locked() -> bool:
    """True while the lock screen is up (see ``lock_state``).

    The desktop capture then shows the lock-screen photo, which reads as
    "weather content" all the way to Start (10-02 16:3x: strip stepped onto
    the Search box while the PC was locked).
    """
    flags = session_lock_flags()
    if flags is not None:
        return lock_state(True, None, None, flags)
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return lock_state(False, None, _input_desktop_default())
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return False
        finally:
            kernel32.CloseHandle(handle)
        return lock_state(True, buf.value.rsplit("\\", 1)[-1], None)
    except Exception:
        return False


def trim_widgets(elements, own=None, capture=capture_screen, min_width=24):
    """Set the Widgets right edge to the end of its visible content.

    Explorer reports the weather button wider than what it draws (228 px
    for "23\u00b0C / \u5c40\u90e8\u591a\u4e91" at 150 %, text ending near 150 px)
    -- and sometimes narrower once the text grows.  The visible end is
    measured up to ``weather_scan_limit``; our own strip is never scanned, so
    the strip cannot push itself away.  ``_weather_scan`` = (limit,
    content runs into the limit) for ``TaskbarLayout.widest_weather``.
    """
    widgets = elements.get("WidgetsButton") if elements else None
    if not widgets or widgets[2] - widgets[0] < min_width:
        return elements
    left, top, right, bottom = widgets
    limit = weather_scan_limit(elements, own)
    if limit - left < min_width:
        trimmed = dict(elements)
        trimmed["_weather_scan"] = (limit, True, scan_bound(elements))   # strip sits on the weather: cannot see it
        return trimmed
    pixels = capture((left, top, limit, bottom))
    height = bottom - top
    # Rightmost ink over every row of the widget (a 2-line news item has a
    # short bold line 1 and a longer, low-contrast grey line 2).  Weather
    # icon->text spacing is ~0.2 x bar height, a full-width CJK colon or
    # word gap up to ~0.35 x (10-03: '\u6e2f\u80a1\u5348\u8bc4\uff1a\u6052\u6307...' cut at
    # the colon); a 0.5 x height blank run ends the widget -- still far
    # short of the Start/Search gap.  Our own strip is never scanned (the
    # limit stops at it).
    edge = content_right(pixels, limit - left, height, threshold=WEATHER_INK_THRESHOLD,
                         max_gap=max(12, round(height * WEATHER_BLANK_RUN))) if pixels else None
    if not edge or edge < min_width // 2:
        return elements
    trimmed = dict(elements)
    at_strip = bool(own) and limit == max(left, own[0])
    # Ink ending well inside the clear gap the strip keeps (0.25 x height)
    # means the text grew toward / under it: the visible part may end
    # at a glyph or colon gap just left of the strip, so a 1 px "touches the
    # limit" test missed it (10-03).  Treat it as running under the strip.
    reach_slack = max(1, round(height * WEATHER_OCCLUDED_SLACK)) if at_strip else 1
    reaches = left + edge >= limit - reach_slack
    bound = scan_bound(elements)
    if (reaches and not at_strip) or not bar_plausible(pixels, limit - left, height):
        # "Content" running into Start/Search is no weather text (the blank
        # run always ends it first): lock screen, a full-screen overlay or a
        # capture glitch.  Keep the previous reading.
        trimmed["_weather_scan"] = (limit, None, bound)
        return trimmed
    trimmed["WidgetsButton"] = (left, top, left + edge, bottom)
    trimmed["_weather_scan"] = (limit, reaches, bound)
    return trimmed


class TaskbarLayout:
    """Cached UI Automation reader for the primary taskbar's buttons.

    Lookups are cheap (~15 ms) but are throttled anyway and invalidated when
    the taskbar window handle or size changes (Explorer restart, DPI/scale or
    alignment change).  Any failure returns an empty mapping so callers fall
    back to the classic tray anchor; nothing here can raise into the UI loop.
    """

    def __init__(self, interval: float = 5.0, retry: float = 1.0, trim=trim_widgets, background=False,
                 locked=screen_locked):
        self.interval = interval
        self.locked = locked if trim is not None else (lambda: False)
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
        # Widest weather content seen recently: [(monotonic time, right)] per
        # Widgets button rect, so a wider text ("局部多云", 3-digit temps)
        # never slides under the strip.
        self._weather_key = None
        self._weather_seen: list = []

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
        self._own_now = exclude
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
            raw_widgets = found.get("WidgetsButton")
            try:
                if self.locked():
                    # Lock screen: the capture shows its photo, not the bar.
                    found = self.hold_weather(found)
                else:
                    trimmed = self.trim(found, exclude)
                    if getattr(self, "_own_now", exclude) != exclude:
                        # The strip moved while the background scan ran: the
                        # capture may hold its pixels (left of the old rect
                        # used as the scan limit).  Keep the served edge;
                        # the next scan measures with the new rect.
                        found = self.hold_weather(found)
                    else:
                        found = self.widest_weather(trimmed, raw_widgets, exclude)
            except Exception as exc:  # worker thread: never die silently mid-update
                logging.getLogger("vram_radar").info("widget trim failed (%s)", type(exc).__name__)
            found = {k: v for k, v in found.items() if not k.startswith("_")}
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

    # A narrower weather reading is followed once it has held this long
    # (the widget briefly shows shorter texts while it updates); a wider one
    # applies at once.
    WEATHER_WINDOW = 20.0

    def hold_weather(self, found) -> dict:
        """Reuse the last served weather edge (same Widgets button) unmeasured."""
        found = dict(found)
        raw, last = found.get("WidgetsButton"), self._elements.get("WidgetsButton") if self._elements else None
        if raw and last and (raw[0], raw[1], raw[3]) == (last[0], last[1], last[3]):
            found["WidgetsButton"] = last
        return found

    def widest_weather(self, found, raw_widgets, own, now=None) -> dict:
        """Settle the measured weather right edge (see ``trim_widgets``).

        * content running into our own strip (we cannot see under it):
          assume it reaches past the strip for now, so the strip steps clear
          and the next scan measures the real end;
        * wider than recent readings: applied immediately;
        * narrower: applied after it held for ``WEATHER_WINDOW`` seconds.
        Readings right up against the strip are used but not remembered (a
        scan taken while the strip moved could include its pixels).
        """
        found = dict(found or {})
        scan = found.pop("_weather_scan", None)
        trimmed = found.get("WidgetsButton")
        if not trimmed or not raw_widgets:
            return found
        now = time.monotonic() if now is None else now
        key = (raw_widgets[0], raw_widgets[1], raw_widgets[3])
        if key != self._weather_key:
            self._weather_key, self._weather_seen = key, []
        limit, occluded, bound = (tuple(scan) + (None,))[:3] if scan else (raw_widgets[2], False, None)
        right = trimmed[2]
        if occluded is None:
            # Implausible scan: keep what was served last (else the raw rect).
            last = self._elements.get("WidgetsButton") if self._elements else None
            right = last[2] if last and (last[0], last[1], last[3]) == (trimmed[0], trimmed[1], trimmed[3]) else raw_widgets[2]
        elif occluded:
            # Explorer's rect when it reaches past the strip (it usually grows
            # with the text); otherwise step right by one bar height (about
            # three characters) and re-measure -- never a jump past the whole
            # strip, which could leave no room before Start.
            step = raw_widgets[3] - raw_widgets[1]
            right = raw_widgets[2] if raw_widgets[2] > limit + 1 else limit + step
        else:
            near_own = bool(own) and 0 <= own[0] - right <= max(6, round((raw_widgets[3] - raw_widgets[1]) * 0.2))
            pending, self._weather_pending = getattr(self, "_weather_pending", None), right
            if not near_own and pending is not None and abs(pending - right) <= 1:
                self._weather_seen.append((now, right))
        self._weather_seen = [(t, r) for t, r in self._weather_seen if now - t <= self.WEATHER_WINDOW][-200:]
        if self._weather_seen:
            right = max(right, *(r for _, r in self._weather_seen))
        if bound is not None:
            right = min(right, bound)   # never claim Start/Search/apps as weather
        if right != trimmed[2]:
            found["WidgetsButton"] = (trimmed[0], trimmed[1], right, trimmed[3])
        return found

    # Re-run the full UI Automation search at most this often; in between
    # only the cached buttons' rectangles are re-read.  A FindAll over the
    # taskbar every 2 s grew native memory ~2.8 MB per 1000 searches
    # (UIAutomationCore client-side state; 10-03 soak) -- most of the app's
    # ~7 MB/h growth.  Rect reads track Start/Search moving (centered icons)
    # just as fast.
    UIA_REFIND = 120.0

    def _read(self, taskbar_handle) -> dict:
        cache = getattr(self, "_uia_cache", None)
        # Without a cached Widgets button (turned on later, Explorer still
        # starting) search again soon, so the strip never sits on the weather.
        refind = self.UIA_REFIND if cache and any(k == "WidgetsButton" for k, _ in cache[2]) else 10.0
        if cache and cache[0] == int(taskbar_handle or 0) and time.monotonic() - cache[1] < refind:
            try:
                found = {}
                for automation_id, element in cache[2]:
                    rect = element.Current.BoundingRectangle
                    if rect.IsEmpty or rect.Width <= 0:
                        continue
                    found[automation_id] = (int(rect.Left), int(rect.Top), int(rect.Right), int(rect.Bottom))
                if any(k in found for k in RIGHT_BOUND_IDS):
                    return found
            except Exception:
                pass   # element gone (Explorer restart, button removed): search again
        self._uia_cache = None
        return self._find_all(taskbar_handle)

    def _find_all(self, taskbar_handle) -> dict:
        """Full UI Automation search (FindAll over the taskbar subtree)."""
        try:
            from System import IntPtr
            from System.Windows.Automation import (AutomationElement, OrCondition, PropertyCondition,
                                                   TreeScope, ControlType)
            root = AutomationElement.FromHandle(IntPtr(int(taskbar_handle)))
            conditions = [PropertyCondition(AutomationElement.AutomationIdProperty, value)
                          for value in (*LEFT_BOUND_IDS, *RIGHT_BOUND_IDS)]
            found, cached = {}, []
            for element in root.FindAll(TreeScope.Descendants, OrCondition(*conditions)):
                rect = element.Current.BoundingRectangle
                if rect.IsEmpty or rect.Width <= 0:
                    continue
                found[element.Current.AutomationId] = (int(rect.Left), int(rect.Top),
                                                       int(rect.Right), int(rect.Bottom))
                cached.append((element.Current.AutomationId, element))
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
                cached = []   # fallback layout: always searched afresh
            self._uia_cache = (int(taskbar_handle), time.monotonic(), cached) if cached else None
            return found
        except Exception as exc:
            logging.getLogger("vram_radar").info("taskbar layout read failed (%s)", type(exc).__name__)
            return {}

    def invalidate(self):
        self._key = None
        self._uia_cache = None


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
            if target[0] == self.current[0] == "left" and target[1] > self.current[1]:
                needed = 1   # weather grew toward/under the strip: step clear at once
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
    preferred = max(0.6, dpi / 96 * STRIP_DENSITY)
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
        text = re.sub(r"\s+(?:<?\d+(?:\.\d)?[hd]|" + RESET_RE.pattern + ")$", "", text)
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
    MonitorInfo = _struct("MonitorInfo")
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
        code = state.get("code")
        countdown = (("Login" if english else "登录") if code in {"login_required", "unsupported_account"} else
                     ("Missing" if english else "未安装") if code == "not_installed" else
                     ("Retry" if english else "重试"))
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
        countdown = reset_short(left, english)
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
    reset_ok = valid_epoch(reset) and reset > now
    if reset_ok:
        brief += " \u00b7 " + reset_full(reset, english)
    if subline and re.match(r"^(记录|As of|旧记录|Old data)", subline):
        brief += f" ({subline})" if english else f"（{subline}）"
    return {"name": short, "value": headline, "countdown": subline or _provider_status(state, english),
            "percent": state.get("quota_percent"),
            "quota": local(state.get("quota")), "reset": reset_short(reset - now, english) if reset_ok else "",
            "reset_full": reset_full(reset, english) if reset_ok else "",
            "low": bool(state.get("low")), "warning": warning, "action": bool(state.get("needs_action")),
            "detail": "\n".join(lines),
            "brief": f"{name}  {brief}"}


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
        full = reset_full(row.get("reset_at"), english) if RESET_RE.fullmatch(countdown) else ""
        if full:
            part += " \u00b7 " + full
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


def menu_tier(state: dict | None, chosen: bool = False) -> int:
    """Row tier in the provider menus: 0 running, 1 installed (not running,
    or not probed yet), 2 everything else (leftover data / not detected,
    greyed, at the bottom).  ``chosen`` only orders rows inside a tier."""
    if isinstance(state, dict):
        if state.get("running") is True:
            return 0
        return 1 if state.get("installed") else 2
    return 1


def tiered(ids, chosen, states) -> list[tuple[int, str]]:
    """(tier, id) in tier order; inside a tier ticked rows come first, then
    the fixed registry order (stable sort)."""
    rows = [(menu_tier(states.get(pid) if isinstance(states, dict) else None), pid not in chosen, index, pid)
            for index, pid in enumerate(ids)]
    return [(tier, pid) for tier, _, _, pid in sorted(rows)]


def limit_menu_text(limit: int, language: str = "zh-CN") -> str:
    return f"Up to {limit} shown" if language == "en" else f"最多显示 {limit} 个"


def limit_hint_text(limit: int, language: str = "zh-CN") -> str:
    """Hint shown when a further model is ticked past ``MAX_SELECTED``."""
    return (f"Up to {limit} shown · untick one first" if language == "en"
            else f"最多同时显示 {limit} 个，请先取消其中一项")


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


def consent_precheck(state: dict | None, app: str, english: bool = False) -> str | None:
    """Why the consent dialog must not be offered yet, or None.

    Session reading needs the app installed and signed in on this PC; asking
    for consent before that would promise something we cannot do.  An
    unknown state (first probe not finished) does not block the dialog.
    """
    if not isinstance(state, dict):
        return None
    if state.get("installed") is False:
        return (f"{app} not found. Install {app} on this PC and sign in, then turn on automatic reading."
                if english else f"未检测到 {app}。请先在本机安装并登录 {app}，再开启自动读取额度。")
    if state.get("signed_in") is False:
        return (f"{app} is not signed in. Sign in to {app} first, then turn on automatic reading."
                if english else f"{app} 尚未登录。请先在 {app} 中登录，再开启自动读取额度。")
    return None


def windows_dpi_scale(fallback: float = 1.0) -> float:
    try:
        dpi = _dll("user32").GetDpiForSystem()
        return max(1.0, dpi / 96) if dpi else fallback
    except Exception:
        return fallback


def windows_consent_dialog(app: str, provider: str, scale: float | None = None, *,
                           icon_path: str | None = None, language: str = "zh-CN") -> bool:
    """Modal 允许/暂不 (UI thread only). True only on 允许."""
    from . import ui_dialogs
    spec = ui_dialogs.consent_spec(app, app, CONSENT_ETA_SECONDS, language=language)
    return ui_dialogs.show_dialog(spec, scale=scale, icon_path=icon_path) == "allow"


def windows_revoke_dialog(name: str, scale: float | None = None, *, icon_path: str | None = None,
                          language: str = "zh-CN") -> bool:
    from . import ui_dialogs
    return ui_dialogs.show_dialog(ui_dialogs.revoke_spec(name, language=language), scale=scale,
                                  icon_path=icon_path) == "revoke"


def windows_notice_dialog(text: str, scale: float | None = None, *, name: str = "",
                          icon_path: str | None = None, language: str = "zh-CN") -> None:
    from . import ui_dialogs
    ui_dialogs.show_dialog(ui_dialogs.notice_spec(text, name, language=language), scale=scale, icon_path=icon_path)


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
                                                         icon_path=self._dialog_icon, language=self._lang()))
        self.confirm_revoke = lambda name: windows_revoke_dialog(name, self._dialog_scale(),
                                                                 icon_path=self._dialog_icon, language=self._lang())
        self.notify_blocked = lambda text: windows_notice_dialog(text, self._dialog_scale(),
                                                                 name=self._dialog_name, icon_path=self._dialog_icon,
                                                                 language=self._lang())
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

    # A model toggle is saved on a worker thread; until the saved profile is
    # visible, a quick second toggle must start from the first one's result
    # (it used to read the old list and silently undo the first tick, and
    # could get past the 4-model limit).
    SELECTION_MEMO_SECONDS = 3.0

    def _current_selection(self) -> list:
        memo = getattr(self, "_selection_memo", None)
        if memo is not None and time.monotonic() - memo[0] < self.SELECTION_MEMO_SECONDS:
            return list(memo[1])
        try:
            return [x for x in (self.providers().get("selected") or ["codex"]) if isinstance(x, str)] or ["codex"]
        except Exception:
            return ["codex"]

    def _remember_selection(self, chosen) -> int:
        if not hasattr(self, "_selection_lock"):
            self._selection_lock = threading.Lock()
            self._selection_sequence = 0
        with self._selection_lock:
            self._selection_sequence += 1
            self._selection_memo = (time.monotonic(), list(chosen))
            return self._selection_sequence

    def _lang(self) -> str:
        try:
            return "en" if self.language() == "en" else "zh-CN"
        except Exception:
            return "zh-CN"

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
        english = self._lang() == "en"
        name = spec.label(english)
        self._dialog_name = name
        blocked = consent_precheck(state, name if spec.session_app in ("", spec.name) else spec.session_app, english)
        if blocked:
            try:
                self.notify_blocked(blocked)
            except Exception:
                logging.getLogger("vram_radar").warning("session consent notice failed")
            return False
        try:
            accepted = self._with_hover_dialog(
                lambda: self.confirm_consent(
                    name if spec.session_app in ("", spec.name) else spec.session_app,
                    spec.session_server or name) is True)
        except Exception:
            logging.getLogger("vram_radar").warning("session consent dialog failed")
            accepted = False
        if accepted:
            # Strip shows 查询中 until the immediate fetch (save_consent
            # triggers it) delivers the first value.
            self._pending[provider_id] = time.time()
            self._action(lambda: self._store_consent(provider_id, True))
            if english:
                self._toast(f"Reading {name} quota…", f"On the taskbar within about {CONSENT_ETA_SECONDS} s", name)
            else:
                self._toast(f"正在读取 {name} 额度…", f"约 {CONSENT_ETA_SECONDS} 秒内显示在任务栏", name)
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
                english = self._lang() == "en"
                confirmed = self._with_hover_dialog(
                    lambda: self.confirm_revoke(spec.label(english)) is True)
            except Exception:
                logging.getLogger("vram_radar").warning("revoke dialog failed")
                confirmed = False
            if confirmed:
                self.revoke_session_consent(provider_id)
                name = spec.label(english)
                if english:
                    self._toast(f"Stopped reading {name} automatically", "Turn it back on from the same menu anytime", name)
                else:
                    self._toast(f"已关闭 {name} 的自动读取", "可随时在同一菜单重新开启", name)
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
        ui_dialogs.show_toast(ui_dialogs.toast_spec(headline, line, name or ("VRAM Radar" if self._lang() == "en" else "显存雷达")), anchor,
                              scale=self._dialog_scale(), icon_path=icon)


    def _menu_is_open(self) -> bool:
        menu = getattr(self, "_menu", None)
        try:
            return bool(menu is not None and menu.Visible)
        except Exception:
            return False

    def _suspend_hover(self) -> None:
        """Hide the card, cancel any delay timer, and require a later re-arm."""
        self._hover_armed = False
        try:
            from . import ui_dialogs
            ui_dialogs.hide_hover_card()
        except Exception:
            pass

    def _try_rearm_hover(self) -> bool:
        """Re-arm after menu/dialog close only on pointer re-enter/move."""
        from .hover_detail import hover_rearm_allowed
        if not hover_rearm_allowed(menu_open=self._menu_is_open(),
                                   dialog_open=bool(getattr(self, "_hover_dialog_open", False))):
            return False
        self._hover_armed = True
        return True

    def _hover_interaction_allowed(self, pointer_over: bool) -> bool:
        from .hover_detail import hover_may_show
        return hover_may_show(
            armed=bool(getattr(self, "_hover_armed", True)),
            pointer_over=pointer_over,
            menu_open=self._menu_is_open(),
            dialog_open=bool(getattr(self, "_hover_dialog_open", False)),
            has_spec=bool(getattr(self, "_hover_spec", None)),
        )

    def _with_hover_dialog(self, callback):
        """Run a menu-spawned modal dialog with hover suppressed."""
        self._hover_dialog_open = True
        self._suspend_hover()
        try:
            return callback()
        finally:
            self._hover_dialog_open = False

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
        # Hover card stays off after a click/menu/dialog until the pointer
        # re-enters or moves over the strip (no instant pop-back).
        self._hover_armed = True
        self._hover_dialog_open = False

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
        self._single_fonts = {}
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
            # Rich hover card: short delay, then one reused no-activate window.
            # Never triggers extra provider polling; content comes from the last tick.
            # Suppressed while the context menu / a menu dialog is open, and after
            # any click until the pointer re-enters or moves over the strip.
            try:
                from . import ui_dialogs
                from .hover_detail import HOVER_DELAY_MS
                if self._hover_interaction_allowed(hovered):
                    anchor = (form.Left, form.Top, form.Right, form.Bottom)
                    already = bool(ui_dialogs._HOVER.get("shown"))
                    ui_dialogs.show_hover_card(
                        self._hover_spec, anchor, scale=self._scale,
                        delay_ms=0 if already else HOVER_DELAY_MS)
                else:
                    ui_dialogs.hide_hover_card()
            except Exception:
                pass

        def on_hover_enter(*_):
            self._try_rearm_hover()
            update_hover()

        for control in [form, *text_controls]:
            control.MouseEnter += on_hover_enter
            control.MouseLeave += update_hover

        # 2 s: a growing weather text is re-measured within a few seconds.
        self._layout = TaskbarLayout(interval=2.0, background=True)
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
                target = self._placer.propose(docked_target(bar, tray, elements, size, strip_margin(self._scale), scale(4)))
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
            # Left or right: drop the hover card immediately and cancel its delay.
            self._suspend_hover()
            if event.Button == MouseButtons.Right:
                cancel_click()
            if event.Button == MouseButtons.Left:
                self._drag = (Cursor.Position.X - form.Left, Cursor.Position.Y - form.Top)
                self._drag_origin = (Cursor.Position.X, Cursor.Position.Y)
                self._drag_moved = False
                sender.Capture = True

        def mouse_move(sender, event):
            if (not getattr(self, "_hover_armed", True)
                    and form.Visible and form.Bounds.Contains(Cursor.Position)
                    and self._try_rearm_hover()):
                update_hover()
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
        def on_menu_opening(*_):
            cancel_click()
            self._suspend_hover()
        menu.Opening += on_menu_opening
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
            # Keep hover disarmed until the pointer re-enters/moves over the strip.
            self._hover_armed = False
            try:
                from . import ui_dialogs
                ui_dialogs.hide_hover_card()
            except Exception:
                pass
            if getattr(self, "_tick_deferred", False):
                self._tick_deferred = False
                self._tick()
        menu.Closed += menu_closed
        # Fallback dismissal if the foreground switch was refused: poll the
        # mouse buttons only while the menu is open (never blocks the click).
        from System.Windows.Forms import ToolStripDropDownCloseReason
        from .menu_dismiss import OutsideClickWatch, menu_rects, win32_buttons, win32_cursor
        outside_watch = OutsideClickWatch(
            Timer(), win32_buttons(), win32_cursor(),
            lambda: menu_rects(menu, form, getattr(self, "_catcher", None)),
            lambda: menu.Close(ToolStripDropDownCloseReason.AppClicked), lambda: menu.Visible)
        self._outside_watch = outside_watch
        menu.Opened += outside_watch.start
        menu.Closed += outside_watch.stop
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
        self._display_menu.DropDownItems.Add(ToolStripSeparator())
        self._icons_title = self._display_menu.DropDownItems.Add("名称显示")
        self._icons_title.Enabled = False
        for value, zh, en in (("text", "文字", "Text"), ("icons", "图标", "Icons")):
            item = self._display_menu.DropDownItems.Add(zh)
            item.Click += lambda _s, _e, v=value: save_choice("usage_labels", v)
            self._display_choices.append((item, "usage_labels", value, zh, en))
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
                self._limit_item.Visible = self._limit_sep.Visible = True
                self._limit_item.Text = limit_menu_text(MAX_SELECTED, self.language())
                if form.Visible:
                    self._toast(text, "", "")
            except Exception:
                logging.getLogger("vram_radar").info("model limit hint failed")

        def toggle_model(provider_id):
            current = self._current_selection()
            from .providers import MAX_SELECTED
            if provider_id not in current and len(current) >= MAX_SELECTED:
                show_limit_hint()  # the strip lays out at most MAX_SELECTED apps legibly
                return
            chosen = [x for x in current if x != provider_id] if provider_id in current else current + [provider_id]
            if not chosen:
                return  # keep at least one; the strip (and this menu) must stay reachable
            sequence = self._remember_selection(chosen)
            def save():
                with self._selection_lock:
                    if sequence != self._selection_sequence:
                        return  # a newer choice supersedes this one
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
        # Its own separator: the greyed note read as one more model row.
        self._limit_sep = ToolStripSeparator()
        self._limit_sep.Visible = False
        self._models_menu.DropDownItems.Add(self._limit_sep)
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
        catcher.MouseEnter += on_hover_enter
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
        self._cjk_fonts = {}
        self._make_font = lambda family, base: Font(family, base.Size, base.Style, GraphicsUnit.Pixel)

        def make_column():
            pair = []
            for font in (self._name_font, self._value_font, self._value_font):
                label = Label()
                label.AutoSize = False
                label.Font = font
                label.TextAlign = ContentAlignment.MiddleLeft
                label.BackColor = Color.Transparent
                label.ForeColor = menu.ForeColor
                label.Location = Point(scale(5), 0)
                label.Size = Size(scale(47), scale(20))
                label.ContextMenuStrip = menu
                label.MouseEnter += on_hover_enter
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
            from .ui_dialogs import taskbar_light
            icon_light = taskbar_light()   # Codex knot / MSIX logos differ per theme
            def set_icon(item, spec, pstate):
                path = pstate.get("install_path") if isinstance(pstate, dict) else None
                key = (path, icon_px, icon_light)
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
                item.Text = spec.label(english)
                item.ShortcutKeyDisplayString = label
                item.Enabled = clickable
                set_icon(item, spec, pstate)
            from .providers import MAX_SELECTED
            full = len(selected) >= MAX_SELECTED
            for pid, (item, spec) in self._model_items.items():
                pstate = provider_states.get(pid)
                item.Text = spec.label(english)
                item.ShowShortcutKeys = True
                item.ShortcutKeyDisplayString = _provider_status(pstate, english)
                set_icon(item, spec, pstate)
                item.Checked = pid in selected
                # Keep unticked items clickable at the limit so a 5th tick can
                # explain itself (hint) instead of silently doing nothing.
                item.Enabled = pid in selected or pstate is None or bool(pstate.get("installed"))
            # Rows in tiers (running, installed, other), ticked first then
            # fixed order inside a tier.  Re-arranged only when the tiers change; ticks
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
                    [self._limit_sep, self._limit_item, self._models_tail_sep, self._rescan_item])
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
            self._limit_item.Visible = self._limit_sep.Visible = full
            self._limit_item.Text = limit_menu_text(MAX_SELECTED, language)
            self._background_title.Text = "Background" if english else "背景"
            self._display_menu.Text = ("Display options" if language == "en" else "显示设置") + (
                (" · Save failed" if language == "en" else " · 保存失败") if self._display_error else "")
            style = options.get("usage_background")
            style = style if style in BACKGROUND_STYLES else "transparent"
            show_icons = options.get("usage_labels") == "icons"
            self._icons_title.Text = "Labels" if english else "名称显示"
            for item, key, value, zh, en in self._display_choices:
                item.Text = en if language == "en" else zh
                item.Checked = (style == value) if key == "usage_background" else (
                    ("icons" if show_icons else "text") == value)
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
                for top, bottom, reset_label in self._extra_columns:
                    top.Font, bottom.Font, reset_label.Font = self._name_font, self._value_font, self._value_font
                old_fit = [font for pair in (*self._fit_fonts.values(), *self._single_fonts.values()) for font in pair]
                self._fit_fonts = {}
                self._single_fonts = {}
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
                # One colour rule for quota and reset, every provider (quota_colors).
                quota_color = Color.FromArgb(*quota_rule_color(
                    reading["percent"], low=reading["low"], warning=reading["warning"],
                    bright=bright, surface=self._palette[0]))
                time_color = quota_color
                self._labels[0].ForeColor = quota_color
                self._labels[0].Text = reading["value"]
                self._countdowns[0].Text = reading["countdown"]
                self._countdowns[0].ForeColor = time_color
            else:
                reading = widget_reading({})
            for label in text_controls:
                label.Visible = bool(rows) and not multi
            cells = []
            cell_ids = []
            if rows and multi:
                cell_ids.append("codex")
                countdown = reading["countdown"]
                if RESET_RE.fullmatch(countdown):
                    value, reset_txt = reading["value"], countdown
                else:
                    value = f"{reading['value']} {countdown}".strip() if countdown != "—" else reading["value"]
                    reset_txt = ""
                cells.append(("Codex", value, reset_txt, quota_color))
            provider_rows = []
            fg = menu.ForeColor
            for spec in others:
                info = provider_reading(provider_states.get(spec.id),
                                         {"id": spec.id, "name": spec.label(english), "short": spec.label(english) if english else spec.short}, language)
                granted = self._pending.get(spec.id)
                if granted is not None:
                    pstate = provider_states.get(spec.id)
                    if still_pending(pstate, granted, time.time()):
                        info = {**info, "value": "Reading" if english else "查询中", "countdown": "",
                                "quota": "", "reset": "", "low": False, "warning": False}
                    else:
                        self._pending.pop(spec.id, None)
                        logging.getLogger("vram_radar").info(
                            "auto read %s: first result %.1f s after consent", spec.id, time.time() - granted)
                provider_rows.append(info)
                value, reset_txt = strip_parts(info.get("quota", ""), info.get("reset", ""), info["value"])
                color = Color.FromArgb(*quota_rule_color(
                    info.get("percent"), low=info["low"], warning=info["warning"], action=info.get("action", False),
                    known=bool(info.get("quota") or info.get("reset")), bright=bright, surface=self._palette[0]))
                cells.append((info["name"], value, reset_txt, color))
                cell_ids.append(spec.id)
            while len(self._extra_columns) < len(cells):
                make_column()
            muted = Color.FromArgb(*(round(f*0.68 + b*0.32) for f, b in zip(
                (fg.R, fg.G, fg.B), self._palette[0])))
            for (name_label, value_label, reset_label), (name, value, reset_txt, color) in zip(self._extra_columns, cells):
                # 图标 mode: the icon replaces the name (tooltip keeps full names).
                name_label.Text = "" if show_icons and multi and name_label.Image is not None else name
                level = getattr(self, "_fit_level", 0)
                value_label.Text = compact_value(value, level)
                reset_label.Text = strip_reset_text(reset_txt) if level < 1 else ""
                # Quota and reset share one colour (same rule for every provider).
                name_label.ForeColor, value_label.ForeColor, reset_label.ForeColor = muted, color, color
                name_label.Visible = value_label.Visible = True
                reset_label.Visible = bool(reset_label.Text)
            for name_label, value_label, reset_label in self._extra_columns[len(cells):]:
                name_label.Visible = value_label.Visible = reset_label.Visible = False
            # Width of the empty taskbar area left of Start (None: free placement
            # or tray side); both layouts are compacted, then clipped, to fit it.
            available = None
            if self._placement == "taskbar" and geometry:
                elements = self._layout.elements(int(user32.FindWindowW("Shell_TrayWnd", None) or 0), geometry[0],
                                                 (form.Left, form.Top, form.Right, form.Bottom) if form.Visible else None)
                gap_area = left_gap(geometry[0], elements, strip_margin(self._scale)) if elements else None
                available = gap_area[1] - gap_area[0] if gap_area else None
            if not multi:
                self._fit_key = None
                self._fit_level = 0
                # Full size when it fits; otherwise smaller type, then clipping (never
                # wider than the gap).  Longer countdowns (168.0h) and localized status
                # messages keep their text at full size whenever there is room.
                def single_fonts(factor):
                    if factor == 1.0:
                        return self._fonts
                    cached = self._single_fonts.get(factor)
                    if cached is None:
                        size = max(1, round(scale(14) * factor))
                        cached = self._single_fonts[factor] = (
                            Font("Segoe UI", size, FontStyle.Bold, GraphicsUnit.Pixel),
                            Font("Segoe UI", size, FontStyle.Bold, GraphicsUnit.Pixel))
                    return cached
                width, _factor = place_single(
                    text_controls, single_fonts,
                    lambda text, base: pick_value_font(text, base, self._cjk_fonts, self._make_font),
                    self._scale, available)
                form.ClientSize = Size(width, scale(40))
            else:
                # Two provider rows per column: "Name  value", full names.  Up
                # to four apps (two columns) fit the empty taskbar area left of
                # Start at the normal font; compact values before clipping.
                used = self._extra_columns[:len(cells)]
                icon_paths = [((provider_states.get(pid) or {}).get("install_path"), pid) for pid in cell_ids] \
                    if show_icons else None
                fit_key = (tuple((n, v, r) for n, v, r, _ in cells), available, self._scale,
                           tuple(icon_paths) if icon_paths else None, icon_light)
                if fit_key != self._fit_key:
                    self._fit_key = fit_key
                    for factor, level in FIT_PLAN:
                        self._fit_level = level
                        for (_, value_label, reset_label), (_, value, reset_txt, _) in zip(used, cells):
                            value_label.Text = compact_value(value, level)
                            reset_label.Text = strip_reset_text(reset_txt) if level < 1 else ""
                            reset_label.Visible = bool(reset_label.Text)
                        fonts = self._fit_fonts.get(factor)
                        if fonts is None:
                            fonts = self._fit_fonts[factor] = (
                                Font("Segoe UI", max(1, round(scale(12)*factor)), FontStyle.Regular, GraphicsUnit.Pixel),
                                Font("Segoe UI", max(1, round(scale(13)*factor)), FontStyle.Bold, GraphicsUnit.Pixel))
                        for top, bottom, reset_label in used:
                            if top.Font is not fonts[0]:
                                top.Font = fonts[0]
                            for label in (bottom, reset_label):
                                want = pick_value_font(label.Text, fonts[1], self._cjk_fonts, self._make_font)
                                if label.Font is not want:
                                    label.Font = want
                        icon_px = strip_icon_px(self._scale, factor)
                        for index, (name_label, *_) in enumerate(used):
                            image = None
                            if icon_paths:
                                path, pid = icon_paths[index]
                                try:
                                    from .ui_dialogs import provider_icon
                                    image = provider_icon(path, icon_px, cells[index][0])
                                except Exception:
                                    image = None
                            set_name_cell(name_label, image, cells[index][0], self._scale, factor)
                        width = place_columns(used, self._scale, factor)
                        if available is None or width <= available:
                            break
                    if available is not None and width > available:
                        width = max(1, int(available))  # clip, never move sides or cover Start
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
            # Native ToolTip stays empty: the hover detail card replaces it so
            # the two never stack. Accessibility still gets the concise text.
            if ("", len(tip_controls)) != getattr(self, "_tip_key", None):
                self._tip_key = ("", len(tip_controls))
                for control in tip_controls:
                    tooltip.SetToolTip(control, "")
            try:
                from .hover_detail import build_hover_rows, hover_card_spec
                from .providers import PROVIDERS
                names = {spec.id: spec.label(english) for spec in PROVIDERS}
                # Canonical English registry names for provider_icon (strip/menu parity).
                icon_names = {spec.id: spec.name for spec in PROVIDERS}
                paths = {}
                for spec in PROVIDERS:
                    if spec.id == "codex":
                        # Codex often has no install_path on the usage snapshot; the
                        # provider probe may. Bundled OpenAI art still wins via icon_name.
                        cpath = state.get("install_path") if isinstance(state, dict) else None
                        if not cpath:
                            cstate = provider_states.get("codex")
                            cpath = cstate.get("install_path") if isinstance(cstate, dict) else None
                        paths["codex"] = cpath
                        continue
                    pstate = provider_states.get(spec.id)
                    paths[spec.id] = pstate.get("install_path") if isinstance(pstate, dict) else None
                hover_rows = build_hover_rows(
                    state if "codex" in selected else None, provider_states, selected,
                    language=language, names=names, icon_paths=paths, icon_names=icon_names)
                self._hover_spec = hover_card_spec(hover_rows, language) if hover_rows else None
                from . import ui_dialogs
                if self._hover_interaction_allowed(bool(self._hovered) and form.Visible):
                    ui_dialogs.show_hover_card(
                        self._hover_spec, (form.Left, form.Top, form.Right, form.Bottom),
                        scale=self._scale, delay_ms=0)
                else:
                    # Leave, empty selection, menu/dialog, or disarmed: never leave a stuck card.
                    ui_dialogs.hide_hover_card()
            except Exception:
                self._hover_spec = None
                try:
                    from . import ui_dialogs
                    ui_dialogs.hide_hover_card()
                except Exception:
                    pass
            for i, (zh, en, _) in enumerate(actions):
                action_items[i].Text = en if language == "en" else zh
            position()
            update_hover()
            obscured = windows_surface_obscured(handle, self._placement == "taskbar") and not menu.Visible
            self._obscured_ticks = self._obscured_ticks + 1 if obscured else 0
            if obscured and (self._obscured_ticks >= 2 or not form.Visible):
                cancel_click()
                self._hovered = False
                try:
                    from . import ui_dialogs
                    ui_dialogs.hide_hover_card()
                except Exception:
                    pass
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

    def _mac_hover_tip(self, rows, language, state) -> str:
        """Natural macOS equivalent of the Windows hover card: richer tooltip text."""
        try:
            from .hover_detail import build_hover_rows, hover_tooltip_text
            from .providers import PROVIDERS
            english = language == "en"
            overview = {}
            try:
                overview = self.providers() if callable(self.providers) else {}
            except Exception:
                overview = {}
            selected = [x for x in (overview.get("selected") or ["codex"]) if isinstance(x, str)] or ["codex"]
            provider_states = overview.get("providers") if isinstance(overview.get("providers"), dict) else {}
            names = {spec.id: spec.label(english) for spec in PROVIDERS}
            hover_rows = build_hover_rows(state if "codex" in selected else None, provider_states,
                                          selected, language=language, names=names)
            text = hover_tooltip_text(hover_rows)
            return text or "\n".join(["Codex", *(row["detail"] for row in rows)])
        except Exception:
            return "\n".join(["Codex", *(row.get("detail", "") for row in rows)])

    def _mac_tick(self) -> None:
        # Called from an NSTimer: an exception escaping into AppKit is
        # reported as an Objective-C exception (and can end the app).
        try:
            self._mac_tick_body()
        except Exception as exc:
            if not getattr(self, "_mac_tick_failed", False):
                self._mac_tick_failed = True
                logging.getLogger("vram_radar").warning("menu-bar update failed (%s)", type(exc).__name__)

    def _mac_tick_body(self) -> None:
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
        title = "C  " + " · ".join(f"{row['label']} {row['value']} ({row['countdown']})" for row in rows[:2])
        self.status_item.button().setTitle_(title)
        self.status_item.button().setToolTip_(self._mac_hover_tip(rows, language, state))
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
        # Only now: a failed update (e.g. the item vanished during sleep/wake
        # or a display change) is retried on the next tick instead of sticking.
        self._last_signature = signature

    def stop(self) -> None:
        self.closed = True
        self.active = False
        def cleanup():
            if sys.platform == "win32":
                if getattr(self, "_outside_watch", None) is not None:
                    self._outside_watch.dispose()
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
                    from .ui_dialogs import close_toast, close_hover_card
                    close_toast()
                    close_hover_card()
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
