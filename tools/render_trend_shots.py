"""Offscreen screenshots: hover sparklines + urgency menu toggle.

Never sends mouse/keyboard input. Uses real installed app icons when present.

Usage: python tools/render_trend_shots.py OUTDIR
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

NOW = 1_700_000_000.0


def install_paths() -> dict[str, str | None]:
    paths: dict[str, str | None] = {}
    try:
        from vram_radar.providers import PROVIDERS
        from vram_radar.providers.base import Environment
        env = Environment()
        for spec in PROVIDERS:
            try:
                state = spec.probe(env)
            except Exception:
                state = None
            path = state.get("install_path") if isinstance(state, dict) else None
            paths[spec.id] = path if isinstance(path, str) and path else None
    except Exception:
        pass
    return paths


def seed_store(path: Path):
    from vram_radar.usage_trend import TrendStore
    store = TrendStore(path)
    base = NOW - 6.5 * 86400
    for i in range(14):
        t = base + i * 12 * 3600
        store.record([("codex:w300", 15.0 + i * 2.5, "used")], now=t, interval=1)
        store.record([("grok:quota", 40.0 + i * 3.0, "used")], now=t, interval=1)
        store.record([("kimi:quota", 55.0 - i * 1.5, "used")], now=t, interval=1)
        store.record([("deepseek:balance", 20.0 - i * 0.6, "balance")], now=t, interval=1)
    return store


def render_menu(out_path: str, *, english: bool, dark: bool, checked: bool, scale: float = 1.5):
    """Owner-draw-ish menu strip bitmap with the urgency toggle highlighted."""
    from System.Drawing import (Bitmap, Font, FontStyle, Graphics, GraphicsUnit, Pen,
                                Rectangle, SolidBrush, StringFormat)
    from System.Drawing.Drawing2D import SmoothingMode
    from System.Drawing.Imaging import PixelFormat
    from System.Drawing.Text import TextRenderingHint
    from vram_radar import ui_dialogs as ui

    pal = ui.palette(dark, (0, 120, 212))
    s = lambda v: int(round(v * scale))
    width, row_h, pad = s(220), s(28), s(8)
    rows = [
        ("Refresh usage", "刷新额度", False),
        ("Hide usage strip", "隐藏额度条", False),
        ("Move freely", "切换为自由移动", False),
        ("Display options", "显示设置", False),
        ("Models", "显示模型", False),
        ("Sort by urgency", "按紧迫程度排序", True),
        ("Quit VRAM Radar", "退出 VRAM Radar", False),
    ]
    height = pad * 2 + row_h * len(rows) + s(6)
    bmp = Bitmap(width, height, PixelFormat.Format24bppRgb)
    g = Graphics.FromImage(bmp)
    g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
    g.SmoothingMode = SmoothingMode.AntiAlias
    g.Clear(ui._color(pal["surface"]))
    font = Font("Segoe UI", float(s(12)), FontStyle.Regular, GraphicsUnit.Pixel)
    fmt = StringFormat(StringFormat.GenericTypographic)
    try:
        y = pad
        for en, zh, is_toggle in rows:
            text = en if english else zh
            if is_toggle and checked:
                brush = SolidBrush(ui._color(pal.get("primary") or (0, 120, 212)))
                g.FillRectangle(brush, Rectangle(s(4), y, width - s(8), row_h - s(2)))
                brush.Dispose()
                ink = SolidBrush(ui._color((255, 255, 255)))
                mark = "✓  " + text
            else:
                ink = SolidBrush(ui._color(pal["text"]))
                mark = ("✓  " if is_toggle and checked else ("    " if is_toggle else "")) + text
                if is_toggle and not checked:
                    mark = "    " + text
            g.DrawString(mark if is_toggle else text, font, ink, float(pad), float(y + s(5)), fmt)
            ink.Dispose()
            y += row_h
        border = Pen(ui._color(pal["border"]), 1)
        g.DrawRectangle(border, 0, 0, width - 1, height - 1)
        border.Dispose()
        bmp.Save(out_path)
    finally:
        font.Dispose()
        fmt.Dispose()
        g.Dispose()
        bmp.Dispose()
    return out_path


def main(out: str) -> list[str]:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
    import clr
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from vram_radar import ui_dialogs as ui
    from vram_radar.hover_detail import build_hover_rows, hover_card_spec
    from vram_radar.providers import PROVIDERS

    os.makedirs(out, exist_ok=True)
    paths = install_paths()
    icon_names = {spec.id: spec.name for spec in PROVIDERS}
    names_zh = {spec.id: spec.label(False) for spec in PROVIDERS}
    names_en = {spec.id: spec.label(True) for spec in PROVIDERS}
    with tempfile.TemporaryDirectory() as tmp:
        store = seed_store(Path(tmp) / "default.json")
        codex = {
            "enabled": True, "state": "ready", "fetched_at": NOW - 180,
            "install_path": paths.get("codex"),
            "windows": [
                {"name": "5 hour", "remaining_percent": 55, "window_minutes": 300,
                 "resets_at": NOW + 2.3 * 3600},
                {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080,
                 "resets_at": NOW + 88 * 3600},
            ],
        }
        providers = {
            "deepseek": {
                "installed": True, "running": True, "signed_in": True,
                "install_path": paths.get("deepseek"),
                "balance": {"CNY": "12.50"},
                "quota": {"zh": "¥12.50", "en": "¥12.50"},
                "headline": {"zh": "¥12.50", "en": "¥12.50"},
                "fetched_at": NOW - 300,
            },
            "grok": {
                "installed": True, "running": True, "signed_in": True,
                "install_path": paths.get("grok"),
                "quota": {"zh": "25%", "en": "25%"}, "quota_percent": 25,
                "brief": {"zh": "已用 75%", "en": "75% used"},
                "reset_at": NOW + 30 * 3600, "fetched_at": NOW - 90,
            },
            "kimi": {
                "installed": True, "running": True, "signed_in": True,
                "install_path": paths.get("kimi"),
                "quota": {"zh": "40%", "en": "40%"}, "quota_percent": 40,
                "brief": {"zh": "已用 60%", "en": "60% used"},
                "reset_at": NOW + 20 * 3600, "fetched_at": NOW - 120,
            },
        }
        # Urgency order: grok(25) < kimi(40) < codex(55) < deepseek(balance last-ish)
        selected = ["grok", "kimi", "codex", "deepseek"]
        made = []
        cases = [
            ("trend_hover_zh_light", "zh-CN", False, names_zh),
            ("trend_hover_zh_dark", "zh-CN", True, names_zh),
            ("trend_hover_en_light", "en", False, names_en),
        ]
        for label, language, dark, names in cases:
            rows = build_hover_rows(
                codex, providers, selected, language=language, now=NOW,
                names=names, icon_paths=paths, icon_names=icon_names,
                trend_store=store)
            spec = hover_card_spec(rows, language)
            target = os.path.join(out, f"{label}.png")
            made.append(ui.save_preview(spec, target, scale=1.5, dark=dark))
        made.append(render_menu(os.path.join(out, "trend_menu_zh_urgency_on.png"),
                                english=False, dark=False, checked=True))
        made.append(render_menu(os.path.join(out, "trend_menu_en_urgency_on.png"),
                                english=True, dark=False, checked=True))
        return made


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    paths = main(out)
    print(len(paths), "files")
    for p in paths:
        print(p)
