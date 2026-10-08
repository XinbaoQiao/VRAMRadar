"""Settings → Extensions is one generic quota-monitoring master switch."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vram_radar.models import Profile
from vram_radar.service import DashboardService
from vram_radar.shell import AppApi
from vram_radar.storage import ProfileStore, SnapshotCache, storage_paths

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "vram_radar" / "web"
REMOVED_IDS = ("quota-usage-options", "quota-usage-details", "refresh-quota-usage", "quota-executable",
               "apply-quota-settings", "auto-quota-settings")
CJK = re.compile(r"[\u3400-\u9fff]")


def extension_block(markup: str) -> str:
    start = markup.index('<details id="extensions-settings"')
    return markup[start:markup.index('<details id="import-panel"', start)]


class ExtensionMarkupTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.app = (WEB / "app.js").read_text(encoding="utf-8")
        self.l10n = (WEB / "localization.js").read_text(encoding="utf-8")

    def test_single_generic_switch(self):
        block = extension_block(self.html)
        self.assertEqual(block.count('type="checkbox"'), 1)
        self.assertIn('id="quota-usage-enabled"', block)
        self.assertIn("<strong>额度监控</strong>", block)
        self.assertIn("开启后自动读取本机 AI 应用的额度，并显示在任务栏", block)
        self.assertIn('id="quota-usage-status"', block)
        self.assertNotIn("Codex", block)
        for tag in ("<button", "role=\"meter\"", "<details id=\"quota"):
            self.assertNotIn(tag, block)

    def test_removed_controls_are_not_referenced(self):
        shell = (ROOT / "src" / "vram_radar" / "shell.py").read_text(encoding="utf-8")
        for element_id in REMOVED_IDS:
            self.assertNotIn(f'id="{element_id}"', self.html)
            self.assertNotIn(f"'{element_id}'", self.app, element_id)
            self.assertNotIn(element_id, shell, element_id)
        for name in ("renderCodexUsage", "pollCodexUsage", "codexExecutable"):
            self.assertNotIn(name, self.app)

    def test_english_strings_are_english(self):
        pairs = {
            "额度监控": "Quota monitoring",
            "开启后自动读取本机 AI 应用的额度，并显示在任务栏":
                "Automatically read quotas from AI apps on this computer and show them on the taskbar.",
            "额度监控已关闭": "Quota monitoring is off",
            "未检测到 AI 应用": "No AI apps detected",
        }
        for zh, en in pairs.items():
            self.assertIn(f"['{zh}', '{en}']", self.l10n)
            self.assertIsNone(CJK.search(en))
        self.assertIn("已检测到 ([\\d,.]+) 个 AI 应用", self.l10n)

    def test_switch_keeps_saved_executable(self):
        start = self.app.index("async function applyCodexSettings(")
        body = self.app[start:self.app.index("\n}\n", start)]
        self.assertIn("executable = currentProfile?.codex_executable || ''", body)
        self.assertIn("codex_executable: currentProfile?.codex_executable || ''", self.app)


class MasterSwitchSemanticsTests(unittest.TestCase):
    def make(self, directory, **extra):
        paths = storage_paths(Path(directory))
        raw = {**Profile.empty("test").to_dict(), "codex_usage_enabled": True,
               "usage_providers": ["codex", "kimi", "grok"],
               "usage_session_consent": {"grok": True, "kimi": True}, **extra}
        profile = Profile.from_dict(raw)
        store = ProfileStore(paths)
        store.save(profile)
        service = DashboardService(profile, SnapshotCache(paths, "test"))
        return AppApi(store.load("test"), store, paths, service, automatic_import_enabled=False)

    def test_existing_profile_shows_on_with_same_models(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make(directory)
            try:
                overview = api.get_usage_providers()
                self.assertTrue(overview["enabled"])
                self.assertEqual(sorted(overview["selected"]), ["codex", "grok", "kimi"])
                self.assertEqual(overview["selected"], list(api.store.load("test").usage_providers))
                self.assertEqual(sorted(overview["session_consent"]), ["grok", "kimi"])
            finally:
                api._codex_usage.close()
                api._usage_providers.close()

    def test_off_then_on_keeps_per_app_choices_and_consents(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make(directory, codex_executable="")
            before = tuple(api.store.load("test").usage_providers)
            self.assertEqual(sorted(before), ["codex", "grok", "kimi"])
            try:
                with patch.object(api._codex_usage, "configure") as codex, \
                        patch.object(api._usage_providers, "configure") as monitor:
                    self.assertTrue(api.save_codex_usage_settings(False, "", api._profile_revision)["ok"])
                    self.assertFalse(api.get_usage_providers()["enabled"])
                    codex.assert_called_with(False, "")
                    self.assertFalse(monitor.call_args[0][0])               # every provider stops
                    self.assertTrue(api.save_codex_usage_settings(True, "", api._profile_revision)["ok"])
                    self.assertTrue(monitor.call_args[0][0])
                    self.assertEqual(tuple(monitor.call_args[0][2]), before)
                saved = api.store.load("test")
                self.assertTrue(saved.codex_usage_enabled)
                self.assertEqual(tuple(saved.usage_providers), before)
                self.assertEqual(sorted(saved.usage_session_consent), ["grok", "kimi"])
            finally:
                api._codex_usage.close()
                api._usage_providers.close()


def css_rule(css: str, selector: str) -> dict[str, str]:
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    if not found:
        raise AssertionError(f"missing CSS rule {selector}")
    return {key.strip(): value.strip() for key, value in
            (part.split(":", 1) for part in found.group(1).split(";") if ":" in part)}


def px(value: str) -> float:
    return float(value.removesuffix("px"))


class CompactSpacingCssTests(unittest.TestCase):
    """Static guarantees that hold on every WebView (Windows WebView2 and macOS WebKit)."""

    def setUp(self):
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_checkbox_is_a_fixed_box_on_the_title_line(self):
        box = css_rule(self.css, ".quota-extension-row .check-label input")
        label = css_rule(self.css, ".quota-extension-row .check-label")
        title = css_rule(self.css, ".quota-extension-row strong")
        self.assertEqual(label["align-items"], "flex-start")
        self.assertEqual(box["min-height"], "0")                    # the generic 38px input height must not apply
        self.assertEqual(box["padding"], "0")
        top = px(box["margin"].split()[0])
        self.assertAlmostEqual(top + px(box["height"]) / 2, px(title["line-height"]) / 2, delta=0.5)

    def test_status_hangs_under_the_label_text_with_a_small_gap(self):
        box = css_rule(self.css, ".quota-extension-row .check-label input")
        label = css_rule(self.css, ".quota-extension-row .check-label")
        top, _right, _bottom, left = (css_rule(self.css, "#quota-usage-status")["margin"].split() + ["0"] * 4)[:4]
        self.assertEqual(px(left), px(box["width"]) + px(label["gap"]))
        self.assertLessEqual(px(top), 6)

    def test_block_padding_is_balanced_and_matches_card_insets(self):
        padding = css_rule(self.css, ".quota-extension-body")["padding"].split()
        self.assertEqual(padding, ["14px", "16px"])                 # same 16px side inset as the card headings

    def test_font_sizes_are_unchanged(self):
        self.assertEqual(css_rule(self.css, ".quota-extension-row small")["font-size"], "var(--font-small)")
        self.assertEqual(css_rule(self.css, "#quota-usage-status")["font-size"], "var(--font-small)")


class CompactSpacingRenderTests(unittest.TestCase):
    """Measured geometry in a headless Chromium-family browser (skipped when none is installed)."""

    @classmethod
    def setUpClass(cls):
        import os
        import sys
        # Same rule as test_english_web_ui: headless Chrome on macOS runners stalls
        # under --virtual-time-budget; the shared web UI is measured on Windows/Linux.
        if os.environ.get("VRAM_RADAR_SKIP_BROWSER_TESTS") or sys.platform == "darwin":
            raise unittest.SkipTest("headless browser rendering is not run on macOS")
        sys.path.insert(0, str(ROOT / "tools"))
        from render_extension_shots import measure
        cls.metrics = measure()
        if cls.metrics is None:
            raise unittest.SkipTest("no headless Chromium-family browser")

    def test_every_language_and_state_has_the_same_compact_rhythm(self):
        self.assertEqual(sorted(self.metrics), ["en_off", "en_on", "zh_off", "zh_on"])
        shape = None
        for name, values in self.metrics.items():
            with self.subTest(name):
                self.assertNotIn("error", values)
                self.assertLessEqual(abs(values["status_left_minus_text_left"]), 1)
                self.assertGreaterEqual(values["row_to_status_gap"], 0)
                self.assertLessEqual(values["row_to_status_gap"], 8)
                self.assertLessEqual(abs(values["checkbox_center_minus_title_center"]), 2)
                self.assertLessEqual(values["title_to_desc_pitch"], 19)
                self.assertLessEqual(abs(values["top_padding"] - values["bottom_padding"]), 4)
                self.assertLessEqual(values["bottom_padding"], 22)
                self.assertEqual({values["title_font"], values["desc_font"], values["status_font"]}, {"13px"})
                current = {key: value for key, value in values.items() if key != "status_text"}
                shape = shape or current
                self.assertEqual(current, shape)


if __name__ == "__main__":
    unittest.main()
