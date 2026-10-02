"""Offscreen pixel check of the multi-model strip layout.

Renders the real ``place_columns`` / ``set_name_cell`` layout with WinForms
labels at several strip scales, in 文字 and 图标 modes, on light and dark
taskbar colours, then measures drawn (non-background) pixels per cell:

* icon right edge -> first value glyph >= 6 px at 100 % display scale
* no overlap between label rects, between columns, or with strip edges
* nothing clipped (preferred text width fits; no ink in the last column)

Usage: python tools/check_strip_pixels.py [--save DIR]   -> JSON, exit 1 on failure
"""
import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import clr
clr.AddReference("System.Drawing"); clr.AddReference("System.Windows.Forms")
from System.Drawing import Bitmap, Color, ContentAlignment, Font, FontStyle, GraphicsUnit, Rectangle, Size
from System.Drawing.Imaging import ImageLockMode, PixelFormat
from System.Windows.Forms import Application, Form, FormBorderStyle, FormStartPosition, Label
from System.Runtime.InteropServices import Marshal
from vram_radar.usage_surface import (STRIP_DENSITY, compact_value, place_columns, set_name_cell, strip_icon_px, strip_reset_text,
                                      FIT_PLAN)
from vram_radar.ui_dialogs import art_box, bundled_icon_path, icon_art_px, icon_source, letter_tile, provider_icon, _load_image


def alpha_bbox(bmp, threshold=40):
    """(left, top, right, bottom) of pixels with alpha > threshold (exclusive right/bottom)."""
    buf, stride = pixels(bmp)
    xs, ys = [], []
    for y in range(bmp.Height):
        for x in range(bmp.Width):
            if buf[y * stride + x * 4 + 3] > threshold:
                xs.append(x); ys.append(y)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None


def ink_box(buf, stride, bg, rect, threshold=40):
    l, t, r, b = rect
    xs, ys = [], []
    for y in range(t, b):
        for x in range(l, r):
            i = y * stride + x * 4
            if abs(buf[i + 2] - bg[0]) + abs(buf[i + 1] - bg[1]) + abs(buf[i] - bg[2]) > threshold:
                xs.append(x); ys.append(y)
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None

DISPLAY_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
THEMES = {"light": ((238, 228, 214), (90, 90, 90), (26, 26, 26), (160, 62, 96)),
          "dark": ((32, 32, 32), (180, 180, 180), (245, 245, 245), (227, 143, 163))}
# (name, quota, reset): both items, quota only (DeepSeek), widest resets.
CELLS = [("Codex", "0%", "6d 12h"), ("Grok", "100%", "6d 23h"), ("DeepSeek", "¥6", ""), ("Kimi", "80%", "23h")]
def install_paths():
    """Real install paths from the providers' own detection (icons as shipped)."""
    import importlib
    from vram_radar.providers.base import Environment
    env, out = Environment(), {}
    for name in ("Codex", "Grok", "DeepSeek", "Kimi"):
        try:
            out[name] = getattr(importlib.import_module("vram_radar.providers"), name.lower()).probe(env).get("install_path")
        except Exception:
            out[name] = None
    return out


def pixels(bmp):
    rect = Rectangle(0, 0, bmp.Width, bmp.Height)
    data = bmp.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
    try:
        buf = bytearray(data.Stride * bmp.Height)
        arr = __import__("System").Array.CreateInstance(__import__("System").Byte, len(buf))
        Marshal.Copy(data.Scan0, arr, 0, len(buf))
        return bytes(arr), data.Stride
    finally:
        bmp.UnlockBits(data)


def ink_columns(buf, stride, bg, rect, height, threshold=40):
    l, t, r, b = rect
    cols = []
    for x in range(max(0, l), r):
        for y in range(max(0, t), min(height, b)):
            i = y * stride + x * 4
            if abs(buf[i + 2] - bg[0]) + abs(buf[i + 1] - bg[1]) + abs(buf[i] - bg[2]) > threshold:
                cols.append(x)
                break
    return cols


def run(save=None):
    results, failures = [], []
    paths = install_paths()
    # (display scale, strip scale): normal taskbars, plus a small-buttons
    # taskbar at 100 % (32 px tall -> strip scale 0.65).
    for display, strip_scale in [(d, max(0.6, d * STRIP_DENSITY)) for d in DISPLAY_SCALES] + [(1.0, 0.65)]:
        for theme, (bg, name_fg, value_fg, low_fg) in THEMES.items():
            for mode in ("text", "icons"):
                for factor, level in (FIT_PLAN[0], FIT_PLAN[-1]):
                    form = Form(); form.FormBorderStyle = getattr(FormBorderStyle, "None")
                    form.BackColor = Color.FromArgb(*bg)
                    scale = lambda v: round(v * strip_scale)
                    name_font = Font("Segoe UI", max(1, round(scale(12) * factor)), FontStyle.Regular, GraphicsUnit.Pixel)
                    value_font = Font("Segoe UI", max(1, round(scale(13) * factor)), FontStyle.Bold, GraphicsUnit.Pixel)
                    used = []
                    for idx, (name, value, reset) in enumerate(CELLS):
                        pair = []
                        quota_fg = low_fg if idx == 0 else value_fg
                        dim_fg = quota_fg   # quota and reset share one colour
                        for font, text, fg in ((name_font, name, name_fg), (value_font, compact_value(value, level), quota_fg),
                                               (value_font, strip_reset_text(reset) if level < 1 else "", dim_fg)):
                            lab = Label(); lab.AutoSize = False; lab.Font = font
                            lab.TextAlign = ContentAlignment.MiddleLeft; lab.BackColor = Color.Transparent
                            lab.ForeColor = Color.FromArgb(*fg); lab.Text = text; lab.Visible = bool(text)
                            form.Controls.Add(lab); pair.append(lab)
                        used.append(tuple(pair))
                    px = strip_icon_px(strip_scale, factor)
                    for name_label, *_ in used:
                        name = name_label.Text
                        image = None
                        if mode == "icons":
                            path = paths.get(name)
                            image = provider_icon(path, px, name) if path else letter_tile(name, px)
                        set_name_cell(name_label, image, name, strip_scale, factor)
                    width = place_columns(used, strip_scale, factor)
                    height = scale(40)
                    form.ClientSize = Size(width, height)
                    # Realise the window (off-screen, no taskbar button) so DrawToBitmap paints the labels.
                    form.ShowInTaskbar = False; form.StartPosition = FormStartPosition.Manual
                    form.Location = __import__("System.Drawing", fromlist=["Point"]).Point(-20000, -20000)
                    form.Show(); Application.DoEvents()
                    bmp = Bitmap(form.Width, form.Height)
                    form.DrawToBitmap(bmp, Rectangle(0, 0, form.Width, form.Height))
                    # DrawToBitmap draws the client area at the client origin for a borderless form.
                    buf, stride = pixels(bmp)
                    key = f"{display:.2f}{'s' if strip_scale < 0.8 else ''}/{theme}/{mode}/f{factor}"
                    need = max(3, math.ceil(4 * display * factor)) if mode == "icons" else 3
                    cells = []
                    rects = []
                    for (name_label, value_label, reset_label), (name, *_) in zip(used, CELLS):
                        nr = (name_label.Left, name_label.Top, name_label.Right, name_label.Bottom)
                        vr = (value_label.Left, value_label.Top, value_label.Right, value_label.Bottom)
                        rects += [nr, vr if not reset_label.Text else None]   # value/reset share padding (ink gap checked)
                        ni = ink_columns(buf, stride, bg, nr, bmp.Height)
                        vi = ink_columns(buf, stride, bg, vr, bmp.Height)
                        gap = (min(vi) - max(ni) - 1) if ni and vi else None
                        reset_gap = None
                        if reset_label.Text:
                            rr = (reset_label.Left, reset_label.Top, reset_label.Right, reset_label.Bottom)
                            rects.append(rr)
                            rects[-2] = (vr[0], vr[1], min(vr[2], rr[0]), vr[3])
                            ri = ink_columns(buf, stride, bg, rr, bmp.Height)
                            reset_gap = (min(ri) - max(vi) - 1) if ri and vi else None
                            # Quota and reset stay two readable items (>= a thin space apart).
                            if reset_gap is None or reset_gap < max(3, math.ceil(2 * display * factor)):
                                failures.append(f"{key} {name}: quota/reset gap {reset_gap}")
                            if reset_label.GetPreferredSize(Size(0, 0)).Width > reset_label.Width or (ri and max(ri) >= rr[2] - 1):
                                failures.append(f"{key} {name}: reset clipped")
                        clipped = (value_label.GetPreferredSize(Size(0, 0)).Width > value_label.Width
                                   or (not name_label.Image and name_label.GetPreferredSize(Size(0, 0)).Width > name_label.Width)
                                   or (name_label.Image is not None and name_label.Image.Width > name_label.Width)
                                   or (name_label.Image is not None and name_label.Image.Height > name_label.Height))
                        edge_ink = bool(vi) and max(vi) >= vr[2] - 1
                        if mode == "icons" and name_label.Image is not None:
                            # The tile itself: no opaque pixel on its border rows/columns.
                            ib = alpha_bbox(name_label.Image)
                            iw = name_label.Image.Width
                            if ib and (ib[0] == 0 or ib[1] == 0 or ib[2] == iw or ib[3] == name_label.Image.Height):
                                failures.append(f"{key} {name}: icon art touches its tile border {ib} in {iw}px")
                        if mode == "icons" and name_label.Image is not None and (paths.get(name) or bundled_icon_path(name)):
                            # Icon art vs a reference downscale of the 256 px source: the
                            # drawn extent must match (nothing cut at the square) and keep
                            # >= 1 px clear of the cell edges.
                            bundled = bundled_icon_path(name, light=(theme == "light"))
                            source = _load_image(bundled) if bundled else icon_source(paths.get(name))
                            sb = art_box(source) if source is not None else None
                            box = ink_box(buf, stride, bg, nr)
                            if sb and box:
                                k = icon_art_px(px) / max(sb[2] - sb[0], sb[3] - sb[1])
                                ew, eh = (sb[2] - sb[0]) * k, (sb[3] - sb[1]) * k
                                aw, ah = box[2] - box[0], box[3] - box[1]
                                if abs(aw - ew) > 2 or abs(ah - eh) > 2:
                                    failures.append(f"{key} {name}: icon extent {aw}x{ah} vs reference {ew:.1f}x{eh:.1f}")
                                if box[0] < nr[0] + 1 or box[1] < nr[1] + 1 or box[3] > nr[3] - 1:
                                    failures.append(f"{key} {name}: icon touches cell edge {box} in {nr}")
                                cells_icon = {"extent": [aw, ah], "reference": [round(ew, 1), round(eh, 1)]}
                            else:
                                failures.append(f"{key} {name}: icon not drawn")
                                cells_icon = None
                        else:
                            cells_icon = None
                        cells.append({"cell": name, "gap": gap, "reset_gap": reset_gap, "icon": cells_icon, "ink": [min(ni) if ni else None, max(ni) if ni else None,
                                                                        min(vi) if vi else None, max(vi) if vi else None]})
                        if gap is None or gap < need:
                            failures.append(f"{key} {name}: gap {gap} < {need}")
                        if clipped or edge_ink:
                            failures.append(f"{key} {name}: clipped")
                    for i, a in enumerate(rects):
                        if a[0] < 0 or a[1] < 0 or a[2] > width or a[3] > height:
                            failures.append(f"{key}: rect {a} outside strip {width}x{height}")
                        for b in rects[i + 1:]:
                            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                                failures.append(f"{key}: overlap {a} {b}")
                    col_gap = used[2][0].Left - max(c.Right for c in (*used[0][1:], *used[1][1:]) if c.Visible)
                    left_ink = [x for c in (*used[0][1:], *used[1][1:]) if c.Visible and c.Text
                                for x in ink_columns(buf, stride, bg, (c.Left, c.Top, c.Right, c.Bottom), bmp.Height)]
                    right_ink = [x for c in (used[2][0], used[3][0])
                                 for x in ink_columns(buf, stride, bg, (c.Left, c.Top, c.Right, c.Bottom), bmp.Height)]
                    col_ink_gap = (min(right_ink) - max(left_ink) - 1) if left_ink and right_ink else None
                    if col_gap < 1 or col_ink_gap is None or col_ink_gap < max(3, math.ceil(6 * display * factor)):
                        failures.append(f"{key}: columns too close ({col_gap}/{col_ink_gap})")
                    results.append({"case": key, "width": width, "column_gap": col_gap, "column_ink_gap": col_ink_gap, "cells": cells})
                    if save and factor == 1.0:
                        bmp.Save(os.path.join(save, "strip_" + key.split("/f")[0].replace("/", "_").replace(".", "_") + ".png"))
                    bmp.Dispose(); form.Dispose()
    return {"ok": not failures, "cases": len(results), "failures": failures[:20], "results": results}


if __name__ == "__main__":
    save = sys.argv[sys.argv.index("--save") + 1] if "--save" in sys.argv else None
    out = run(save)
    full = "--full" in sys.argv
    print(json.dumps(out if full else {k: v for k, v in out.items() if k != "results"}, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)