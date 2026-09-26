import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from vram_radar.usage_monitor import (CodexUsageMonitor, UsageError, find_codex,
                                   parse_usage, read_usage)
from vram_radar.models import ConfigError, Profile
from vram_radar.shell import AppApi
from vram_radar.service import DashboardService
from vram_radar.storage import ProfileStore, SnapshotCache, storage_paths

ACCOUNT = {"account": {"type": "chatgpt", "planType": "pro", "email": "private@example.test"}}
WINDOW = {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 1800000000}


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("worker did not settle")
        time.sleep(0.01)


class UsageParserTests(unittest.TestCase):
    def test_multi_bucket_preferred_over_legacy_and_identity_not_exposed(self):
        result = parse_usage(ACCOUNT, {"rateLimitsByLimitId": {
            "other": {"limitName": "Other quota", "secondary": {"usedPercent": 101}},
            "codex": {"primary": WINDOW, "secondary": {"usedPercent": 0, "windowDurationMins": 10080}},
        }, "rateLimits": {"primary": {"usedPercent": 99}}})
        self.assertEqual([w["remaining_percent"] for w in result["windows"]], [63, 100, 0])
        self.assertEqual(result["windows"][0]["window_minutes"], 300)
        self.assertNotIn("private@", json.dumps(result))

    def test_legacy_fallback_and_missing_fields_are_unknown(self):
        result = parse_usage(ACCOUNT, {"rateLimitsByLimitId": {}, "rateLimits": {"primary": {}}})
        self.assertIsNone(result["windows"][0]["remaining_percent"])
        self.assertIsNone(result["windows"][0]["window_minutes"])
        self.assertIsNone(result["windows"][0]["resets_at"])
        self.assertEqual(parse_usage(ACCOUNT, {})["windows"], [])

    def test_invalid_numbers_never_become_full_quota(self):
        for value in [None, True, "20", float("nan"), float("inf"), 10 ** 1000]:
            with self.subTest(value=str(value)[:25]):
                window = parse_usage(ACCOUNT, {"rateLimits": {"primary": {
                    "usedPercent": value, "windowDurationMins": value, "resetsAt": value}}})["windows"][0]
                self.assertEqual([window[k] for k in ("remaining_percent", "window_minutes", "resets_at")], [None] * 3)

    def test_clamps_percent_and_preserves_actual_duration(self):
        window = parse_usage(ACCOUNT, {"rateLimits": {"primary": {
            "usedPercent": -10, "windowDurationMins": 60, "resetsAt": -1}}})["windows"][0]
        self.assertEqual(window["remaining_percent"], 100)
        self.assertEqual(window["window_minutes"], 60)
        self.assertIsNone(window["resets_at"])

    def test_login_and_api_key_accounts_are_explicit(self):
        for account, code in [({}, "login_required"), ({"account": {"type": "apiKey"}}, "unsupported_account")]:
            with self.assertRaisesRegex(UsageError, code):
                parse_usage(account, {})


class UsageProcessTests(unittest.TestCase):
    def run_fixture(self, mode="ok", timeout=2, cancel=None):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            script = runtime / "fixture.py"
            script.write_text('''import sys, json, time, os
mode = sys.argv[1]
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    assert method in ('initialize', 'initialized', 'account/read', 'account/rateLimits/read')
    assert 'OPENAI_API_KEY' not in os.environ
    if method == 'initialized': continue
    if mode == 'timeout': time.sleep(10)
    if mode == 'oversize': print('x' * 1048577, flush=True); continue
    if mode == 'bad': print('raw-private-error', flush=True); continue
    if mode == 'exit': sys.exit(1)
    if mode == 'rpc_error':
        print(json.dumps({'id': request['id'], 'error': {'message': 'private-server-error'}}), flush=True)
        continue
    if method == 'account/read':
        assert request['params'] == {'refreshToken': False}
        result = {'account': None if mode == 'logged_out' else {'type': 'chatgpt', 'planType': 'pro'}}
    elif method == 'account/rateLimits/read':
        result = {'rateLimits': {'primary': {'usedPercent': 37, 'windowDurationMins': 300}}}
    else: result = {}
    print(json.dumps({'method': 'notice', 'params': {'private': 'ignored'}}), flush=True)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''', encoding="utf-8")
            created = []
            real_popen = subprocess.Popen

            def launch(*args, **kwargs):
                self.assertNotIn("shell", kwargs)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                process = real_popen(*args, **kwargs)
                created.append(process)
                return process

            with patch("vram_radar.usage_monitor.subprocess.Popen", side_effect=launch), patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-key"}):
                try:
                    return read_usage(runtime, "", cancel or threading.Event(), timeout=timeout,
                                      command=[sys.executable, "-u", str(script), mode])
                finally:
                    self.assertTrue(all(process.poll() is not None for process in created))

    def test_real_subprocess_handshake_reads_quota_and_reaps_child(self):
        result = self.run_fixture()
        self.assertEqual(result["windows"][0]["remaining_percent"], 63)

    def test_timeout_is_bounded_and_child_reaped(self):
        from vram_radar.usage_monitor import _stop_process
        start = time.monotonic()
        cleanup_started = []

        def stop(process):
            cleanup_started.append(time.monotonic())
            _stop_process(process)

        with patch("vram_radar.usage_monitor._stop_process", side_effect=stop):
            with self.assertRaisesRegex(UsageError, "timeout"):
                self.run_fixture("timeout", timeout=0.2)
        # Verify the RPC deadline separately from OS process-tree cleanup.
        # Windows taskkill itself has a 3 s budget; fixture cleanup must not be
        # included in the same 3 s deadline. run_fixture also checks real reaping.
        self.assertEqual(len(cleanup_started), 1)
        self.assertLess(cleanup_started[0] - start, 3)
        self.assertLess(time.monotonic() - cleanup_started[0], 10)

    def test_malformed_rpc_and_eof_errors_never_echo_raw_output(self):
        for mode, code in [("bad", "invalid_response"), ("rpc_error", "service_error"),
                           ("exit", "disconnected"), ("oversize", "disconnected"),
                           ("logged_out", "login_required")]:
            with self.subTest(mode=mode), self.assertRaisesRegex(UsageError, code):
                self.run_fixture(mode)

    def test_exited_posix_group_permission_race_still_reaps_and_closes(self):
        from unittest.mock import MagicMock
        from vram_radar.usage_monitor import _stop_process
        process = MagicMock()
        process.poll.return_value = 1
        with patch("vram_radar.usage_monitor.os.name", "posix"), \
             patch("vram_radar.usage_monitor.signal.SIGKILL", 9, create=True), \
             patch("vram_radar.usage_monitor.os.killpg", create=True, side_effect=PermissionError):
            _stop_process(process)
        process.wait.assert_called_once_with(timeout=2)
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_live_posix_group_permission_denial_is_not_silenced(self):
        from unittest.mock import MagicMock
        from vram_radar.usage_monitor import _stop_process
        process = MagicMock()
        process.poll.return_value = None
        with patch("vram_radar.usage_monitor.os.name", "posix"), \
             patch("vram_radar.usage_monitor.signal.SIGKILL", 9, create=True), \
             patch("vram_radar.usage_monitor.os.killpg", create=True, side_effect=PermissionError), \
             self.assertRaises(PermissionError):
            _stop_process(process)

    def test_cancellation_interrupts_pending_read(self):
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(UsageError, "cancelled"):
                self.run_fixture("timeout", cancel=cancel)
        finally:
            timer.cancel()

    def test_windows_override_does_not_allow_shell_shims(self):
        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "codex.cmd"
            shim.write_text("", encoding="utf-8")
            with patch("vram_radar.usage_monitor.sys.platform", "win32"):
                with self.assertRaisesRegex(UsageError, "invalid_executable"):
                    find_codex(str(shim))

    def test_mac_gui_install_is_found_without_path(self):
        target = Path("/Applications/Codex.app/Contents/Resources/codex")
        with patch("vram_radar.usage_monitor.sys.platform", "darwin"), patch("shutil.which", return_value=None), \
             patch.object(Path, "is_file", lambda value: value == target), patch("os.access", return_value=True):
            self.assertEqual(find_codex(), target.resolve())

    def test_windows_versioned_desktop_install(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "OpenAI" / "Codex" / "bin" / "1.2" / "codex.exe"
            target.parent.mkdir(parents=True)
            target.touch()
            with patch("vram_radar.usage_monitor.sys.platform", "win32"), patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"LOCALAPPDATA": directory, "APPDATA": directory}):
                self.assertEqual(find_codex(), target.resolve())

    def test_auto_detection_follows_desktop_upgrade_without_saved_binary_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "OpenAI" / "Codex" / "bin" / "1.0" / "codex.exe"
            new = root / "OpenAI" / "Codex" / "bin" / "2.0" / "codex.exe"
            old.parent.mkdir(parents=True)
            old.touch()
            with patch("vram_radar.usage_monitor.sys.platform", "win32"), patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"LOCALAPPDATA": directory, "APPDATA": directory}):
                self.assertEqual(find_codex(), old.resolve())
                new.parent.mkdir(parents=True)
                new.touch()
                os.utime(old, (1, 1))
                self.assertEqual(find_codex(), new.resolve())

    def test_custom_npm_prefix_native_payload_found_from_cmd_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "codex" / "codex.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            with patch("vram_radar.usage_monitor.sys.platform", "win32"), \
                 patch("shutil.which", side_effect=lambda name: str(root / 'codex.cmd') if name == 'codex.cmd' else None), \
                 patch.dict(os.environ, {"LOCALAPPDATA": directory, "APPDATA": directory}):
                self.assertEqual(find_codex(), native.resolve())

    def test_vscode_codex_is_detected_without_desktop_or_cli_install(self):
        for operating_system, target, binary in [("win32", "windows-x86_64", "codex.exe"),
                                                  ("darwin", "macos-aarch64", "codex")]:
            with self.subTest(platform=operating_system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                native = root / f".vscode/extensions/openai.chatgpt-1.0/bin/{target}/{binary}"
                native.parent.mkdir(parents=True)
                native.touch()
                real_is_file = Path.is_file
                with patch("vram_radar.usage_monitor.sys.platform", operating_system), patch("platform.machine", return_value="arm64" if operating_system == "darwin" else "AMD64"), \
                     patch("shutil.which", return_value=None), patch.object(Path, "home", return_value=root), \
                     patch.object(Path, "is_file", lambda p: p == native and real_is_file(p)), patch("os.access", return_value=True), \
                     patch.dict(os.environ, {"LOCALAPPDATA": directory, "APPDATA": directory}):
                    self.assertEqual(find_codex(), native.resolve())

    def test_inaccessible_install_location_does_not_block_path_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "codex.exe"
            target.touch()
            with patch("vram_radar.usage_monitor.sys.platform", "win32"), patch.object(Path, "glob", side_effect=PermissionError), \
                 patch("shutil.which", return_value=str(target)):
                self.assertEqual(find_codex(), target.resolve())


class UsageMonitorTests(unittest.TestCase):
    def test_install_and_login_recover_automatically_without_manual_refresh(self):
        fetch = Mock(side_effect=[UsageError("not_installed"), UsageError("login_required"),
                                 {"windows": [WINDOW], "fetched_at": time.time()}])
        with patch("vram_radar.usage_monitor.SETUP_RETRY_SECONDS", 0.03):
            monitor = CodexUsageMonitor(Path("unused"), fetch=fetch, interval=300)
            try:
                monitor.configure(True, "")
                until(lambda: monitor.snapshot()["state"] == "ready")
                self.assertEqual(fetch.call_count, 3)
                self.assertTrue(all(call.args[1] == "" for call in fetch.call_args_list))
                self.assertGreater(monitor.next_read - time.monotonic(), 290)
            finally:
                monitor.close()

    def test_disabled_does_no_work_and_periodic_refresh_is_independent(self):
        fetch = Mock(return_value={"windows": [], "fetched_at": time.time()})
        monitor = CodexUsageMonitor(Path("unused"), fetch=fetch, interval=0.03)
        try:
            self.assertEqual(monitor.snapshot(force=True)["state"], "disabled")
            fetch.assert_not_called()
            monitor.configure(True, "")
            until(lambda: fetch.call_count >= 2)
            monitor.configure(False, "")
            count = fetch.call_count
            time.sleep(0.07)
            self.assertEqual(fetch.call_count, count)
        finally:
            monitor.close()

    def test_disable_discards_inflight_result_and_cancels(self):
        started, stopped = threading.Event(), threading.Event()

        def fetch(runtime, executable, cancel):
            started.set()
            cancel.wait(2)
            stopped.set()
            return {"windows": [{"remaining_percent": 100}]}

        monitor = CodexUsageMonitor(Path("unused"), fetch=fetch)
        try:
            monitor.configure(True, "old")
            self.assertTrue(started.wait(2))
            monitor.configure(False, "")
            self.assertTrue(stopped.wait(2))
            self.assertEqual(monitor.snapshot()["windows"], [])
            self.assertEqual(monitor.snapshot()["state"], "disabled")
        finally:
            monitor.close()

    def test_path_change_discards_old_account_and_retries_new_path(self):
        entered = threading.Event()

        def fetch(runtime, executable, cancel):
            if executable == "old":
                entered.set()
                cancel.wait(2)
            return {"windows": [], "plan": executable}

        monitor = CodexUsageMonitor(Path("unused"), fetch=fetch)
        try:
            monitor.configure(True, "old")
            self.assertTrue(entered.wait(2))
            monitor.configure(True, "new")
            until(lambda: monitor.snapshot().get("plan") == "new")
        finally:
            monitor.close()

    def test_failure_clears_old_quota_and_unknown_exceptions_are_redacted(self):
        fetch = Mock(side_effect=[{"windows": [WINDOW], "fetched_at": time.time()}, RuntimeError("private")])
        monitor = CodexUsageMonitor(Path("unused"), fetch=fetch, interval=0.05)
        try:
            monitor.configure(True, "")
            until(lambda: monitor.snapshot()["state"] == "error")
            self.assertEqual(monitor.snapshot()["windows"], [])
            self.assertNotIn("private", json.dumps(monitor.snapshot()))
        finally:
            monitor.close()


class UsageSettingsTests(unittest.TestCase):
    def test_saved_enable_switch_restores_monitor_before_webview_opens(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            store = ProfileStore(paths)
            profile = Profile.from_dict({**Profile.empty("test").to_dict(), "codex_usage_enabled": True})
            store.save(profile)
            with patch("vram_radar.shell.CodexUsageMonitor") as monitor:
                api = AppApi(store.load("test"), store, paths, Mock(), automatic_import_enabled=False)
                monitor.return_value.configure.assert_called_once_with(True, "")
                self.assertEqual(api.profile.codex_executable, "")

    def test_profile_defaults_roundtrip_and_invalid_types(self):
        raw = Profile.empty("test").to_dict()
        self.assertFalse(raw["codex_usage_enabled"])
        for key, value in [("codex_usage_enabled", "yes"), ("codex_executable", "bad\npath")]:
            with self.assertRaises(ConfigError):
                Profile.from_dict({**raw, key: value})
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(storage_paths(Path(directory)))
            profile = Profile.from_dict({**raw, "codex_usage_enabled": True, "codex_executable": "/Applications/Codex.app/Contents/Resources/codex"})
            store.save(profile)
            self.assertEqual(store.load("test"), profile)

    def test_independent_save_does_not_query_servers_and_checks_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            profile = Profile.empty("test")
            service = DashboardService(profile, SnapshotCache(paths, "test"))
            api = AppApi(profile, ProfileStore(paths), paths, service, automatic_import_enabled=False)
            with patch.object(api._codex_usage, "configure") as configure, patch.object(service, "request_refresh") as refresh:
                result = api.save_codex_usage_settings(True, "", 0)
                self.assertTrue(result["ok"])
                self.assertTrue(api.store.load("test").codex_usage_enabled)
                self.assertTrue(service.profile.codex_usage_enabled)
                refresh.assert_not_called()
                self.assertEqual(api.save_codex_usage_settings(False, "", 0)["code"], "profile_changed")
                self.assertTrue(api.profile.codex_usage_enabled)
                configure.assert_called_with(True, "")
                self.assertTrue(api.save_codex_usage_settings(False, "", 1)["ok"])
            api._codex_usage.close()

    def test_failed_save_does_not_enable_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            profile = Profile.empty("test")
            api = AppApi(profile, Mock(), paths, Mock(), automatic_import_enabled=False)
            api.store.save.side_effect = OSError("synthetic write failure")
            with patch.object(api._codex_usage, "configure") as configure:
                self.assertFalse(api.save_codex_usage_settings(True, "", 0)["ok"])
                configure.assert_not_called()
                self.assertFalse(api.profile.codex_usage_enabled)

    def test_display_choices_persist_without_refreshing_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            profile = Profile.empty("test")
            service = DashboardService(profile, SnapshotCache(paths, "test"))
            api = AppApi(profile, ProfileStore(paths), paths, service, automatic_import_enabled=False)
            with patch.object(service, "request_refresh") as refresh:
                self.assertTrue(api.save_codex_display("codex_show_disks", True)["ok"])
                self.assertTrue(api.save_codex_display("codex_time_format", "hours")["ok"])
                saved = api.store.load("test")
                self.assertTrue(saved.codex_show_disks)
                self.assertEqual(saved.codex_time_format, "hours")
                self.assertFalse(api.save_codex_display("codex_time_format", "invalid")["ok"])
                self.assertFalse(api.save_codex_display("codex_show_disks", "yes")["ok"])
                refresh.assert_not_called()
                self.assertTrue(api.set_navigator_size(320, 560)["ok"])
                resized = api.store.load("test")
                self.assertEqual((resized.navigator_width, resized.navigator_height), (320, 560))
                self.assertFalse(api.set_navigator_size(-1, 560)["ok"])
                self.assertFalse(api.set_navigator_size(True, 560)["ok"])
            with patch.object(api.store, "save", side_effect=OSError("write failed")):
                self.assertFalse(api.save_codex_display("codex_show_disks", False)["ok"])
                self.assertTrue(api.profile.codex_show_disks)
            api._codex_usage.close()
