"""Render the main window headlessly and require English-only text in English mode."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_english_web_ui  # noqa: E402

# Headless Chrome on macOS runners stalls under --virtual-time-budget (the
# release job timed out); the web UI is the same on every platform and is
# rendered on Windows and Linux.
BROWSER = (None if os.environ.get("VRAM_RADAR_SKIP_BROWSER_TESTS") or sys.platform == "darwin"
           else check_english_web_ui.find_browser(None))


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
        repeats = {key: value["grammar"] for key, value in report["scenarios"].items() if value.get("grammar")}
        self.assertEqual(repeats, {})  # e.g. "A100 Cluster，数据已过期，数据已过期"
        self.assertTrue(report["ok"], report)


class RepeatRuleTests(unittest.TestCase):
    def test_chinese_repeat_rule(self):
        rule = check_english_web_ui.REPEAT_ZH
        for text in ("A100 Cluster，数据已过期，数据已过期", "4090 Workstation，网络不可达，网络不可达，2 个任务"):
            self.assertTrue(rule.search(text), text)
        for text in ("A100 Cluster，数据已过期", "A100 Cluster，在线，8 GPU · 120 GiB 可用", "空闲，空闲中"):
            self.assertFalse(rule.search(text), text)

    def test_english_singular_rule(self):
        flagged = lambda text: any(rule.search(text) for rule in check_english_web_ui.GRAMMAR)
        for text in ("1 servers", "Skipped 1 servers you removed", "1 items"):
            self.assertTrue(flagged(text), text)
        for text in ("1/1 servers ready", "11 servers", "0.1 hours", "2 items", "1 server"):
            self.assertFalse(flagged(text), text)

    def test_english_repeat_rule(self):
        rule = check_english_web_ui.REPEAT_EN
        self.assertTrue(rule.search("A100 Cluster, Data is stale, Data is stale"))
        self.assertFalse(rule.search("A100 Cluster, Data is stale"))



@unittest.skipUnless(__import__("shutil").which("node"), "Node.js unavailable")
class NavigatorLabelFilterTests(unittest.TestCase):
    def test_only_the_state_like_summary_is_dropped(self):
        import json, re, shutil, subprocess
        source = (Path(__file__).resolve().parents[1] / "src" / "vram_radar" / "web" / "app.js").read_text(encoding="utf-8")
        body = source[source.index("function renderServerNavigatorItem"):]
        predicate = re.search(r"\.filter\((\(part, i, parts\) => [^\n]+)\)\n", body).group(1)
        cases = [["A100 Cluster", "数据已过期", "数据已过期", ""],
                 ["离线", "离线", "离线", ""],
                 ["Lab", "监控就绪", "8 GPU · 120 GiB 可用", "2 个任务"],
                 ["Lab", "网络不可达", "网络不可达", "网络不可达"]]
        script = f"const f = {predicate}; process.stdout.write(JSON.stringify({json.dumps(cases, ensure_ascii=False)}.map(c => c.filter(f).join('，'))));"
        out = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(out.stdout), ["A100 Cluster，数据已过期", "离线，离线", "Lab，监控就绪，8 GPU · 120 GiB 可用，2 个任务",
                                                  "Lab，网络不可达，网络不可达"])


if __name__ == "__main__":
    unittest.main()
