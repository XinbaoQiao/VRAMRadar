"""Strip value column: quota, reset and state words share one bold weight."""
import sys
import unittest

from vram_radar import usage_surface as us

CJK_STATES = ["\u672a\u8fd0\u884c", "\u672a\u5b89\u88c5", "\u672a\u767b\u5f55", "\u9700\u767b\u5f55",
              "\u5df2\u7528\u5c3d", "\u53ef\u7528", "\u67e5\u8be2\u4e2d", "\u00a56", "\uffe56"]
LATIN = ["0%", "1d 7h", "6d 2h", "45m", "\u00a56", "Not running", "Missing", "Signed out", "Sign in", "Used up", "OK"]


class Family(unittest.TestCase):
    def test_cjk_states_use_yahei_latin_uses_segoe(self):
        for text in CJK_STATES[:7]:
            self.assertEqual(us.value_font_family(text), us.CJK_VALUE_FAMILY, text)
        for text in LATIN:
            self.assertEqual(us.value_font_family(text), us.LATIN_VALUE_FAMILY, text)
        self.assertEqual(us.value_font_family(""), us.LATIN_VALUE_FAMILY)

    def test_pick_keeps_size_and_style_and_caches(self):
        class F:
            def __init__(self, name, size, style): self.Name, self.Size, self.Style = name, size, style
        base, cache, made = F("Segoe UI", 19.0, 1), {}, []
        make = lambda fam, b: made.append(fam) or F(fam, b.Size, b.Style)
        self.assertIs(us.pick_value_font("0%", base, cache, make), base)
        a = us.pick_value_font("\u672a\u8fd0\u884c", base, cache, make)
        b = us.pick_value_font("\u9700\u767b\u5f55", base, cache, make)
        self.assertIs(a, b)
        self.assertEqual((a.Name, a.Size, a.Style, made), (us.CJK_VALUE_FAMILY, 19.0, 1, [us.CJK_VALUE_FAMILY]))


@unittest.skipUnless(sys.platform == "win32", "GDI text rendering")
class RenderedWeight(unittest.TestCase):
    """Pixel check: the CJK state word is drawn as heavy as bold, not like the
    lighter Segoe UI font-link fallback (10-02 v8 screenshot)."""

    def ink(self, family, style, text, px=17):
        import clr  # noqa: F401
        clr.AddReference("System.Drawing"); clr.AddReference("System.Windows.Forms")
        from System.Drawing import Bitmap, Color, Font, Graphics, GraphicsUnit, Point
        from System.Windows.Forms import TextRenderer
        bmp = Bitmap(140, 40)
        g = Graphics.FromImage(bmp)
        g.Clear(Color.White)
        font = Font(family, px, style, GraphicsUnit.Pixel)
        TextRenderer.DrawText(g, text, font, Point(2, 2), Color.Black, Color.White)
        g.Dispose(); font.Dispose()
        return sum(1 for x in range(140) for y in range(40) if bmp.GetPixel(x, y).R < 128)

    def test_cjk_state_bold_like_values(self):
        import clr  # noqa: F401
        clr.AddReference("System.Drawing")
        from System.Drawing import FontStyle
        for text in CJK_STATES[:6]:
            chosen = self.ink(us.value_font_family(text), FontStyle.Bold, text)
            fallback = self.ink("Segoe UI", FontStyle.Bold, text)
            regular = self.ink(us.CJK_VALUE_FAMILY, FontStyle.Regular, text)
            self.assertGreater(chosen, fallback * 1.1, text)   # heavier than the font-link fallback
            self.assertGreater(chosen, regular * 1.4, text)    # clearly bold


if __name__ == "__main__":
    unittest.main()