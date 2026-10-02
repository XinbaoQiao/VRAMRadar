"""Quota and reset time as two separate strip items, per provider."""
import re
import time
import unittest
from decimal import Decimal

from vram_radar.providers import grok, grok_usage, kimi, kimi_usage
from vram_radar.providers.deepseek import balance_labels
from vram_radar.reset_format import RESET_RE, reset_full, reset_short, strip_parts
from vram_radar.usage_surface import codex_brief, compact_value, provider_reading, quota_lines, widget_reading

CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
NOW = 1_800_000_000.0
H = 3600


def read(state, pid="x", lang="en"):
    return provider_reading(state, {"id": pid, "name": pid, "short": pid}, lang, now=NOW)


class Format(unittest.TestCase):
    def test_short_hours_then_days(self):
        self.assertEqual([reset_short(s, True) for s in (None, 0, -5, float("nan"), 200, 33.9 * H, 47.9 * H, 48 * H, 3.2 * 86400)],
                         ["", "", "", "", "4m", "1d 9h", "1d 23h", "2d", "3d 4h"])
        for text in ("4m", "1d 9h", "3d", "5\u5c0f\u65f6", "6\u592912\u5c0f\u65f6", "45\u5206\u949f"):
            self.assertTrue(RESET_RE.fullmatch(text))
        self.assertIsNone(RESET_RE.fullmatch("50%"))

    def test_full_local_time_both_languages(self):
        t = time.localtime(NOW)
        clock = f"{t.tm_hour:02d}:{t.tm_min:02d}"
        self.assertEqual(reset_full(NOW, False), f"\u91cd\u7f6e: {t.tm_mon}\u6708{t.tm_mday}\u65e5 {clock}")
        en = reset_full(NOW, True)
        self.assertTrue(en.startswith("Resets ") and en.endswith(clock))
        self.assertIsNone(CJK.search(en))
        self.assertEqual(reset_full(None, True), "")

    def test_composition_quota_reset_both_neither(self):
        self.assertEqual(strip_parts("66%", "3d 4h", "OK"), ("66%", "3d 4h"))
        self.assertEqual(strip_parts("\u00a56", "", "OK"), ("\u00a56", ""))
        self.assertEqual(strip_parts("", "33.9h", "OK"), ("", "33.9h"))
        self.assertEqual(strip_parts("", "", "Login"), ("Login", ""))

    def test_compaction_drops_day_countdowns_too(self):
        self.assertEqual(compact_value("0% 2.0d", 1), "0%")
        self.assertEqual(compact_value("0% 33.9h", 1), "0%")


class Providers(unittest.TestCase):
    def test_codex_quota_is_percent_reset_is_countdown(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 0, "window_minutes": 10080, "resets_at": NOW + 47.6 * H},
            {"remaining_percent": 40, "window_minutes": 10080, "resets_at": NOW + 80 * H}]}
        rows = quota_lines(state, "en", now=NOW)
        self.assertEqual((rows[0]["value"], rows[0]["countdown"], rows[0]["reset_at"]), ("0%", "1d 23h", NOW + 47.6 * H))
        self.assertEqual(widget_reading(state, 1, "en", now=NOW)["countdown"], "3d 8h")
        self.assertIn(reset_full(NOW + 47.6 * H, True), codex_brief(rows, "en"))

    def test_grok_weekly_reset_from_next_reset_timestamp(self):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + 3.2 * 86400))
        parsed = grok_usage.parse_usage({"usagePercent": 34, "nextResetTimestampUtc": stamp})
        self.assertAlmostEqual(parsed["reset_at"], NOW + 3.2 * 86400, delta=1)
        state = grok._apply_session({"facts": []}, parsed)
        info = read(state, "Grok")
        self.assertEqual((info["quota"], info["reset"]), ("66%", "3d 4h"))
        self.assertTrue(info["brief"].endswith(reset_full(parsed["reset_at"], True)))
        self.assertIn("\u91cd\u7f6e: ", read(state, "Grok", "zh-CN")["brief"])
        no_reset = read(grok._apply_session({"facts": []}, {"status": "ok", "used_percent": 34, "reset_at": None}))
        self.assertEqual((no_reset["quota"], no_reset["reset"]), ("66%", ""))

    def test_grok_stale_keeps_marker_and_reset(self):
        state = grok._apply_session({"facts": []}, {"status": "ok", "used_percent": 50, "reset_at": NOW + 30 * H,
                                                    "stale_error": True, "fetched_at": time.time() - 7200})
        self.assertTrue(state["stale"])
        info = read(state, "Grok")
        self.assertTrue(info["warning"])
        self.assertIn("(read ", info["brief"])
        self.assertEqual(info["reset"], "1d 6h")

    def test_kimi_share_and_reset(self):
        state = kimi._show_reading({"facts": []}, {"used_percent": 20, "reset_at": NOW + 33.9 * H}, NOW)
        info = read(state, "Kimi")
        self.assertEqual((info["quota"], info["reset"]), ("80%", "1d 9h"))
        used_up = read(kimi._show_reading({"facts": []}, {"used_percent": 100, "reset_at": NOW + 5 * H}, NOW), "Kimi")
        self.assertEqual((used_up["quota"], used_up["reset"]), ("Used up", "5h"))
        reset_only = read(kimi._show_reading({"facts": []}, {"is_member": True, "reset_at": NOW + 5 * H}, NOW), "Kimi")
        self.assertEqual(strip_parts(reset_only["quota"], reset_only["reset"], reset_only["value"]), ("", "5h"))
        self.assertEqual(kimi_usage._used_percent(0.2), 20.0)   # amountUsedRatio share

    def test_deepseek_balance_without_reset(self):
        head, brief = balance_labels([{"currency": "CNY", "kind": "granted", "amount": Decimal("6.00")}], [])
        info = read({"installed": True, "headline": head, "quota": head, "brief": brief, "facts": []}, "DeepSeek")
        self.assertEqual((info["quota"], info["reset"]), (head["en"], ""))

    def test_generic_neither_falls_back_to_status(self):
        info = read({"installed": True, "headline": {"zh": "\u5df2\u5b89\u88c5", "en": "Installed"}, "facts": []}, "Gen")
        self.assertEqual(strip_parts(info["quota"], info["reset"], info["value"]), ("Installed", ""))

    def test_past_reset_not_shown_and_english_has_no_cjk(self):
        info = read({"installed": True, "headline": {"zh": "34%", "en": "34%"}, "quota": {"zh": "34%", "en": "34%"},
                     "reset_at": NOW - 10, "facts": []}, "Grok")
        self.assertEqual(info["reset"], "")
        live = read({"installed": True, "headline": {"zh": "34%", "en": "34%"}, "reset_at": NOW + 99 * H, "facts": []})
        self.assertIsNone(CJK.search(live["brief"] + live["reset"]))


if __name__ == "__main__":
    unittest.main()