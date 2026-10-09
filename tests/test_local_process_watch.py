"""Process watches use synthetic snapshots and disposable state, never SSH."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vram_radar.connectors import (
    DIRECT_METADATA_LIMIT, DIRECT_PROTOCOL_HEADER, DIRECT_PROTOCOL_END,
    _build_local_process_snapshot, query_direct_ssh,
)
from vram_radar.models import ConfigError, Profile, ServerProfile, normalize_task_completion_watch
from vram_radar.shell import AppApi
from vram_radar.storage import ProfileStore, storage_paths


IDENTITY = "a" * 64
KEY = f"local_process:30485:{IDENTITY}"
WATCH = {
    "server_id": "synthetic", "task_key": KEY, "task_kind": "local_process",
    "task_id": "30485", "label": "train.py", "owner": "owner", "owner_scope": "mine",
}


def profile(*, automatic=False, watches=()):
    return Profile.from_dict({
        "schema_version": 1, "id": "local", "display_name": "Synthetic",
        "task_completion_alert_enabled": automatic, "task_completion_watches": list(watches),
        "servers": [{"id": "synthetic", "display_name": "Synthetic", "backend": "direct_ssh",
                     "host": "example.invalid"}],
    })


def snapshot(revision=1, *, present=True, device=True, complete=True, identity=IDENTITY,
             connection="online", origin="live", paused=False, in_flight=False):
    return {
        "monitoring": {"revision": revision, "paused": paused, "in_flight": in_flight},
        "servers": [{
            "server_id": "synthetic",
            "connection": {"state": connection, "data_origin": origin,
                           "last_success_at": f"2026-10-09T00:00:{revision:02d}Z"},
            "processes": {
                "supported": True, "current_user": "owner", "active": [],
                "local_gpu_access_supported": True,
                "local_gpu_access": ([{"pid": "30485", "name": "train.py", "user": "owner",
                                      "owner_scope": "mine", "process_identity": identity}]
                                     if device else []),
                "local_process_identities": {"30485": identity} if present else {},
                "local_processes_complete": complete,
            },
        }],
    }


class LocalProcessWatchTests(unittest.TestCase):
    def make_api(self, *, automatic=False, watches=(WATCH,), initial=None):
        service = Mock()
        service.snapshot.return_value = initial or snapshot()
        api = AppApi(profile(automatic=automatic, watches=watches), store=Mock(), paths=Mock(), service=service)
        notify = Mock(return_value=True)
        api.bind_notification_callback(notify)
        api.get_snapshot()
        return api, service, notify

    def test_current_account_watch_round_trips_with_exact_generation(self):
        value = profile(watches=(WATCH,))
        self.assertEqual(Profile.from_dict(value.to_dict()), value)
        self.assertEqual(value.task_completion_watches[0]["task_key"], KEY)

    def test_local_watch_rejects_missing_identity_wrong_pid_and_other_owner(self):
        for changes in ({"task_key": "local_process:30485"},
                        {"task_key": f"local_process:999:{IDENTITY}"},
                        {"task_id": "30485:extra"}, {"owner_scope": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                normalize_task_completion_watch({**WATCH, **changes})

    def test_follow_and_unfollow_persist_without_remote_work(self):
        api, service, notify = self.make_api(watches=())
        result = api.set_task_completion_watch("synthetic", KEY, "local_process", "30485", "train.py",
                                               True, "owner", "mine")
        self.assertTrue(result["ok"])
        self.assertEqual(api.get_profile()["task_completion_watches"], [WATCH])
        result = api.set_task_completion_watch("synthetic", KEY, "local_process", "30485", "train.py",
                                               False, "owner", "mine")
        self.assertTrue(result["ok"])
        service.snapshot.return_value = snapshot(2, present=False, device=False)
        api.get_snapshot()
        service.snapshot.return_value = snapshot(3, present=False, device=False)
        api.get_snapshot()
        notify.assert_not_called()

    def test_exit_needs_two_distinct_successful_samples_and_removes_only_local_watch(self):
        gpu_watch = {**WATCH, "task_kind": "process", "task_key": "process:30485", "label": "GPU row"}
        initial = snapshot()
        initial["servers"][0]["processes"]["active"] = [{"pid": "30485", "name": "GPU row",
                                                         "user": "owner", "owner_scope": "mine"}]
        api, service, notify = self.make_api(watches=(WATCH, gpu_watch), initial=initial)
        first = snapshot(2, present=False, device=False)
        first["servers"][0]["processes"]["active"] = initial["servers"][0]["processes"]["active"]
        service.snapshot.return_value = first
        api.get_snapshot()
        api.get_snapshot()
        # An unrelated fleet revision does not create a new server sample.
        first["monitoring"]["revision"] = 3
        api.get_snapshot()
        notify.assert_not_called()
        second = copy.deepcopy(first)
        second["servers"][0]["connection"]["last_success_at"] = "2026-10-09T00:00:04Z"
        service.snapshot.return_value = second
        completed = api.get_snapshot()
        api.get_snapshot()
        notify.assert_called_once_with("任务已完成", "train.py 已结束。")
        event = completed["notifications"]["events"][0]
        self.assertEqual((event["task_kind"], event["task_key"], event["watched"]),
                         ("local_process", KEY, True))
        self.assertEqual(api.get_profile()["task_completion_watches"], [gpu_watch])

    def test_closing_gpu_descriptors_is_not_process_exit(self):
        api, service, notify = self.make_api()
        for revision in (2, 3, 4):
            service.snapshot.return_value = snapshot(revision, device=False)
            api.get_snapshot()
        notify.assert_not_called()
        self.assertEqual(len(api.get_profile()["task_completion_watches"]), 1)

    def test_automatic_gpu_alerts_do_not_follow_device_users(self):
        api, service, notify = self.make_api(automatic=True, watches=())
        for revision in (2, 3):
            service.snapshot.return_value = snapshot(revision, present=False, device=False)
            api.get_snapshot()
        notify.assert_not_called()

    def test_pid_reuse_with_the_same_command_does_not_transfer_the_watch(self):
        api, service, notify = self.make_api(automatic=True)
        for revision in (2, 3, 4):
            service.snapshot.return_value = snapshot(revision, identity="b" * 64)
            api.get_snapshot()
        notify.assert_called_once_with("任务已完成", "train.py 已结束。")
        self.assertEqual(api.get_profile()["task_completion_watches"], [])

    def test_partial_or_failed_samples_cannot_complete_a_watch(self):
        for flags in ({"complete": False}, {"connection": "stale"}, {"origin": "cache"},
                      {"paused": True}, {"in_flight": True}):
            with self.subTest(flags=flags):
                api, service, notify = self.make_api()
                for revision in (2, 3):
                    service.snapshot.return_value = snapshot(revision, present=False, device=False, **flags)
                    api.get_snapshot()
                notify.assert_not_called()
                self.assertEqual(len(api.get_profile()["task_completion_watches"]), 1)

    def test_unreadable_identity_is_unknown_even_when_scan_is_complete(self):
        api, service, notify = self.make_api()
        for revision in (2, 3):
            service.snapshot.return_value = snapshot(revision, device=False, identity=None)
            api.get_snapshot()
        notify.assert_not_called()

    def test_incomplete_scan_resets_an_unconfirmed_absence(self):
        api, service, notify = self.make_api()
        for revision, complete in ((2, True), (3, False), (4, True)):
            service.snapshot.return_value = snapshot(revision, present=False, device=False, complete=complete)
            api.get_snapshot()
        notify.assert_not_called()
        service.snapshot.return_value = snapshot(5, present=False, device=False)
        api.get_snapshot()
        notify.assert_called_once()

    def test_followed_process_can_exit_before_next_poll(self):
        api, service, notify = self.make_api(watches=())
        service.snapshot.return_value = snapshot(2, present=False, device=False)
        self.assertTrue(api.set_task_completion_watch("synthetic", KEY, "local_process", "30485", "train.py",
                                                      True, "owner", "mine")["ok"])
        notify.assert_not_called()
        service.snapshot.return_value = snapshot(3, present=False, device=False)
        api.get_snapshot()
        notify.assert_called_once()

    def test_restart_preserves_the_generation_after_gpu_access_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            value = profile(watches=(WATCH,))
            store.save(value)
            first_service = Mock()
            first_service.snapshot.return_value = snapshot()
            first = AppApi(value, store=store, paths=paths, service=first_service)
            first.get_snapshot()
            service = Mock()
            service.snapshot.return_value = snapshot(2, device=False)
            restarted = AppApi(store.load("local"), store=store, paths=paths, service=service)
            notify = Mock(return_value=True)
            restarted.bind_notification_callback(notify)
            for revision in (3, 4):
                service.snapshot.return_value = snapshot(revision, present=False, device=False)
                restarted.get_snapshot()
            notify.assert_called_once()
            self.assertEqual(store.load("local").task_completion_watches, ())

    def test_malformed_or_truncated_identity_protocol_never_proves_absence(self):
        for encoded in ("not-hex", "bad".encode().hex(),
                        (f"30485|{IDENTITY}\n30485|{IDENTITY}").encode().hex()):
            with self.subTest(encoded=encoded):
                self.assertEqual(_build_local_process_snapshot({"LOCAL_PROCESS_HEX": encoded,
                                                                "LOCAL_PROCESS_COMPLETE": "1"}), ({}, False))
        self.assertEqual(_build_local_process_snapshot({}), ({}, False))
        text = f"30485|{IDENTITY}\n30625|-"
        self.assertEqual(_build_local_process_snapshot({"LOCAL_PROCESS_HEX": text.encode().hex(),
                                                        "LOCAL_PROCESS_COMPLETE": "1"}),
                         ({"30485": IDENTITY, "30625": None}, True))

    def test_direct_collector_preserves_scoped_identity_without_gpu_allocation(self):
        metadata = f"VRAM_LOCAL_ID {IDENTITY}\nVRAM_PROC\n30485 1001 owner 120 python train.py"
        fields = {
            "HOST_HEX": b"synthetic".hex(), "CURRENT_UID": "1001", "CURRENT_USER_HEX": b"owner".hex(),
            "GPU_HEX": b"0, GPU-a, Synthetic GPU, 8192, 1024, 7168, 10, 30".hex(),
            "PROCESS_A_SUPPORTED": "1", "PROCESS_A_HEX": "",
            "PROCESS_B_SUPPORTED": "1", "PROCESS_B_HEX": "", "METADATA_LIMIT": str(DIRECT_METADATA_LIMIT),
            "PID_VIEW": "isolated", "GPU_ACCESS_SUPPORTED": "1", "GPU_ACCESS_LIMITED": "0",
            "GPU_ACCESS_HEX": ("30485|" + metadata.encode().hex()).encode().hex(),
            "LOCAL_PROCESS_HEX": f"30485|{IDENTITY}".encode().hex(), "LOCAL_PROCESS_COMPLETE": "1",
        }
        output = "\n".join([DIRECT_PROTOCOL_HEADER, *(f"{key}={value}" for key, value in fields.items()), DIRECT_PROTOCOL_END])
        server = ServerProfile(id="synthetic", display_name="Synthetic", backend="direct_ssh", host="example.invalid")
        with patch("vram_radar.connectors.run_remote", return_value=output) as remote:
            collected = query_direct_ssh(server)
        self.assertEqual(remote.call_count, 1)
        state = collected["processes"]
        self.assertTrue(state["local_processes_complete"])
        self.assertEqual(state["local_process_identities"], {"30485": IDENTITY})
        self.assertEqual(state["local_gpu_access"][0]["process_identity"], IDENTITY)
        self.assertNotIn("allocations", state["local_gpu_access"][0])
        self.assertIn(KEY, AppApi._active_tasks(collected))
