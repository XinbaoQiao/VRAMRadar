import os, sys, unittest


@unittest.skipUnless(sys.platform == "win32", "WinForms offscreen render")
class StripPixelTests(unittest.TestCase):
    def test_icons_and_text_never_touch_at_any_scale(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import check_strip_pixels
        out = check_strip_pixels.run()
        self.assertTrue(out["ok"], out["failures"])
        self.assertEqual(out["cases"], 40)


if __name__ == "__main__":
    unittest.main()