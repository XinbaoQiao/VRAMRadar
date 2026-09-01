from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from vram_radar.storage import SnapshotCache, WindowStateStore, storage_paths
from vram_radar.window_state import WindowGeometry, WindowStateController


class SnapshotCacheReliabilityTests(unittest.TestCase):
    def test_schema_v2_cache_is_bound_to_the_exact_connection_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            payload = {"server_id": "gpu", "view_kind": "live-memory", "total_gpus": 1}

            cache.save(
                "gpu",
                "2026-08-29T00:00:00Z",
                payload,
                connection_fingerprint="endpoint-a",
            )

            self.assertEqual(
                cache.load("gpu", connection_fingerprint="endpoint-a")["payload"],
                payload,
            )
            self.assertIsNone(cache.load("gpu", connection_fingerprint="endpoint-b"))

    def test_legacy_schema_v1_cache_is_rejected_instead_of_reused_for_a_new_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            path = cache.path_for("gpu")
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"schema_version":1,"server_id":"gpu",'
                '"last_success_at":"2026-08-29T00:00:00Z",'
                '"payload":{"server_id":"gpu","total_gpus":8}}',
                encoding="utf-8",
            )

            self.assertIsNone(cache.load("gpu", connection_fingerprint="current-endpoint"))


class WindowStatePersistenceTests(unittest.TestCase):
    def test_geometry_round_trip_uses_a_machine_local_json_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = WindowStateStore(storage_paths(Path(temporary)))

            path = store.save(WindowGeometry(1260, 820))

            self.assertEqual(store.load(), WindowGeometry(1260, 820))
            self.assertEqual(path.name, "window-state.json")
            self.assertIn('"schema_version":1', path.read_text(encoding="utf-8"))

    def test_missing_corrupt_and_abnormally_small_geometry_use_the_safe_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = WindowStateStore(storage_paths(Path(temporary)))
            self.assertEqual(store.load(), WindowGeometry())
            store.path.parent.mkdir(parents=True)

            for document in (
                "not-json",
                '{"schema_version":1,"width":480,"height":320}',
                '{"schema_version":1,"width":true,"height":780}',
                '{"schema_version":2,"width":1180,"height":780}',
            ):
                store.path.write_text(document, encoding="utf-8")
                self.assertEqual(store.load(), WindowGeometry())

    def test_resize_burst_is_debounced_to_the_latest_valid_normal_size(self):
        saved = []
        completed = threading.Event()
        store = SimpleNamespace(
            load=lambda: WindowGeometry(),
            save=lambda geometry: (saved.append(geometry), completed.set()),
        )
        controller = WindowStateController(store, debounce_seconds=0.02)

        controller.on_resized(900, 640)
        controller.on_resized(960, 680)

        self.assertTrue(completed.wait(1))
        self.assertEqual(saved, [WindowGeometry(960, 680)])
        controller.close()

    def test_background_commit_can_skip_native_probe_after_resize_validation(self):
        probe_threads = []
        saved = []
        completed = threading.Event()

        def normal_state():
            probe_threads.append(threading.get_ident())
            return True

        def save(geometry):
            saved.append(geometry)
            completed.set()

        store = SimpleNamespace(load=lambda: WindowGeometry(), save=save)
        controller = WindowStateController(
            store,
            debounce_seconds=0.02,
            normal_state=normal_state,
            recheck_normal_state_on_commit=False,
        )

        event_thread = threading.get_ident()
        controller.on_resized(980, 690)

        self.assertTrue(completed.wait(1))
        self.assertEqual(probe_threads, [event_thread])
        self.assertEqual(saved, [WindowGeometry(980, 690)])
        controller.close()

    def test_minimize_and_maximize_sentinels_never_replace_the_last_normal_size(self):
        saved = []
        store = SimpleNamespace(load=lambda: WindowGeometry(1000, 700), save=saved.append)
        controller = WindowStateController(store, debounce_seconds=10)

        controller.on_resized(1100, 740)
        controller.on_minimized()
        controller.on_resized(480, 320)
        controller.flush()
        controller.on_restored()
        controller.on_resized(1040, 720)
        controller.on_maximized()
        controller.flush()

        self.assertEqual(
            saved,
            [WindowGeometry(1100, 740), WindowGeometry(1040, 720)],
        )
        self.assertEqual(controller.geometry, WindowGeometry(1040, 720))

        controller.on_restored()
        controller.on_resized(1020, 710)
        controller.flush()
        self.assertEqual(
            saved,
            [
                WindowGeometry(1100, 740),
                WindowGeometry(1040, 720),
                WindowGeometry(1020, 710),
            ],
        )
        controller.close()

    def test_close_commits_the_latest_valid_resize_before_debounce_expires(self):
        saved = []
        store = SimpleNamespace(load=lambda: WindowGeometry(), save=saved.append)
        controller = WindowStateController(store, debounce_seconds=10)

        controller.on_resized(980, 690)
        controller.close()

        self.assertEqual(saved, [WindowGeometry(980, 690)])
        self.assertEqual(controller.geometry, WindowGeometry(980, 690))

    def test_suspend_waits_for_a_timer_that_already_claimed_the_geometry(self):
        save_entered = threading.Event()
        release_save = threading.Event()
        suspend_finished = threading.Event()
        saved = []

        def save(geometry):
            save_entered.set()
            release_save.wait(1)
            saved.append(geometry)

        store = SimpleNamespace(load=lambda: WindowGeometry(), save=save)
        controller = WindowStateController(store, debounce_seconds=0)

        controller.on_resized(930, 660)
        self.assertTrue(save_entered.wait(1))
        suspender = threading.Thread(
            target=lambda: (controller.on_minimized(), suspend_finished.set()),
        )
        suspender.start()
        self.assertFalse(suspend_finished.wait(0.03))
        release_save.set()
        self.assertTrue(suspend_finished.wait(1))
        suspender.join(1)

        self.assertEqual(saved, [WindowGeometry(930, 660)])
        self.assertEqual(controller.geometry, WindowGeometry(930, 660))
        controller.close()

    def test_stale_cancelled_timer_cannot_overwrite_post_restore_resize(self):
        saved = []
        completed = threading.Event()

        def save(geometry):
            saved.append(geometry)
            if len(saved) == 2:
                completed.set()

        store = SimpleNamespace(load=lambda: WindowGeometry(), save=save)
        controller = WindowStateController(store, debounce_seconds=0.03)

        controller.on_resized(900, 640)
        controller.on_minimized()
        controller.on_restored()
        controller.on_resized(1020, 710)

        self.assertTrue(completed.wait(1))
        self.assertEqual(
            saved,
            [WindowGeometry(900, 640), WindowGeometry(1020, 710)],
        )
        self.assertEqual(controller.geometry, WindowGeometry(1020, 710))
        controller.close()

    def test_slow_older_write_cannot_finish_after_the_newer_geometry(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        saved = []

        def save(geometry):
            if not saved:
                first_entered.set()
                release_first.wait(1)
            saved.append(geometry)
            if len(saved) == 2:
                second_finished.set()

        store = SimpleNamespace(load=lambda: WindowGeometry(), save=save)
        controller = WindowStateController(store, debounce_seconds=0)

        controller.on_resized(900, 640)
        self.assertTrue(first_entered.wait(1))
        controller.on_resized(1020, 710)
        release_first.set()

        self.assertTrue(second_finished.wait(1))
        controller.close()
        self.assertEqual(saved, [WindowGeometry(900, 640), WindowGeometry(1020, 710)])
        self.assertEqual(controller.geometry, WindowGeometry(1020, 710))

    def test_native_state_check_defeats_async_maximize_event_reordering(self):
        normal = {"value": True}
        probes = []
        saved = threading.Event()
        commit_probe_entered = threading.Event()
        release_commit_probe = threading.Event()
        store = SimpleNamespace(load=lambda: WindowGeometry(), save=lambda _geometry: saved.set())

        def normal_state():
            probes.append(threading.get_ident())
            if len(probes) > 1:
                commit_probe_entered.set()
                release_commit_probe.wait(1)
            return normal["value"]

        controller = WindowStateController(
            store,
            debounce_seconds=0.02,
            normal_state=normal_state,
            recheck_normal_state_on_commit=True,
        )
        release_maximize_handler = threading.Event()

        def delayed_maximize_handler():
            release_maximize_handler.wait(1)
            controller.on_maximized()

        handler = threading.Thread(target=delayed_maximize_handler)
        handler.start()
        controller.on_resized(1920, 1040)
        # The native form changes state before pywebview's independently
        # threaded maximized handler is scheduled. The commit boundary must
        # reject the resize even while that handler is intentionally delayed.
        normal["value"] = False
        self.assertTrue(commit_probe_entered.wait(1))
        release_commit_probe.set()

        self.assertFalse(saved.wait(0.08))
        release_maximize_handler.set()
        handler.join(1)
        controller.close()
        self.assertFalse(saved.is_set())
        self.assertGreaterEqual(len(probes), 2)
        self.assertNotEqual(probes[0], probes[1])


if __name__ == "__main__":
    unittest.main()
