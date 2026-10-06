"""One visual design for all of VRAM Radar's own popups (Windows).

Two components share the same tokens (see docs/dialog-design.md):

* ``show_dialog(spec)`` -- modal card: app icon, bold headline, 2-3 short
  bullet lines, an optional small note, primary/secondary buttons.
* ``show_toast(spec, anchor)`` -- non-activating notice above the strip that
  fades out on its own (progress, limits, confirmations).
* ``show_hover_card(spec, anchor)`` -- non-activating detail card on strip
  hover (one reused window; never takes focus).

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
    # Compact hover card above the taskbar strip (one reusable window).
    "hover_width": 300, "hover_pad": 12, "hover_icon": 20, "hover_gap": 10,
    "hover_name_px": 13, "hover_body_px": 12, "hover_note_px": 11,
    "hover_row_gap": 10, "hover_line_gap": 2, "hover_offset": 8,
    "hover_spark_w": 48, "hover_spark_h": 14, "hover_spark_gap": 6,
    # Content-fit card width (DIP): clamp between min and max (and the work area).
    "hover_min_width": 140, "hover_max_width": 440,
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

def consent_spec(app: str, name: str | None = None, eta_seconds: int = 10, icon=None,
                 language: str = "zh-CN") -> dict:
    name = name or app
    if language == "en":
        return {"kind": "dialog", "icon": icon, "icon_name": name,
                "headline": f"Let VRAM Radar read your {name} quota automatically?",
                "lines": [f"Uses {app}'s sign-in on this PC for a read-only quota check",
                          "Login data stays in memory only; never saved or uploaded",
                          "Turn it off anytime under right-click > Read quota automatically"],
                "note": f"Shows on the taskbar within about {eta_seconds} s, then updates every 5 minutes",
                "buttons": [("allow", "Allow", True), ("cancel", "Not now", False)], "cancel": "cancel"}
    return {"kind": "dialog", "icon": icon, "icon_name": name,
            "headline": f"允许显存雷达自动读取 {name} 额度？",
            "lines": [f"使用 {app} 在本机的登录状态，只读查询额度",
                      "登录信息只在内存中使用，不保存、不上传",
                      "可随时在右键菜单「自动读取额度」中关闭"],
            "note": f"允许后约 {eta_seconds} 秒内显示在任务栏，之后每 5 分钟更新",
            "buttons": [("allow", "允许", True), ("cancel", "暂不", False)], "cancel": "cancel"}


def revoke_spec(name: str, icon=None, language: str = "zh-CN") -> dict:
    if language == "en":
        return {"kind": "dialog", "icon": icon, "icon_name": name,
                "headline": f"Stop reading {name} quota automatically?",
                "lines": [f"The taskbar stops querying {name} and shows local status only",
                          "You can turn it back on from the same menu anytime"],
                "note": "",
                "buttons": [("revoke", "Turn off", True), ("cancel", "Cancel", False)], "cancel": "cancel"}
    return {"kind": "dialog", "icon": icon, "icon_name": name,
            "headline": f"关闭 {name} 的自动读取？",
            "lines": [f"任务栏不再查询 {name} 额度，只显示本机状态", "之后可随时在同一菜单重新开启"],
            "note": "",
            "buttons": [("revoke", "关闭", True), ("cancel", "取消", False)], "cancel": "cancel"}


def notice_spec(text: str, name: str = "", icon=None, language: str = "zh-CN") -> dict:
    """A one-button notice from a short message: the first sentence becomes
    the headline, the rest the body ("未检测到 Kimi。请先…" -> two parts;
    English splits on ". ")."""
    import re
    text = str(text or "").strip()
    parts = [part.strip() for part in (re.split(r"(?<=\.)\s+", text) if language == "en" else text.split("。"))
             if part.strip()]
    head, lines = (parts[0], parts[1:]) if parts else ("", [])
    return {"kind": "dialog", "icon": icon, "icon_name": name or head,
            "headline": head, "lines": lines, "note": "",
            "buttons": [("ok", "OK" if language == "en" else "确定", True)], "cancel": "ok"}


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
    except (ImportError, OSError, ValueError, TypeError):
        return False


def taskbar_light() -> bool:
    """Taskbar/shell theme (SystemUsesLightTheme), which can differ from apps'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return int(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0]) == 1
    except (ImportError, OSError, ValueError, TypeError):
        return False


def system_accent():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value = int(winreg.QueryValueEx(key, "AccentColor")[0]) & 0xFFFFFFFF
            return (value & 255, (value >> 8) & 255, (value >> 16) & 255)  # ABGR
    except (ImportError, OSError, ValueError, TypeError):
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
ICON_CACHE_LIMIT = 128


def _remember(cache: dict, key, value, limit: int = ICON_CACHE_LIMIT):
    """Bounded icon cache: the oldest entry is dropped (never Disposed -- a
    dropped bitmap may still be shown by a menu item; .NET frees it later).
    Keys include DPI size and theme, so months of DPI/theme switches grew it."""
    while len(cache) >= limit:
        cache.pop(next(iter(cache)))
        if cache is _SOURCES:
            _BOXES.clear()  # keyed by id(source): ids of dropped sources get reused
    cache[key] = value
    return value
ICON_EXTENSIONS = (".ico", ".png")


def pick_msix_asset(names, base: str, px: int, light: bool = False) -> str | None:
    """Best file for an MSIX logo ``base`` (e.g. "Square44x44Logo.png") at
    px: the smallest ``targetsize-N`` >= px (unplated, matching the
    taskbar theme), else the largest, else ``scale-*``, else the base."""
    import re
    stem, ext = os.path.splitext(base)
    sized = []
    for name in names:
        m = re.fullmatch(re.escape(stem) + r"\.targetsize-(\d+)(_altform-(light)?unplated)?" + re.escape(ext), name, re.I)
        if m:
            unplated = bool(m.group(2))
            themed = unplated and (bool(m.group(3)) == bool(light))
            sized.append((int(m.group(1)), themed, unplated, name))
    if sized:
        best = max(t[1:3] for t in sized)
        pool = [t for t in sized if t[1:3] == best]
        bigger = [t for t in pool if t[0] >= px]
        return (min(bigger) if bigger else max(pool))[3]
    scaled = sorted((name for name in names if re.fullmatch(re.escape(stem) + r"\.scale-\d+" + re.escape(ext), name, re.I)),
                    key=lambda n: int(re.findall(r"scale-(\d+)", n)[0]))
    if scaled:
        return scaled[-1]
    return base if base in names else None


def msix_logo(folder: str, px: int, light: bool = False) -> str | None:
    """App logo file of an MSIX package folder (AppxManifest Square44x44Logo)."""
    import re
    try:
        with open(os.path.join(folder, "AppxManifest.xml"), encoding="utf-8", errors="replace") as handle:
            manifest = handle.read(400_000)
    except OSError:
        return None
    m = re.search(r'Square44x44Logo="([^"]+)"', manifest) or re.search(r"<Logo>([^<]+)</Logo>", manifest)
    if not m:
        return None
    rel = m.group(1).replace("/", os.sep).replace("\\", os.sep)
    directory = os.path.join(folder, os.path.dirname(rel))
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    chosen = pick_msix_asset(names, os.path.basename(rel), px, light)
    return os.path.join(directory, chosen) if chosen else None


def icon_candidates(path: str, px: int = 32, *, light: bool | None = None) -> list[str]:
    """Where an app's icon may live: an MSIX package's logo, the exe itself,
    then common resource files next to it (Electron/Tauri apps ship
    resources\\icon.ico/png)."""
    if not path:
        return []
    if os.path.isdir(path):
        # The largest logo (targetsize-256 has ~1 px margin); the small
        # hinted sizes put the art right on the square edge.
        theme = taskbar_light() if light is None else bool(light)
        logo = msix_logo(path, max(px, 256), light=theme)
        return [logo] if logo else []
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
    path = _rounded(1, 1, px - 3, px - 3, max(2, px // 5))  # 1 px clear like fitted icons
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


def glyph_icon(glyph: str, px: int, rgb=(26, 26, 26)):
    """Menu icon drawn from a symbol (e.g. "↻") at exactly px x px, cached;
    same face as the owner-drawn top-level menu icons."""
    key = ("glyph", glyph, int(px), tuple(rgb))
    if key in _ICONS:
        return _ICONS[key]
    from System.Drawing import (Bitmap, Color, Font, FontStyle, Graphics, GraphicsUnit, RectangleF, SolidBrush,
                                StringAlignment, StringFormat)
    from System.Drawing.Text import TextRenderingHint
    bitmap = Bitmap(px, px)
    graphics = Graphics.FromImage(bitmap)
    graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    font = Font("Segoe UI Symbol", max(6.0, px * 1.0), FontStyle.Bold, GraphicsUnit.Pixel)
    brush = SolidBrush(Color.FromArgb(*rgb))
    fmt = StringFormat()
    fmt.Alignment = fmt.LineAlignment = StringAlignment.Center
    try:
        graphics.Clear(Color.Transparent)
        graphics.DrawString(glyph, font, brush, RectangleF(0, 0, px, px), fmt)
    finally:
        for item in (font, brush, fmt, graphics):
            item.Dispose()
    return _remember(_ICONS, key, bitmap)


ICON_SOURCE_PX = 256
_SOURCES: dict = {}


def icon_source(path: str | None, *, light: bool | None = None):
    """Largest available frame of the app's icon (256 px exe/ico frame or
    the biggest MSIX logo PNG), cached; None when there is none."""
    folder = str(path or "")
    # MSIX logos come in light/dark variants: the theme is part of the key,
    # or a theme switch kept the old (low-contrast) logo until restart.
    theme = taskbar_light() if light is None else bool(light)
    key = folder + ("|light" if theme else "|dark") if folder and os.path.isdir(folder) else folder
    if key in _SOURCES:
        return _SOURCES[key]
    source = None
    for candidate in icon_candidates(folder, ICON_SOURCE_PX, light=theme):
        try:
            if not os.path.isfile(candidate):
                continue
            source = (_exe_icon(candidate, ICON_SOURCE_PX) if candidate.lower().endswith(".exe")
                      else _load_image(candidate))
            if source is not None:
                break
        except Exception as exc:
            LOG.info("app icon unavailable (%s)", type(exc).__name__)
    return _remember(_SOURCES, key, source)


def _load_image(path: str):
    from System.Drawing import Bitmap, Icon, Size
    if path.lower().endswith(".ico"):
        icon = Icon(path, Size(ICON_SOURCE_PX, ICON_SOURCE_PX))
        try:
            return icon.ToBitmap()
        finally:
            icon.Dispose()
    with_file = Bitmap(path)
    try:
        return Bitmap(with_file)          # detached copy: no file lock
    finally:
        with_file.Dispose()


ICON_FILL = 0.86
_BOXES: dict = {}


def art_box(source, threshold: int = 40):
    """(left, top, right, bottom) of the visible art (alpha > threshold), cached."""
    key = id(source)
    if key in _BOXES:
        return _BOXES[key]
    from System import Array, Byte
    from System.Drawing import Rectangle
    from System.Drawing.Imaging import ImageLockMode, PixelFormat
    from System.Runtime.InteropServices import Marshal
    w, h = source.Width, source.Height
    data = source.LockBits(Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
    try:
        arr = Array.CreateInstance(Byte, data.Stride * h)
        Marshal.Copy(data.Scan0, arr, 0, len(arr))
        stride = data.Stride
    finally:
        source.UnlockBits(data)
    buf = bytes(arr)
    rows = [y for y in range(h) if any(buf[y * stride + x * 4 + 3] > threshold for x in range(w))]
    if not rows:
        box = (0, 0, w, h)
    else:
        cols = [x for x in range(w) if any(buf[y * stride + x * 4 + 3] > threshold for y in range(rows[0], rows[-1] + 1))]
        box = (cols[0], rows[0], cols[-1] + 1, rows[-1] + 1)
    return _remember(_BOXES, key, box)


def icon_art_px(px: int) -> int:
    """Side of the art inside a px tile: ~86 %, always >= 1 px clear per side."""
    return max(1, min(px - 2, round(px * ICON_FILL)))


def fit_icon(source, px: int, pad: int = 1):
    """The visible art of ``source`` (cropped to its alpha box) scaled with
    high-quality area filtering, aspect kept, to ~86 % of a transparent
    px x px tile and centred -- many app logos (the Codex or ChatGPT knot) are
    drawn edge to edge, which looked cut off at strip sizes."""
    from System.Drawing import Bitmap, Graphics, GraphicsUnit, Rectangle
    from System.Drawing.Drawing2D import CompositingQuality, InterpolationMode, PixelOffsetMode, SmoothingMode, WrapMode
    from System.Drawing.Imaging import ImageAttributes, PixelFormat
    inner = icon_art_px(px) if pad else px
    bx = art_box(source)
    w, h = bx[2] - bx[0], bx[3] - bx[1]
    k = inner / max(w, h)
    dw, dh = max(1, round(w * k)), max(1, round(h * k))
    out = Bitmap(px, px, PixelFormat.Format32bppArgb)
    g = Graphics.FromImage(out)
    attrs = ImageAttributes()
    try:
        g.InterpolationMode = InterpolationMode.HighQualityBicubic
        g.PixelOffsetMode = PixelOffsetMode.HighQuality
        g.CompositingQuality = CompositingQuality.HighQuality
        g.SmoothingMode = SmoothingMode.HighQuality
        attrs.SetWrapMode(WrapMode.TileFlipXY)   # no fade/cut at the source edges
        g.DrawImage(source, Rectangle((px - dw) // 2, (px - dh) // 2, dw, dh), bx[0], bx[1], w, h, GraphicsUnit.Pixel, attrs)
    finally:
        attrs.Dispose()
        g.Dispose()
    return out


# Official artwork copied (unmodified) from the installed app into our assets,
# used instead of the app's own files when those are hard to read or vary:
# Codex desktop package (MSIX)  assets\Square44x44Logo.targetsize-256_altform-(light)unplated.png
# (flat knot, complete, but drawn edge to edge -> fit_icon adds the margin).
BUNDLED_ICONS = {"codex": ("openai-light.png", "openai-dark.png"), "chatgpt": ("openai-light.png", "openai-dark.png")}



def surface_is_light(surface_rgb) -> bool:
    """True when a card/strip fill is a light background (pick dark glyphs)."""
    return _lum(tuple(surface_rgb)[:3]) >= 140


def icon_contrast_ok(ink_rgb, surface_rgb, *, min_delta: float = 55.0) -> bool:
    """Whether average icon ink contrasts enough with the surface fill."""
    return abs(_lum(tuple(ink_rgb)[:3]) - _lum(tuple(surface_rgb)[:3])) >= min_delta


def bundled_icon_path(name: str, light: bool | None = None) -> str | None:
    files = BUNDLED_ICONS.get((name or "").strip().lower())
    if not files:
        return None
    light = taskbar_light() if light is None else light
    path = os.path.join(os.path.dirname(__file__), "assets", "provider-icons", files[0 if light else 1])
    return path if os.path.isfile(path) else None


def provider_icon(path: str | None, px: int, name: str = "", accent=None, *,
                  light: bool | None = None):
    """Bitmap of the installed app's own icon at ``px`` (cached), or a letter tile.

    ``light`` selects light/dark brand variants (bundled Codex art, MSIX logos).
    Default follows the taskbar theme (strip parity). Hover cards pass the card
    surface theme so a dark card gets the light glyph, not a black silhouette.
    """
    theme_light = taskbar_light() if light is None else bool(light)
    bundled = bundled_icon_path(name, light=theme_light)
    key = (str(bundled or path or ""), int(px), name, theme_light)
    if key in _ICONS:
        return _ICONS[key]
    bitmap = None
    try:
        source = _SOURCES.get(bundled) if bundled in _SOURCES else None
        if bundled and source is None:
            source = _remember(_SOURCES, bundled, _load_image(bundled))
        # Same order as the strip: bundled brand art when registered, else the
        # installed app icon. Fall back to the install path if bundled load fails.
        if source is None:
            source = icon_source(path, light=theme_light)
        if source is not None:
            bitmap = fit_icon(source, int(px))
    except Exception as exc:
        LOG.info("app icon unavailable (%s)", type(exc).__name__)
    if bitmap is None:
        try:
            bitmap = letter_tile(name, px, accent)
        except Exception:
            bitmap = None
    return _remember(_ICONS, key, bitmap)


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
                 icon_path: str | None = None, highlight=None) -> str:
    pal = palette(dark, accent)
    if spec.get("kind") == "hover":
        bitmap, _ = render_hover(spec, scale, pal, accent=accent, highlight=highlight)
    else:
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



# -- hover detail card --------------------------------------------------------

_HOVER = {"form": None, "image": None, "delay": None, "signature": None, "on_tick": None, "shown": False,
          "layout": None, "on_row_click": None, "on_leave": None, "clickable": None,
          "render": None, "feedback": None, "result_timer": None}

# Row highlight strength over the card surface: (dark card -> white, light card -> black).
ROW_HIGHLIGHT = {"hover": (0.12, 0.08), "pressed": (0.22, 0.15)}


def _feedback():
    feedback = _HOVER.get("feedback")
    if feedback is None:
        from .app_launch import RowFeedback
        feedback = _HOVER["feedback"] = RowFeedback()
    return feedback


def row_highlight_rgb(surface, level: str, primary=None) -> tuple:
    """Opaque colour of a hover-card row highlight (the card itself is opaque).

    hover ~12 % white on a dark card / ~8 % black on a light one; pressed is
    deeper; 'flash' (app opened) is a light accent tint; 'failed' is neutral grey.
    """
    surface = tuple(int(v) for v in tuple(surface)[:3])
    light = surface_is_light(surface)
    if level == "flash":
        return _mix(surface, tuple(primary or DEFAULT_ACCENT)[:3], 0.18 if light else 0.30)
    if level == "failed":
        return _mix(surface, (128, 128, 128), 0.30)
    dark_t, light_t = ROW_HIGHLIGHT.get(level, ROW_HIGHLIGHT["hover"])
    return _mix(surface, (0, 0, 0), light_t) if light else _mix(surface, (255, 255, 255), dark_t)


def row_highlight_rect(layout, provider_id, scale: float = 1.0):
    """(x, y, width, height) of the rounded highlight behind one card row, or None.

    Spans the full card width minus a small inset, the row content plus a few
    pixels, and never leaves the row's own click band (no overlap with neighbours).
    """
    if not layout or not provider_id:
        return None
    s = lambda v: int(round(v * scale))
    width = int(layout.get("width") or 0)
    for (pid, top, bottom), (_hid, band_top, band_bottom) in zip(layout.get("row_spans") or (),
                                                                  layout.get("row_hits") or ()):
        if pid != provider_id:
            continue
        inset = s(4)
        y0 = max(band_top + 1, top - s(5))
        y1 = min(band_bottom - 1, bottom + s(5))
        if width <= 2 * inset or y1 <= y0:
            return None
        return inset, y0, width - 2 * inset, y1 - y0
    return None


def hover_row_at(row_hits, y) -> str | None:
    """Provider id of the hover-card row band containing client ``y``."""
    for provider_id, top, bottom in row_hits or ():
        if top <= y < bottom:
            return provider_id or None
    return None


def set_hover_handlers(*, on_row_click=None, on_leave=None, clickable=None) -> None:
    """Card interaction callbacks (UI thread): row click -> provider id,
    pointer leaving the card, and ``clickable(provider_id) -> bool`` for the hand cursor."""
    _HOVER.update(on_row_click=on_row_click, on_leave=on_leave, clickable=clickable)


def hover_card_contains(point) -> bool:
    form = _HOVER.get("form")
    if form is None or not _HOVER.get("shown"):
        return False
    try:
        bounds = form.Bounds
        return bounds.Left <= point[0] < bounds.Right and bounds.Top <= point[1] < bounds.Bottom
    except Exception:
        return False


def render_hover(spec: dict, scale: float, pal: dict, *, accent=None, max_width: int | None = None,
                 highlight=None):
    """(Bitmap, layout) for a multi-app hover detail card.

    Width fits the widest row (title + sparkline, or any text line) plus the
    same padding on the right as on the left, clamped to ``hover_min_width`` ..
    ``hover_max_width`` and to ``max_width`` (work-area pixels) when given.
    Lines only wrap when the clamp is hit.  ``highlight`` = (provider id, level)
    draws a rounded row highlight behind that row's content (layout unchanged).
    """
    import math
    from System.Drawing import (Bitmap, Font, FontStyle, Graphics, GraphicsUnit, Pen, RectangleF, SizeF,
                                SolidBrush, StringFormat)
    from System.Drawing.Drawing2D import InterpolationMode, SmoothingMode
    from System.Drawing.Imaging import PixelFormat
    from System.Drawing.Text import TextRenderingHint

    s = lambda v: int(round(v * scale))
    t = TOKENS
    pad = s(t["hover_pad"])
    icon_px = s(t["hover_icon"])
    gap = s(t["hover_gap"])
    text_x = pad + icon_px + gap
    fonts = {
        "name": Font(FONT, float(s(t["hover_name_px"])), FontStyle.Bold, GraphicsUnit.Pixel),
        "body": Font(FONT, float(s(t["hover_body_px"])), FontStyle.Regular, GraphicsUnit.Pixel),
        "note": Font(FONT, float(s(t["hover_note_px"])), FontStyle.Regular, GraphicsUnit.Pixel),
    }
    fmt = StringFormat(StringFormat.GenericTypographic)
    probe_bitmap = Bitmap(1, 1)
    probe = Graphics.FromImage(probe_bitmap)
    probe.TextRenderingHint = TextRenderingHint.AntiAliasGridFit

    def measure(text, font, w):
        return int(math.ceil(probe.MeasureString(text or " ", font, SizeF(w, 10000), fmt).Height))

    def natural(text, font):
        # Single-line width with the same typographic format used to draw.
        return int(math.ceil(probe.MeasureString(text or " ", font, SizeF(100000, 10000), fmt).Width)) + 1

    muted = pal["secondary"]
    status = _mix(pal["secondary"], pal["surface"], 0.15)
    rows = [row for row in (spec.get("rows") or []) if row.get("name")]
    blocks = []
    y = pad
    icons = []
    sparks = []
    spark_w = s(t.get("hover_spark_w", 48))
    spark_h = s(t.get("hover_spark_h", 14))
    spark_gap = s(t.get("hover_spark_gap", 8))

    # Pass 1: widest content (title + spark, every text line).
    content_w = 0
    for row in rows:
        pts = row.get("spark") or []
        row_spark = isinstance(pts, (list, tuple)) and len(pts) >= 2
        title = natural(row["name"], fonts["name"]) + ((spark_gap + spark_w) if row_spark else 0)
        content_w = max(content_w, title)
        for line in row.get("lines") or []:
            text = (line.get("text") or "").strip()
            if not text:
                continue
            tone = line.get("tone") or "body"
            key = "note" if tone in {"note", "secondary", "status"} else "body"
            content_w = max(content_w, natural(text, fonts[key]))
    min_w = s(t.get("hover_min_width", 140))
    max_w = s(t.get("hover_max_width", 440))
    if max_width is not None and max_width > 0:
        max_w = min(max_w, int(max_width))
    max_w = max(max_w, text_x + 40 + pad)
    width = max(min_w, min(max_w, text_x + content_w + pad))
    text_w = max(40, width - text_x - pad)
    row_spans = []
    for index, row in enumerate(rows):
        if index:
            y += s(t["hover_row_gap"])
        spark_pts = row.get("spark") or []
        has_spark = isinstance(spark_pts, (list, tuple)) and len(spark_pts) >= 2
        # Spark sits just after the model name (small gap), not flush to the card edge.
        name_natural = int(math.ceil(probe.MeasureString(row["name"] or " ", fonts["name"]).Width))
        if has_spark:
            name_w = max(40, min(name_natural, text_w - spark_w - spark_gap))
            draw_name_w = name_w
        else:
            name_w = text_w
            draw_name_w = text_w
        name_h = measure(row["name"], fonts["name"], draw_name_w)
        row_top = y
        title_h = max(name_h, spark_h if has_spark else 0)
        icon_top = row_top + max(0, (title_h - icon_px) // 2)
        blocks.append(("name", row["name"], text_x, row_top + max(0, (title_h - name_h) // 2),
                       draw_name_w, name_h, pal["text"]))
        if has_spark:
            spark_x = text_x + name_w + spark_gap
            # Keep inside the card if the name was clipped to fit.
            spark_x = min(spark_x, width - pad - spark_w)
            spark_y = row_top + max(0, (title_h - spark_h) // 2)
            sparks.append((list(spark_pts), spark_x, spark_y, spark_w, spark_h))
        y = row_top + title_h
        for line in row.get("lines") or []:
            text = (line.get("text") or "").strip()
            if not text:
                continue
            tone = line.get("tone") or "body"
            colour = status if tone == "status" else muted if tone in {"note", "secondary"} else pal["text"]
            font_key = "note" if tone in {"note", "secondary", "status"} else "body"
            h = measure(text, fonts[font_key], text_w)
            y += s(t["hover_line_gap"])
            blocks.append((font_key, text, text_x, y, text_w, h, colour))
            y += h
        icons.append((row.get("icon_path"), row.get("icon_name") or row["name"], icon_top))
        y = max(y, icon_top + icon_px, row_top + title_h)
        row_spans.append((row.get("provider_id") or "", row_top, y))
    height = max(y + pad, pad * 2 + icon_px)
    # Clickable bands: each row owns half of the gaps around it (whole card covered).
    row_hits = []
    for index, (pid, top, bottom) in enumerate(row_spans):
        band_top = 0 if index == 0 else (row_spans[index - 1][2] + top) // 2
        band_bottom = height if index == len(row_spans) - 1 else (bottom + row_spans[index + 1][1]) // 2
        row_hits.append((pid, band_top, band_bottom))
    # Rightmost painted content (measured before fonts are disposed).
    content_right = text_x
    for _key, _text, bx, _by, bw, _bh, _c in blocks:
        content_right = max(content_right, bx + min(bw, natural(_text, fonts[_key])))
    for _pts, sx, _sy, sw, _sh in sparks:
        content_right = max(content_right, sx + sw)
    layout = {"width": width, "height": height, "buttons": [], "content_h": height,
              "pad": pad, "content_right": int(content_right),
              "content_w": int(content_w), "text_x": text_x, "row_hits": row_hits, "row_spans": row_spans}
    bitmap = Bitmap(width, height, PixelFormat.Format24bppRgb)
    g = Graphics.FromImage(bitmap)
    disposables = list(fonts.values()) + [fmt, probe, probe_bitmap]
    g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    g.InterpolationMode = InterpolationMode.HighQualityBicubic
    g.SmoothingMode = SmoothingMode.AntiAlias
    try:
        g.Clear(_color(pal["surface"]))
        if highlight:
            rect = row_highlight_rect(layout, highlight[0], scale)
            if rect is not None:
                from System.Drawing.Drawing2D import GraphicsPath
                x0, y0, w0, h0 = rect
                diameter = max(2, min(s(12), h0, w0))
                band = GraphicsPath()
                try:
                    for ax, ay, angle in ((x0, y0, 180), (x0 + w0 - diameter, y0, 270),
                                          (x0 + w0 - diameter, y0 + h0 - diameter, 0),
                                          (x0, y0 + h0 - diameter, 90)):
                        band.AddArc(ax, ay, diameter, diameter, angle, 90)
                    band.CloseFigure()
                    tint = SolidBrush(_color(row_highlight_rgb(pal["surface"], highlight[1], pal.get("primary"))))
                    disposables.append(tint)
                    g.FillPath(tint, band)
                finally:
                    band.Dispose()
        # Match brand glyph to THIS card's fill (not the taskbar theme).
        theme_light = surface_is_light(pal["surface"])
        for path, name, top in icons:
            icon = provider_icon(path, icon_px, name, accent, light=theme_light)
            if icon is not None:
                g.DrawImage(icon, pad, top, icon_px, icon_px)
        for font_key, text, x, by, w, h, colour in blocks:
            brush = SolidBrush(_color(colour)); disposables.append(brush)
            g.DrawString(text, fonts[font_key], brush, RectangleF(x, by, w + 1, h + s(2)), fmt)
        # Compact 7-day sparklines on the name row (theme-aware; omit if <2 pts).
        if sparks:
            from .usage_trend import sparkline_path
            from .quota_colors import quota_color as quota_rule_color
            from System.Drawing import PointF
            from System.Drawing.Drawing2D import GraphicsPath
            for pts, sx, sy, sw, sh in sparks:
                coords = sparkline_path([(float(a), float(b)) for a, b in pts], sw, sh, pad=1)
                if len(coords) < 2:
                    continue
                last_v = float(pts[-1][1])
                rem = max(0.0, min(100.0, 100.0 - last_v)) if last_v <= 100.0 else 50.0
                ink = quota_rule_color(rem, known=True, bright=not theme_light, surface=pal["surface"])
                points = [PointF(sx + x, sy + y) for x, y in coords]
                # Soft fill under the line, then a 1.5 DIP stroke.
                path = GraphicsPath()
                try:
                    path.AddLines(points)
                    path.AddLine(points[-1], PointF(points[-1].X, sy + sh - 1))
                    path.AddLine(PointF(points[-1].X, sy + sh - 1), PointF(points[0].X, sy + sh - 1))
                    path.CloseFigure()
                    fill_rgb = _mix(ink, pal["surface"], 0.72)
                    fill = SolidBrush(_color(fill_rgb)); disposables.append(fill)
                    g.FillPath(fill, path)
                finally:
                    path.Dispose()
                pen = Pen(_color(ink), max(1.0, 1.5 * scale)); disposables.append(pen)
                g.DrawLines(pen, points)
        border = Pen(_color(pal["border"]), 1); disposables.append(border)
        g.DrawRectangle(border, 0, 0, width - 1, height - 1)
    finally:
        for item in disposables:
            try:
                item.Dispose()
            except Exception:
                pass
        g.Dispose()
    return bitmap, layout


def hide_hover_card() -> None:
    """Hide and keep the single hover window for reuse."""
    import ctypes
    form = _HOVER.get("form")
    _HOVER["signature"] = None
    _HOVER["shown"] = False
    _cancel_hover_delay()
    _cancel_result_timer()
    _feedback().reset()
    try:
        if form is not None:
            handle = int(form.Handle.ToInt64())
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_HIDEWINDOW
            user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(0), 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0010 | 0x0080)
            if form.Visible:
                form.Hide()
    except Exception:
        pass


def close_hover_card() -> None:
    """Full teardown used when the strip shuts down."""
    hide_hover_card()
    form, image, delay = _HOVER.get("form"), _HOVER.get("image"), _HOVER.get("delay")
    _HOVER.update(form=None, image=None, delay=None, signature=None, on_tick=None, shown=False,
                  layout=None, on_row_click=None, on_leave=None, clickable=None, render=None)
    try:
        if delay is not None:
            delay.Dispose()
        if form is not None:
            form.Close(); form.Dispose()
        if image is not None:
            image.Dispose()
    except Exception:
        pass


def _present_hover(spec, anchor, scale, dark, accent):
    import ctypes
    from System.Drawing import Point, Size
    from System.Windows.Forms import (AutoScaleMode, Form, FormBorderStyle, FormStartPosition,
                                      ImageLayout, Screen)

    if not spec or not spec.get("rows"):
        hide_hover_card()
        return None
    scale_v = scale or system_scale()
    dark_v = system_dark() if dark is None else dark
    accent_v = accent or system_accent()
    pal = palette(dark_v, accent_v)
    try:
        if anchor:
            work = Screen.FromPoint(Point(int(anchor[0]), int(anchor[1]))).WorkingArea
        else:
            work = Screen.PrimaryScreen.WorkingArea
        edge = int(round(TOKENS["hover_offset"] * float(scale_v)))
        max_width = max(1, int(work.Width) - 2 * edge)
    except Exception:
        max_width = None
    signature = (spec.get("language"), max_width,
                 tuple((row.get("name"),
                        tuple((line.get("text"), line.get("tone")) for line in row.get("lines") or []),
                        row.get("icon_path"),
                        tuple((round(float(a), 1), round(float(b), 2))
                              for a, b in (row.get("spark") or [])[:48]))
                       for row in spec.get("rows") or []),
                 round(float(scale_v), 3), bool(dark_v))
    form = _HOVER.get("form")
    if form is None:
        form = Form()
        form.AutoScaleMode = getattr(AutoScaleMode, "None")
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.BackgroundImageLayout = getattr(ImageLayout, "None")
        _ = form.Handle
        handle = int(form.Handle.ToInt64())
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
        set_style = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
        get_style.restype = ctypes.c_ssize_t
        set_style.restype = ctypes.c_ssize_t
        set_style(handle, -20, get_style(handle, -20) | 0x08000000 | 0x00000080 | 0x00000008)
        _attach_hover_mouse(form)
        _HOVER["form"] = form
    handle = int(form.Handle.ToInt64())
    if signature != _HOVER.get("signature"):
        _HOVER["render"] = (spec, scale_v, pal, accent_v, max_width)
        _swap_hover_image(form)
        _HOVER["signature"] = signature
        _dwm_style(handle, pal, dark_v)
    width, height = form.ClientSize.Width, form.ClientSize.Height
    gap = int(round(TOKENS["hover_offset"] * float(scale_v)))
    if anchor:
        area = Screen.FromPoint(Point(int(anchor[0]), int(anchor[1]))).WorkingArea
        from .hover_detail import card_position
        x, y = card_position(
            (int(anchor[0]), int(anchor[1]), int(anchor[2]), int(anchor[3])),
            (width, height),
            (area.Left, area.Top, area.Right, area.Bottom), gap)
    else:
        area = Screen.PrimaryScreen.WorkingArea
        x, y = area.Right - width - gap, area.Bottom - height - gap
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    # HWND_TOPMOST, SWP_NOACTIVATE | SWP_SHOWWINDOW
    user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1), int(x), int(y), int(width), int(height),
                        0x0010 | 0x0040)
    _HOVER["shown"] = True
    return form


def _swap_hover_image(form) -> bool:
    """Render the card (with the current row highlight) into ``form``."""
    from System.Drawing import Size
    render = _HOVER.get("render")
    if not render or form is None:
        return False
    spec, scale_v, pal, accent_v, max_width = render
    bitmap, layout = render_hover(spec, scale_v, pal, accent=accent_v, max_width=max_width,
                                  highlight=_feedback().highlight())
    old = _HOVER.get("image")
    form.BackgroundImage = bitmap
    if form.ClientSize.Width != layout["width"] or form.ClientSize.Height != layout["height"]:
        form.ClientSize = Size(layout["width"], layout["height"])
    _HOVER["image"] = bitmap
    _HOVER["layout"] = layout
    if old is not None and old is not bitmap:
        try:
            old.Dispose()
        except Exception:
            pass
    form.Invalidate()
    return True


def _rerender_hover() -> None:
    try:
        if _HOVER.get("shown"):
            _swap_hover_image(_HOVER.get("form"))
    except Exception:
        logging.getLogger("vram_radar").info("hover highlight render failed")


def _cancel_result_timer() -> None:
    timer = _HOVER.get("result_timer")
    _HOVER["result_timer"] = None
    _HOVER["result_token"] = None
    if timer is not None:
        try:
            timer.Stop()
            timer.Dispose()
        except Exception:
            pass


def _one_shot(delay_ms: int, callback):
    """WinForms one-shot timer on the UI thread; returns the timer."""
    from System.Windows.Forms import Timer
    timer = Timer()
    timer.Interval = max(1, int(delay_ms))

    def tick(*_):
        timer.Stop()
        callback()
    timer.Tick += tick
    timer.Start()
    return timer


def hover_row_result(provider_id: str, outcome: str, *, then=None, flash_ms: int | None = None,
                     schedule=None) -> str | None:
    """UI thread: after a row click, show the confirmation flash (app opened or
    focused) or a brief neutral grey (failed), then call ``then`` (usually hides
    the card).  No flash for 'debounced' / 'none'.  ``schedule(ms, fn)`` is the
    one-shot timer (injectable for tests)."""
    from .app_launch import FEEDBACK_FLASH_MS
    _cancel_result_timer()
    level = _feedback().finish(provider_id, outcome)
    if level:
        _rerender_hover()
    token = object()
    _HOVER["result_token"] = token

    def done():
        if _HOVER.get("result_token") is not token:
            return                      # superseded by a newer result
        _cancel_result_timer()
        _feedback().result = None
        if callable(then):
            try:
                then()
            except Exception:
                pass
    delay = int(flash_ms if flash_ms is not None else FEEDBACK_FLASH_MS) if level else 1
    _HOVER["result_timer"] = (schedule or _one_shot)(delay, done)
    return level


def _attach_hover_mouse(form) -> None:
    """Rows of detected apps: highlight under the pointer, deeper while pressed,
    hand cursor; a click (down and up on the same row) calls ``on_row_click``.
    The card is WS_EX_NOACTIVATE: clicks arrive without activating it, and the
    card is hidden only after the click was resolved (never before the hit test)."""
    from System.Windows.Forms import Cursor, Cursors, MouseButtons
    from .app_launch import row_cursor

    def row_at_y(y):
        layout = _HOVER.get("layout") or {}
        return hover_row_at(layout.get("row_hits"), int(y))

    def clickable(pid):
        check = _HOVER.get("clickable")
        return bool(pid) and row_cursor(pid, check) == "hand"

    def apply_cursor(pid):
        want = Cursors.Hand if clickable(pid) else Cursors.Default
        if form.Cursor != want:
            form.Cursor = want      # WinForms answers WM_SETCURSOR (HTCLIENT) with this

    def pointer_row():
        point = form.PointToClient(Cursor.Position)
        return row_at_y(point.Y)

    def on_move(_sender, event):
        try:
            pid = row_at_y(event.Y)
            apply_cursor(pid)
            if _feedback().move(pid, clickable(pid)):
                _rerender_hover()
        except Exception:
            pass

    def on_enter(*_):
        try:
            pid = pointer_row()
            apply_cursor(pid)
            if _feedback().move(pid, clickable(pid)):
                _rerender_hover()
        except Exception:
            pass

    def on_down(_sender, event):
        try:
            if event.Button != MouseButtons.Left:
                return
            pid = row_at_y(event.Y)
            if _feedback().down(pid, clickable(pid)):
                _rerender_hover()
        except Exception:
            pass

    def on_up(_sender, event):
        try:
            if event.Button != MouseButtons.Left:
                return
            feedback = _feedback()
            before = feedback.highlight()
            fire = feedback.up(row_at_y(event.Y))
            callback = _HOVER.get("on_row_click")
            if fire and callable(callback) and clickable(fire):
                callback(fire)      # opens the app; the result handler flashes, then hides
            elif feedback.highlight() != before:
                _rerender_hover()
        except Exception:
            pass

    def on_leave(*_):
        try:
            if _feedback().leave():
                _rerender_hover()
            callback = _HOVER.get("on_leave")
            if callable(callback):
                callback()
        except Exception:
            pass

    form.MouseMove += on_move
    form.MouseEnter += on_enter
    form.MouseDown += on_down
    form.MouseUp += on_up
    form.MouseLeave += on_leave


def _cancel_hover_delay() -> None:
    """Stop and drop the pending hover-delay timer (avoids Tick handler build-up)."""
    delay = _HOVER.get("delay")
    on_tick = _HOVER.get("on_tick")
    _HOVER["on_tick"] = None
    _HOVER["delay"] = None
    if delay is None:
        return
    try:
        if on_tick is not None:
            delay.Tick -= on_tick
        delay.Stop()
        delay.Dispose()
    except Exception:
        pass


def show_hover_card(spec: dict, anchor=None, *, scale: float | None = None,
                    dark: bool | None = None, accent=None, delay_ms: int = 0):
    """Schedule or show the hover card.  UI thread only; never activates."""
    from System.Windows.Forms import Timer

    _cancel_hover_delay()
    if not delay_ms:
        return _present_hover(spec, anchor, scale, dark, accent)
    held = {"spec": spec, "anchor": anchor, "scale": scale, "dark": dark, "accent": accent}
    delay = Timer()
    _HOVER["delay"] = delay
    delay.Interval = max(1, int(delay_ms))

    def on_tick_handler(*_):
        _cancel_hover_delay()
        _present_hover(held["spec"], held["anchor"], held["scale"], held["dark"], held["accent"])

    _HOVER["on_tick"] = on_tick_handler
    delay.Tick += on_tick_handler
    delay.Start()
    return None
