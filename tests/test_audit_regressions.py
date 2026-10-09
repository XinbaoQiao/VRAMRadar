"""Regression tests for the 2026-10-02 global audit (one test per bug)."""
from __future__ import annotations

import ctypes
import datetime
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from vram_radar.models import Profile
from vram_radar.providers import base, deepseek, deepseek_balance, grok, grok_usage, kimi_usage
from vram_radar.storage import ProfileStore, atomic_write_text, storage_paths

CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def pointer_cache_size():
    cache = getattr(ctypes, "_pointer_type_cache", None)
    return None if cache is None else len(cache)


@unittest.skipUnless(sys.platform == "win32", "Win32 calls")
class CtypesTypeLeakTests(unittest.TestCase):
    """A ctypes class made per call and given to POINTER() is cached forever:
    the 1 s strip tick leaked ~20 MB/h (windows_surface_obscured)."""

    def assert_no_growth(self, call, repeat=25):
        if pointer_cache_size() is None:
            self.skipTest("ctypes has no pointer type cache")
        call()
        before = pointer_cache_size()
        for _ in range(repeat):
            call()
        self.assertEqual(pointer_cache_size(), before)

    def test_strip_obscured_check(self):
        from vram_radar.usage_surface import windows_surface_obscured
        self.assert_no_growth(lambda: windows_surface_obscured(0, True))

    def test_screen_capture(self):
        from vram_radar.usage_surface import capture_screen
        self.assert_no_growth(lambda: capture_screen((0, 0, 2, 2)))

    def test_process_list_and_file_version(self):
        self.assert_no_growth(base.list_processes, 5)
        self.assert_no_growth(lambda: base.file_version(Path(sys.executable)), 5)

    def test_local_gpu_types_are_shared(self):
        from vram_radar import local_gpu
        self.assertIs(local_gpu._win_types()["Item"], local_gpu._win_types()["Item"])

        def dxgi():
            try:
                local_gpu._dxgi_adapters()
            except local_gpu.BackendUnavailable:
                pass
        self.assert_no_growth(dxgi, 5)


class ProfileRecoveryTests(unittest.TestCase):
    """An invalid profile made the app exit silently at startup (exit 2)."""

    def store_with(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        paths = storage_paths(Path(directory.name))
        (paths.config / "profiles").mkdir(parents=True)
        path = paths.config / "profiles" / "default.toml"
        path.write_text(text, encoding="utf-8")
        return ProfileStore(paths), path

    def test_bad_value_is_reset_and_other_settings_kept(self):
        raw = {**Profile.empty("default").to_dict(), "usage_labels": "icons",
               "usage_session_consent": {"grok": True, "kimi": True}}
        import tomli_w
        text = tomli_w.dumps(raw).replace('ui_language = "zh-CN"', 'ui_language = "ja"')
        store, path = self.store_with(text)
        profile, recovery = store.load_or_recover("default")
        self.assertEqual(profile.usage_labels, "icons")
        self.assertEqual(profile.usage_session_consent, ("grok", "kimi"))
        self.assertEqual(profile.ui_language, "zh-CN")
        self.assertEqual(recovery["dropped"], ["ui_language"])
        backup = Path(recovery["backup"])
        self.assertEqual(backup.read_text(encoding="utf-8"), text)   # original copied unchanged
        self.assertEqual(path.read_text(encoding="utf-8"), text)     # nothing rewritten on load
        self.assertEqual(store.list_profiles(), ["default"])          # backup is not a profile

    def test_syntax_error_starts_with_defaults(self):
        store, _ = self.store_with('ui_language = "en\n')
        profile, recovery = store.load_or_recover("default")
        self.assertEqual(profile, Profile.empty("default"))
        self.assertFalse(recovery["salvaged"])
        self.assertTrue(Path(recovery["backup"]).is_file())

    def test_valid_profile_untouched(self):
        import tomli_w
        store, path = self.store_with(tomli_w.dumps(Profile.empty("default").to_dict()))
        profile, recovery = store.load_or_recover("default")
        self.assertIsNone(recovery)
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_build_runtime_survives_and_notices(self):
        from vram_radar.shell import build_runtime, profile_recovery_notice
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "config" / "profiles"
            folder.mkdir(parents=True)
            (folder / "default.toml").write_text('schema_version = 1\nid = "default"\nui_language = "fr"\n',
                                                 encoding="utf-8")
            _, _, profile, service = build_runtime("default", Path(home), automatic_import_enabled=False)
            self.assertEqual(profile.id, "default")
            codes = [n.get("code") for n in service.snapshot().get("notices", [])]
            self.assertIn("profile_recovered", codes)
            import logging
            for handler in list(logging.getLogger("vram_radar").handlers):
                handler.close()
                logging.getLogger("vram_radar").removeHandler(handler)
        english = profile_recovery_notice({"backup": "C:/x", "dropped": ["ui_language"], "salvaged": True}, "en")
        chinese = profile_recovery_notice({"backup": "C:/x", "dropped": [], "salvaged": False}, "zh-CN")
        self.assertIsNone(CJK.search(english["message"]))
        self.assertIsNotNone(CJK.search(chinese["message"]))


class AtomicWriteTests(unittest.TestCase):
    def test_retries_a_briefly_locked_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "a.toml"
            real = Path.replace
            calls = {"n": 0}

            def flaky(self, other):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise PermissionError("in use")
                return real(self, other)
            with patch.object(Path, "replace", flaky):
                atomic_write_text(target, "x = 1\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["a.toml"])

    def test_gives_up_eventually(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Path, "replace", side_effect=PermissionError("locked")), \
                    patch("vram_radar.storage.REPLACE_RETRY_SECONDS", 0):
                with self.assertRaises(PermissionError):
                    atomic_write_text(Path(directory) / "a.toml", "x")
            self.assertEqual(list(Path(directory).iterdir()), [])


class HttpFailureTests(unittest.TestCase):
    """http.client.HTTPException is not an OSError: a truncated reply escaped
    and failed the whole provider probe ("Probe failed", shown as missing)."""

    def broken_opener(self):
        opener = Mock()
        opener.open.side_effect = http.client.IncompleteRead(b"")
        return opener

    def test_grok_kimi_deepseek_requests(self):
        with patch.object(grok_usage, "_OPENER", self.broken_opener()):
            self.assertEqual(grok_usage._post_usage("https://example.invalid", "t"), {"status": "error"})
            self.assertEqual(kimi_usage._post("GetSubscription", "t", "https://example.invalid"), ("error", None))
        with patch.object(deepseek_balance, "_OPENER", self.broken_opener()):
            self.assertEqual(deepseek_balance._get("https://example.invalid", {}), (None, None))

    def test_caches_back_off_when_fetch_raises(self):
        cache = grok_usage.UsageCache(Mock(side_effect=RuntimeError("x")), clock=lambda: 1000.0,
                                      signature=lambda _p: (1, 1))
        self.assertEqual(cache.get(Path("."))["status"], "error")
        self.assertGreater(cache.next_at, 1000.0)
        balance = deepseek_balance.BalanceCache(Mock(side_effect=http.client.BadStatusLine("x")), clock=lambda: 5.0)
        self.assertEqual(balance.get(Path("."), (1, 1))["status"], "network")

    def test_grok_session_error_does_not_raise(self):
        # grok.py used LOG without defining it: the error path itself raised NameError.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sand-secrets.json").write_text("{}", encoding="utf-8")
            with patch.object(grok.CACHE, "get", side_effect=RuntimeError("boom")):
                self.assertEqual(grok._session_usage([folder]), {"status": "error"})


class StaleSessionValueTests(unittest.TestCase):
    def test_last_good_is_not_served_after_its_reset(self):
        now = {"t": 1000.0}
        replies = iter([{"status": "ok", "used_percent": 80.0, "reset_at": 1500.0}, {"status": "error"},
                        {"status": "error"}])
        cache = grok_usage.UsageCache(lambda *_: next(replies), interval=10, clock=lambda: now["t"],
                                      signature=lambda _p: (1, 1))
        cache.get(Path("."))
        now["t"] = 1200.0
        view = cache.get(Path("."))
        self.assertEqual((view["status"], view.get("stale_error")), ("ok", "error"))   # before reset: last value
        now["t"] = 2000.0
        cache.next_at = 0
        self.assertNotEqual(cache.get(Path(".")).get("status"), "ok")                   # after reset: not 80 % anymore

    def test_grok_stale_reading_says_when_it_was_read(self):
        state = {"facts": [], "headline": {}, "subline": {}}
        old = time.time() - 3 * 3600
        out = grok._apply_session(state, {"status": "ok", "used_percent": 40.0, "reset_at": time.time() + 86400,
                                          "stale_error": "error", "fetched_at": old})
        self.assertTrue(out["stale"])
        self.assertTrue(out["subline"]["en"].startswith("read "))
        fresh = grok._apply_session({"facts": [], "headline": {}, "subline": {}},
                                    {"status": "ok", "used_percent": 40.0, "reset_at": None})
        self.assertFalse(fresh.get("stale", False))


class TimestampTests(unittest.TestCase):
    UTC = datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc).timestamp()

    def test_offsetless_timestamps_are_utc(self):
        for value in ("2026-10-05T00:00:00", "2026-10-05T00:00:00Z", "2026-10-05T09:00:00+09:00"):
            self.assertEqual(grok_usage._reset_ms(value), self.UTC, value)
            self.assertEqual(kimi_usage._as_time(value), self.UTC, value)
        self.assertAlmostEqual(grok_usage._reset_ms("2026-10-05T00:00:00.123456789Z"), self.UTC + 0.123456, places=3)
        self.assertEqual(grok_usage._reset_ms({"seconds": str(int(self.UTC))}), self.UTC)

    def test_non_finite_or_absurd_times_are_rejected(self):
        for value in ("inf", float("inf"), "nan", 1e300, -5, True):
            self.assertIsNone(kimi_usage._as_time(value), value)
        self.assertIsNone(grok_usage._reset_ms({"seconds": "inf"}))
        self.assertIsNone(grok_usage._reset_ms({"seconds": True}))


class FormatTokensTests(unittest.TestCase):
    def test_unit_switch_after_rounding(self):
        cases = {999: "999", 999.6: "1k", 1500: "1.5k", 999_940: "999.9k", 999_950: "1M",
                 999_999_999: "1B", 5e9: "5B", 0: "0"}
        for value, text in cases.items():
            self.assertEqual(base.format_tokens(value), text, value)


class DeepSeekTodayTests(unittest.TestCase):
    def test_today_rolls_over_at_midnight_without_file_changes(self):
        deepseek._cache.clear()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            row = {"record": {"rows": {"tokenUsage": {"val": {"totals": {"outputTokens": 120}}},
                                       "sessionStats": {"val": {"turns": 1}},
                                       "sessionListMetadata": {"val": {"lastPromptAt": time.time() * 1000}}}}}
            (folder / "s.json").write_text(json.dumps(row), encoding="utf-8")
            self.assertEqual(deepseek.summarize_sessions(folder)["today_tokens"], 120)
            tomorrow = time.localtime(time.time() + 86400 * 2)
            with patch("time.localtime", return_value=tomorrow):
                self.assertEqual(deepseek.summarize_sessions(folder)["today_tokens"], 0)
        deepseek._cache.clear()


class CodexMonitorTransientTests(unittest.TestCase):
    def test_timeout_keeps_last_quota_and_retries_sooner(self):
        from vram_radar.usage_monitor import CodexUsageMonitor, UsageError
        good = {"windows": [{"id": "codex:primary", "remaining_percent": 60, "window_minutes": 300,
                             "resets_at": time.time() + 3600}], "fetched_at": time.time()}
        retry_entered = threading.Event()
        def response(_runtime, _executable, cancel):
            if fetch.call_count == 1:
                return good
            if fetch.call_count == 2:
                raise UsageError("timeout")
            # Entering this next fetch proves the timeout was committed and
            # retried. Keep it pending until close() so a delayed observer
            # cannot exhaust a finite fixture or sample a later good result.
            retry_entered.set()
            cancel.wait()
            return good
        fetch = Mock(side_effect=response)
        monitor = CodexUsageMonitor(Path("unused"), fetch=fetch, interval=0.05)
        seen = []
        try:
            monitor.configure(True, "")
            self.assertTrue(retry_entered.wait(3), "timeout was not retried")
            seen.append(monitor.snapshot())
        finally:
            monitor.close()
        state = seen[0]
        self.assertNotEqual(state["state"], "error")
        self.assertEqual(state["refresh_error"], "timeout")
        self.assertEqual(state["windows"][0]["remaining_percent"], 60)

    def test_missing_codex_says_so(self):
        from vram_radar.usage_surface import widget_reading
        state = {"enabled": True, "state": "error", "code": "not_installed", "windows": []}
        self.assertEqual(widget_reading(state, language="en")["countdown"], "Missing")
        self.assertIsNotNone(CJK.search(widget_reading(state)["countdown"]))


class SurfaceLogicTests(unittest.TestCase):
    def surface(self, providers=None, snapshot=None):
        from vram_radar.usage_surface import CodexUsageSurface
        return CodexUsageSurface(Mock(), snapshot or (lambda: {}), language=lambda: "zh-CN",
                                 open_settings=lambda: None, refresh=lambda: None, disable=lambda: None,
                                 quit_application=lambda: None,
                                 providers=providers or (lambda: {"selected": ["codex"]}))

    def test_quick_second_toggle_starts_from_the_first(self):
        surface = self.surface()
        self.assertEqual(surface._current_selection(), ["codex"])
        first = surface._remember_selection(["codex", "grok"])
        self.assertEqual(surface._current_selection(), ["codex", "grok"])   # profile not saved yet
        self.assertEqual(surface._remember_selection(["codex", "grok", "kimi"]), first + 1)
        surface.SELECTION_MEMO_SECONDS = 0
        self.assertEqual(surface._current_selection(), ["codex"])           # memo expired: saved profile wins

    def test_mac_timer_callback_never_raises(self):
        surface = self.surface(snapshot=Mock(side_effect=RuntimeError("bad")))
        surface._mac_tick()
        surface._mac_tick()
        self.assertTrue(surface._mac_tick_failed)


@unittest.skipUnless(sys.platform == "win32", "screen reader runs on Windows only")
class ScreenWatcherRestartTests(unittest.TestCase):
    def test_stop_then_start_keeps_a_reader_running(self):
        from vram_radar.providers.screen_usage import ScreenUsageWatcher
        if os.environ.get("VRAM_RADAR_NO_SCREEN_READ"):
            self.skipTest("screen reading disabled")
        watcher = ScreenUsageWatcher(["no-such-app.exe"], interval=0.02)
        watcher.start()
        watcher.stop()
        watcher.start()      # immediately, while the old worker may still be exiting
        time.sleep(0.2)
        try:
            self.assertTrue(watcher._thread.is_alive())
            self.assertFalse(watcher._stop.is_set())
        finally:
            watcher.stop()


class UpdateStageCleanupTests(unittest.TestCase):
    def test_only_old_download_folders_are_removed(self):
        from vram_radar.updater import prune_stale_stages
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, other = root / ("a" * 32), root / ("b" * 32), root / "keep-me"
            for folder in (old, new, other):
                folder.mkdir()
                (folder / "x.part").write_bytes(b"1")
            stamp = time.time() - 10 * 86400
            for folder in (old, other):
                os.utime(folder, (stamp, stamp))
            self.assertEqual(prune_stale_stages(root), 1)
            self.assertEqual(sorted(p.name for p in root.iterdir()), sorted([new.name, other.name]))


@unittest.skipUnless(sys.platform == "win32", "System.Drawing")
class ThemeIconKeyTests(unittest.TestCase):
    def test_msix_logo_cache_follows_the_taskbar_theme(self):
        from vram_radar import ui_dialogs
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ui_dialogs, "icon_candidates", return_value=[]):
                for light in (True, False):
                    with patch.object(ui_dialogs, "taskbar_light", return_value=light):
                        ui_dialogs.icon_source(folder)
            keys = [k for k in ui_dialogs._SOURCES if k.startswith(folder)]
            self.assertEqual(sorted(keys), sorted([folder + "|light", folder + "|dark"]))


if __name__ == "__main__":
    unittest.main()
