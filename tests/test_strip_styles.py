"""Concise tooltip, six-app limit, background styles and Grok on-screen usage."""
import unittest

from vram_radar.models import Profile, normalize_usage_background
from vram_radar.providers import MAX_SELECTED, PROVIDER_IDS, grok, normalize_selection
from vram_radar.providers.screen_usage import parse_menu_texts
from vram_radar.usage_surface import (BACKGROUND_STYLES, _luminance, codex_brief, concise_tooltip,
                                      provider_reading, quota_lines, strip_look)


class Tooltip(unittest.TestCase):
    def test_one_short_line_per_app_without_diagnostics(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 85, "window_minutes": 300, "resets_at": 1000 + 2.3 * 3600},
            {"remaining_percent": 60, "window_minutes": 10080, "resets_at": 1000 + 88 * 3600}]}
        rows = quota_lines(state, now=1000)
        deepseek = provider_reading({"installed": True, "version": "1.2.3", "signed_in": True,
                                     "headline": {"zh": "¥6.00", "en": "¥6.00"},
                                     "subline": {"zh": "12k tok", "en": "12k tok"},
                                     "facts": [{"zh": "路径 C:\\x", "en": "path"}]},
                                    {"id": "deepseek", "name": "DeepSeek"}, now=1000)
        kimi = provider_reading({"installed": True, "stale": True, "headline": {"zh": "可用", "en": "OK"},
                                 "brief": {"zh": "额度未用尽 · 免费版", "en": "x"},
                                 "subline": {"zh": "记录 09-03", "en": "As of 09-03"}, "facts": []},
                                {"id": "kimi", "name": "Kimi"}, now=1000)
        tip = concise_tooltip(rows, [deepseek, kimi])
        lines = tip.split("\n")
        from vram_radar.reset_format import reset_full
        self.assertEqual(lines[0], "Codex  5h 85% \u00b7 " + reset_full(1000 + 2.3 * 3600, False)
                         + " | 7d 60% \u00b7 " + reset_full(1000 + 88 * 3600, False))
        self.assertEqual(lines[1:], [
                                 "DeepSeek  ¥6.00", "Kimi  额度未用尽 · 免费版（记录 09-03）"])
        self.assertNotIn("1.2.3", tip)
        self.assertNotIn("C:\\", tip)

    def test_future_reset_is_appended(self):
        info = provider_reading({"installed": True, "headline": {"zh": "可用", "en": "OK"}, "reset_at": 1000 + 7200,
                                 "facts": []}, {"id": "kimi", "name": "Kimi"}, now=1000)
        from vram_radar.reset_format import reset_full
        self.assertEqual(info["brief"], info["brief"].split(" \u00b7 ")[0] + " \u00b7 " + reset_full(1000 + 7200, False))
        self.assertEqual((info["quota"], info["reset"]), ("", "2\u5c0f\u65f6"))

    def test_codex_error_line(self):
        self.assertEqual(codex_brief(quota_lines({"enabled": True, "state": "error", "code": "login_required"})),
                         "Codex  请登录 Codex")


class SixApps(unittest.TestCase):
    def test_selection_is_capped(self):
        self.assertEqual(MAX_SELECTED, 4)
        self.assertEqual(normalize_selection(list(PROVIDER_IDS)), tuple(PROVIDER_IDS[:4]))
        raw = Profile.empty("t").to_dict()
        raw["usage_providers"] = list(PROVIDER_IDS)
        self.assertEqual(Profile.from_dict(raw).usage_providers, tuple(PROVIDER_IDS[:4]))

    def test_old_profile_with_pie_display_loads_as_text(self):
        for legacy in (True, False, "yes", None):
            raw = Profile.empty("t").to_dict()
            raw["codex_show_disks"] = legacy
            profile = Profile.from_dict(raw)
            self.assertFalse(hasattr(profile, "codex_show_disks"))
            self.assertNotIn("codex_show_disks", profile.to_dict())  # dropped on next save

    def test_limit_hint_text(self):
        from vram_radar.usage_surface import limit_hint_text
        self.assertEqual(limit_hint_text(4), "最多同时显示 4 个，请先取消一个")
        self.assertIn("Up to 4", limit_hint_text(4, "en"))


class Backgrounds(unittest.TestCase):
    def test_styles_and_contrast(self):
        dark_bar, light_bar = (32, 32, 32), (240, 240, 240)
        for base in (dark_bar, light_bar):
            for style in BACKGROUND_STYLES:
                look = strip_look(style, base, (0, 120, 215))
                surface = look["surface"]
                # Text always contrasts with what is under it.
                self.assertGreater(abs(_luminance(look["foreground"]) - _luminance(surface)), 100, (style, base))
                self.assertEqual(look["keyed"], style != "match")
        self.assertIsNone(strip_look("transparent", dark_bar)["fill"])
        self.assertLess(_luminance(strip_look("dark", dark_bar)["fill"]), _luminance(dark_bar))
        self.assertGreater(_luminance(strip_look("light", dark_bar)["fill"]), _luminance(dark_bar))
        self.assertEqual(strip_look("bogus", dark_bar)["style"], "transparent")

    def test_profile_persists_style(self):
        raw = Profile.empty("t").to_dict()
        self.assertEqual(raw["usage_background"], "transparent")
        raw["usage_background"] = "dark"
        self.assertEqual(Profile.from_dict(raw).usage_background, "dark")
        self.assertEqual(normalize_usage_background("from-a-newer-release"), "transparent")


class GrokScreen(unittest.TestCase):
    def test_menu_text_parsing(self):
        items = [["Settings"], ["Help"], ["Weekly usage", "23%"],
                 ["Weekly usage", "Resets in 3 days", "23%"], ["Zoom", "100%"]]
        self.assertEqual(parse_menu_texts(items), {"percent_used": 23.0, "reset_text": "Resets in 3 days"})
        self.assertIsNone(parse_menu_texts([["Zoom", "100%"], ["Settings"]]))
        self.assertEqual(parse_menu_texts([["本周用量", "将于 10月5日 重置", "7.5%"]])["percent_used"], 7.5)
        self.assertIsNone(parse_menu_texts([["Usage", "250%"]]))

    def test_overlay(self):
        state = {"installed": True, "signed_in": True, "headline": {"zh": "已登录", "en": "Signed in"},
                 "facts": []}
        plain = grok.overlay(dict(state, facts=[]), None, now=10_000)
        self.assertIn("头像菜单", plain["brief"]["zh"])
        self.assertEqual(plain["headline"]["zh"], "已登录")
        seen = grok.overlay(dict(state, facts=[]), {"percent_used": 23.0, "reset_text": "Resets in 3 days",
                                                    "seen_at": 10_000 - 60}, now=10_000)
        self.assertEqual(seen["headline"]["zh"], "77%")
        self.assertTrue(seen["brief"]["zh"].startswith("已用 23% · Resets in 3 days"))
        old = grok.overlay(dict(state, facts=[]), {"percent_used": 23.0, "seen_at": 0}, now=8 * 86400 + 1)
        self.assertEqual(old["headline"]["zh"], "已登录")


if __name__ == "__main__":
    unittest.main()
