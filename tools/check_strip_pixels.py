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
from vram_radar.usage_surface import (STRIP_DENSITY, compact_value, place_columns, set_name_cell, strip_icon_px,
                                      FIT_PLAN)
from vram_radar.ui_dialogs import letter_tile, provider_icon

DISPLAY_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
THEMES = {"light": ((238, 228, 214), (90, 90, 90), (26, 26, 26), (160, 62, 96)),
          "dark": ((32, 32, 32), (180, 180, 180), (245, 245, 245), (227, 143, 163))}
CELLS = [("Codex", "0% 47.9h"), ("Grok", "100%"), ("DeepSeek", "¥6"), ("Kimi", "168.0h")]
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
    for display in DISPLAY_SCALES:
        strip_scale = max(0.6, display * STRIP_DENSITY)
        for theme, (bg, name_fg, value_fg, low_fg) in THEMES.items():
            for mode in ("text", "icons"):
                for factor, level in (FIT_PLAN[0], FIT_PLAN[-1]):
                    form = Form(); form.FormBorderStyle = getattr(FormBorderStyle, "None")
                    form.BackColor = Color.FromArgb(*bg)
                    scale = lambda v: round(v * strip_scale)
                    name_font = Font("Segoe UI", max(1, round(scale(12) * factor)), FontStyle.Regular, GraphicsUnit.Pixel)
                    value_font = Font("Segoe UI", max(1, round(scale(13) * factor)), FontStyle.Bold, GraphicsUnit.Pixel)
                    used = []
                    for idx, (name, value) in enumerate(CELLS):
                        pair = []
                        for font, text, fg in ((name_font, name, name_fg), (value_font, compact_value(value, level),
                                                                           low_fg if idx == 0 else value_fg)):
                            lab = Label(); lab.AutoSize = False; lab.Font = font
                            lab.TextAlign = ContentAlignment.MiddleLeft; lab.BackColor = Color.Transparent
                            lab.ForeColor = Color.FromArgb(*fg); lab.Text = text
                            form.Controls.Add(lab); pair.append(lab)
                        used.append(tuple(pair))
                    px = strip_icon_px(strip_scale, factor)
                    for name_label, _ in used:
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
                    key = f"{display:.2f}/{theme}/{mode}/f{factor}"
                    need = math.ceil(6 * display * factor) if mode == "icons" else 1
                    cells = []
                    rects = []
                    for (name_label, value_label), (name, _) in zip(used, CELLS):
                        nr = (name_label.Left, name_label.Top, name_label.Right, name_label.Bottom)
                        vr = (value_label.Left, value_label.Top, value_label.Right, value_label.Bottom)
                        rects += [nr, vr]
                        ni = ink_columns(buf, stride, bg, nr, bmp.Height)
                        vi = ink_columns(buf, stride, bg, vr, bmp.Height)
                        gap = (min(vi) - max(ni) - 1) if ni and vi else None
                        clipped = (value_label.GetPreferredSize(Size(0, 0)).Width > value_label.Width
                                   or (not name_label.Image and name_label.GetPreferredSize(Size(0, 0)).Width > name_label.Width)
                                   or (name_label.Image is not None and name_label.Image.Width > name_label.Width)
                                   or (name_label.Image is not None and name_label.Image.Height > name_label.Height))
                        edge_ink = bool(vi) and max(vi) >= vr[2] - 1
                        cells.append({"cell": name, "gap": gap, "ink": [min(ni) if ni else None, max(ni) if ni else None,
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
                    col_gap = used[2][0].Left - max(used[0][1].Right, used[1][1].Right)
                    if col_gap < 1:
                        failures.append(f"{key}: columns touch ({col_gap})")
                    results.append({"case": key, "width": width, "column_gap": col_gap, "cells": cells})
                    if save and factor == 1.0:
                        bmp.Save(os.path.join(save, f"strip_{display:.2f}_{theme}_{mode}.png".replace(".", "_", 1)))
                    bmp.Dispose(); form.Dispose()
    return {"ok": not failures, "cases": len(results), "failures": failures[:20], "results": results}


if __name__ == "__main__":
    save = sys.argv[sys.argv.index("--save") + 1] if "--save" in sys.argv else None
    out = run(save)
    full = "--full" in sys.argv
    print(json.dumps(out if full else {k: v for k, v in out.items() if k != "results"}, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)