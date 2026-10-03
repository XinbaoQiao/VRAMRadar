import os, sys, unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "WinForms offscreen render")
class StripPixelTests(unittest.TestCase):
    def test_icons_and_text_never_touch_at_any_scale(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import check_strip_pixels
        out = check_strip_pixels.run()
        self.assertTrue(out["ok"], out["failures"])
        self.assertEqual(out["cases"], 48 + 60)  # multi-app columns + Codex-only gap cases

    def test_bundled_icons_without_installed_apps(self):
        # CI runners have none of the apps installed; Codex still draws its bundled art.
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import check_strip_pixels
        empty = {name: None for name in ("Codex", "Grok", "DeepSeek", "Kimi")}
        with patch.object(check_strip_pixels, "install_paths", return_value=empty):
            out = check_strip_pixels.run()
        self.assertTrue(out["ok"], out["failures"])


if __name__ == "__main__":
    unittest.main()