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


if __name__ == "__main__":
    unittest.main()
