"""Small native quota surface, independent of the main WebView's visibility."""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from typing import Callable


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
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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


def taskbar_scale(dpi, geometry):
    preferred = max(0.6, dpi / 96 * 0.85)
    return min(preferred, max(0.6, (geometry[0][3]-geometry[0][1]-6)/40)) if geometry else preferred


def windows_taskbar_dpi(fallback=96):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    return user32.GetDpiForWindow(user32.FindWindowW("Shell_TrayWnd", None)) or fallback


def windows_taskbar_palette():
    """Follow the shell theme, including its optional system accent color."""
    import winreg
    light, accent = False, False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light = bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
            try:
                accent = bool(winreg.QueryValueEx(key, "ColorPrevalence")[0])
            except OSError:
                pass
    except OSError:
        pass
    background = (243, 243, 243) if light else (32, 32, 32)
    if accent and not light:
        try:
            import ctypes
            color, opaque = ctypes.c_uint(), ctypes.c_int()
            if ctypes.windll.dwmapi.DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
                background = ((color.value >> 16) & 255, (color.value >> 8) & 255, color.value & 255)
        except OSError:
            pass
    bright = sum(v*w for v, w in zip(background, (.2126, .7152, .0722))) > 145
    foreground = (28, 28, 28) if bright else (240, 240, 240)
    hover = tuple(max(0, v-15) if bright else min(255, v+20) for v in background)
    return background, foreground, hover, bright


def windows_surface_obscured(widget, docked=True):
    """Yield to fullscreen applications and to a hidden/restarting taskbar."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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
    if name.value in {"Shell_TrayWnd", "Progman", "WorkerW"}:
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
    """CodexUsage's two disks: remaining quota and remaining window time.

    Layout/time notation adapted from Amygdala42/CodexUsage (MIT),
    WidgetRenderer.cs at cd72934ef01960c221aa8461b06ab657cf2e803d.
    """
    now = time.time() if now is None else now
    rows = quota_lines(state, language, now=now)
    row = rows[min(index, len(rows)-1)] if rows else {"value": "—", "low": False}
    windows = state.get("windows", [])
    window = windows[index] if 0 <= index < len(windows) else {}
    percent = window.get("remaining_percent") if row["value"] != "—" else None
    reset, minutes = window.get("resets_at"), window.get("window_minutes")
    left = reset - now if isinstance(reset, (int, float)) and math.isfinite(reset) else None
    time_percent = None
    if left is not None and isinstance(minutes, (int, float)) and math.isfinite(minutes) and minutes > 0:
        time_percent = max(0, min(100, left / (minutes * 60) * 100))
    english = language == "en"
    warning = False
    if state.get("state") == "error":
        countdown = ("Login" if english else "登录") if state.get("code") in {
            "login_required", "unsupported_account"} else ("Retry" if english else "重试")
        warning, time_percent = True, None
    elif state.get("stale"):
        countdown, warning, time_percent = ("Stale" if english else "过期"), True, None
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
            "time_percent": time_percent, "warning": warning, "low": row["low"]}


class CodexUsageSurface:
    def __init__(self, window, snapshot: Callable[[], dict], *, language: Callable[[], str],
                 open_settings: Callable[[], object], refresh: Callable[[], object],
                 disable: Callable[[], object], quit_application: Callable[[], object],
                 open_home: Callable[[], object] | None = None,
                 display_options: Callable[[], dict] | None = None,
                 save_display: Callable[[str, object], dict] | None = None):
        self.window, self.snapshot, self.language = window, snapshot, language
        self.open_settings, self.refresh = open_settings, refresh
        self.open_home = open_home or open_settings
        self.display_options = display_options or (lambda: {})
        self.save_display = save_display
        self._show_disks = False
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

        def rounded_path():
            path = GraphicsPath()
            diameter, width, height = scale(16), form.Width-1, form.Height-1
            for x, y, angle in [(0, 0, 180), (width-diameter, 0, 270),
                                 (width-diameter, height-diameter, 0), (0, height-diameter, 90)]:
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
            track = SolidBrush(track_color)
            try:
                if self._hovered:
                    graphics.DrawPath(pen, path)
                self._disk_bounds = []
                if self._show_disks:
                    diameter, row_height = scale(14), scale(20)
                    inset = (row_height-diameter)//2
                    self._disk_bounds = [(scale(7), row*row_height+inset, diameter, diameter) for row in (0, 1)]
                    for bounds, percent, color in zip(self._disk_bounds,
                            (self._reading["percent"], self._reading["time_percent"]), (quota_color, time_color)):
                        graphics.FillEllipse(track, *bounds)
                        brush = SolidBrush(color)
                        rim = Pen(Color.FromArgb(142, 151, 157), 1)
                        try:
                            if percent is not None and percent >= 100:
                                graphics.FillEllipse(brush, *bounds)
                            elif percent is not None and percent > 0:
                                graphics.FillPie(brush, *map(float, bounds), -90.0, float(percent*3.6))
                            graphics.DrawEllipse(rim, *bounds)
                        finally:
                            brush.Dispose()
                            rim.Dispose()
            finally:
                track.Dispose()
                pen.Dispose()
                path.Dispose()

        form.Paint += paint
        # Showing the strip must not interrupt typing in another application.
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
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
            label.BackColor = form.BackColor
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

        def position():
            geometry = windows_taskbar_geometry()
            if self._placement == "taskbar" and geometry:
                form.Location = Point(*taskbar_anchor(*geometry, (form.Width, form.Height)))
                return
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
        menu.Closed += lambda *_: user32.PostMessageW(handle, 0, 0, 0)
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
            brush = SolidBrush(form.BackColor)
            try:
                event.Graphics.FillRectangle(brush, event.AffectedBounds)
            finally:
                brush.Dispose()
        def fill_item(_sender, event):
            brush = SolidBrush(form.BackColor)
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
            brush = SolidBrush(form.BackColor)
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
            brush = SolidBrush(self._menu_hover if event.Item.Selected else form.BackColor)
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
            brush = SolidBrush(form.BackColor)
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
        for key, value, zh, en in [
                ("codex_show_disks", False, "纯文字", "Text only"),
                ("codex_show_disks", True, "饼图与数字", "Disks and text")]:
            item = self._display_menu.DropDownItems.Add(zh)
            item.Click += lambda _s, _e, k=key, v=value: save_choice(k, v)
            self._display_choices.append((item, key, value, zh, en))
        for item in (self._dock_item, self._window_menu):
            item.Padding = Padding(4, 4, 8, 4)
        menu.Items.Add(ToolStripSeparator())
        menu.Items.Add(action_items[-1])
        menu_scale = windows_taskbar_dpi()/96
        self._menu_icon_font = Font("Segoe UI Symbol", 12, FontStyle.Regular)
        descriptions = {
            action_items[0]: ("↻", "立即同步最新额度", "Fetch the latest usage"),
            action_items[1]: ("−", "可在设置中重新开启", "Restore from Settings"),
            self._dock_item: ("✓", "固定显示在展开箭头旁", "Keep beside the system tray"),
            self._display_menu: ("☷", "显示设置", "Display options"),
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

        def choose(identity):
            self._selected_id = identity
            tick()

        def tick(*_):
            nonlocal quota_color, time_color, track_color, warning_color
            if self.closed:
                return
            state = self.snapshot()
            language = self.language()
            options = self.display_options()
            show_disks = bool(options.get("codex_show_disks", False))
            if show_disks != self._show_disks:
                self._show_disks = show_disks
                form.Invalidate()
            self._display_menu.Text = ("Display options" if language == "en" else "显示设置") + (
                (" · Save failed" if language == "en" else " · 保存失败") if self._display_error else "")
            for item, key, value, zh, en in self._display_choices:
                item.Text = en if language == "en" else zh
                item.Checked = show_disks == value
            palette = windows_taskbar_palette()
            if palette != getattr(self, "_palette", None):
                self._palette = palette
                background, foreground, hover, bright = palette
                form.BackColor = Color.FromArgb(*background)
                menu.BackColor, menu.ForeColor = form.BackColor, Color.FromArgb(*foreground)
                self._menu_hover = Color.FromArgb(*hover)
                quota_color = Color.FromArgb(*( (25, 160, 20) if bright else (67, 220, 55) ))
                time_color = Color.FromArgb(*( (0, 98, 150) if bright else (143, 208, 248) ))
                track_color = Color.FromArgb(*( (210, 215, 218) if bright else (65, 72, 77) ))
                warning_color = Color.FromArgb(*( (139, 85, 0) if bright else (224, 176, 100) ))
                self._labels[0].ForeColor = quota_color
                for label in text_controls:
                    label.BackColor = form.BackColor
                self._window_menu.DropDown.BackColor = form.BackColor
                self._window_menu.DropDown.ForeColor = menu.ForeColor
                self._display_menu.DropDown.BackColor = form.BackColor
                self._display_menu.DropDown.ForeColor = menu.ForeColor
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
                for old_font in old_fonts:
                    old_font.Dispose()
            rows = quota_lines(state, language)
            self.active = bool(rows)
            if not rows:
                click_timer.Stop()
                self._last_click = None
                form.Hide()
                return
            windows = state.get("windows", [])
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
            reading = widget_reading(state, index, language)
            quota_color = Color.FromArgb(*usage_color(reading["percent"], bright=self._palette[3]))
            time_color = Color.FromArgb(*usage_color(reading["remaining_seconds"], bright=self._palette[3], waiting=True))
            self._labels[0].ForeColor = quota_color
            self._labels[0].Text = reading["value"]
            self._countdowns[0].Text = reading["countdown"]
            # Keep the gap tight without shrinking type or clipping longer
            # countdowns (for example 168.0h) and localized status messages.
            text_width = max(scale(47), *(label.GetPreferredSize(Size(0, 0)).Width for label in text_controls))
            for label in text_controls:
                label.Size = Size(text_width, scale(20))
            left_padding = 25 if self._show_disks else 5
            for y, label in zip((0, 20), text_controls):
                label.Location = Point(scale(left_padding), scale(y))
            form.ClientSize = Size(scale(left_padding) + text_width + scale(2), scale(40))
            self._countdowns[0].ForeColor = time_color
            if reading != self._reading:
                self._reading = reading
                form.Invalidate()
            hint = "Click: GPU home · Double-click: quota details" if language == "en" else "单击打开 GPU 主页 · 双击查看额度详情"
            tip = "\n".join(row["detail"] for row in rows) + "\n" + hint
            form.AccessibleName = "Codex · " + rows[index]["label"]
            form.AccessibleDescription = tip
            for control in [form, *text_controls]:
                tooltip.SetToolTip(control, tip)
            for i, (zh, en, _) in enumerate(actions):
                action_items[i].Text = en if language == "en" else zh
            position()
            update_hover()
            if windows_surface_obscured(handle, self._placement == "taskbar") and not menu.Visible:
                cancel_click()
                form.Hide()
                return
            if not form.Visible:
                form.Show()
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos(handle, -1, 0, 0, 0, 0, 0x0013)

        timer = Timer()
        self.timer = timer
        timer.Interval = 1000
        timer.Tick += tick
        self._tick = tick
        tick()
        timer.Start()

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
                if self.form is not None:
                    self.form.Close()
                    self.form.Dispose()
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
