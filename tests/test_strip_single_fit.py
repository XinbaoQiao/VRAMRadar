"""Codex-only strip: compacted, then clipped, to the empty taskbar gap (never past Start)."""
import sys
import types
import unittest
from unittest import mock

from vram_radar.usage_surface import SINGLE_FIT_FACTORS, docked_point, docked_target, left_gap, place_single


class FakeSize:
    def __init__(self, width, height):
        self.Width, self.Height = width, height


class FakeFont:
    def __init__(self, size):
        self.Size = size


class FakeLabel:
    """Preferred width grows with text length and font size, like Segoe UI Bold."""

    def __init__(self, text):
        self.Text, self.Font, self.Size, self.Location = text, None, None, None

    def GetPreferredSize(self, _proposed):
        return FakeSize(round(len(self.Text) * self.Font.Size * 0.62) + 2, round(self.Font.Size * 1.4))


DRAWING = types.SimpleNamespace(Point=lambda x, y: (x, y), Size=FakeSize)


def layout(texts, available, strip_scale=1.5):
    labels = [FakeLabel(text) for text in texts]
    fonts = {}

    def fonts_for(factor):
        fonts.setdefault(factor, (FakeFont(round(21 * factor)), FakeFont(round(21 * factor))))
        return fonts[factor]

    with mock.patch.dict(sys.modules, {"System": types.SimpleNamespace(Drawing=DRAWING), "System.Drawing": DRAWING}):
        width, factor = place_single(labels, fonts_for, lambda _text, base: base, strip_scale, available)
    return width, factor, labels


class SingleStripFitTests(unittest.TestCase):
    def test_full_size_when_it_fits_or_without_a_gap(self):
        for available in (None, 400):
            width, factor, labels = layout(["82%", "6d 7h"], available)
            self.assertEqual(factor, 1.0)
            self.assertTrue(all(label.Font.Size == 21 for label in labels))
            self.assertGreaterEqual(width, round(5 * 1.5) + round(47 * 1.5) + round(2 * 1.5))

    def test_shrinks_type_before_clipping(self):
        natural, _, _ = layout(["100%", "168.0h"], None)
        width, factor, labels = layout(["100%", "168.0h"], natural - 5)
        self.assertLess(factor, 1.0)
        self.assertLessEqual(width, natural - 5)
        self.assertTrue(all(label.GetPreferredSize(None).Width <= label.Size.Width for label in labels))

    def test_clipped_to_gap_when_even_ninety_percent_does_not_fit(self):
        width, factor, labels = layout(["100%", "168.0h"], 51)
        self.assertEqual((width, factor), (51, SINGLE_FIT_FACTORS[-1]))
        self.assertTrue(all(label.Location[0] + label.Size.Width <= width for label in labels))

    def test_font_floor_matches_multi_app_layout(self):
        self.assertEqual(SINGLE_FIT_FACTORS[0], 1.0)
        self.assertGreaterEqual(min(SINGLE_FIT_FACTORS), 0.9)

    def test_narrow_gap_reading_never_reaches_start(self):
        # 150 % taskbar where only 51 px are free between the weather and Start.
        bar = (0, 1528, 2560, 1600)
        elements = {"WidgetsButton": (9, 1528, 422, 1600), "StartButton": (509, 1528, 577, 1600)}
        gap = left_gap(bar, elements, 18)
        width, _, _ = layout(["82%", "6d 7h"], gap[1] - gap[0])
        x, _ = docked_point(bar, docked_target(bar, (2200, 1528, 2560, 1600), elements, (width, 51), 18), width)
        self.assertEqual(x, gap[0])
        self.assertLessEqual(x + width, elements["StartButton"][0] - 18)


if __name__ == "__main__":
    unittest.main()
