"""Use disposable procfs fixtures; never contact a configured server."""

from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vram_radar.connectors import (
    MAX_REMOTE_STDIN_BYTES, _build_direct_processes, _build_local_gpu_access,
    _parse_process_metadata, query_direct_ssh,
)
from vram_radar.models import ServerProfile
from vram_radar.namespace_probe import REMOTE_NAMESPACE_PROBE


class ProcessNamespaceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "proc"
        self.host = Path(self.temporary.name) / "host"
        self.ns = {"__name__": "fixture"}
        exec(compile(REMOTE_NAMESPACE_PROBE, "namespace-probe", "exec"), self.ns)
        original_read = self.ns["read"]
        self.ns["read"] = lambda path, limit=16384: original_read(self.real_path(path), limit)
        self.links = {
            "/host/proc/1/ns/pid": self.ns["INIT_PID_NS"],
            self.path("self/ns/pid"): "pid:[4026532900]",
        }
        self.ns["link"] = lambda path: self.links.get(str(path).replace("\\", "/"), "")
        self.ns["os"] = SimpleNamespace(sysconf=lambda _: 100, scandir=__import__("os").scandir,
                                         getpid=lambda: 999, getppid=lambda: 998)
        self.write(self.root / "self/mountinfo", "30 20 0:10 / /host/proc ro - proc proc ro\n")
        self.write(self.root / "uptime", "1000.0 0\n")
        self.write(self.host / "uptime", "1000.0 0\n")
        self.write(self.host / "sys/kernel/random/boot_id", "00000000-0000-0000-0000-000000000001\n")
        self.process(self.root, "15109", nspid="15109")
        self.process(self.host, "2349206", nspid="2349206 15109")
        self.links["/host/proc/2349206/ns/pid"] = "pid:[4026532900]"
        self.links[self.path("15109/ns/pid")] = "pid:[4026532900]"
        self.sampled_at = datetime.now(timezone.utc)

    def path(self, suffix):
        return self.root.as_posix() + "/" + suffix

    def real_path(self, path):
        return str(self.host) + str(path)[len("/host/proc"):] if str(path).startswith("/host/proc") else path

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def process(self, root, pid, *, uid="1001", nspid=None, ticks="200", command="train.py"):
        self.write(root / pid / "stat", pid + " (worker with spaces) " + " ".join(["S"] + ["0"] * 18 + [ticks]))
        self.write(root / pid / "status", "Uid:\t" + "\t".join([uid] * 4) + "\nNSpid:\t" + (nspid or pid) + "\n")
        self.write(root / pid / "cmdline", "python\x00" + command + "\x00--token\x00NAMESPACE_SECRET\x00")

    def access(self, pid="15109", device="/dev/nvidia0"):
        fd = self.root / pid / "fd/4"
        self.write(fd, "")
        self.links[fd.as_posix()] = device

    def resolved_process(self, text):
        rows = "GPU-a, 2349206, [Not Found], 4946"
        return _build_direct_processes(rows, rows, {"2349206": text},
                                      [{"gpu_index": "0", "gpu_uuid": "GPU-a"}], "1001", "owner",
                                      sampled_at=self.sampled_at)["active"][0]

    def test_exact_host_view_recovers_owner_and_keeps_gpu_pid_and_memory(self):
        resolved, local, _ = self.ns["probe"](str(self.root).replace("\\", "/"), ["2349206"], "1001")
        process = self.resolved_process(resolved["2349206"])
        self.assertEqual(process["owner_scope"], "mine")
        self.assertEqual(process["pid"], "2349206")
        self.assertEqual(process["visible_pid"], "15109")
        self.assertEqual(process["pid_mapping_source"], "host_proc")
        self.assertEqual(process["memory_used_gib"], 4.83)
        self.assertEqual(process["elapsed_seconds"], 998)
        self.assertIsNotNone(process["process_identity"])
        self.assertNotIn("NAMESPACE_SECRET", str(process))
        self.assertEqual(local, [])

    def test_isolated_host_view_is_rejected_and_visible_owner_stays_separate(self):
        self.links["/host/proc/1/ns/pid"] = "pid:[4026532901]"
        self.access()
        resolved, rows, limited = self.ns["probe"](self.root.as_posix(), ["2349206"], "1001")
        self.assertEqual(resolved, {})
        self.assertFalse(limited)
        process = self.resolved_process("VRAM_METADATA NAMESPACE_UNMAPPED")
        self.assertEqual(process["owner_scope"], "unknown")
        self.assertEqual(process["metadata_reason"], "pid_namespace_unmapped")
        text = "\n".join(pid + "|" + data.encode().hex() for pid, data in rows).encode().hex()
        visible = _build_local_gpu_access(text, "1001", sampled_at=self.sampled_at)
        self.assertEqual(visible[0]["pid"], "15109")
        self.assertEqual(visible[0]["owner_scope"], "mine")
        self.assertNotIn("memory_used_gib", visible[0])
        self.assertNotIn("allocations", visible[0])
        self.assertNotIn("NAMESPACE_SECRET", str(visible))

    def test_sibling_namespace_cannot_borrow_a_matching_local_pid(self):
        self.process(self.host, "2349206", uid="2002", nspid="2349206 15109", command="other.py")
        self.links["/host/proc/2349206/ns/pid"] = "pid:[4026532901]"
        result = self.ns["resolve"](self.root.as_posix(), "/host/proc", "2349206")
        process = self.resolved_process(result)
        self.assertEqual(process["owner_scope"], "other")
        self.assertIsNone(process["visible_pid"])
        self.assertEqual(process["command_visibility"], "hidden_for_privacy")
        self.assertNotIn("train.py", str(process))

    def test_reused_local_pid_cannot_supply_a_mapping(self):
        self.process(self.root, "15109", ticks="201", command="unrelated.py")
        process = self.resolved_process(self.ns["resolve"](self.root.as_posix(), "/host/proc", "2349206"))
        self.assertIsNone(process["visible_pid"])
        self.assertEqual(process["name"], "train.py")

    def test_host_identity_change_during_metadata_read_is_rejected(self):
        original = self.ns["metadata"]
        def changing(root, pid):
            item = original(root, pid)
            if root == "/host/proc":
                self.process(self.host, "2349206", ticks="201", nspid="2349206 15109")
            return item
        self.ns["metadata"] = changing
        self.assertIsNone(self.ns["resolve"](self.root.as_posix(), "/host/proc", "2349206"))

    def test_mapping_change_during_local_read_is_rejected(self):
        original = self.ns["metadata"]
        def changing(root, pid):
            item = original(root, pid)
            if root == self.root.as_posix():
                self.process(self.host, "2349206", nspid="2349206 15110")
            return item
        self.ns["metadata"] = changing
        self.assertIsNone(self.ns["resolve"](self.root.as_posix(), "/host/proc", "2349206"))

    def test_unreadable_mount_binding_is_not_assumed_to_be_host_proc(self):
        del self.links["/host/proc/1/ns/pid"]
        self.assertEqual(self.ns["host_roots"](self.root.as_posix()), [])

    def test_non_proc_mount_or_subtree_cannot_be_a_host_source(self):
        for record in ("30 20 0:10 / /host/proc ro - ext4 dev ro\n",
                       "30 20 0:10 /subtree /host/proc ro - proc proc ro\n"):
            self.write(self.root / "self/mountinfo", record)
            self.assertEqual(self.ns["host_roots"](self.root.as_posix()), [])

    def test_device_access_requires_current_uid_and_an_actual_device_descriptor(self):
        self.access()
        self.process(self.root, "43", uid="2002")
        self.access("43")
        self.process(self.root, "44")
        self.access("44", "/dev/nvidiactl")
        self.process(self.root, "45")
        rows, _ = self.ns["device_users"](self.root.as_posix(), "1001", set())
        self.assertEqual([pid for pid, _ in rows], ["15109"])

    def test_optional_malformed_rows_do_not_invent_owners(self):
        values = ["invalid", "15109|nohex", "15109|" + b"VRAM_PROC\n15109 2002 other 1 python train.py".hex()]
        for text in values:
            self.assertEqual(_build_local_gpu_access(text.encode().hex(), "1001", sampled_at=self.sampled_at), [])

    def test_invalid_mapping_header_is_not_accepted(self):
        for header in ("VRAM_HOSTPROC 0", "VRAM_HOSTPROC 15109 extra", "VRAM_HOSTPROC unknown"):
            self.assertIsNone(_parse_process_metadata("2349206", header + "\nVRAM_PROC\n2349206 1001 owner 1 python"))

    @unittest.skipUnless(shutil.which("bash"), "bash unavailable")
    def test_generated_probe_rejects_pid_collision_without_optional_python(self):
        server = ServerProfile(id="synthetic", display_name="Synthetic", backend="direct_ssh", host="example.invalid")
        def execute(_server, script, **_kwargs):
            self.assertLessEqual(len(script.encode()), MAX_REMOTE_STDIN_BYTES)
            prelude = r'''
nvidia-smi() {
    case "$1" in
        --query-gpu=*) printf '0, GPU-a, Synthetic GPU, 8192, 4946, 3246, 80, 60\n' ;;
        *) printf 'GPU-a, 2349206, [Not Found], 4946\n' ;;
    esac
}
readlink() { printf 'pid:[4026532900]'; }
id() { case "$1" in -u) printf 1001 ;; *) printf owner ;; esac; }
ps() {
    case "$1" in -eo) printf '2349206 1001\n' ;; *) printf '2349206 1001 false-owner 42 1 python unrelated.py\n' ;; esac
}
python3() { return 1; }
'''
            completed = subprocess.run([shutil.which("bash"), "--noprofile", "--norc"], input=prelude + script,
                                       text=True, capture_output=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout
        with patch("vram_radar.connectors.run_remote", side_effect=execute):
            snapshot = query_direct_ssh(server)
        process = snapshot["processes"]["active"][0]
        self.assertEqual(process["owner_scope"], "unknown")
        self.assertEqual(process["metadata_reason"], "pid_namespace_unmapped")
        self.assertIsNone(process["user"])
        self.assertEqual(process["memory_used_gib"], 4.83)
        self.assertFalse(snapshot["processes"]["local_gpu_access_supported"])
