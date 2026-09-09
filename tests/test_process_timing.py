from copy import deepcopy
import unittest
from unittest.mock import Mock, patch
import shutil
import subprocess

from vram_radar.connectors import _parse_process_metadata, query_direct_ssh
from vram_radar.process_timing import ProcessTimingTracker
from vram_radar.models import Profile, ServerProfile
from vram_radar.service import DashboardService


class ProcessTimingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash unavailable")
    def test_remote_probe_shell_and_missing_proc_are_safe(self):
        with patch("vram_radar.connectors.run_remote", side_effect=RuntimeError("capture")) as remote:
            with self.assertRaises(RuntimeError):
                query_direct_ssh(ServerProfile(id="gpu", display_name="GPU", backend="direct_ssh", host="test.invalid"))
        script = remote.call_args.args[1]
        checked = subprocess.run([shutil.which("bash"), "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        helper = script.split("process_start_ticks()", 1)[1].split("for pid in $pids; do", 1)[0]
        before = script.split("for pid in $pids; do", 1)[1].split("\n", 2)[1]
        checked = subprocess.run([shutil.which("bash")], text=True, capture_output=True,
            input="set -eu\nprocess_start_ticks()" + helper + "pid=999999999\n" + before + "\nprintf 'survived'\n")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(checked.stdout, "survived")

    def setUp(self):
        self.tracker = ProcessTimingTracker()

    def sample(self, now, elapsed=0, identity="boot-start", pid="42"):
        process = {"pid": pid, "process_identity": identity,
                   "elapsed_seconds": elapsed, "started_at": "sample-start"}
        payload = {"processes": {"active": [process]}}
        self.tracker.update(payload, now, 60)
        return process

    def test_normal_durations_are_unchanged(self):
        for now, elapsed in [(0, 123), (15, 138), (30, 153), (60, None)]:
            self.assertEqual(self.sample(now, elapsed), {
                "pid": "42", "process_identity": "boot-start",
                "elapsed_seconds": elapsed, "started_at": "sample-start"})

    def test_zero_requires_three_samples_and_thirty_seconds(self):
        self.assertEqual(self.sample(0)["elapsed_seconds"], 0)
        self.assertEqual(self.sample(15)["elapsed_seconds"], 0)
        result = self.sample(30)
        self.assertIsNone(result["elapsed_seconds"])
        self.assertIsNone(result["started_at"])
        self.assertEqual(result["observed_running_seconds"], 30)
        self.assertEqual(self.sample(45)["observed_running_seconds"], 45)

    def test_fast_refresh_does_not_mark_new_process_abnormal(self):
        for now in range(10):
            self.assertEqual(self.sample(now)["elapsed_seconds"], 0)

    def test_pid_reuse_and_reboot_reset_observation(self):
        self.sample(0)
        self.sample(15)
        self.assertEqual(self.sample(30, identity="new-boot-or-start")["elapsed_seconds"], 0)

    def test_failure_gap_and_disappearance_reset_observation(self):
        for reset in [lambda: self.tracker.clear(),
                      lambda: self.tracker.update({"processes": {"active": []}}, 20, 60)]:
            self.sample(0)
            self.sample(15)
            reset()
            self.assertEqual(self.sample(30)["elapsed_seconds"], 0)
        self.assertEqual(self.sample(200)["elapsed_seconds"], 0)

    def test_missing_identity_never_accumulates_an_estimate(self):
        for now in [0, 30, 60]:
            result = self.sample(now, identity=None)
            self.assertEqual(result["timing_status"], "unverified")
            self.assertNotIn("observed_running_seconds", result)
            self.assertIsNone(result["elapsed_seconds"])

    def test_recovered_clock_resumes_normal_path(self):
        for now in [0, 15, 30]:
            self.sample(now)
        result = self.sample(45, 86400)
        self.assertEqual(result["elapsed_seconds"], 86400)
        self.assertNotIn("timing_status", result)
        self.assertEqual(self.tracker.observations, {})

    def test_instances_are_isolated_and_slurm_untouched(self):
        self.sample(0)
        self.sample(15)
        other = ProcessTimingTracker()
        payload = {"tasks": {"active": [{"elapsed": "00:00"}]}}
        original = deepcopy(payload)
        other.update(payload, 40, 60)
        self.assertEqual(payload, original)
        self.assertEqual(other.observations, {})

    def test_identity_uses_boot_pid_and_start_and_preserves_metadata(self):
        boot = "12345678-1234-1234-1234-123456789abc"
        legacy = "42 1000 user 0 12.5 python train.py"
        parsed = _parse_process_metadata("42", f"VRAM_ID {boot} 1200\n{legacy}")
        self.assertEqual(parsed["command"], "python train.py")
        self.assertEqual(parsed["cpu_percent"], 12.5)
        self.assertNotIn(boot, parsed["process_identity"])
        changed = _parse_process_metadata("42", f"VRAM_ID {boot} 1201\n{legacy}")
        self.assertNotEqual(parsed["process_identity"], changed["process_identity"])
        self.assertIsNone(_parse_process_metadata("42", legacy)["process_identity"])

    def test_service_updates_without_mutating_probe_and_pause_resets(self):
        profile = Profile.from_dict({"schema_version": 1, "id": "timing-test", "display_name": "Test", "servers": [
            {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "test.invalid"}]})
        cache = Mock()
        cache.load.return_value = None
        now = [0]
        service = DashboardService(profile, cache, clock=lambda: now[0])
        source = {"view_kind": "live-memory", "gpus": [], "processes": {"active": [
            {"pid": "42", "process_identity": "identity", "elapsed_seconds": 0}]}}
        original = deepcopy(source)
        for instant in [0, 15, 30]:
            now[0] = instant
            service._record_success("gpu", source)
        self.assertEqual(source, original)
        self.assertEqual(service.states["gpu"].payload["processes"]["active"][0]["timing_status"], "unavailable")
        service.pause()
        service.resume()
        now[0] = 45
        service._record_success("gpu", source)
        self.assertEqual(service.states["gpu"].payload["processes"]["active"][0]["elapsed_seconds"], 0)
