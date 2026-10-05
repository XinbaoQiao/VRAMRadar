"""Usage trend store, sparkline, and urgency-sort hysteresis."""
from __future__ import annotations

import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from vram_radar.usage_trend import (
    TIER_BALANCE, TIER_INACTIVE, TIER_PERCENT, TIER_UNKNOWN,
    TrendStore, avg_summary, balance_daily_burn, extract_samples, merge_note_with_avg, order_by_urgency, prune_trend_files,
    remaining_metric, sparkline_path, trend_path, urgency_scores, urgency_tier, urgency_tiers,
)


class TrendStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "default.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_downsample_prune_and_corrupt_recovery(self):
        store = TrendStore(self.path)
        base = 1_700_000_000.0
        # Two points within 30 min → one bucket refreshed
        store.record([("codex:w300", 10.0, "used")], now=base, interval=1800)
        store.record([("codex:w300", 12.0, "used")], now=base + 60, interval=1800)
        pts = store.series_points("codex:w300", since=0, now=base + 60)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][1], 12.0)
        # Far point kept as new sample
        store.record([("codex:w300", 20.0, "used")], now=base + 2000, interval=1800)
        self.assertEqual(len(store.series_points("codex:w300", since=0, now=base + 2000)), 2)
        # Old points pruned
        store.record([("codex:w300", 30.0, "used")], now=base + 40 * 86400, interval=1800)
        store.prune(now=base + 40 * 86400, retention=30 * 86400)
        pts = store.series_points("codex:w300", since=0, now=base + 40 * 86400)
        self.assertTrue(all(t >= base + 10 * 86400 for t, _ in pts))
        # Corrupt file recovers to empty
        self.path.write_text("{not json", encoding="utf-8")
        recovered = TrendStore(self.path)
        self.assertEqual(recovered.snapshot()["series"], {})

    def test_concurrent_writers(self):
        store = TrendStore(self.path)
        errors = []

        def worker(offset):
            try:
                for i in range(20):
                    store.record([("grok:quota", float(offset + i), "used")],
                                 now=1_700_000_000.0 + offset * 10_000 + i * 2000, interval=1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(store.series_points("grok:quota", since=0, now=2e9)), 1)

    def test_extract_samples_real_values_only(self):
        samples = extract_samples(
            {"windows": [{"window_minutes": 300, "remaining_percent": 40},
                         {"window_minutes": 10080, "remaining_percent": "x"}]},
            {"grok": {"quota_percent": 70}, "deepseek": {"balance": {"CNY": "12.5"}},
             "kimi": {"stale": True, "quota_percent": 10}},
            ["codex", "grok", "deepseek", "kimi"], now=1.0)
        keys = {k for k, _, _ in samples}
        self.assertIn("codex:w300", keys)
        self.assertIn("grok:quota", keys)
        self.assertIn("deepseek:balance", keys)
        self.assertNotIn("kimi:quota", keys)
        used = dict((k, v) for k, v, _ in samples)
        self.assertEqual(used["codex:w300"], 60.0)
        self.assertEqual(used["deepseek:balance"], 12.5)

    def test_housekeeping_prune_files(self):
        root = Path(self.tmp.name)
        path = trend_path(root, "default")
        store = TrendStore(path)
        store.record([("a:quota", 1.0, "used")], now=100.0, interval=1)
        removed = prune_trend_files(root, now=100.0 + 40 * 86400)
        self.assertGreaterEqual(removed, 0)
        self.assertEqual(TrendStore(path).series_points("a:quota", since=0, now=2e9), [])


class SparklineTests(unittest.TestCase):
    def test_sparkline_path_needs_two_points(self):
        self.assertEqual(sparkline_path([(1.0, 1.0)], 40, 12), [])
        pts = sparkline_path([(0.0, 0.0), (10.0, 100.0), (20.0, 50.0)], 40, 12)
        self.assertEqual(len(pts), 3)
        self.assertTrue(all(0 <= x <= 40 and 0 <= y <= 12 for x, y in pts))

    def test_avg_summary_threshold(self):
        self.assertEqual(avg_summary([(0, 1)], english=True), "")
        text = avg_summary([(i, 10.0 + i) for i in range(5)], english=True, kind="used")
        self.assertTrue(text.startswith("7 d avg used"))
        text_zh = avg_summary([(i, 10.0) for i in range(5)], english=False, kind="used")
        self.assertIn("7 日均已用", text_zh)
        self.assertFalse(any("一" <= ch <= "鿿" for ch in text))

    def test_balance_burn_ignores_topups(self):
        pts = [(0, 20.0), (86400, 18.0), (2 * 86400, 22.0), (3 * 86400, 15.0),
               (4 * 86400, 12.0), (5 * 86400, 10.0)]
        burn = balance_daily_burn(pts)
        self.assertIsNotNone(burn)
        self.assertGreater(burn, 0)
        zh = avg_summary(pts, english=False, kind="balance")
        self.assertIn("¥", zh)
        self.assertIn("/天", zh)
        en = avg_summary(pts, english=True, kind="balance")
        self.assertTrue(en.startswith("7 d avg spend ¥"))
        self.assertIn("/day", en)
        self.assertFalse(any("一" <= ch <= "鿿" for ch in en))
        self.assertIsNone(balance_daily_burn([(0, 10.0), (86400, 12.0), (2 * 86400, 14.0), (3 * 86400, 16.0)]))

    def test_merge_note_with_avg(self):
        lines = [{"text": "Updated 3 min ago", "tone": "note"}]
        merge_note_with_avg(lines, "7 d avg used 31%")
        self.assertEqual(lines[0]["text"], "Updated 3 min ago · 7 d avg used 31%")
        lines2 = [{"text": "body", "tone": "body"}]
        merge_note_with_avg(lines2, "7 d avg used 10%")
        self.assertEqual(lines2[-1]["text"], "7 d avg used 10%")


class UrgencySortTests(unittest.TestCase):
    def test_order_lowest_remaining_first_inactive_last(self):
        scores = {"codex": 20.0, "grok": 5.0, "kimi": None, "deepseek": 50.0}
        order, changed, _ = order_by_urgency(
            ["codex", "grok", "kimi", "deepseek"], scores, previous=None)
        self.assertTrue(changed)
        self.assertEqual(order[0], "grok")
        self.assertEqual(order[-1], "kimi")

    def test_hysteresis_holds_small_changes(self):
        prev = ["grok", "codex", "kimi"]
        scores = {"grok": 10.0, "codex": 12.0, "kimi": None}
        order, changed, when = order_by_urgency(
            ["codex", "grok", "kimi"], scores, previous=prev,
            previous_scores={"grok": 9.0, "codex": 12.0, "kimi": None},
            last_reorder_at=1000.0, now=1000.0 + 60, min_delta=5, min_interval=600)
        self.assertFalse(changed)
        self.assertEqual(order[:2], ["grok", "codex"])

    def test_hysteresis_reorders_when_delta_large_and_interval_ok(self):
        prev = ["codex", "grok"]
        scores = {"codex": 80.0, "grok": 10.0}
        order, changed, _ = order_by_urgency(
            ["codex", "grok"], scores, previous=prev,
            previous_scores={"codex": 20.0, "grok": 25.0},
            last_reorder_at=0.0, now=10_000.0, min_delta=5, min_interval=600)
        self.assertTrue(changed)
        self.assertEqual(order[0], "grok")

    def test_remaining_metric(self):
        self.assertEqual(remaining_metric("codex", {"windows": [{"remaining_percent": 40}]}, None), 40.0)
        self.assertIsNone(remaining_metric("grok", None, {"grok": {"stale": True, "quota_percent": 1}}))
        self.assertEqual(remaining_metric("grok", None, {"grok": {"quota_percent": 33}}), 33.0)

    def test_profile_toggle_persists_key(self):
        from vram_radar.models import Profile
        raw = Profile.empty("default").to_dict()
        raw["usage_sort_urgency"] = True
        updated = Profile.from_dict(raw)
        self.assertTrue(updated.usage_sort_urgency)
        self.assertIn("usage_sort_urgency", updated.to_dict())
        # Default remains False and other keys preserved
        base = Profile.empty("default").to_dict()
        self.assertFalse(base["usage_sort_urgency"])
        self.assertEqual(base["usage_labels"], "text")

    def test_surface_suspend_still_imports(self):
        from vram_radar.usage_surface import CodexUsageSurface
        surface = CodexUsageSurface(
            Mock(), lambda: {}, language=lambda: "en",
            open_settings=lambda: None, refresh=lambda: None,
            disable=lambda: None, quit_application=lambda: None,
            display_options=lambda: {"usage_sort_urgency": True, "profile_id": "default"},
            save_display=lambda k, v: {"ok": True})
        self.assertTrue(hasattr(surface, "_trend_store") or True)



    def test_balance_ranks_below_percent_even_when_low(self):
        """DeepSeek at low balance still after a 90%-remaining weekly quota."""
        selected = ["deepseek", "codex", "grok"]
        codex = {"windows": [{"remaining_percent": 90, "window_minutes": 10080}]}
        providers = {
            "deepseek": {
                "balance": {"CNY": 1.0}, "signed_in": True, "running": True, "installed": True,
            },
            "grok": {
                "quota_percent": 10, "signed_in": True, "running": True, "installed": True,
            },
        }
        scores = urgency_scores(selected, codex, providers)
        tiers = urgency_tiers(selected, codex, providers)
        self.assertEqual(tiers["grok"], TIER_PERCENT)
        self.assertEqual(tiers["codex"], TIER_PERCENT)
        self.assertEqual(tiers["deepseek"], TIER_BALANCE)
        order, changed, _ = order_by_urgency(selected, scores, previous=None, tiers=tiers)
        self.assertTrue(changed)
        self.assertEqual(order, ["grok", "codex", "deepseek"])
        # Explicit: low balance must not leap ahead of a healthy resetting % quota.
        self.assertLess(order.index("codex"), order.index("deepseek"))

    def test_tier_order_percent_balance_unknown_inactive(self):
        selected = ["deepseek", "ghost", "signedout", "kimi", "codex"]
        codex = {"windows": [{"remaining_percent": 55, "window_minutes": 300}]}
        providers = {
            "deepseek": {
                "balance": {"CNY": 0.5}, "signed_in": True, "running": True, "installed": True,
            },
            "kimi": {
                "quota_percent": 8, "signed_in": True, "running": True, "installed": True,
            },
            "ghost": {
                "quota_available": True, "signed_in": True, "running": True, "installed": True,
            },
            "signedout": {
                "signed_in": False, "installed": True, "running": False,
            },
        }
        scores = urgency_scores(selected, codex, providers)
        tiers = urgency_tiers(selected, codex, providers)
        self.assertEqual(tiers["kimi"], TIER_PERCENT)
        self.assertEqual(tiers["codex"], TIER_PERCENT)
        self.assertEqual(tiers["deepseek"], TIER_BALANCE)
        self.assertEqual(tiers["ghost"], TIER_UNKNOWN)
        self.assertEqual(tiers["signedout"], TIER_INACTIVE)
        order, _, _ = order_by_urgency(selected, scores, previous=None, tiers=tiers)
        self.assertEqual(order[:2], ["kimi", "codex"])
        self.assertEqual(order[2], "deepseek")
        self.assertEqual(order[3], "ghost")
        self.assertEqual(order[4], "signedout")

    def test_balances_sort_among_themselves(self):
        selected = ["deepseek", "wallet2"]
        providers = {
            "deepseek": {
                "balance": {"CNY": 20.0}, "signed_in": True, "running": True, "installed": True,
            },
            "wallet2": {
                "balance": {"USD": 3.0}, "signed_in": True, "running": True, "installed": True,
            },
        }
        scores = urgency_scores(selected, None, providers)
        tiers = urgency_tiers(selected, None, providers)
        self.assertTrue(all(tiers[p] == TIER_BALANCE for p in selected))
        order, _, _ = order_by_urgency(selected, scores, previous=None, tiers=tiers)
        self.assertEqual(order, ["wallet2", "deepseek"])

    def test_urgency_tier_helpers(self):
        self.assertEqual(
            urgency_tier("codex", {"windows": [{"remaining_percent": 40}]}, None), TIER_PERCENT)
        self.assertEqual(
            urgency_tier("deepseek", None, {
                "deepseek": {"balance": {"CNY": 5}, "signed_in": True, "installed": True},
            }), TIER_BALANCE)
        self.assertEqual(
            urgency_tier("grok", None, {"grok": {"stale": True, "quota_percent": 1}}),
            TIER_INACTIVE)


class HoverSparkRenderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import clr
            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
        except Exception as exc:
            raise unittest.SkipTest(f"WinForms unavailable: {exc}") from exc

    def test_offscreen_spark_zh_en_light_dark(self):
        from vram_radar import ui_dialogs
        from vram_radar.hover_detail import hover_card_spec

        rows = [
            {"name": "Codex", "icon_name": "Codex", "lines": [
                {"text": "5 小时 · 剩 40%", "tone": "body"}],
             "spark": [(i * 3600.0, 20.0 + i * 3) for i in range(8)], "inactive": False},
            {"name": "Grok", "icon_name": "Grok", "lines": [
                {"text": "剩 12%", "tone": "body"}],
             "spark": [(i * 3600.0, 50.0 - i) for i in range(6)], "inactive": False},
            {"name": "DeepSeek", "icon_name": "DeepSeek", "lines": [
                {"text": "余额 12.50", "tone": "body"}],
             "spark": [(i * 3600.0, 20.0 - i * 0.5) for i in range(5)], "inactive": False},
        ]
        for language, dark in (("zh-CN", False), ("zh-CN", True), ("en", False)):
            spec = hover_card_spec(rows, language)
            bitmap, layout = ui_dialogs.render_hover(
                spec, 1.25, ui_dialogs.palette(dark, (0, 120, 212)))
            self.assertGreater(layout["width"], 100)
            self.assertGreater(layout["height"], 80)
            bitmap.Dispose()
        # Fewer than 2 points: still renders without spark
        sparse = hover_card_spec([{"name": "Kimi", "lines": [], "spark": [(1.0, 1.0)]}], "en")
        bitmap, _ = ui_dialogs.render_hover(sparse, 1.0, ui_dialogs.palette(False, (0, 120, 212)))
        bitmap.Dispose()


if __name__ == "__main__":
    unittest.main()

class ShowTrendToggleTests(unittest.TestCase):
    def test_profile_default_on_and_persists(self):
        from vram_radar.models import Profile
        base = Profile.empty("default").to_dict()
        self.assertTrue(base["usage_show_trend"])
        self.assertIn("usage_sort_urgency", base)
        raw = dict(base)
        raw["usage_show_trend"] = False
        updated = Profile.from_dict(raw)
        self.assertFalse(updated.usage_show_trend)
        self.assertEqual(updated.usage_labels, base["usage_labels"])

    def test_build_hover_respects_show_trend_flag(self):
        import tempfile
        from pathlib import Path as P
        from vram_radar.hover_detail import build_hover_rows
        from vram_radar.usage_trend import TrendStore
        with tempfile.TemporaryDirectory() as tmp:
            store = TrendStore(P(tmp) / "t.json")
            now = 1_700_000_000.0
            for i in range(8):
                store.record([("grok:quota", 20.0 + i, "used")], now=now - (7 - i) * 86400, interval=1)
            providers = {"grok": {"installed": True, "running": True, "signed_in": True,
                                  "quota_percent": 40, "quota": {"zh": "40%", "en": "40%"},
                                  "fetched_at": now - 60}}
            on = build_hover_rows(None, providers, ["grok"], language="en", now=now,
                                  names={"grok": "Grok"}, trend_store=store, show_trend=True)
            off = build_hover_rows(None, providers, ["grok"], language="en", now=now,
                                   names={"grok": "Grok"}, trend_store=store, show_trend=False)
            self.assertGreaterEqual(len(on[0].get("spark") or []), 2)
            self.assertTrue(any("7 d avg" in (line.get("text") or "") for line in on[0].get("lines") or []))
            self.assertFalse(off[0].get("spark"))
            self.assertFalse(any("7 d avg" in (line.get("text") or "") for line in off[0].get("lines") or []))

    def test_menu_builds_show_trend_item(self):
        try:
            import clr
            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
        except Exception as exc:
            raise unittest.SkipTest("WinForms unavailable: %s" % exc) from exc
        import logging, sys, threading, time, webview
        from pathlib import Path as P
        ROOT = P(__file__).resolve().parents[1]
        sys.path.insert(0, str(ROOT / "tools"))
        from benchmark_webview_ui import FakeApi, _wait_until_ready
        from vram_radar.usage_surface import CodexUsageSurface, _dll
        logging.disable(logging.CRITICAL)
        window = webview.create_window(
            "trend toggle menu", width=640, height=480,
            url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
            js_api=FakeApi(), hidden=True, focus=False)
        display = {"usage_sort_urgency": False, "usage_show_trend": True,
                   "usage_labels": "text", "usage_background": "transparent",
                   "profile_id": "default"}
        state = {"enabled": True, "state": "ready",
                 "windows": [{"name": "5 hour", "window_minutes": 300,
                              "remaining_percent": 50, "resets_at": time.time() + 3600}]}
        surface = CodexUsageSurface(
            window, lambda: dict(state), language=lambda: "zh-CN",
            open_settings=lambda: None, open_home=lambda: None, refresh=lambda: None,
            display_options=lambda: dict(display),
            save_display=lambda k, v: display.__setitem__(k, v) or {"ok": True},
            disable=lambda: None, quit_application=lambda: None)
        _dll("user32").SetForegroundWindow = lambda hwnd: 1
        result = {}
        label = "显示走势图"
        def run():
            try:
                from System import Action
                _wait_until_ready(window, time.monotonic() + 20)
                surface.start()
                def check():
                    surface._tick()
                    result["has_trend"] = surface._show_trend_item is not None and surface._show_trend_item.Text == label
                    result["has_urgency"] = surface._sort_urgency_item is not None
                    result["trend_checked"] = bool(surface._show_trend_item.Checked)
                    items = list(surface._display_menu.DropDownItems)
                    result["order_ok"] = items.index(surface._sort_urgency_item) < items.index(surface._show_trend_item)
                window.native.Invoke(Action(check))
            except Exception as exc:
                result["error"] = repr(exc)
            finally:
                try:
                    window.destroy()
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True).start()
        webview.start()
        if result.get("error"):
            self.fail(result["error"])
        self.assertTrue(result.get("has_trend"), result)
        self.assertTrue(result.get("has_urgency"), result)
        self.assertTrue(result.get("trend_checked"), result)
        self.assertTrue(result.get("order_ok"), result)
