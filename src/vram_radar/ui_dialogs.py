"""One visual design for all of VRAM Radar's own popups (Windows).

Two components share the same tokens (see docs/dialog-design.md):

* ``show_dialog(spec)`` -- modal card: app icon, bold headline, 2-3 short
  bullet lines, an optional small note, primary/secondary buttons.
* ``show_toast(spec, anchor)`` -- non-activating notice above the strip that
  fades out on its own (progress, limits, confirmations).

``spec`` dicts are built by the pure helpers below (testable without .NET);
the whole surface is drawn into one bitmap (crisp at any DPI, identical in
screenshots), the window only hit-tests the buttons.
"""
from __future__ import annotations

import logging
import os
import sys

LOG = logging.getLogger("vram_radar")

# Design tokens, in device-independent pixels (x DPI/96 at runtime).
TOKENS = {
    "width": 420, "pad": 24, "icon": 40, "icon_gap": 16,
    "headline_px": 16, "body_px": 13, "note_px": 12,
    "headline_gap": 10, "line_gap": 6, "note_gap": 12,
    "bullet": 5, "bullet_gap": 10,
    "footer_pad": 20, "button_h": 32, "button_min_w": 104, "button_gap": 8, "button_radius": 4,
    "corner": 8,
    "toast_width": 320, "toast_pad": 14, "toast_icon": 24, "toast_gap": 12,
    "toast_headline_px": 13, "toast_body_px": 12, "toast_ms": 3500, "toast_offset": 8,
}
FONT = "Microsoft YaHei UI"   # Segoe-like Latin + CJK in one face (Windows 11 UI font for zh-CN)

PALETTES = {
    "light": {"surface": (255, 255, 255), "footer": (243, 243, 243), "border": (224, 224, 224),
              "text": (26, 26, 26), "secondary": (96, 96, 96),
              "button": (253, 253, 253), "button_hover": (245, 245, 245), "button_border": (208, 208, 208),
              "button_text": (26, 26, 26)},
    "dark": {"surface": (44, 44, 44), "footer": (32, 32, 32), "border": (64, 64, 64),
             "text": (255, 255, 255), "secondary": (200, 200, 200),
             "button": (56, 56, 56), "button_hover": (64, 64, 64), "button_border": (78, 78, 78),
             "button_text": (255, 255, 255)},
}
DEFAULT_ACCENT = (0, 120, 212)


def _mix(a, b, t):
    return tuple(max(0, min(255, round(x * (1 - t) + y * t))) for x, y in zip(a, b))


def _lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def palette(dark: bool, accent=None) -> dict:
    """Colours for one theme.  Like Windows 11, the accent is shaded darker on
    light and lighter on dark surfaces so the primary button keeps contrast."""
    p = dict(PALETTES["dark" if dark else "light"])
    base = tuple(accent or DEFAULT_ACCENT)
    primary = _mix(base, (255, 255, 255), 0.35) if dark else _mix(base, (0, 0, 0), 0.12)
    p["primary"] = primary
    p["primary_hover"] = _mix(primary, (0, 0, 0) if dark else (255, 255, 255), 0.1)
    p["primary_text"] = (0, 0, 0) if _lum(primary) > 150 else (255, 255, 255)
    p["bullet"] = primary
    p["focus"] = p["text"]
    return p


# -- content specs (pure) -------------------------------------------------

def consent_spec(app: str, name: str | None = None, eta_seconds: int = 10, icon=None) -> dict:
    name = name or app
    return {"kind": "dialog", "icon": icon, "icon_name": name,
            "headline": f"允许显存雷达自动读取 {name} 额度？",
            "lines": [f"使用 {app} 在本机的登录状态，只读查询额度",
                      "登录信息只在内存中使用，不保存、不上传",
                      "可随时在右键菜单「自动读取额度」中关闭"],
            "note": f"允许后约 {eta_seconds} 秒内显示在任务栏，之后每 5 分钟更新",
            "buttons": [("allow", "允许", True), ("cancel", "暂不", False)], "cancel": "cancel"}


def revoke_spec(name: str, icon=None) -> dict:
    return {"kind": "dialog", "icon": icon, "icon_name": name,
            "headline": f"关闭 {name} 的自动读取？",
            "lines": [f"任务栏不再查询 {name} 额度，只显示本机状态", "之后可随时在同一菜单重新开启"],
            "note": "",
            "buttons": [("revoke", "关闭", True), ("cancel", "取消", False)], "cancel": "cancel"}


def notice_spec(text: str, name: str = "", icon=None) -> dict:
    """A one-button notice from a short message: the first sentence becomes
    the headline, the rest the body ("未检测到 Kimi。请先…" -> two parts)."""
    text = str(text or "").strip()
    head, _, rest = text.partition("。")
    lines = [part.strip() for part in rest.split("。") if part.strip()]
    return {"kind": "dialog", "icon": icon, "icon_name": name or head,
            "headline": head, "lines": lines, "note": "",
            "buttons": [("ok", "知道了", True)], "cancel": "ok"}


def toast_spec(headline: str, line: str = "", name: str = "", icon=None) -> dict:
    return {"kind": "toast", "icon": icon, "icon_name": name or headline,
            "headline": headline, "lines": [line] if line else [], "note": "", "buttons": []}


def button_rects(width: int, top: int, scale: float, buttons) -> list[tuple[int, int, int, int]]:
    """Footer button rectangles (x, y, w, h).  Two or more buttons share the
    row equally (Windows 11 dialog style, primary first); one sits right."""
    s = lambda v: int(round(v * scale))
    pad, gap, h = s(TOKENS["footer_pad"]), s(TOKENS["button_gap"]), s(TOKENS["button_h"])
    y = top + pad
    count = len(buttons)
    if count == 0:
        return []
    if count == 1:
        w = s(TOKENS["button_min_w"]) + s(16)
        return [(width - s(TOKENS["pad"]) - w, y, w, h)]
    inner = width - 2 * s(TOKENS["pad"])
    w = (inner - gap * (count - 1)) // count
    return [(s(TOKENS["pad"]) + i * (w + gap), y, w, h) for i in range(count)]


def hit_button(rects, point) -> int | None:
    x, y = point
    for index, (bx, by, bw, bh) in enumerate(rects):
        if bx <= x < bx + bw and by <= y < by + bh:
            return index
    return None


# -- system theme -----------------------------------------------------------

def system_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0]) == 0
    except (OSError, ValueError, TypeError):
        return False


def system_accent():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value = int(winreg.QueryValueEx(key, "AccentColor")[0]) & 0xFFFFFFFF
            return (value & 255, (value >> 8) & 255, (value >> 16) & 255)  # ABGR
    except (OSError, ValueError, TypeError):
        return None


def system_scale() -> float:
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, dpi / 96) if dpi else 1.0
    except Exception:
        return 1.0


# -- icons --------------------------------------------------------------------

_ICONS: dict = {}
ICON_EXTENSIONS = (".ico", ".png")


def icon_candidates(path: str) -> list[str]:
    """Where an app's icon may live: the exe itself, then common resource
    files next to it (Electron/Tauri apps ship resources\\icon.ico/png)."""
    if not path:
        return []
    folder = os.path.dirname(path)
    out = [path] if path.lower().endswith((".exe", ".ico", ".png")) else []
    for sub in ("", "resources", os.path.join("resources", "app"), "assets"):
        for name in ("icon.ico", "icon.png", "app.ico", "logo.png"):
            out.append(os.path.join(folder, sub, name))
    return out


def _exe_icon(path: str, px: int):
    import ctypes
    from ctypes import wintypes
    from System import IntPtr
    from System.Drawing import Bitmap, Icon
    user32 = ctypes.windll.user32
    user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
                                            wintypes.UINT, wintypes.UINT]
    user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    handle, ident = ctypes.c_void_p(), wintypes.UINT()
    # Picks the closest image in the exe (incl. 256 px PNG frames) and
    # resamples it to exactly px x px: crisp at 150 %.
    count = user32.PrivateExtractIconsW(path, 0, px, px, ctypes.byref(handle), ctypes.byref(ident), 1, 0)
    if count < 1 or not handle.value:
        return None
    try:
        icon = Icon.FromHandle(IntPtr(handle.value))
        try:
            return icon.ToBitmap()   # an independent 32-bit ARGB copy
        finally:
            icon.Dispose()           # FromHandle does not own the HICON...
    finally:
        user32.DestroyIcon(handle)   # ...so free it here (no GDI/USER leak)


def _file_icon(path: str, px: int):
    from System.Drawing import Bitmap, Graphics, Icon, Size
    from System.Drawing.Drawing2D import InterpolationMode
    if path.lower().endswith(".ico"):
        icon = Icon(path, Size(px, px))
        try:
            frame = icon.ToBitmap()
            if frame.Width == px and frame.Height == px:
                return frame
            try:
                return Bitmap(frame, Size(px, px))
            finally:
                frame.Dispose()
        finally:
            icon.Dispose()
    source = Bitmap(path)
    try:
        bitmap = Bitmap(px, px)
        graphics = Graphics.FromImage(bitmap)
        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic
        graphics.DrawImage(source, 0, 0, px, px)
        graphics.Dispose()
        return bitmap
    finally:
        source.Dispose()


def letter_tile(name: str, px: int, accent=None):
    """Neutral fallback: rounded tile with the app's initial."""
    from System.Drawing import Bitmap, Color, Font, FontStyle, Graphics, GraphicsUnit, SolidBrush, StringAlignment, StringFormat, RectangleF
    from System.Drawing.Drawing2D import SmoothingMode
    from System.Drawing.Text import TextRenderingHint
    colour = _mix(tuple(accent or DEFAULT_ACCENT), (128, 128, 128), 0.35)
    bitmap = Bitmap(px, px)
    graphics = Graphics.FromImage(bitmap)
    graphics.SmoothingMode = SmoothingMode.AntiAlias
    graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    path = _rounded(0, 0, px - 1, px - 1, max(2, px // 5))
    brush, white = SolidBrush(Color.FromArgb(*colour)), SolidBrush(Color.White)
    font = Font(FONT, max(6.0, px * 0.5), FontStyle.Bold, GraphicsUnit.Pixel)
    fmt = StringFormat()
    fmt.Alignment = fmt.LineAlignment = StringAlignment.Center
    try:
        graphics.FillPath(brush, path)
        graphics.DrawString((name or "?")[:1].upper(), font, white, RectangleF(0, 0, px, px), fmt)
    finally:
        for item in (path, brush, white, font, fmt, graphics):
            item.Dispose()
    return bitmap


def provider_icon(path: str | None, px: int, name: str = "", accent=None):
    """Bitmap of the installed app's own icon at ``px`` (cached), or a letter tile."""
    key = (str(path or ""), int(px), name)
    if key in _ICONS:
        return _ICONS[key]
    bitmap = None
    for candidate in icon_candidates(str(path or "")):
        try:
            if not os.path.isfile(candidate):
                continue
            bitmap = _exe_icon(candidate, px) if candidate.lower().endswith(".exe") else _file_icon(candidate, px)
            if bitmap is not None:
                break
        except Exception as exc:
            LOG.info("app icon unavailable (%s)", type(exc).__name__)
    if bitmap is None:
        try:
            bitmap = letter_tile(name, px, accent)
        except Exception:
            bitmap = None
    _ICONS[key] = bitmap
    return bitmap


# -- drawing --------------------------------------------------------------------

def _rounded(x, y, w, h, r):
    from System.Drawing.Drawing2D import GraphicsPath
    path = GraphicsPath()
    d = max(1, r * 2)
    path.AddArc(x, y, d, d, 180, 90)
    path.AddArc(x + w - d, y, d, d, 270, 90)
    path.AddArc(x + w - d, y + h - d, d, d, 0, 90)
    path.AddArc(x, y + h - d, d, d, 90, 90)
    path.CloseFigure()
    return path


def _color(rgb):
    from System.Drawing import Color
    return Color.FromArgb(*rgb)


def render(spec: dict, scale: float, pal: dict, icon=None, *, hover=None, focus=None, pressed=None):
    """(Bitmap, layout) of a dialog/toast.  ``layout["buttons"]`` are the
    button rects in bitmap pixels."""
    import math
    from System.Drawing import (Bitmap, Font, FontStyle, Graphics, GraphicsUnit, Pen, RectangleF, SizeF, SolidBrush,
                                StringAlignment, StringFormat)
    from System.Drawing.Drawing2D import InterpolationMode, SmoothingMode
    from System.Drawing.Imaging import PixelFormat
    from System.Drawing.Text import TextRenderingHint
    s = lambda v: int(round(v * scale))
    toast = spec.get("kind") == "toast"
    t = TOKENS
    width = s(t["toast_width"] if toast else t["width"])
    pad = s(t["toast_pad"] if toast else t["pad"])
    icon_px = s(t["toast_icon"] if toast else t["icon"])
    text_x = pad + icon_px + s(t["toast_gap"] if toast else t["icon_gap"])
    text_w = width - text_x - pad
    fonts = {
        "head": Font(FONT, float(s(t["toast_headline_px"] if toast else t["headline_px"])), FontStyle.Bold, GraphicsUnit.Pixel),
        "body": Font(FONT, float(s(t["toast_body_px"] if toast else t["body_px"])), FontStyle.Regular, GraphicsUnit.Pixel),
        "note": Font(FONT, float(s(t["note_px"])), FontStyle.Regular, GraphicsUnit.Pixel),
        "button": Font(FONT, float(s(t["body_px"])), FontStyle.Regular, GraphicsUnit.Pixel),
    }
    # Grayscale anti-aliased GDI+ text: even stroke weight on every surface
    # colour (ClearType on coloured fills looked bold/fringed).
    fmt = StringFormat(StringFormat.GenericTypographic)
    centred = StringFormat(StringFormat.GenericTypographic)
    centred.Alignment = centred.LineAlignment = StringAlignment.Center
    probe_bitmap = Bitmap(1, 1)
    probe = Graphics.FromImage(probe_bitmap)
    probe.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    def measure(text, font, w):
        return int(math.ceil(probe.MeasureString(text, font, SizeF(w, 10000), fmt).Height))
    bullet, bullet_gap = s(t["bullet"]), s(t["bullet_gap"])
    blocks = []
    y = pad
    head_h = measure(spec["headline"], fonts["head"], text_w)
    blocks.append(("head", spec["headline"], text_x, y, text_w, head_h))
    y += head_h
    lines = [line for line in spec.get("lines") or [] if line]
    if lines:
        y += s(4) if toast else s(t["headline_gap"])
    for index, line in enumerate(lines):
        if toast:
            h = measure(line, fonts["body"], text_w)
            blocks.append(("plain", line, text_x, y, text_w, h))
        else:
            lw = text_w - bullet - bullet_gap
            h = measure(line, fonts["body"], lw)
            blocks.append(("bullet", line, text_x + bullet + bullet_gap, y, lw, h))
        y += h + (s(t["line_gap"]) if index < len(lines) - 1 else 0)
    if spec.get("note"):
        y += s(t["note_gap"])
        h = measure(spec["note"], fonts["note"], text_w)
        blocks.append(("note", spec["note"], text_x, y, text_w, h))
        y += h
    content_h = max(y, pad + icon_px) + pad
    buttons = spec.get("buttons") or []
    footer_h = (2 * s(t["footer_pad"]) + s(t["button_h"])) if buttons else 0
    height = content_h + footer_h
    rects = button_rects(width, content_h, scale, buttons)

    bitmap = Bitmap(width, height, PixelFormat.Format24bppRgb)
    g = Graphics.FromImage(bitmap)
    disposables = list(fonts.values()) + [fmt, centred, probe, probe_bitmap]
    g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    try:
        g.Clear(_color(pal["surface"]))
        if footer_h:
            footer = SolidBrush(_color(pal["footer"])); disposables.append(footer)
            g.FillRectangle(footer, 0, content_h, width, footer_h)
            line_pen = Pen(_color(pal["border"]), 1); disposables.append(line_pen)
            g.DrawLine(line_pen, 0, content_h, width, content_h)
        g.InterpolationMode = InterpolationMode.HighQualityBicubic
        g.SmoothingMode = SmoothingMode.AntiAlias
        if icon is not None:
            g.DrawImage(icon, pad, pad, icon_px, icon_px)
        bullet_brush = SolidBrush(_color(pal["bullet"])); disposables.append(bullet_brush)
        for kind, text, x, by, w, h in blocks:
            font = fonts["head"] if kind == "head" else fonts["note"] if kind == "note" else fonts["body"]
            colour = pal["secondary"] if kind in ("note", "plain") else pal["text"]
            if kind == "bullet":
                first = measure("国", font, w)
                g.FillEllipse(bullet_brush, text_x, by + (first - bullet) / 2.0, bullet, bullet)
            brush = SolidBrush(_color(colour)); disposables.append(brush)
            g.DrawString(text, font, brush, RectangleF(x, by, w + 1, h + s(2)), fmt)
        radius = s(t["button_radius"])
        for index, ((_, caption, primary), (bx, by, bw, bh)) in enumerate(zip(buttons, rects)):
            hot = index == hover or index == pressed
            fill = (pal["primary_hover"] if hot else pal["primary"]) if primary else (
                pal["button_hover"] if hot else pal["button"])
            path = _rounded(bx, by, bw - 1, bh - 1, radius); disposables.append(path)
            brush = SolidBrush(_color(fill)); disposables.append(brush)
            g.FillPath(brush, path)
            if not primary:
                pen = Pen(_color(pal["button_border"]), 1); disposables.append(pen)
                g.DrawPath(pen, path)
            if index == focus:
                ring = _rounded(bx - s(3), by - s(3), bw + s(5), bh + s(5), radius + s(2)); disposables.append(ring)
                pen = Pen(_color(pal["focus"]), float(max(1, s(1.5)))); disposables.append(pen)
                g.DrawPath(pen, ring)
            text_brush = SolidBrush(_color(pal["primary_text"] if primary else pal["button_text"]))
            disposables.append(text_brush)
            g.DrawString(caption, fonts["button"], text_brush, RectangleF(bx, by + 1, bw, bh), centred)
    finally:
        for item in disposables:
            item.Dispose()
        g.Dispose()
    return bitmap, {"width": width, "height": height, "buttons": rects, "content_h": content_h}


def framed(bitmap, scale: float, pal: dict):
    """Screenshot form: rounded corners (transparent outside) plus border."""
    from System.Drawing import Bitmap, Color, Graphics, Pen, TextureBrush
    from System.Drawing.Drawing2D import SmoothingMode
    radius = int(round(TOKENS["corner"] * scale))
    out = Bitmap(bitmap.Width, bitmap.Height)
    g = Graphics.FromImage(out)
    g.SmoothingMode = SmoothingMode.AntiAlias
    g.Clear(Color.Transparent)
    path = _rounded(0, 0, bitmap.Width - 1, bitmap.Height - 1, radius)
    brush, pen = TextureBrush(bitmap), Pen(_color(pal["border"]), 1)
    try:
        g.FillPath(brush, path)
        g.DrawPath(pen, path)
    finally:
        brush.Dispose(); pen.Dispose(); path.Dispose(); g.Dispose()
    return out


def save_preview(spec: dict, target: str, *, scale: float = 1.5, dark: bool = False, accent=None,
                 icon_path: str | None = None) -> str:
    pal = palette(dark, accent)
    icon_px = int(round((TOKENS["toast_icon"] if spec.get("kind") == "toast" else TOKENS["icon"]) * scale))
    icon = provider_icon(icon_path, icon_px, spec.get("icon_name", ""), accent)
    bitmap, _ = render(spec, scale, pal, icon)
    out = framed(bitmap, scale, pal)
    out.Save(target)
    out.Dispose(); bitmap.Dispose()
    return target


# -- windows ------------------------------------------------------------------

def _dwm_style(handle: int, pal: dict, dark: bool) -> None:
    try:
        import ctypes
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(handle), 33, ctypes.byref(value), 4)
        r, g, b = pal["border"]
        border = ctypes.c_uint(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(handle), 34, ctypes.byref(border), 4)
        mode = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(handle), 20, ctypes.byref(mode), 4)
    except Exception:
        pass


def _icon_for(spec, scale, accent, icon_path):
    px = int(round((TOKENS["toast_icon"] if spec.get("kind") == "toast" else TOKENS["icon"]) * scale))
    return provider_icon(icon_path, px, spec.get("icon_name", ""), accent)


def show_dialog(spec: dict, *, scale: float | None = None, icon_path: str | None = None,
                dark: bool | None = None, accent=None) -> str:
    """Modal; returns the chosen button id (Esc / close = ``spec["cancel"]``)."""
    import ctypes
    from System.Drawing import Size
    from System.Windows.Forms import (AutoScaleMode, Form, FormBorderStyle, FormStartPosition,
                                      ImageLayout, Keys, MouseButtons)
    scale = scale or system_scale()
    dark = system_dark() if dark is None else dark
    accent = accent or system_accent()
    pal = palette(dark, accent)
    icon = _icon_for(spec, scale, accent, icon_path)
    buttons = spec.get("buttons") or [("ok", "OK", True)]
    primary = next((i for i, b in enumerate(buttons) if b[2]), 0)
    state = {"hover": None, "pressed": None, "focus": None, "result": spec.get("cancel") or buttons[-1][0],
             "rects": []}
    form = Form()
    def paint():
        image, layout = render(spec, scale, pal, icon, hover=state["hover"], focus=state["focus"],
                               pressed=state["pressed"])
        old = form.BackgroundImage
        form.BackgroundImage = image
        state["rects"] = layout["buttons"]
        if old is not None:
            old.Dispose()
        return layout
    try:
        form.AutoScaleMode = getattr(AutoScaleMode, "None")
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.CenterScreen
        form.ShowInTaskbar = False
        form.TopMost = True
        form.KeyPreview = True
        form.Text = spec.get("headline", "VRAM Radar")
        form.BackgroundImageLayout = getattr(ImageLayout, "None")
        layout = paint()
        form.ClientSize = Size(layout["width"], layout["height"])
        _dwm_style(int(form.Handle.ToInt64()), pal, dark)
        def finish(index):
            state["result"] = buttons[index][0]
            form.Close()
        def mouse_move(_s, e):
            index = hit_button(state["rects"], (e.X, e.Y))
            if index != state["hover"]:
                state["hover"] = index
                paint()
        def mouse_down(_s, e):
            if e.Button != MouseButtons.Left:
                return
            index = hit_button(state["rects"], (e.X, e.Y))
            if index is None:
                user32 = ctypes.windll.user32   # drag by the card body
                user32.ReleaseCapture()
                user32.SendMessageW(ctypes.c_void_p(int(form.Handle.ToInt64())), 0xA1, 2, 0)
                return
            state["pressed"] = index
            paint()
        def mouse_up(_s, e):
            index = hit_button(state["rects"], (e.X, e.Y))
            pressed, state["pressed"] = state["pressed"], None
            if index is not None and index == pressed:
                finish(index)
            else:
                paint()
        def mouse_leave(*_):
            if state["hover"] is not None:
                state["hover"] = None
                paint()
        def key_down(_s, e):
            if e.KeyCode == Keys.Escape:
                form.Close()
            elif e.KeyCode in (Keys.Enter, Keys.Space):
                finish(state["focus"] if state["focus"] is not None else primary)
            elif e.KeyCode in (Keys.Tab, Keys.Left, Keys.Right):
                step = -1 if (e.KeyCode == Keys.Left or e.Shift) else 1
                current = state["focus"] if state["focus"] is not None else primary - step
                state["focus"] = (current + step) % len(buttons)
                paint()
        form.MouseMove += mouse_move
        form.MouseDown += mouse_down
        form.MouseUp += mouse_up
        form.MouseLeave += mouse_leave
        form.KeyDown += key_down
        def tab_key(_s, e):  # Tab is a dialog key; let KeyDown see it
            if e.KeyCode == Keys.Tab:
                e.IsInputKey = True
        form.PreviewKeyDown += tab_key
        form.ShowDialog()
        return state["result"]
    finally:
        image = form.BackgroundImage
        form.Dispose()
        if image is not None:
            image.Dispose()


_TOAST = {"form": None}


def show_toast(spec: dict, anchor=None, *, scale: float | None = None, icon_path: str | None = None,
               dark: bool | None = None, accent=None, ms: int | None = None):
    """Non-activating notice above ``anchor`` (x0, y0, x1, y1 screen px);
    replaces any previous toast and closes itself.  UI thread only."""
    import ctypes
    from System.Drawing import Point, Size
    from System.Windows.Forms import (AutoScaleMode, Form, FormBorderStyle, FormStartPosition, ImageLayout,
                                      Screen, Timer)
    close_toast()
    scale = scale or system_scale()
    dark = system_dark() if dark is None else dark
    accent = accent or system_accent()
    pal = palette(dark, accent)
    icon = _icon_for(spec, scale, accent, icon_path)
    image, layout = render(spec, scale, pal, icon)
    form = Form()
    form.AutoScaleMode = getattr(AutoScaleMode, "None")
    form.FormBorderStyle = getattr(FormBorderStyle, "None")
    form.StartPosition = FormStartPosition.Manual
    form.ShowInTaskbar = False
    form.TopMost = True
    form.Text = spec.get("headline", "")
    form.BackgroundImageLayout = getattr(ImageLayout, "None")
    form.BackgroundImage = image
    width, height = layout["width"], layout["height"]
    form.ClientSize = Size(width, height)
    gap = int(round(TOKENS["toast_offset"] * scale))
    if anchor:
        area = Screen.FromPoint(Point(int(anchor[0]), int(anchor[1]))).WorkingArea
        x = min(max(area.Left + gap, int(anchor[0])), area.Right - width - gap)
        y = int(anchor[1]) - height - gap
        if y < area.Top:
            y = int(anchor[3]) + gap
    else:
        area = Screen.PrimaryScreen.WorkingArea
        x, y = area.Right - width - gap, area.Bottom - height - gap
    handle = int(form.Handle.ToInt64())
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW(ctypes.c_void_p(handle), -20,
                          user32.GetWindowLongW(ctypes.c_void_p(handle), -20) | 0x08000000 | 0x80 | 0x8)
    _dwm_style(handle, pal, dark)
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1), x, y, width, height, 0x0040 | 0x0010)
    timer = Timer()
    timer.Interval = int(ms or TOKENS["toast_ms"])
    timer.Tick += lambda *_: close_toast()
    form.Click += lambda *_: close_toast()
    _TOAST.update(form=form, timer=timer, image=image)
    timer.Start()
    return form


def close_toast() -> None:
    form, timer, image = _TOAST.get("form"), _TOAST.get("timer"), _TOAST.get("image")
    _TOAST.update(form=None, timer=None, image=None)
    try:
        if timer is not None:
            timer.Stop(); timer.Dispose()
        if form is not None:
            form.Close(); form.Dispose()
        if image is not None:
            image.Dispose()
    except Exception:
        pass