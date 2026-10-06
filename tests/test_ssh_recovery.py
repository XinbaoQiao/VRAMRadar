import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vram_radar.connectors import ConnectorFailure, classify_process_error
from vram_radar.models import Profile, ServerProfile
from vram_radar.service import DashboardService, connection_fingerprint
from vram_radar.ssh_recovery import HostSshRecovery
from vram_radar.storage import SnapshotCache, storage_paths


def rejection():
    return classify_process_error("user@gpu.test: Permission denied (publickey,password).", returncode=255)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ServerProfile(id="gpu", display_name="GPU", backend="direct_ssh", host="gpu.test")
        self.paths = storage_paths(self.root)
        self.profile = Profile.from_dict({"schema_version": 1, "id": "test", "display_name": "Test",
                                          "servers": [self.server.to_dict()]})
        self.config_path = self.paths.config / "ssh-recovery.json"
        self.helper = self.root / "repair.py"
        self.helper.write_text('import json, os\nassert "RADAR_FAKE_SECRET" not in os.environ\n'
                               'print(json.dumps({"server": "gpu", "status": "ok"}))\n')
        self.config = {"schema_version": 1, "profiles": {"test": {"gpu": {
            "connection_fingerprint": connection_fingerprint(self.server),
            "python": sys.executable, "helper": str(self.helper),
            "files_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                              for name in (sys.executable, str(self.helper))},
        }}}}
        self.now = 1000.0
        self.recovery = HostSshRecovery(self.config_path, "test", clock=lambda: self.now)

    def register(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def service(self, query, recovery=None):
        return DashboardService(self.profile, SnapshotCache(self.paths, "test"), query=query,
                                ssh_recovery=recovery, clock=lambda: self.now)

    def result(self, status="ok", server="gpu", code=0):
        return SimpleNamespace(returncode=code, stdout=json.dumps({"server": server, "status": status}).encode(),
                               stderr=b"untrusted output must never be reported", stdout_truncated=False)

    def test_precise_public_key_failure_classification(self):
        for methods in ("publickey", "publickey,password", "publickey,password,keyboard-interactive"):
            self.assertEqual(classify_process_error(f"Permission denied ({methods}).", returncode=255).reason,
                             "public_key_rejected")
        for text, code, password in (
            ("Permission denied (publickey).", 1, False),
            ("Permission denied (publickey,password).", 255, True),
            ("Permission denied (password).", 255, False),
            ("Authentication failed.", 255, False),
            ("Too many authentication failures\nPermission denied (publickey).", 255, False),
            ("Host key verification failed.\nPermission denied (publickey).", 255, False),
            ("Connection timed out\nPermission denied (publickey).", 255, False),
            ('Load key "key": incorrect passphrase supplied to decrypt private key\nPermission denied (publickey).', 255, False),
        ):
            with self.subTest(text=text):
                self.assertNotEqual(classify_process_error(text, returncode=code, password_auth=password).reason,
                                    "public_key_rejected")

    def test_successful_collection_never_calls_helper(self):
        recover = Mock()
        service = self.service(Mock(return_value={"collected": True}), recover)
        self.assertEqual(service._query(self.server), {"collected": True})
        recover.assert_not_called()

    def test_recovery_retries_once_without_password_or_recursion(self):
        recover = Mock(return_value=True)
        query = Mock(side_effect=[rejection(), {"collected": True}])
        self.server = replace(self.server, identity_file=str(self.root / "identity"),
                              prefer_identity_auth=True, auth_ref="unused-password")
        service = self.service(query, recover)
        service.secret_store = Mock()
        self.assertEqual(service._query(self.server), {"collected": True})
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args_list[0], query.call_args_list[1])
        recover.assert_called_once_with(self.server)
        service.secret_store.get.assert_not_called()

    def test_second_rejection_is_bounded_and_rescheduled(self):
        recover = Mock(return_value=True)
        query = Mock(side_effect=rejection())
        service = self.service(query, recover)
        with self.assertRaises(ConnectorFailure) as raised:
            service._query(self.server)
        self.assertEqual(query.call_count, 2)
        recover.assert_called_once()
        service._record_failure("gpu", raised.exception)
        self.assertEqual(service.states["gpu"].next_attempt_monotonic, self.now + 300)

    def test_other_failures_do_not_recover(self):
        for text in ("Host key verification failed.", "Connection timed out", "Too many authentication failures"):
            recover = Mock()
            service = self.service(Mock(side_effect=classify_process_error(text, returncode=255)), recover)
            with self.assertRaises(ConnectorFailure):
                service._query(self.server)
            recover.assert_not_called()

    def test_recovery_failure_does_not_fall_through_to_password(self):
        self.server = replace(self.server, auth_ref="saved")
        self.register()
        service = self.service(Mock(side_effect=rejection()), self.recovery)
        service.secret_store = Mock()
        with patch("vram_radar.ssh_recovery._run_bounded_process", return_value=self.result(code=1)):
            with self.assertRaises(ConnectorFailure) as raised:
                service._query(self.server)
        self.assertEqual(raised.exception.code, "auth_recovery_pending")
        self.assertNotIn("untrusted", str(raised.exception))
        service.secret_store.get.assert_not_called()

    def test_no_registration_and_other_profile_never_execute(self):
        with patch("vram_radar.ssh_recovery._run_bounded_process") as run:
            self.assertIsNone(self.recovery(self.server))
            self.register()
            self.assertIsNone(HostSshRecovery(self.config_path, "other")(self.server))
            run.assert_not_called()

    def test_mutated_target_helper_or_registration_fails_closed(self):
        self.register()
        with patch("vram_radar.ssh_recovery._run_bounded_process") as run:
            self.server = replace(self.server, host="different.test")
            with self.assertRaises(ConnectorFailure) as raised:
                self.recovery(self.server)
            self.assertEqual(raised.exception.state, "security_blocked")
            self.server = replace(self.server, host="gpu.test")
            self.helper.write_text("raise SystemExit('changed')")
            with self.assertRaises(ConnectorFailure):
                self.recovery(self.server)
            self.config_path.write_text("broken")
            with self.assertRaises(ConnectorFailure):
                self.recovery(self.server)
            run.assert_not_called()

    def test_concurrent_calls_share_recovery_then_cool_down(self):
        self.register()
        with patch("vram_radar.ssh_recovery._run_bounded_process", return_value=self.result()) as run:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(self.recovery, [self.server] * 4)), [True] * 4)
            self.assertEqual(run.call_count, 1)
            self.now += 10
            with self.assertRaises(ConnectorFailure):
                self.recovery(self.server)
            self.now += 300
            self.assertTrue(self.recovery(self.server))
            self.assertEqual(run.call_count, 2)

    def test_invalid_receipt_timeout_and_nonzero_exit_retry_sparsely(self):
        self.register()
        for outcome in (self.result(status="health_unknown"), self.result(server="another"),
                        self.result(code=1), subprocess.TimeoutExpired("helper", 90),
                        SimpleNamespace(stdout=b"not json", returncode=0, stdout_truncated=False)):
            with self.subTest(outcome=outcome):
                recovery = HostSshRecovery(self.config_path, "test", clock=lambda: self.now)
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch("vram_radar.ssh_recovery._run_bounded_process", **kwargs) as run:
                    for _ in range(2):
                        with self.assertRaises(ConnectorFailure) as raised:
                            recovery(self.server)
                        self.assertEqual(raised.exception.retry_after_seconds, 300)
                    self.assertEqual(run.call_count, 1)

    def test_real_bounded_helper_process_gets_no_parent_secret(self):
        self.register()
        with patch.dict(os.environ, {"RADAR_FAKE_SECRET": "synthetic-only"}):
            self.assertTrue(self.recovery(self.server))


if __name__ == "__main__":
    unittest.main()
