"""Render the main window headlessly and require English-only text in English mode."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_english_web_ui  # noqa: E402

BROWSER = None if os.environ.get("VRAM_RADAR_SKIP_BROWSER_TESTS") else check_english_web_ui.find_browser(None)


@unittest.skipUnless(BROWSER, "no headless Chromium-family browser available")
class EnglishWebUiTests(unittest.TestCase):
    def test_rendered_main_window_has_no_cjk_in_english(self):
        report = check_english_web_ui.run("en", BROWSER)
        hits = {key: value.get("cjk") or value.get("error") for key, value in report["scenarios"].items()
                if value.get("cjk") or value.get("error")}
        self.assertEqual(hits, {})
        self.assertTrue(report["ok"])

    def test_rendered_main_window_stays_chinese_in_chinese_mode(self):
        report = check_english_web_ui.run("zh-CN", BROWSER)
        self.assertTrue(report["ok"], report)


if __name__ == "__main__":
    unittest.main()
