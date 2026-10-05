"""Render hover detail card screenshots offscreen (no mouse input).
Usage: python tools/render_hover_card.py OUTDIR
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

NOW = 1_700_000_000


def main(out: str) -> list[str]:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
    import clr  # noqa: F401
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from vram_radar import ui_dialogs as ui
    from vram_radar.hover_detail import build_hover_rows, hover_card_spec

    os.makedirs(out, exist_ok=True)
    codex = {
        "enabled": True, "state": "ready", "fetched_at": NOW - 180,
        "windows": [
            {"name": "5 hour", "remaining_percent": 85, "window_minutes": 300, "resets_at": NOW + 2.3 * 3600},
            {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080, "resets_at": NOW + 88 * 3600},
        ],
    }
    providers = {
        "deepseek": {
            "installed": True, "running": True, "signed_in": True,
            "quota": {"zh": "¥6.00", "en": "¥6.00"}, "headline": {"zh": "¥6.00", "en": "¥6.00"},
            "fetched_at": NOW - 300,
        },
        "grok": {
            "installed": True, "running": True, "signed_in": True,
            "quota": {"zh": "25%", "en": "25%"}, "quota_percent": 25,
            "brief": {"zh": "已用 75%", "en": "75% used"},
            "reset_at": NOW + 30 * 3600, "fetched_at": NOW - 90,
        },
        "claude": {"installed": True, "running": False, "signed_in": True},
    }
    names = {"deepseek": "DeepSeek", "grok": "Grok", "claude": "Claude"}
    made = []
    cases = [
        ("zh_light", "zh-CN", False, ["codex", "deepseek", "grok", "claude"]),
        ("zh_dark", "zh-CN", True, ["codex", "deepseek", "grok", "claude"]),
        ("en_light", "en", False, ["codex", "deepseek", "grok", "claude"]),
        ("en_dark", "en", True, ["codex", "deepseek", "grok", "claude"]),
        ("zh_all_inactive", "zh-CN", False, ["claude"]),
        ("en_all_inactive", "en", False, ["claude"]),
    ]
    for label, language, dark, selected in cases:
        rows = build_hover_rows(
            codex if "codex" in selected else None, providers, selected,
            language=language, now=NOW, names=names)
        spec = hover_card_spec(rows, language)
        target = os.path.join(out, f"hover_{label}.png")
        made.append(ui.save_preview(spec, target, scale=1.5, dark=dark))
    return made


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    paths = main(out)
    print(len(paths), "files")
