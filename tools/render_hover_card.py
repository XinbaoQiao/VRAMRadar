"""Render hover detail card screenshots offscreen (no mouse input).

Uses the same provider_icon pipeline as the taskbar strip. On the user's PC,
probes real install paths so DeepSeek/Grok/Kimi/etc. show their app icons.

Usage: python tools/render_hover_card.py OUTDIR [--prefix hover2]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

NOW = 1_700_000_000


def install_paths() -> dict[str, str | None]:
    """Probe providers for install_path (read-only; no UI, no live app changes)."""
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


def main(out: str, prefix: str = "hover") -> list[str]:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
    import clr  # noqa: F401
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

    codex = {
        "enabled": True, "state": "ready", "fetched_at": NOW - 180,
        "install_path": paths.get("codex"),
        "windows": [
            {"name": "5 hour", "remaining_percent": 85, "window_minutes": 300,
             "resets_at": NOW + 2.3 * 3600},
            {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080,
             "resets_at": NOW + 88 * 3600},
        ],
    }
    providers = {
        "deepseek": {
            "installed": True, "running": True, "signed_in": True,
            "install_path": paths.get("deepseek"),
            "quota": {"zh": "¥6.00", "en": "¥6.00"},
            "headline": {"zh": "¥6.00", "en": "¥6.00"},
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
        "claude": {"installed": bool(paths.get("claude")), "running": False,
                   "install_path": paths.get("claude")},
    }
    selected = ["codex", "grok", "kimi", "deepseek"]
    made = []
    cases = [
        (f"{prefix}_zh_light", "zh-CN", False, names_zh),
        (f"{prefix}_zh_dark", "zh-CN", True, names_zh),
        (f"{prefix}_en_light", "en", False, names_en),
    ]
    for label, language, dark, names in cases:
        rows = build_hover_rows(
            codex, providers, selected, language=language, now=NOW,
            names=names, icon_paths=paths, icon_names=icon_names)
        # Sanity: every row with an install path or Codex must not rely on a
        # missing icon_name (provider_icon still decides letter vs real).
        for row in rows:
            assert row.get("icon_name"), row
        spec = hover_card_spec(rows, language)
        target = os.path.join(out, f"{label}.png")
        made.append(ui.save_preview(spec, target, scale=1.5, dark=dark))
    return made


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    prefix = "hover"
    if "--prefix" in sys.argv:
        i = sys.argv.index("--prefix")
        if i + 1 < len(sys.argv):
            prefix = sys.argv[i + 1]
    paths = main(out, prefix=prefix)
    print(len(paths), "files")
