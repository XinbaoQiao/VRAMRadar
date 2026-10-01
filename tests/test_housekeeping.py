"""Housekeeping: bounded disk/in-memory growth for a long-running app."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import Mock, patch

from vram_radar import housekeeping as hk
from vram_radar.storage import storage_paths

DAY = 86400.0


def touch(path: Path, when: float, size: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (when, when))
    os.utime(path.parent, (when, when))
    return path


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1_800_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def test_logs_keep_rotation_set_and_drop_strays(self):
        logs = self.root / "logs"
        for name in ("app.log", "app.log.1", "app.log.2", "app.log.3", "app.log.4", "app.log.9"):
            touch(logs / name, self.now)
        touch(logs / "old-debug.log", self.now - 40 * DAY)
        touch(logs / "new-debug.log", self.now - DAY)
        self.assertEqual(hk.prune_log_files(logs, now=self.now), 3)
        self.assertEqual(sorted(p.name for p in logs.iterdir()),
                         ["app.log", "app.log.1", "app.log.2", "app.log.3", "new-debug.log"])

    def test_invalid_profile_copies_keep_newest_five_per_profile(self):
        profiles = self.root / "profiles"
        for index in range(8):
            touch(profiles / f"default.toml.invalid-2026100{index}", self.now - (8 - index) * 60)
        touch(profiles / "other.toml.invalid-1", self.now)
        touch(profiles / "default.toml", self.now - DAY)
        self.assertEqual(hk.prune_invalid_profile_backups(profiles), 3)
        left = sorted(p.name for p in profiles.glob("default.toml.invalid-*"))
        self.assertEqual(left, [f"default.toml.invalid-2026100{i}" for i in range(3, 8)])
        self.assertTrue((profiles / "default.toml").exists())
        self.assertTrue((profiles / "other.toml.invalid-1").exists())

    def test_snapshots_of_removed_servers_expire_configured_never(self):
        folder = self.root / "cache" / "default"
        touch(folder / "GONE.json", self.now - 100 * DAY)
        touch(folder / "RECENT-GONE.json", self.now - 10 * DAY)
        touch(folder / "KEPT.json", self.now - 400 * DAY)
        self.assertEqual(hk.prune_snapshot_cache(folder, ["KEPT"], now=self.now), 1)
        self.assertEqual(sorted(p.stem for p in folder.iterdir()), ["KEPT", "RECENT-GONE"])

    def test_webview_folders_of_ended_runs_are_removed(self):
        cache = self.root / "cache"
        for name in ("100", "200", "300", "notes"):
            touch(cache / "webview" / name / "EBWebView" / "f", self.now, 10)
        removed = hk.prune_webview_sessions(cache, current_pid=300, alive=lambda pid: pid == 200)
        self.assertEqual(removed, 1)
        self.assertEqual(sorted(p.name for p in (cache / "webview").iterdir()), ["200", "300", "notes"])

    def test_pid_alive(self):
        self.assertTrue(hk.pid_alive(os.getpid()))
        self.assertFalse(hk.pid_alive(0))

    def test_maintenance_never_raises(self):
        paths = storage_paths(self.root)
        with patch.object(hk, "prune_log_files", side_effect=PermissionError("locked")):
            summary = hk.run_maintenance(paths, "default", now=self.now, alive=lambda pid: True)
        self.assertEqual(summary["logs"], "failed:PermissionError")
        self.assertEqual(summary["update_stages"], 0)
        self.assertIn("cache", summary["bytes"])


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "runtime" / "default.health.json"
        self.now = [100_000.0]

    def tearDown(self):
        self.tmp.cleanup()

    def dog(self):
        return hk.HealthWatchdog(self.state, clock=lambda: self.now[0], started_at=0.0)

    def test_needs_consecutive_strikes_uptime_and_rate_limit(self):
        dog = self.dog()
        high = {"private_bytes": 500 * 1024 * 1024}
        with patch.object(hk.LOG, "warning") as warning:
            self.assertIsNone(dog.check({"private_bytes": 100}))
            self.assertIsNone(dog.check(high))
            self.assertIsNone(dog.check(high))
            self.assertIn("private_bytes", dog.check(high))
            self.assertEqual(warning.call_count, 1)       # logged once per episode
        dog.record_restart()
        self.assertTrue(self.state.exists())
        # A new process (after the restart) must respect the persisted 6 h limit.
        self.now[0] += 3 * 3600
        again = hk.HealthWatchdog(self.state, clock=lambda: self.now[0], started_at=0.0)
        with patch.object(hk.LOG, "warning"):
            for _ in range(5):
                self.assertIsNone(again.check(high))
            self.now[0] += 4 * 3600
            self.assertIsNotNone(again.check(high))

    def test_no_restart_in_first_hour(self):
        dog = hk.HealthWatchdog(self.state, clock=lambda: self.now[0], started_at=self.now[0] - 60)
        with patch.object(hk.LOG, "warning"):
            for _ in range(5):
                self.assertIsNone(dog.check({"handles": 50_000}))

    def test_real_sample_is_well_below_limits(self):
        sample = hk.process_sample()
        self.assertIsNone(self.dog().over_limit(sample))
        if sys.platform == "win32":
            self.assertGreater(sample["private_bytes"], 0)
            self.assertGreater(sample["handles"], 0)


class HousekeeperTests(unittest.TestCase):
    def test_schedule_idle_gate_and_single_restart(self):
        now = [0.0]
        maintenance = Mock(return_value={"logs": 0})
        dog = Mock()
        dog.check.return_value = "private_bytes=1>0"
        idle = [False]
        restart = Mock(return_value=True)
        keeper = hk.Housekeeper(maintenance, watchdog=dog, sampler=lambda: {}, restart=restart,
                                is_idle=lambda: idle[0], clock=lambda: now[0])
        with patch.object(hk.LOG, "info"), patch.object(hk.LOG, "warning"):
            for minute in range(0, 3 * 24 * 60):
                now[0] = minute * 60.0
                if minute == 2000:
                    idle[0] = True
                keeper.step()
        self.assertEqual(maintenance.call_count, 3)       # 10 min after start, then daily
        restart.assert_called_once()                       # waited for idle, then only once
        dog.record_restart.assert_called_once()

    def test_failing_maintenance_is_contained(self):
        keeper = hk.Housekeeper(Mock(side_effect=RuntimeError("x")), first_maintenance=0, clock=lambda: 1.0)
        with patch.object(hk.LOG, "info") as info:
            keeper.step()
        self.assertIn("failed", info.call_args[0][0])


class BoundedMemoryTests(unittest.TestCase):
    def test_icon_cache_is_bounded_and_boxes_reset(self):
        from vram_radar import ui_dialogs
        saved = (dict(ui_dialogs._ICONS), dict(ui_dialogs._SOURCES), dict(ui_dialogs._BOXES))
        try:
            ui_dialogs._ICONS.clear(); ui_dialogs._SOURCES.clear(); ui_dialogs._BOXES.clear()
            for index in range(1000):
                ui_dialogs._remember(ui_dialogs._ICONS, ("glyph", "?", index, (1, 2, 3)), object())
            self.assertEqual(len(ui_dialogs._ICONS), ui_dialogs.ICON_CACHE_LIMIT)
            ui_dialogs._BOXES[1] = (0, 0, 1, 1)
            for index in range(ui_dialogs.ICON_CACHE_LIMIT + 1):
                ui_dialogs._remember(ui_dialogs._SOURCES, f"s{index}", object())
            self.assertEqual(ui_dialogs._BOXES, {})
        finally:
            for cache, old in zip((ui_dialogs._ICONS, ui_dialogs._SOURCES, ui_dialogs._BOXES), saved):
                cache.clear(); cache.update(old)

    def test_task_absence_markers_are_pruned(self):
        from vram_radar.shell import prune_missing_markers
        markers = {("A", "t1"): "m", ("A", "t2"): "m", ("B", "t1"): "m"}
        self.assertEqual(prune_missing_markers(markers, "A", {"t1": {}}), 1)
        self.assertEqual(set(markers), {("A", "t1"), ("B", "t1")})

    def test_webview_profile_lives_in_our_cache_on_windows(self):
        from vram_radar import shell
        with patch.object(shell.sys, "platform", "win32"):
            options = shell.webview_start_options(False, Path("icon.png"), Path("C:/profile/cache/webview/42"))
        self.assertTrue(options["private_mode"])
        self.assertEqual(Path(options["storage_path"]), Path("C:/profile/cache/webview/42"))
        self.assertEqual(hk.webview_storage_path(Path("c"), 42), Path("c") / "webview" / "42")


class LongRunSoakTests(unittest.TestCase):
    """120 simulated days of logs, downloads, crashes and caches."""

    def test_disk_usage_stays_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            for folder in (paths.logs, paths.cache, paths.config / "profiles", paths.runtime):
                folder.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("vram_radar.soak")
            logger.propagate = False
            handler = RotatingFileHandler(paths.logs / "app.log", maxBytes=hk.LOG_MAX_BYTES,
                                          backupCount=hk.LOG_BACKUP_COUNT, encoding="utf-8")
            logger.addHandler(handler)
            start = time.time() - 130 * DAY
            peak = 0
            line = "WARNING server=X code=ssh_timeout " + "y" * 110
            try:
                for day in range(120):
                    now = start + day * DAY
                    for _ in range(24 * 40):                       # ~6 KB/h of log text
                        logger.warning(line)
                    for index in range(3):                         # update checks/downloads
                        touch(paths.cache / "updates" / f"{day:08x}{index:024x}" / "VRAMRadar-Setup.exe.part",
                              now, 50_000)
                    for index in range(4):                         # killed mid-write
                        touch(paths.cache / "default" / f".S{index}.json.{day:016x}{index:016x}.tmp", now)
                    touch(paths.config / "profiles" / f"default.toml.invalid-{day:04d}", now, 2_000)
                    touch(paths.cache / "default" / f"REMOVED-{day}.json", now, 5_000)
                    touch(paths.cache / "default" / "LIVE.json", now, 5_000)
                    touch(paths.cache / "webview" / str(10_000 + day) / "EBWebView" / "Cache" / "data", now, 100_000)
                    summary = hk.run_maintenance(paths, "default", ["LIVE"], now=now + 3600,
                                                 current_pid=1, alive=lambda pid: False)
                    peak = max(peak, sum(summary["bytes"].values()))
                final = sum(summary["bytes"].values())
            finally:
                logger.removeHandler(handler)
                handler.close()
            self.assertLessEqual(hk.directory_size(paths.logs), 4 * hk.LOG_MAX_BYTES + 200_000)
            self.assertLessEqual(len(list((paths.cache / "updates").iterdir())), 9)     # <= 3 days
            self.assertLessEqual(len(list(paths.cache.rglob("*.tmp"))), 8)               # <= 1 day
            self.assertEqual(len(list((paths.config / "profiles").glob("*.invalid-*"))), 5)
            self.assertEqual(list((paths.cache / "webview").iterdir()), [])
            removed_left = len(list((paths.cache / "default").glob("REMOVED-*.json")))
            self.assertLessEqual(removed_left, 92)                                       # 90-day window
            self.assertTrue((paths.cache / "default" / "LIVE.json").exists())
            self.assertLess(peak, 6_000_000)
            print(f"\nsoak: 120 days, peak {peak/1e6:.2f} MB, final {final/1e6:.2f} MB, "
                  f"logs {hk.directory_size(paths.logs)/1e6:.2f} MB")

    def test_memory_stays_bounded_over_30_days_of_ticks(self):
        from vram_radar import ui_dialogs
        from vram_radar.shell import prune_missing_markers
        now = [0.0]
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        dog = hk.HealthWatchdog(Path(holder.name) / "health.json",
                                clock=lambda: now[0], started_at=0.0)
        keeper = hk.Housekeeper(lambda: {"ok": 1}, watchdog=dog, sampler=lambda: {"private_bytes": 1},
                                restart=lambda reason: True, is_idle=lambda: True, clock=lambda: now[0])
        markers: dict = {}
        saved = dict(ui_dialogs._ICONS)
        tracemalloc.start()
        try:
            def tick(minute):
                now[0] = minute * 60.0
                keeper.step()
                ui_dialogs._remember(ui_dialogs._ICONS, ("glyph", "?", 16 + minute % 300, (minute % 7,)), b"")
                markers[("S", f"task{minute}")] = "m"
                prune_missing_markers(markers, "S", {f"task{minute}": {}})
            with patch.object(hk.LOG, "info"):
                for minute in range(2000):                          # warm-up
                    tick(minute)
                base = tracemalloc.get_traced_memory()[0]
                for minute in range(2000, 30 * 24 * 60):
                    tick(minute)
                grown = tracemalloc.get_traced_memory()[0] - base
        finally:
            tracemalloc.stop()
            ui_dialogs._ICONS.clear(); ui_dialogs._ICONS.update(saved)
        self.assertLessEqual(len(markers), 1)
        self.assertLessEqual(len(ui_dialogs._ICONS), ui_dialogs.ICON_CACHE_LIMIT)
        self.assertLess(grown, 256 * 1024, f"python heap grew {grown} bytes over 30 simulated days")
        print(f"\nsoak: 43200 ticks, python heap growth {grown/1024:.1f} KB")


if __name__ == "__main__":
    unittest.main()