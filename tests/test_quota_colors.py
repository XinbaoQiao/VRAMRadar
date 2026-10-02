"""Single quota colour rule (OKLab gradient) and the reset countdown wording."""
import math
import unittest

from vram_radar.quota_colors import (NEUTRAL, contrast, curve_lab, js_table, luminance, quota_color, to_oklab,
                                     usage_color)
from vram_radar.reset_format import RESET_RE, reset_short
from vram_radar.usage_surface import BACKGROUND_STYLES, strip_look

M, H, D = 60, 3600, 86400


def hue(rgb_or_lab, lab=False):
    L, a, b = rgb_or_lab if lab else to_oklab(rgb_or_lab)
    return math.degrees(math.atan2(b, a)) % 360


class Gradient(unittest.TestCase):
    def test_hue_runs_monotonically_from_berry_to_blue(self):
        for bright in (False, True):
            raw = [hue(curve_lab(p / 100, bright), lab=True) for p in range(101)]
            self.assertTrue(all(b >= a - 1e-6 for a, b in zip(raw, raw[1:])), bright)
            shown = [hue(usage_color(p, bright=bright)) for p in range(101)]
            self.assertTrue(all(b >= a - 3 for a, b in zip(shown, shown[1:])), bright)   # byte rounding only
            self.assertGreater(shown[-1] - shown[0], 150)

    def test_even_perceptual_steps(self):
        for bright in (False, True):
            steps = [math.dist(curve_lab(p / 100, bright), curve_lab((p + 1) / 100, bright)) for p in range(100)]
            self.assertLess(max(steps) / min(steps), 1.05)          # arc-length parametrised
            shown = [math.dist(to_oklab(usage_color(p, bright=bright)), to_oklab(usage_color(p + 1, bright=bright)))
                     for p in range(100)]
            self.assertLess(max(shown), 0.03)                        # no visible jumps after contrast fix
            self.assertGreater(len({usage_color(p / 10, bright=bright) for p in range(1001)}), 150)

    def test_readable_on_every_style_and_taskbar(self):
        for base in ((32, 32, 32), (243, 243, 243), (0, 120, 215), (128, 128, 128)):
            for style in BACKGROUND_STYLES:
                look = strip_look(style, base, (0, 120, 215))
                surface, bright = look["surface"], look["bright"]
                best = contrast((0, 0, 0) if luminance(surface) > 0.18 else (255, 255, 255), surface)
                need = min(4.5, 0.8 * best) - 0.05
                for p in (None, *range(0, 101, 5)):
                    color = usage_color(p, bright=bright, surface=surface)
                    self.assertGreaterEqual(contrast(color, surface), need, (base, style, p, color))

    def test_state_grey_full_contrast_everywhere(self):
        for base in ((32, 32, 32), (243, 243, 243), (0, 120, 215), (128, 128, 128), (118, 118, 118), (200, 60, 90)):
            for style in BACKGROUND_STYLES:
                look = strip_look(style, base, (0, 120, 215))
                surface, bright = look["surface"], look["bright"]
                grey = quota_color(None, action=True, bright=bright, surface=surface)
                self.assertGreaterEqual(contrast(grey, surface), 4.5, (base, style, grey))
                L, a, b = to_oklab(grey)
                self.assertLess(math.hypot(a, b), 0.03, grey)               # stays grey
                self.assertEqual(quota_color(None, low=True, bright=bright, surface=surface),
                                 usage_color(0, bright=bright, surface=surface))   # used up stays a quota value

    def test_ends_and_clamping(self):
        self.assertEqual(usage_color(-5), usage_color(0))
        self.assertEqual(usage_color(250), usage_color(100))
        self.assertEqual(usage_color(float("nan")), usage_color(None))
        self.assertEqual(usage_color(None), NEUTRAL[False])

    def test_one_rule_for_every_provider(self):
        self.assertEqual(quota_color(37), usage_color(37))                         # Codex / Grok / Kimi share
        self.assertEqual(quota_color(None, low=True), usage_color(0))               # used up / empty balance
        self.assertEqual(quota_color(None, known=True), usage_color(100))           # balance, "available", reset only
        self.assertEqual(quota_color(80, warning=True), usage_color(None))          # stale / error: not current
        self.assertEqual(quota_color(None), usage_color(None))                      # bare status
        self.assertEqual(quota_color(5, low=True), usage_color(5))                  # a percentage wins over "low"

    def test_web_table_matches(self):
        table = js_table()
        self.assertEqual(len(table["light"]), 101)
        self.assertEqual(table["dark"][42], usage_color(42))


class Countdown(unittest.TestCase):
    def test_same_compact_letters_in_both_languages(self):
        from vram_radar.reset_format import reset_short
        for seconds, text in ((32 * 3600, "1d 8h"), (75 * 3600, "3d 3h"), (5 * 3600, "5h"), (45 * 60, "45m"), (48 * 3600 + 1800, "2d")):
            self.assertEqual((reset_short(seconds), reset_short(seconds, True)), (text, text))

    CASES = [(0, "", ""), (-30, "", ""), (1, "1m", "1m"), (59 * M, "59m", "59m"),
             (59 * M + 30, "59m", "59m"), (H, "1h", "1h"), (H + 59 * M, "1h", "1h"),
             (23 * H + 59 * M, "23h", "23h"), (D, "1d", "1d"), (D + 3 * H, "1d 3h", "1d 3h"),
             (47 * H, "1d 23h", "1d 23h"), (6 * D + 12 * H, "6d 12h", "6d 12h"),
             (2 * D + 30 * M, "2d", "2d"), (45 * M, "45m", "45m"), (5 * H, "5h", "5h")]

    def test_edge_cases_both_languages(self):
        for seconds, en, zh in self.CASES:
            self.assertEqual((reset_short(seconds, True), reset_short(seconds)), (en, zh), seconds)
            for text in (en, zh):
                if text:
                    self.assertTrue(RESET_RE.fullmatch(text), text)
        for bad in (None, float("nan"), float("inf"), True, "5"):
            self.assertEqual(reset_short(bad, True), "")

    def test_english_has_no_cjk(self):
        for seconds, en, _ in self.CASES:
            self.assertTrue(all(ord(c) < 128 for c in en))


if __name__ == "__main__":
    unittest.main()