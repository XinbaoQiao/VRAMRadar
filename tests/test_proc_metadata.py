import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from vram_radar.connectors import PROC_METADATA_FALLBACK, PROCESS_LSTART_FALLBACK, _parse_process_metadata, _build_direct_processes


@unittest.skipUnless(shutil.which("bash"), "bash unavailable")
class ProcMetadataFallbackTests(unittest.TestCase):
    def probe(self, *, uptime=True, reuse=False, missing=False, empty_command=False):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            process = root / "42"
            process.mkdir()
            stat = "42 (worker with spaces) " + " ".join(["S"] + ["0"] * 18 + ["200"])
            (process / "stat").write_text(stat + "\n", encoding="utf-8")
            (process / "status").write_text("Name:\tpython\nUid:\t1001\t1001\t1001\t1001\n", encoding="utf-8")
            (process / "cmdline").write_bytes(b"python\0train.py\0--run-name\0experiment\0--token\0SYNTHETIC_SECRET\0")
            if empty_command:
                (process / "cmdline").write_bytes(b"")
                (process / "comm").write_text("python worker\n", encoding="utf-8")
            if uptime:
                (root / "uptime").write_text("1000.0 0.0\n", encoding="utf-8")
            if missing:
                (process / "stat").unlink()
            script = PROC_METADATA_FALLBACK + "\nproc_root=$1\n"
            # Convert only the owned fixture path on Git Bash for Windows.
            script += 'if command -v cygpath >/dev/null 2>&1; then proc_root=$(cygpath -u "$proc_root"); fi\n'
            script += 'getconf() { printf "100"; }\n'
            if reuse:
                script += 'dd() { command dd "$@"; sed "s/200$/201/" "$proc_root/42/stat" > "$proc_root/42/next"; mv "$proc_root/42/next" "$proc_root/42/stat"; }\n'
            script += 'proc_metadata 42 "$proc_root"\n'
            return subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-c", script, "fixture", str(root)], capture_output=True, timeout=10)

    def test_recovers_task_and_owner_and_retains_command_redaction(self):
        result = self.probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = "VRAM_PROC\n" + result.stdout.decode()
        metadata = _parse_process_metadata("42", text)
        self.assertEqual(metadata["uid"], "1001")
        self.assertEqual(metadata["elapsed_seconds"], 998)
        self.assertIsNone(metadata["cpu_percent"])
        processes = _build_direct_processes(
            "GPU-a, 42, [Not Found], 512", "GPU-a, 42, [Not Found], 512", {"42": text},
            [{"gpu_uuid": "GPU-a", "gpu_index": "0"}], "1001", "owner",
            sampled_at=datetime.now(timezone.utc))
        process = processes["active"][0]
        self.assertEqual(process["name"], "experiment · train.py")
        self.assertEqual(process["owner_scope"], "mine")
        self.assertEqual(process["metadata_source"], "proc")
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(process))

    def test_task_survives_unavailable_clock(self):
        result = self.probe(uptime=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = _parse_process_metadata("42", "VRAM_PROC\n" + result.stdout.decode())
        self.assertIsNone(metadata["elapsed_seconds"])
        self.assertIn("train.py", metadata["command"])

    def test_changed_start_tick_rejects_mixed_process_identity(self):
        result = self.probe(reuse=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_missing_pid_is_not_reconstructed(self):
        result = self.probe(missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_empty_command_uses_process_comm(self):
        result = self.probe(empty_command=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = _parse_process_metadata("42", "VRAM_PROC\n" + result.stdout.decode())
        self.assertEqual(metadata["command"], "python worker")

    def test_numeric_command_is_not_misread_as_cpu(self):
        metadata = _parse_process_metadata("42", "VRAM_PROC\n42 1001 owner 10 123 --task job")
        self.assertEqual(metadata["command"], "123 --task job")
        self.assertIsNone(metadata["cpu_percent"])

    def test_lstart_overrides_zero_without_losing_command(self):
        for prefix, tail in [("", "0.5 python train.py"), ("VRAM_PROC\n", "python train.py")]:
            metadata = _parse_process_metadata("42", "VRAM_ELAPSED 3600\n" + prefix + "42 1001 owner 0 " + tail)
            self.assertEqual(metadata["elapsed_seconds"], 3600)
            self.assertEqual(metadata["timing_source"], "lstart")
            self.assertEqual(metadata["command"], "python train.py")

    def test_lstart_shell_rejects_future_invalid_and_missing_dates(self):
        for start, expected in [("1000", "2600"), ("5000", None), ("invalid", None), ("", None)]:
            script = PROCESS_LSTART_FALLBACK + "\nps() { printf 'Thu Oct 8 00:00:00 2026'; }\n"
            script += 'date() { if [ "$1" = "-d" ]; then printf "%s" "' + start + '"; else printf 3600; fi; }\nprocess_lstart_elapsed 42\n'
            result = subprocess.run([shutil.which("bash"), "-c", script], capture_output=True, timeout=10)
            if expected is None:
                self.assertNotEqual(result.returncode, 0)
            else:
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.decode(), expected)
