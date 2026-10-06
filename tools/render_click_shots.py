"""Offscreen hover-card click feedback screenshots (no mouse input, no windows shown).

Renders the hover card in normal / hover / pressed / opened (flash) states for
light and dark themes, Chinese and English, into one folder.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from render_trend_shots import NOW, install_paths, seed_store  # noqa: E402

STATES = (("normal", None), ("hover", "hover"), ("pressed", "pressed"), ("opened", "flash"))


def card_spec(language: str, store, paths):
    from vram_radar.hover_detail import build_hover_rows, hover_card_spec
    from vram_radar.providers import PROVIDERS
    english = language == "en"
    names = {spec.id: spec.label(english) for spec in PROVIDERS}
    icon_names = {spec.id: spec.name for spec in PROVIDERS}
    codex = {"enabled": True, "state": "ready", "fetched_at": NOW - 180, "install_path": paths.get("codex"),
             "windows": [{"name": "5 hour", "remaining_percent": 55, "window_minutes": 300,
                          "resets_at": NOW + 2.3 * 3600},
                         {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080,
                          "resets_at": NOW + 88 * 3600}]}
    providers = {
        "kimi": {"installed": True, "running": True, "signed_in": True, "install_path": paths.get("kimi"),
                 "quota": {"zh": "40%", "en": "40%"}, "quota_percent": 40,
                 "brief": {"zh": "已用 60%", "en": "60% used"},
                 "reset_at": NOW + 20 * 3600, "fetched_at": NOW - 120},
        "grok": {"installed": True, "running": True, "signed_in": True, "install_path": paths.get("grok"),
                 "quota": {"zh": "25%", "en": "25%"}, "quota_percent": 25,
                 "brief": {"zh": "已用 75%", "en": "75% used"},
                 "reset_at": NOW + 30 * 3600, "fetched_at": NOW - 90},
        "deepseek": {"installed": True, "running": True, "signed_in": True, "install_path": paths.get("deepseek"),
                     "balance": {"CNY": "12.50"}, "quota": {"zh": "¥12.50", "en": "¥12.50"},
                     "headline": {"zh": "¥12.50", "en": "¥12.50"}, "fetched_at": NOW - 300},
    }
    rows = build_hover_rows(codex, providers, ["codex", "kimi", "grok", "deepseek"], language=language,
                            now=NOW, names=names, icon_paths=paths, icon_names=icon_names, trend_store=store)
    return hover_card_spec(rows, language)


def main(out: str, target_row: str = "kimi") -> list[str]:
    import clr
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from vram_radar import ui_dialogs as ui

    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    paths = install_paths()
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        store = seed_store(Path(tmp) / "trend.json")
        for language, tag in (("zh-CN", "zh"), ("en", "en")):
            spec = card_spec(language, store, paths)
            for dark in (False, True):
                theme = "dark" if dark else "light"
                for state, level in STATES:
                    target = folder / f"card_{state}_{tag}_{theme}.png"
                    ui.save_preview(spec, str(target), scale=1.5, dark=dark,
                                    highlight=(target_row, level) if level else None)
                    made.append(str(target))
    return made


if __name__ == "__main__":
    for path in main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "click_shots")):
        print(path)
