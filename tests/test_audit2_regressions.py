"""Regression tests for the second global audit (after a7ee5d7)."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vram_radar import updates
from vram_radar.storage import prune_orphan_temporaries, system_ui_language
from vram_radar.update_helper import run_update

ROOT = Path(__file__).resolve().parents[1]


class _Truncated:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        raise http.client.IncompleteRead(b"{", 100)


class UpdateNetworkTests(unittest.TestCase):
    def test_truncated_release_response_is_a_failure_result(self):
        with patch("logging.Logger.warning"):
            result = updates.check_latest_release("0.9.9", current_tag="v0.9.9", current_commit=None,
                                                  opener=lambda *a, **k: _Truncated(), platform_name="win32")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "update_network_failed")

    def test_truncated_installer_download_reports_a_clean_failure(self):
        from vram_radar import shell
        release = {"ok": True, "update_available": True, "latest_version": "1.0.0",
                   "release_url": "https://github.com/x/y/releases/tag/v1.0.0",
                   "asset": {"name": "VRAMRadar-Setup-1.0.0.exe", "size": 10, "sha256": "0" * 64,
                             "url": "https://github.com/x/y/releases/download/v1.0.0/VRAMRadar-Setup-1.0.0.exe"}}
        with tempfile.TemporaryDirectory() as temporary:
            api = SimpleNamespace(paths=SimpleNamespace(cache=temporary))
            with patch.object(shell, "check_latest_release", return_value=release), \
                    patch.object(shell, "download_verified_asset", side_effect=http.client.IncompleteRead(b"", 5)), \
                    patch("logging.Logger.warning"):
                result = shell.AppApi._perform_latest_update(api)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])
        self.assertNotIn("IncompleteRead", result["error"])

    def test_update_retry_backs_off(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        script = (ROOT / "src" / "vram_radar" / "web" / "app.js").read_text(encoding="utf-8")
        consts = "\n".join(re.findall(r"^const UPDATE_CHECK_(?:INTERVAL|RETRY)_MS = [^\n]+$", script, re.M))
        start = script.index("function updateRetryDelay(")
        body = script[start:script.index("\n}\n", start) + 3]
        program = consts + "\n" + body + (
            "\nconsole.log(JSON.stringify([1,2,3,4,8,20].map(n => updateRetryDelay(n) / 60000)"
            ".concat([updateRetryDelay(1, 'update_rate_limited') / 60000])));")
        output = subprocess.run([node, "-e", program], capture_output=True, text=True, encoding="utf-8",
                                check=True, timeout=30).stdout
        self.assertEqual(json.loads(output), [5, 10, 20, 40, 360, 360, 60])
        self.assertIn("updateCheckFailures = 0;", script)


class UpdateHelperRecoveryTests(unittest.TestCase):
    def make(self, root: Path):
        install_root = root / "install"
        install_root.mkdir()
        executable = install_root / "VRAMRadar.exe"
        executable.write_bytes(b"old application")
        (install_root / ".vram-radar-installed").write_text("old marker", encoding="utf-8")
        stage = root / "stage"
        stage.mkdir()
        installer = stage / "VRAMRadar-Setup-0.7.0.exe"
        installer.write_bytes(b"verified installer")
        plan = {"schema_version": 1, "pid": 123, "app_executable": str(executable),
                "install_root": str(install_root), "installer": str(installer),
                "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(), "version": "0.7.0",
                "activation_path": str(root / "activation.json"), "restart_arguments": ["--profile", "test"]}
        plan_path = stage / "update-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return install_root, executable, installer, plan_path

    def test_locked_install_folder_restarts_the_existing_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            install_root, executable, _installer, plan_path = self.make(Path(temporary))
            original = Path.replace

            def replace(self, target):
                if Path(self) == install_root.resolve():
                    raise PermissionError("locked")
                return original(self, target)

            with patch("vram_radar.update_helper._wait_for_exit", return_value=True), \
                    patch("vram_radar.update_helper.subprocess.run", return_value=subprocess.CompletedProcess([], 0)), \
                    patch("vram_radar.update_helper.subprocess.Popen") as restart, \
                    patch("vram_radar.update_helper.time.sleep"), patch.object(Path, "replace", replace):
                with self.assertRaises(PermissionError):
                    run_update(plan_path)
            restart.assert_called_once()
            self.assertEqual(restart.call_args[0][0], [str(executable.resolve()), "--profile", "test"])
            self.assertEqual(executable.read_bytes(), b"old application")

    def test_installer_changed_after_exit_restarts_the_existing_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            _install_root, executable, installer, plan_path = self.make(Path(temporary))

            def run_command(command, **_kwargs):
                if "--quit-existing" in command:
                    installer.write_bytes(b"tampered")
                return subprocess.CompletedProcess([], 0)

            with patch("vram_radar.update_helper._wait_for_exit", return_value=True), \
                    patch("vram_radar.update_helper.subprocess.run", side_effect=run_command), \
                    patch("vram_radar.update_helper.subprocess.Popen") as restart:
                with self.assertRaisesRegex(ValueError, "before execution"):
                    run_update(plan_path)
            restart.assert_called_once()
            self.assertEqual(restart.call_args[0][0][0], str(executable.resolve()))

    def test_failed_rollback_still_starts_the_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            install_root, executable, _installer, plan_path = self.make(Path(temporary))
            original = Path.replace

            def replace(self, target):
                if Path(target) == install_root.resolve() and ".update-backup-" in Path(self).name:
                    raise PermissionError("still locked")
                return original(self, target)

            with patch("vram_radar.update_helper._wait_for_exit", return_value=True), \
                    patch("vram_radar.update_helper.subprocess.run",
                          side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 5)]), \
                    patch("vram_radar.update_helper.subprocess.Popen") as restart, \
                    patch("vram_radar.update_helper.time.sleep"), patch.object(Path, "replace", replace):
                with self.assertRaises(PermissionError):
                    run_update(plan_path)
            restart.assert_called_once()
            started = Path(restart.call_args[0][0][0])
            self.assertIn(".update-backup-", started.parent.name)
            self.assertEqual(started.read_bytes(), b"old application")


class FirstRunLanguageTests(unittest.TestCase):
    def test_locale_detection(self):
        self.assertEqual(system_ui_language("linux", {"LANG": "en_US.UTF-8"}), "en")
        self.assertEqual(system_ui_language("linux", {"LANG": "de_DE.UTF-8"}), "en")
        self.assertEqual(system_ui_language("linux", {"LANG": "zh_CN.UTF-8"}), "zh-CN")
        self.assertEqual(system_ui_language("linux", {"LANG": "C"}), "zh-CN")
        self.assertEqual(system_ui_language("linux", {}), "zh-CN")

    @unittest.skipUnless(sys.platform == "win32", "Windows UI language API")
    def test_windows_ui_language(self):
        import ctypes
        with patch.object(ctypes.windll.kernel32, "GetUserDefaultUILanguage", return_value=0x0409, create=True):
            self.assertEqual(system_ui_language("win32"), "en")
        with patch.object(ctypes.windll.kernel32, "GetUserDefaultUILanguage", return_value=0x0804, create=True):
            self.assertEqual(system_ui_language("win32"), "zh-CN")
        with patch.object(ctypes.windll.kernel32, "GetUserDefaultUILanguage", return_value=0x0404, create=True):
            self.assertEqual(system_ui_language("win32"), "zh-CN")

    def runtime(self, home: Path):
        from vram_radar import shell
        import logging
        try:
            with patch.object(shell, "system_ui_language", return_value="en"):
                return shell.build_runtime("default", home, automatic_import_enabled=False)[2]
        finally:
            for handler in list(logging.getLogger("vram_radar").handlers):
                handler.close()
                logging.getLogger("vram_radar").removeHandler(handler)

    def test_fresh_profile_follows_system_language_existing_choice_kept(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self.runtime(Path(home)).ui_language, "en")
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "config" / "profiles"
            folder.mkdir(parents=True)
            (folder / "default.toml").write_text('schema_version = 1\nid = "default"\nui_language = "zh-CN"\n',
                                                 encoding="utf-8")
            self.assertEqual(self.runtime(Path(home)).ui_language, "zh-CN")


class OrphanTemporaryTests(unittest.TestCase):
    def test_old_atomic_write_leftovers_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "default").mkdir()
            old = root / "default" / ".4090.json.c19e303f978a4c0e98823c138c5a2e1b.tmp"
            fresh = root / "default" / ".A100.json.0123456789abcdef0123456789abcdef.tmp"
            unrelated = root / "default" / ".notes.tmp"
            for path in (old, fresh, unrelated):
                path.write_text("", encoding="utf-8")
            past = time.time() - 3 * 86400
            os.utime(old, (past, past))
            os.utime(unrelated, (past, past))
            self.assertEqual(prune_orphan_temporaries(root), 1)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())


class MacMenuRetryTests(unittest.TestCase):
    def test_failed_menu_update_is_retried_next_tick(self):
        from vram_radar import usage_surface
        surface = usage_surface.CodexUsageSurface(Mock(), lambda: {}, language=lambda: "en",
                                                  open_settings=lambda: None, refresh=lambda: None,
                                                  disable=lambda: None, quit_application=lambda: None)
        item = Mock()
        item.button.return_value.setTitle_.side_effect = [RuntimeError("item gone"), None]
        status_bar = Mock()
        status_bar.systemStatusBar.return_value.statusItemWithLength_.return_value = item
        surface._mac_classes = (Mock(), Mock(), status_bar, 0)
        rows = [{"label": "5h", "value": "30%", "countdown": "2h", "detail": "5h 30%"}]
        with patch.object(usage_surface, "quota_lines", return_value=rows), patch("logging.Logger.warning"):
            surface._mac_tick()
            self.assertIsNone(surface._last_signature)
            surface._mac_tick()
        self.assertEqual(item.button.return_value.setTitle_.call_count, 2)
        item.setMenu_.assert_called_once()
        self.assertIsNotNone(surface._last_signature)


class DocsConsistencyTests(unittest.TestCase):
    def test_usage_doc_matches_current_menu_and_error_policy(self):
        text = (ROOT / "docs" / "subscription-usage.md").read_text(encoding="utf-8")
        self.assertNotIn("**Text only**", text)
        self.assertIn("**Icons**", text)
        self.assertIn("marked stale", text)


if __name__ == "__main__":
    unittest.main()