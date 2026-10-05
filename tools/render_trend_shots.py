"""Offscreen trend2 hover + real context-menu screenshots (no mouse input)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

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


def series_with_reset(base_used: float, rise: float, reset_to: float, n: int = 28):
    """~7 days of 6h samples with a mid-window reset drop."""
    pts = []
    t0 = NOW - 6.8 * 86400
    used = base_used
    for i in range(n):
        t = t0 + i * 6 * 3600
        if i == n // 2:
            used = reset_to
        else:
            used = min(95.0, used + rise + (0.4 if i % 3 == 0 else -0.15))
        pts.append((t, max(2.0, used)))
    return pts


def series_balance():
    """Declining balance with one top-up (excluded from burn)."""
    t0 = NOW - 6.5 * 86400
    vals = [22.0, 20.5, 19.0, 17.8, 24.0, 22.5, 21.0, 19.5, 18.0, 16.5, 15.2, 14.0, 12.8]
    return [(t0 + i * 12 * 3600, v) for i, v in enumerate(vals)]


def seed_store(path: Path):
    from vram_radar.usage_trend import TrendStore
    store = TrendStore(path)
    for key, pts, kind in (
        ("codex:w300", series_with_reset(18, 2.2, 8, 30), "used"),
        ("grok:quota", series_with_reset(35, 2.8, 12, 28), "used"),
        ("kimi:quota", series_with_reset(48, -1.1, 70, 26), "used"),  # recovering after reset-like jump
        ("deepseek:balance", series_balance(), "balance"),
    ):
        for t, v in pts:
            store.record([(key, v, kind)], now=t, interval=1)
    return store


def render_hovers(out: Path, store, paths, prefix: str = "trend2_hover") -> list[str]:
    import clr
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from vram_radar import ui_dialogs as ui
    from vram_radar.hover_detail import build_hover_rows, hover_card_spec
    from vram_radar.providers import PROVIDERS

    icon_names = {spec.id: spec.name for spec in PROVIDERS}
    names_zh = {spec.id: spec.label(False) for spec in PROVIDERS}
    names_en = {spec.id: spec.label(True) for spec in PROVIDERS}
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
    selected = ["grok", "kimi", "codex", "deepseek"]
    made = []
    for label, language, dark, names in (
        (f"{prefix}_zh_light", "zh-CN", False, names_zh),
        (f"{prefix}_zh_dark", "zh-CN", True, names_zh),
        (f"{prefix}_en_light", "en", False, names_en),
    ):
        rows = build_hover_rows(
            codex, providers, selected, language=language, now=NOW,
            names=names, icon_paths=paths, icon_names=icon_names, trend_store=store)
        # Sparks must be multi-point (not collinear-looking single segment).
        for row in rows:
            assert len(row.get("spark") or []) >= 8, row.get("name")
        spec = hover_card_spec(rows, language)
        target = str(out / f"{label}.png")
        made.append(ui.save_preview(spec, target, scale=1.5, dark=dark))
    return made


def render_real_menu(out_path: Path) -> str:
    """Capture the live ContextMenuStrip via DrawToBitmap (same path as validate_usage_surface)."""
    import logging
    import webview
    from System import Action
    from System.Drawing import Bitmap, Point, Rectangle
    from System.Drawing.Imaging import ImageFormat
    from benchmark_webview_ui import FakeApi, _wait_until_ready
    from vram_radar.usage_surface import CodexUsageSurface

    logging.disable(logging.CRITICAL)
    window = webview.create_window(
        "VRAM Radar trend menu shot", width=800, height=600,
        url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
        js_api=FakeApi(), hidden=True, focus=False)
    language = {"value": "zh-CN"}
    display = {
        "codex_time_format": "decimal",
        "usage_background": "transparent",
        "usage_labels": "text",
        "usage_sort_urgency": True,
        "profile_id": "default",
    }
    providers = {
        "enabled": True,
        "selected": ["codex", "grok", "kimi", "deepseek"],
        "session_consent": ("grok", "kimi"),
        "providers": {
            "codex": {"installed": True, "running": True},
            "grok": {"installed": True, "running": True, "signed_in": True, "quota_percent": 25},
            "kimi": {"installed": True, "running": True, "signed_in": True, "quota_percent": 40},
            "deepseek": {"installed": True, "running": True, "signed_in": True,
                          "balance": {"CNY": "12.50"}},
        },
    }
    state = {
        "enabled": True, "state": "ready",
        "windows": [
            {"name": "5 hour", "window_minutes": 300, "remaining_percent": 55,
             "resets_at": time.time() + 8200},
        ],
    }
    surface = CodexUsageSurface(
        window, lambda: dict(state), language=lambda: language["value"],
        open_settings=lambda: None, open_home=lambda: None, refresh=lambda: None,
        display_options=lambda: dict(display),
        save_display=lambda k, v: display.__setitem__(k, v) or {"ok": True},
        providers=lambda: dict(providers),
        disable=lambda: None, quit_application=lambda: None)
    # Do not steal foreground.
    from vram_radar.usage_surface import _dll
    _dll("user32").SetForegroundWindow = lambda hwnd: 1
    done = {"path": None, "error": None}
    timeout = threading.Timer(60, window.destroy)

    def run():
        try:
            _wait_until_ready(window, time.monotonic() + 25)
            surface.start()

            def invoke(cb):
                window.native.Invoke(Action(cb))

            def tick_and_capture():
                from System.Drawing import Graphics
                from System.Windows.Forms import Application as WinFormsApp
                surface._tick()
                if surface._sort_urgency_item is not None:
                    surface._sort_urgency_item.Checked = True
                    surface._sort_urgency_item.Text = "按紧迫程度排序"
                surface._menu.Show(Point(160, 100))
                surface._display_menu.ShowDropDown()
                WinFormsApp.DoEvents()
                time.sleep(0.2)
                WinFormsApp.DoEvents()
                parent = surface._menu.Bounds
                child = surface._display_menu.DropDown.Bounds
                left = min(parent.Left, child.Left)
                top = min(parent.Top, child.Top)
                right = max(parent.Right, child.Right)
                bottom = max(parent.Bottom, child.Bottom)
                width = max(1, right - left)
                height = max(1, bottom - top)
                bmp = Bitmap(width, height)
                g = Graphics.FromImage(bmp)
                try:
                    # Fill with menu surface colour so gaps are not black.
                    g.Clear(surface._menu.BackColor)
                    main = Bitmap(max(1, surface._menu.Width), max(1, surface._menu.Height))
                    sub = Bitmap(max(1, surface._display_menu.DropDown.Width),
                                 max(1, surface._display_menu.DropDown.Height))
                    try:
                        surface._menu.DrawToBitmap(main, Rectangle(0, 0, main.Width, main.Height))
                        surface._display_menu.DropDown.DrawToBitmap(
                            sub, Rectangle(0, 0, sub.Width, sub.Height))
                        g.DrawImage(main, parent.Left - left, parent.Top - top)
                        g.DrawImage(sub, child.Left - left, child.Top - top)
                    finally:
                        main.Dispose()
                        sub.Dispose()
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    bmp.Save(str(out_path), ImageFormat.Png)
                    done["path"] = str(out_path)
                finally:
                    g.Dispose()
                    bmp.Dispose()
                    surface._menu.Close()

            invoke(tick_and_capture)
        except Exception as exc:
            done["error"] = repr(exc)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    timeout.start()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    webview.start()
    timeout.cancel()
    thread.join(10)
    if done["error"]:
        raise RuntimeError(done["error"])
    if not done["path"]:
        raise RuntimeError("menu screenshot failed")
    return done["path"]


def main(out: str) -> list[str]:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = install_paths()
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        store = seed_store(Path(tmp) / "default.json")
        made.extend(render_hovers(out_dir, store, paths))
    made.append(render_real_menu(out_dir / "trend2_menu_zh.png"))
    return made


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "."
    files = main(dest)
    print(len(files), "files")
    for f in files:
        print(f)
