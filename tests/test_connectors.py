import os
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vram_radar.askpass import request_password

from vram_radar.connectors import (
    MAX_CONCURRENT_DIRECTORY_QUERIES,
    MAX_CONCURRENT_REMOTE_CAPTURES,
    ConnectorFailure,
    allocated_gpus_from_tasks,
    build_node_view,
    classify_process_error,
    job_ids_by_node_from_tasks,
    parse_history_task_rows,
    parse_job_rows,
    parse_live_task_rows,
    parse_node_rows,
    parse_nvidia_smi_rows,
    parse_directory_protocol,
    parse_directory_version_protocol,
    query_account_directory,
    query_account_directory_version,
    query_direct_ssh,
    query_slurm_ssh,
    redact_command_preview,
    resolve_identity_path,
    resolve_ssh_config_path,
    run_remote,
    ssh_copy_argv,
    ssh_copy_details,
    ssh_login_argv,
    ssh_argv,
)
from vram_radar.models import ServerProfile


class ConnectorTests(unittest.TestCase):
    @staticmethod
    def direct_protocol(
        *,
        gpu_rows: str,
        process_a: str = "",
        process_b: str = "",
        metadata: dict[str, str] | None = None,
        process_supported: bool = True,
        home_directory: str = "/srv/vram-radar-account",
    ) -> str:
        encode = lambda value: value.encode("utf-8").hex()
        lines = [
            "VRAM_RADAR_DIRECT|1",
            f"HOST_HEX={encode('gpu-node')}",
            "CURRENT_UID=1001",
            f"CURRENT_USER_HEX={encode('alice')}",
            f"HOME_HEX={encode(home_directory)}",
            f"GPU_HEX={encode(gpu_rows)}",
            f"PROCESS_A_SUPPORTED={int(process_supported)}",
            f"PROCESS_A_HEX={encode(process_a)}",
            "METADATA_LIMIT=128",
        ]
        for pid, value in (metadata or {}).items():
            lines.append(f"META|{pid}|OK|{encode(value)}")
        lines.extend(
            [
                f"PROCESS_B_SUPPORTED={int(process_supported)}",
                f"PROCESS_B_HEX={encode(process_b)}",
                "END|1",
            ]
        )
        return "\n".join(lines) + "\n"

    def test_parse_nvidia_smi(self):
        rows = parse_nvidia_smi_rows("0, NVIDIA GeForce RTX 4090, 24564, 15, 24095, 0, 31\n")
        self.assertEqual(rows[0]["memory_free_gib"], 23.53)
        self.assertEqual(rows[0]["temperature_c"], 31.0)

    @patch("vram_radar.connectors.run_remote")
    def test_direct_query_groups_multi_gpu_processes_and_redacts_commands(self, remote):
        canary = "CANARY-SECRET-4090"
        gpu_rows = (
            "0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 12000, 12564, 80, 60\n"
            "1, GPU-b, NVIDIA GeForce RTX 4090, 24564, 10000, 14564, 70, 58"
        )
        stable = (
            f"GPU-a, 1234, /usr/bin/python--token={canary}, 8192\n"
            f"GPU-b, 1234, /usr/bin/python--token={canary}, 4096\n"
        )
        process_a = stable + "GPU-a, 9999, short-lived, 100\n"
        process_b = stable + "GPU-b, 7777, new-process, 100\n"
        remote.return_value = self.direct_protocol(
            gpu_rows=gpu_rows,
            process_a=process_a,
            process_b=process_b,
            metadata={
                "1234": (
                    "1234 1001 alice 3661 /opt/python train.py --run-name exp-a "
                    f"--api-key={canary} https://bob:password@example.test/data"
                ),
                "9999": "9999 1002 bob 10 short-lived",
            },
        )
        server = ServerProfile(id="gpu", display_name="4090", backend="direct_ssh", ssh_alias="gpu-alias")

        snapshot = query_direct_ssh(server)

        self.assertEqual(snapshot["total_gpus"], 2)
        self.assertEqual(snapshot["total_vram_gib"], 47.98)
        self.assertEqual(
            snapshot["account"],
            {"username": "alice", "home_directory": "/srv/vram-radar-account"},
        )
        self.assertTrue(snapshot["processes"]["supported"])
        self.assertEqual(len(snapshot["processes"]["active"]), 1)
        process = snapshot["processes"]["active"][0]
        self.assertEqual(process["owner_scope"], "mine")
        self.assertEqual(process["name"], "exp-a · train.py")
        self.assertEqual(process["memory_used_gib"], 12.0)
        self.assertEqual([item["gpu_index"] for item in process["allocations"]], ["0", "1"])
        self.assertEqual(snapshot["processes"]["dropped_transient_count"], 1)
        self.assertEqual(snapshot["processes"]["deferred_new_count"], 1)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn(canary, serialized)
        self.assertNotIn("bob:password", serialized)
        self.assertIn("[已隐藏]", serialized)
        script = remote.call_args.args[1]
        self.assertIn("--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", script)
        self.assertIn("ps -ww", script)
        self.assertIn('owner[pid] == current_uid ? 0', script)
        self.assertIn("sort -k1,1n -k2,2n", script)
        self.assertIn("sed -n '1,128p'", script)
        self.assertNotIn("nvitop", script)

    @patch("vram_radar.connectors.run_remote")
    def test_direct_process_failure_does_not_hide_gpu_memory(self, remote):
        remote.return_value = self.direct_protocol(
            gpu_rows="0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 15, 24549, 0, 31",
            process_supported=False,
        )
        server = ServerProfile(id="gpu", display_name="4090", backend="direct_ssh", ssh_alias="gpu-alias")

        snapshot = query_direct_ssh(server)

        self.assertEqual(snapshot["gpus"][0]["memory_free_gib"], 23.97)
        self.assertFalse(snapshot["processes"]["supported"])
        self.assertEqual(snapshot["processes"]["active"], [])

    @patch("vram_radar.connectors.run_remote")
    def test_missing_process_metadata_is_not_misclassified_as_other_user(self, remote):
        process_rows = "GPU-a, 1234, worker, 1024"
        remote.return_value = self.direct_protocol(
            gpu_rows="0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 1024, 23540, 10, 40",
            process_a=process_rows,
            process_b=process_rows,
        )
        server = ServerProfile(id="gpu", display_name="4090", backend="direct_ssh", ssh_alias="gpu-alias")

        process = query_direct_ssh(server)["processes"]["active"][0]

        self.assertEqual(process["owner_scope"], "unknown")
        self.assertIsNone(process["user"])
        self.assertEqual(process["metadata_visibility"], "none")

    @patch("vram_radar.connectors.run_remote")
    def test_other_user_command_is_hidden_by_default(self, remote):
        canary = "OTHER-USER-CANARY"
        process_rows = "GPU-a, 2222, /usr/bin/python, 2048"
        remote.return_value = self.direct_protocol(
            gpu_rows="0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 2048, 22516, 10, 40",
            process_a=process_rows,
            process_b=process_rows,
            metadata={
                "2222": (
                    f"2222 1002 bob 7200 python train.py --run-name {canary} "
                    f"--env API_TOKEN {canary}"
                )
            },
        )
        server = ServerProfile(id="gpu", display_name="4090", backend="direct_ssh", ssh_alias="gpu-alias")

        snapshot = query_direct_ssh(server)
        process = snapshot["processes"]["active"][0]

        self.assertEqual(process["owner_scope"], "other")
        self.assertEqual(process["name"], "python")
        self.assertIsNone(process["command_preview"])
        self.assertEqual(process["command_visibility"], "hidden_for_privacy")
        self.assertNotIn(canary, json.dumps(snapshot, ensure_ascii=False))

    @patch("vram_radar.connectors.run_remote")
    def test_other_user_command_can_be_shown_only_as_a_redacted_preview(self, remote):
        canary = "OTHER-USER-SECRET"
        process_rows = "GPU-a, 2222, /usr/bin/python, 2048"
        remote.return_value = self.direct_protocol(
            gpu_rows="0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 2048, 22516, 10, 40",
            process_a=process_rows,
            process_b=process_rows,
            metadata={
                "2222": (
                    "2222 1002 bob 7200 python train.py --run-name team-ablation "
                    f"--env API_TOKEN {canary} https://alice:{canary}@example.test/data"
                )
            },
        )
        server = ServerProfile(
            id="gpu",
            display_name="4090",
            backend="direct_ssh",
            ssh_alias="gpu-alias",
            show_other_user_commands=True,
        )

        snapshot = query_direct_ssh(server)
        process = snapshot["processes"]["active"][0]

        self.assertEqual(process["owner_scope"], "other")
        self.assertEqual(process["name"], "team-ablation · train.py")
        self.assertEqual(process["command_visibility"], "redacted_other")
        self.assertIn("--run-name team-ablation", process["command_preview"])
        self.assertIn("[已隐藏]", process["command_preview"])
        self.assertNotIn(canary, json.dumps(snapshot, ensure_ascii=False))

    def test_command_redaction_covers_flags_assignments_and_url_userinfo(self):
        canary = "CANARY-SECRET"
        preview, _ = redact_command_preview(
            f"API_TOKEN={canary} train --password {canary} --api-key={canary} "
            f"--header 'X-Api-Key: {canary}' --header 'Bearer {canary}' --env API_TOKEN {canary} "
            f"https://alice:{canary}@example.test"
        )
        self.assertNotIn(canary, preview)
        self.assertGreaterEqual(preview.count("[已隐藏]"), 7)

    def test_build_slurm_snapshot(self):
        nodes = parse_node_rows("a100-1|GPU-LARGE|mix|gpu:A100-40G:2\n")
        allocation = "a100-1|cpu=8,gres/gpu:A100-40G=1\n"
        jobs = parse_job_rows(allocation)
        live = parse_live_task_rows(
            "8123|RUNNING|alice|01:30|02:00|2026-08-26T02:00:00|a100-1|a100-1|1|gres/gpu:A100-40G:1|world-model-train\n"
        )
        view = build_node_view(
            nodes,
            jobs,
            {"A100-40G": 40},
            job_ids_by_node=job_ids_by_node_from_tasks(live),
            live_tasks=live,
        )
        self.assertEqual(view[0]["free_gpus"], 1)
        self.assertEqual(view[0]["free_vram_gib"], 40)
        self.assertEqual(view[0]["tasks"][0]["job_id"], "8123")

    def test_slurm_inventory_deduplicates_partition_rows_and_accepts_untyped_gpus(self):
        nodes = parse_node_rows(
            "gpu-1|short|idle|gpu:4\n"
            "gpu-1|long*|idle|gpu:4\n"
        )

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["total_gpus"], 4)
        self.assertEqual(nodes[0]["gpu_entries"], [("GPU", 4)])
        self.assertEqual(nodes[0]["partitions"], ["short", "long"])

    def test_draining_node_never_contributes_free_capacity(self):
        nodes = parse_node_rows("gpu-1|short|drng|gpu:A100:4\n")
        view = build_node_view(nodes, {}, {"A100": 80})

        self.assertEqual(view[0]["free_gpus"], 0)
        self.assertEqual(view[0]["free_vram_gib"], 0)

    @patch("vram_radar.connectors.run_remote")
    def test_slurm_query_uses_node_local_allocations_for_multi_node_job(self, remote):
        home_hex = "/srv/vram-radar-macos".encode("utf-8").hex()
        remote.return_value = (
            "__VRAM_RADAR_CURRENT_USER__=alice\n"
            f"__VRAM_RADAR_HOME_HEX__={home_hex}\n"
            "a100-1|GPU-LARGE|mix|gpu:A100-40G:2\n"
            "a100-2|GPU-LARGE|mix|gpu:A100-40G:2\n"
            "__VRAM_RADAR_SPLIT__\n"
            "a100-1|cpu=8,gres/gpu=1\n"
            "a100-2|cpu=8,gres/gpu=1\n"
            "__VRAM_RADAR_LIVE_TASKS__\n"
            "8123|RUNNING|alice|01:30|02:00|2026-08-26T02:00:00|a100-[1-2]|a100-[1-2]|2|gres/gpu:2|world-model-train\n"
            "__VRAM_RADAR_TASK_HISTORY__\n"
            "__VRAM_RADAR_HISTORY_SUPPORTED__=0\n"
        )
        server = ServerProfile(
            id="a100",
            display_name="A100",
            backend="slurm_ssh",
            ssh_alias="a100-alias",
            gpu_memory_gib={"A100-40G": 40},
        )

        snapshot = query_slurm_ssh(server)

        self.assertEqual([node["allocated_gpus"] for node in snapshot["nodes"]], [2, 2])
        self.assertEqual(snapshot["free_gpus"], 0)
        self.assertEqual(snapshot["total_vram_gib"], 160)
        self.assertEqual(snapshot["tasks"]["current_user"], "alice")
        self.assertEqual(
            snapshot["account"],
            {"username": "alice", "home_directory": "/srv/vram-radar-macos"},
        )
        self.assertEqual(snapshot["tasks"]["active"][0]["user"], "alice")
        self.assertEqual(snapshot["tasks"]["active"][0]["name"], "world-model-train")
        self.assertEqual(snapshot["tasks"]["active"][0]["gpu_count"], 4)
        script = remote.call_args.args[1]
        self.assertIn("$(id -un)", script)
        self.assertIn("${HOME:-}", script)
        self.assertIn("scontrol show nodes -o", script)
        self.assertIn("%i|%T|%u|%M|%l|%V|%N|%R|%D|%b|%j", script)
        self.assertIn("JobIDRaw,State,User,Elapsed,End,NodeList,ReqTRES,AllocTRES,ExitCode,JobName%256", script)
        self.assertNotIn("scontrol show hostnames", script)
        self.assertNotIn("squeue -t RUNNING -h -o '%i|%N|%b'", script)

    @patch("vram_radar.connectors.run_remote")
    def test_account_directory_query_is_bounded_and_never_reads_file_contents(self, remote):
        encode = lambda value: value.encode("utf-8").hex()
        remote.return_value = "\n".join(
            [
                "VRAM_RADAR_DIRECTORY|1",
                f"USER_HEX={encode('alice')}",
                f"HOME_HEX={encode('/srv/vram-radar-account')}",
                f"ROOT_HEX={encode('/srv/vram-radar-account/projects/radar')}",
                "ROOT_SOURCE=auto",
                f"ROOT_VERSION_HEX={encode('2049:42:4096:1787932800:1787932801')}",
                "WARNING_HEX=",
                "LIMIT=160",
                "MAX_DEPTH=1",
                "SUPPORTED=1",
                f"ENTRY|{encode('src')}|directory|4096|1787932800",
                f"ENTRY|{encode('notes.txt')}|file|1536|1787932860",
                f"ENTRY|{encode('latest')}|symlink|12|1787932920",
                "TRUNCATED=0",
                "END|1",
            ]
        ) + "\n"
        server = ServerProfile(id="gpu", display_name="4090", backend="direct_ssh", ssh_alias="gpu-alias")

        account = query_account_directory(server)

        self.assertEqual(account["username"], "alice")
        self.assertEqual(account["home_directory"], "/srv/vram-radar-account")
        tree = account["directory_tree"]
        self.assertEqual(tree["max_depth"], 1)
        self.assertEqual(tree["entry_limit"], 160)
        self.assertEqual(tree["root"], "/srv/vram-radar-account/projects/radar")
        self.assertEqual(tree["root_source"], "auto")
        self.assertEqual(tree["version_token"], "2049:42:4096:1787932800:1787932801")
        self.assertEqual([entry["kind"] for entry in tree["entries"]], ["directory", "file", "symlink"])
        self.assertEqual(tree["entries"][1]["size_bytes"], 1536)
        self.assertEqual(tree["entries"][2]["parent_path"], "")
        self.assertEqual(
            tree["entries"][2]["absolute_path"],
            "/srv/vram-radar-account/projects/radar/latest",
        )
        script = remote.call_args.args[1]
        self.assertIn("entry_count > 160", script)
        self.assertIn('find "$root_dir" -mindepth 1 -maxdepth 1 -print0', script)
        self.assertIn("with os.scandir(root) as scanner", script)
        self.assertIn("itertools.islice(scanner, limit + 1)", script)
        self.assertIn("follow_symlinks=False", script)
        self.assertIn("if command -v python3", script)
        self.assertIn("else\n    entry_count=0", script)
        self.assertIn("candidate_budget=160", script)
        self.assertIn("printf 'ROOT_VERSION_HEX='", script)
        self.assertNotIn("scanned <= 160", script)
        self.assertNotIn("scanned <= 240", script)
        self.assertNotIn('children=("$parent"/*)', script)
        self.assertIn("[ -L \"$entry\" ]", script)
        self.assertIn("pyproject.toml package.json Cargo.toml", script)
        self.assertIn("$home_dir/Documents/GitHub", script)
        self.assertNotIn("cat ", script)
        self.assertNotIn("readlink", script)

    @patch("vram_radar.connectors.run_remote")
    def test_directory_query_requests_a_specific_root_without_shell_interpolation(self, remote):
        encode = lambda value: value.encode("utf-8").hex()
        remote.return_value = "\n".join(
            [
                "VRAM_RADAR_DIRECTORY|1",
                f"USER_HEX={encode('alice')}",
                f"HOME_HEX={encode('/srv/vram-radar-account')}",
                f"ROOT_HEX={encode('/srv/vram-radar-account/work dir/project')}",
                "ROOT_SOURCE=requested",
                "WARNING_HEX=",
                "LIMIT=160",
                "MAX_DEPTH=1",
                "SUPPORTED=1",
                "TRUNCATED=0",
                "END|1",
            ]
        )
        server = ServerProfile(id="gpu", display_name="GPU", backend="direct_ssh", ssh_alias="gpu")

        account = query_account_directory(
            server,
            root_path="/srv/vram-radar-account/work dir/project",
            root_source="requested",
        )

        self.assertEqual(account["directory_tree"]["root"], "/srv/vram-radar-account/work dir/project")
        self.assertIn("requested_root='/srv/vram-radar-account/work dir/project'", remote.call_args.args[1])

    @patch("vram_radar.connectors.run_remote")
    def test_directory_version_probe_reads_only_bounded_root_metadata(self, remote):
        encode = lambda value: value.encode("utf-8").hex()
        remote.return_value = "\n".join(
            [
                "VRAM_RADAR_DIRECTORY_VERSION|1",
                f"ROOT_HEX={encode('/srv/alice/code')}",
                f"VERSION_HEX={encode('2049:42:4096:1787932800:1787932801')}",
                "SUPPORTED=1",
                "END|1",
            ]
        )
        server = ServerProfile(id="gpu", display_name="GPU", backend="direct_ssh", ssh_alias="gpu")

        result = query_account_directory_version(server, root_path="/srv/alice/code")

        self.assertTrue(result["supported"])
        self.assertEqual(result["root"], "/srv/alice/code")
        self.assertEqual(result["version_token"], "2049:42:4096:1787932800:1787932801")
        script = remote.call_args.args[1]
        self.assertIn("metadata = os.stat(sys.argv[1], follow_symlinks=False)", script)
        self.assertIn("metadata.st_mtime_ns, metadata.st_ctime_ns", script)
        self.assertIn("stat -c '%d:%i:%s:%Y:%Z'", script)
        self.assertIn("stat -f '%d:%i:%z:%m:%c'", script)
        self.assertNotIn("find ", script)
        self.assertNotIn("scandir", script)
        self.assertNotIn("cat ", script)

    def test_directory_version_protocol_rejects_unsafe_or_inconsistent_responses(self):
        encode = lambda value: value.encode("utf-8").hex()
        with self.assertRaises(ConnectorFailure):
            parse_directory_version_protocol("\n".join([
                "VRAM_RADAR_DIRECTORY_VERSION|1",
                f"ROOT_HEX={encode('../escape')}",
                f"VERSION_HEX={encode('token')}",
                "SUPPORTED=1",
                "END|1",
            ]))
        with self.assertRaises(ConnectorFailure):
            parse_directory_version_protocol("\n".join([
                "VRAM_RADAR_DIRECTORY_VERSION|1",
                f"ROOT_HEX={encode('/srv/alice/code')}",
                "VERSION_HEX=",
                "SUPPORTED=1",
                "END|1",
            ]))

    def test_directory_protocol_rejects_parent_traversal(self):
        encode = lambda value: value.encode("utf-8").hex()
        output = "\n".join(
            [
                "VRAM_RADAR_DIRECTORY|1",
                f"USER_HEX={encode('alice')}",
                f"HOME_HEX={encode('/srv/vram-radar-account')}",
                f"ROOT_HEX={encode('/srv/vram-radar-account')}",
                "ROOT_SOURCE=home",
                "WARNING_HEX=",
                "LIMIT=160",
                "MAX_DEPTH=1",
                "SUPPORTED=1",
                f"ENTRY|{encode('../etc/passwd')}|file|1|1787932800",
                "TRUNCATED=0",
                "END|1",
            ]
        )

        with self.assertRaises(ConnectorFailure):
            parse_directory_protocol(output)

    @patch("vram_radar.connectors.run_remote")
    def test_directory_queries_have_a_dedicated_concurrency_budget(self, remote):
        encode = lambda value: value.encode("utf-8").hex()
        response = "\n".join(
            [
                "VRAM_RADAR_DIRECTORY|1",
                f"USER_HEX={encode('alice')}",
                f"HOME_HEX={encode('/srv/alice')}",
                f"ROOT_HEX={encode('/srv/alice/code')}",
                "ROOT_SOURCE=requested",
                "WARNING_HEX=",
                "LIMIT=160",
                "MAX_DEPTH=1",
                "SUPPORTED=1",
                "TRUNCATED=0",
                "END|1",
            ]
        )
        condition = threading.Condition()
        release = threading.Event()
        active = 0
        maximum_active = 0

        def capture(*_args, **_kwargs):
            nonlocal active, maximum_active
            with condition:
                active += 1
                maximum_active = max(maximum_active, active)
                if maximum_active == MAX_CONCURRENT_DIRECTORY_QUERIES:
                    release.set()
                condition.notify_all()
            self.assertTrue(release.wait(5))
            with condition:
                active -= 1
            return response

        remote.side_effect = capture
        server = ServerProfile(id="gpu", display_name="GPU", backend="direct_ssh", ssh_alias="gpu")
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda index: query_account_directory(
                        server,
                        root_path=f"/srv/alice/code-{index}",
                        root_source="requested",
                    ),
                    range(6),
                )
            )

        self.assertEqual(len(results), 6)
        self.assertEqual(maximum_active, MAX_CONCURRENT_DIRECTORY_QUERIES)

    def test_slurm_task_parsers_keep_gpu_jobs_and_recent_terminal_state(self):
        active = parse_live_task_rows(
            "90|PENDING|bob|0:00|1:00|2026-08-26T02:00:00|(null)|Resources|1|gres/gpu:1|ours|seed=3\n"
            "91|RUNNING|carol|0:10|1:00|2026-08-26T02:10:00|cpu-1|cpu-1|1|N/A|cpu-only\n"
        )
        supported, recent = parse_history_task_rows(
            "__VRAM_RADAR_HISTORY_SUPPORTED__=1\n"
            "88|COMPLETED|alice|00:20:00|2026-08-26T03:00:00|a100-1|cpu=8,gres/gpu=1||0:0|other user|ablation\n"
            "88.batch|COMPLETED|alice|00:20:00|2026-08-26T03:00:00|a100-1|gres/gpu=1||0:0|batch-step\n"
        )
        self.assertEqual([task["job_id"] for task in active], ["90"])
        self.assertEqual(active[0]["user"], "bob")
        self.assertEqual(active[0]["submitted_at"], "2026-08-26T02:00:00")
        self.assertEqual(active[0]["reason"], "Resources")
        self.assertEqual(active[0]["name"], "ours|seed=3")
        self.assertTrue(supported)
        self.assertEqual([task["state"] for task in recent], ["COMPLETED"])
        self.assertEqual(recent[0]["user"], "alice")
        self.assertEqual(recent[0]["name"], "other user|ablation")

    def test_multinode_gpu_counts_and_compressed_history_nodes(self):
        active = parse_live_task_rows(
            "90|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|gpu[01-02]|gpu[01-02]|2|gres/gpu:2|distributed-train\n"
        )
        supported, recent = parse_history_task_rows(
            "__VRAM_RADAR_HISTORY_SUPPORTED__=1\n"
            "88|COMPLETED|bob|00:20|2026-08-26T03:00:00|gpu[01-02]|cpu=8||0:0|history-train\n",
            {"gpu01", "gpu02"},
        )
        self.assertEqual(active[0]["gpu_count"], 4)
        self.assertTrue(supported)
        self.assertEqual([task["job_id"] for task in recent], ["88"])

    def test_visible_tasks_fill_missing_slurm_gpu_allocations(self):
        tasks = parse_live_task_rows(
            "90|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|gpu[01-02]|gpu[01-02]|2|gres/gpu:2|distributed-train\n"
        )

        self.assertEqual(allocated_gpus_from_tasks(tasks), {"gpu01": 2, "gpu02": 2})

    def test_compressed_multinode_tasks_are_mapped_locally_to_every_node(self):
        tasks = parse_live_task_rows(
            "90|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|rack[1-2]gpu[01-02]|rack[1-2]gpu[01-02]|4|gres/gpu:1|distributed-train\n"
            "91|RUNNING|bob|00:05|01:00|2026-08-26T02:05:00|rack2gpu[01-02]|rack2gpu[01-02]|2|gres/gpu:1|evaluation\n"
        )

        mapping = job_ids_by_node_from_tasks(tasks)

        self.assertEqual(set(mapping), {"rack1gpu01", "rack1gpu02", "rack2gpu01", "rack2gpu02"})
        self.assertEqual(mapping["rack1gpu01"], ["90"])
        self.assertEqual(mapping["rack2gpu02"], ["90", "91"])

    def test_large_compressed_cluster_mapping_is_complete_and_bounded(self):
        tasks = parse_live_task_rows(
            "900|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|gpu[0000-4095]|gpu[0000-4095]|4096|gres/gpu:1|large-train\n"
        )

        mapping = job_ids_by_node_from_tasks(tasks)
        allocations = allocated_gpus_from_tasks(tasks)

        self.assertEqual(len(mapping), 4096)
        self.assertEqual(mapping["gpu0000"], ["900"])
        self.assertEqual(mapping["gpu4095"], ["900"])
        self.assertEqual(len(allocations), 4096)
        self.assertEqual(sum(allocations.values()), 4096)

    def test_compressed_node_stride_does_not_expand_past_range_end(self):
        tasks = parse_live_task_rows(
            "900|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|gpu[01-06:2]|gpu[01-06:2]|3|gres/gpu:1|strided-train\n"
        )

        self.assertEqual(set(job_ids_by_node_from_tasks(tasks)), {"gpu01", "gpu03", "gpu05"})

    def test_pathological_compressed_node_range_fails_at_domain_limit(self):
        tasks = parse_live_task_rows(
            "900|RUNNING|alice|00:10|01:00|2026-08-26T02:00:00|gpu[1-999999999]|gpu[1-999999999]|1|gres/gpu:1|invalid-scale\n"
        )

        with self.assertRaises(ConnectorFailure) as raised:
            job_ids_by_node_from_tasks(tasks)

        self.assertEqual(raised.exception.code, "node_list_too_large")

    def test_alias_and_direct_host_arguments_are_separate(self):
        alias = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")
        direct = ServerProfile(id="b", display_name="B", backend="direct_ssh", host="gpu.test", port=10022, username="alice")
        self.assertIn("gpu-alias", ssh_argv(alias, "hostname"))
        direct_argv = ssh_argv(direct, "hostname")
        self.assertIn("alice@gpu.test", direct_argv)
        self.assertIn("10022", direct_argv)

    def test_alias_overrides_are_canonical_and_target_is_option_terminated(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="gpu-alias",
            host="gpu.example",
            username="alice",
            port=10022,
            port_override=True,
        )

        argv = ssh_login_argv(server)

        self.assertEqual(argv[-2:], ["--", "gpu-alias"])
        self.assertIn("HostName=gpu.example", argv)
        self.assertEqual(argv[argv.index("-l") + 1], "alice")
        self.assertEqual(argv[argv.index("-p") + 1], "10022")

    def test_alias_connection_explicitly_uses_existing_default_openssh_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".ssh" / "config"
            config.parent.mkdir()
            config.write_text("Host gpu-alias\n  HostName gpu.example\n", encoding="utf-8")
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-alias",
            )
            with patch("vram_radar.connectors.Path.home", return_value=home):
                argv = ssh_login_argv(server)

        self.assertEqual(argv[argv.index("-F") + 1], str(config))
        self.assertEqual(argv[-2:], ["--", "gpu-alias"])

    def test_relative_identity_and_config_paths_share_the_ssh_directory_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch("vram_radar.connectors.Path.home", return_value=home):
                server = ServerProfile(
                    id="a",
                    display_name="A",
                    backend="direct_ssh",
                    ssh_alias="gpu-alias",
                    identity_file="id_ed25519",
                    ssh_config_file="config.work",
                )
                self.assertEqual(
                    resolve_identity_path(server),
                    str((home / ".ssh" / "id_ed25519").resolve()),
                )
                self.assertEqual(
                    resolve_ssh_config_path(server),
                    str((home / ".ssh" / "config.work").resolve()),
                )

    def test_home_variable_paths_fall_back_to_platform_home_when_environment_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.dict(os.environ, {}, clear=True), patch(
                "vram_radar.connectors.Path.home", return_value=home
            ):
                for value in (
                    "$HOME/.ssh/id_ed25519",
                    "${HOME}/.ssh/id_ed25519",
                    "$env:USERPROFILE/.ssh/id_ed25519",
                    "%USERPROFILE%/.ssh/id_ed25519",
                ):
                    server = ServerProfile(
                        id="a",
                        display_name="A",
                        backend="direct_ssh",
                        ssh_alias="gpu-alias",
                        identity_file=value,
                    )
                    with self.subTest(value=value):
                        self.assertEqual(
                            resolve_identity_path(server),
                            str((home / ".ssh" / "id_ed25519").resolve()),
                        )

    def test_imported_alias_uses_its_exact_openssh_config_file(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="editor-only-alias",
            ssh_config_file="D:/ssh/editor-ssh.conf",
        )

        argv = ssh_argv(server, "hostname")

        expected_config = (
            str(Path("D:/ssh/editor-ssh.conf").resolve())
            if os.name == "nt"
            else "D:/ssh/editor-ssh.conf"
        )
        self.assertEqual(argv[1:3], ["-F", expected_config])
        self.assertIn("editor-only-alias", argv)

    def test_key_auth_keeps_openssh_config_identity_and_agent_path(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="gpu-key-alias",
            ssh_config_file="/srv/tester/.ssh/config",
            identity_file="/srv/tester/.ssh/id_ed25519",
        )

        argv = ssh_argv(server, "hostname")
        joined = " ".join(argv)

        self.assertEqual(argv[1:3], ["-F", "/srv/tester/.ssh/config"])
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("-i /srv/tester/.ssh/id_ed25519", joined)
        self.assertIn("gpu-key-alias", argv)
        self.assertNotIn("PubkeyAuthentication=no", joined)
        self.assertNotIn("PreferredAuthentications=password", joined)

    def test_identity_verification_disables_unrelated_agent_keys(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="gpu-key-alias",
            identity_file="/srv/tester/.ssh/id_ed25519",
        )

        joined = " ".join(ssh_argv(server, "hostname", identities_only=True))

        self.assertIn("IdentitiesOnly=yes", joined)
        self.assertIn("-i /srv/tester/.ssh/id_ed25519", joined)

    def test_copied_login_preserves_preferred_identity_only_semantics(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="gpu-key-alias",
            identity_file="/srv/tester/.ssh/id_ed25519",
            prefer_identity_auth=True,
        )

        self.assertIn("IdentitiesOnly=yes", ssh_login_argv(server))

    def test_copied_alias_expands_exact_endpoint_and_preserves_config_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName 192.0.2.44\n"
                "  User alice\n"
                "  Port 22022\n"
                "  ProxyJump bastion\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            argv = ssh_copy_argv(server)

        self.assertEqual(argv[1:3], ["-F", str(config.resolve())])
        self.assertIn("HostName=192.0.2.44", argv)
        self.assertEqual(argv[argv.index("-l") + 1], "alice")
        self.assertEqual(argv[argv.index("-p") + 1], "22022")
        self.assertEqual(argv[-2:], ["--", "gpu-copy"])

    def test_copy_does_not_freeze_user_or_port_omitted_from_the_selected_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName 192.0.2.44\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            details = ssh_copy_details(server)

        self.assertFalse(details.endpoint_complete)
        self.assertEqual(details.resolution.reason, "endpoint_fields_unspecified")
        self.assertEqual(details.argv, ("ssh", "-F", str(config.resolve()), "--", "gpu-copy"))
        self.assertNotIn("-l", details.argv)
        self.assertNotIn("-p", details.argv)

    def test_copied_alias_resolves_includes_with_first_value_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragment = root / "gpu.conf"
            fragment.write_text(
                "Host gpu-copy\n"
                "  HostName first.example\n"
                "  User alice\n"
                "  Port 2222\n",
                encoding="utf-8",
            )
            config = root / "config"
            config.write_text(
                f'Include "{fragment}"\n'
                "Host *\n"
                "  HostName must-not-win.example\n"
                "  User fallback\n"
                "  Port 22\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            argv = ssh_copy_argv(server)

        self.assertIn("HostName=first.example", argv)
        self.assertNotIn("HostName=must-not-win.example", argv)
        self.assertEqual(argv[argv.index("-l") + 1], "alice")
        self.assertEqual(argv[argv.index("-p") + 1], "2222")

    def test_copied_alias_does_not_guess_through_conditional_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName 192.0.2.44\n"
                "  User alice\n"
                'Match exec "test -f ~/.ssh/use-special-port"\n'
                "  Port 22022\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            with patch("vram_radar.connectors.subprocess.Popen") as popen:
                argv = ssh_copy_argv(server)

        popen.assert_not_called()
        self.assertEqual(argv, ["ssh", "-F", str(config.resolve()), "--", "gpu-copy"])

    def test_copy_details_exposes_incomplete_resolution_without_second_parse(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName 192.0.2.44\n"
                "  User alice\n"
                'Match exec "test -f ~/.ssh/use-special-port"\n'
                "  Port 22022\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            details = ssh_copy_details(server)

        self.assertFalse(details.endpoint_complete)
        self.assertEqual(details.resolution.status, "dynamic")
        self.assertEqual(details.resolution.reason, "conditional_match")
        self.assertTrue(details.warning)
        self.assertEqual(details.argv[-2:], ("--", "gpu-copy"))

    def test_copy_details_does_not_claim_unmatched_host_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host another-host\n"
                "  HostName 192.0.2.99\n"
                "  User bob\n"
                "  Port 22\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            details = ssh_copy_details(server)

        self.assertFalse(details.endpoint_complete)
        self.assertEqual(details.resolution.reason, "host_alias_not_found")
        self.assertFalse(any(value.startswith("HostName=") for value in details.argv))

    def test_match_uncertainty_survives_later_matching_host_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                'Match exec "test -f ~/.ssh/use-special-port"\n'
                "  Port 22022\n"
                "Host *\n"
                "  HostName 192.0.2.44\n"
                "  User alice\n"
                "  Port 22\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            details = ssh_copy_details(server)

        self.assertFalse(details.endpoint_complete)
        self.assertEqual(details.resolution.reason, "conditional_match")

    def test_copied_alias_does_not_guess_dynamic_hostname_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName %h.cluster.example\n"
                "  User alice\n"
                "  Port 22\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            argv = ssh_copy_argv(server)

        self.assertEqual(argv, ["ssh", "-F", str(config.resolve()), "--", "gpu-copy"])

    def test_copied_alias_does_not_recurse_through_include_cycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                f'Include "{config}"\n'
                "Host gpu-copy\n"
                "  HostName 192.0.2.44\n"
                "  User alice\n"
                "  Port 22\n",
                encoding="utf-8",
            )
            server = ServerProfile(
                id="a",
                display_name="A",
                backend="direct_ssh",
                ssh_alias="gpu-copy",
                ssh_config_file=str(config),
            )

            argv = ssh_copy_argv(server)

        self.assertEqual(argv, ["ssh", "-F", str(config.resolve()), "--", "gpu-copy"])

    def test_saved_password_selects_one_prompt_password_only_authentication(self):
        server = ServerProfile(
            id="a",
            display_name="A",
            backend="direct_ssh",
            ssh_alias="gpu-alias",
            identity_file="should-not-be-loaded-with-password",
        )

        argv = ssh_argv(server, "hostname", password_auth=True)

        joined = " ".join(argv)
        self.assertIn("BatchMode=no", joined)
        self.assertIn("NumberOfPasswordPrompts=1", joined)
        self.assertIn("PasswordAuthentication=yes", joined)
        self.assertIn("KbdInteractiveAuthentication=yes", joined)
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", joined)
        self.assertIn("PubkeyAuthentication=no", joined)
        self.assertNotIn("BatchMode=yes", joined)
        self.assertNotIn("should-not-be-loaded-with-password", joined)

    def test_security_and_auth_failures_do_not_retry(self):
        security = classify_process_error("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!")
        auth = classify_process_error("Permission denied (publickey).")
        self.assertEqual(security.state, "security_blocked")
        self.assertFalse(security.retryable)
        self.assertEqual(auth.state, "auth_required")
        self.assertFalse(auth.retryable)

    def test_password_retry_prompt_is_classified_as_authentication_failure(self):
        failure = classify_process_error(
            "Permission denied, please try again.",
            returncode=255,
            password_auth=True,
        )

        self.assertEqual(failure.code, "auth_failed")
        self.assertEqual(failure.state, "auth_required")
        self.assertFalse(failure.retryable)

    def test_encrypted_or_agent_refused_keys_have_actionable_auth_errors(self):
        encrypted = classify_process_error(
            'Load key "/srv/tester/.ssh/id_ed25519": incorrect passphrase supplied to decrypt private key\n'
            "Permission denied (publickey).",
            returncode=255,
        )
        refused = classify_process_error(
            "sign_and_send_pubkey: signing failed for ED25519 from agent: agent refused operation\n"
            "Permission denied (publickey).",
            returncode=255,
        )

        self.assertEqual(encrypted.code, "identity_passphrase_required")
        self.assertEqual(encrypted.state, "auth_required")
        self.assertEqual(refused.code, "ssh_agent_refused")
        self.assertEqual(refused.state, "auth_required")

    def test_authorized_keys_conflict_is_actionable_and_retryable(self):
        failure = classify_process_error("VRAM_RADAR_KEY_CONFLICT", returncode=46)

        self.assertEqual(failure.code, "ssh_key_remote_conflict")
        self.assertTrue(failure.retryable)
        self.assertIn("其他程序修改", str(failure))

    def test_local_proxy_command_missing_is_not_reported_as_remote_collector_success(self):
        failure = classify_process_error(
            "/bin/sh: nc: command not found",
            returncode=255,
        )

        self.assertEqual(failure.code, "proxy_command_missing")
        self.assertEqual(failure.state, "misconfigured")
        self.assertFalse(failure.retryable)

    @patch("vram_radar.connectors._run_bounded_process")
    def test_remote_query_is_noninteractive_and_windowless(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"", stdout_truncated=False)
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        self.assertEqual(run_remote(server, "hostname"), "ok")

        options = run.call_args.kwargs
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertIsNone(options["env"])
        self.assertEqual(options["stdout_limit"], 8 * 1024 * 1024)
        self.assertEqual(options["stderr_limit"], 64 * 1024)
        self.assertIsNone(options["input_data"])

    @patch("vram_radar.connectors._run_bounded_process")
    def test_remote_stdin_is_bounded_and_never_added_to_ssh_argv(self, run):
        public_line = b"ssh-ed25519 PUBLIC-BLOB\n"
        run.return_value = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"", stdout_truncated=False)
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        self.assertEqual(run_remote(server, "read key", stdin_data=public_line), "ok")

        argv = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertNotIn("PUBLIC-BLOB", " ".join(argv))
        self.assertEqual(options["stdin"], subprocess.PIPE)
        self.assertEqual(options["input_data"], public_line)

    @patch("vram_radar.connectors.ssh_argv")
    def test_remote_stdin_writer_completes_without_shell_or_environment_interpolation(self, argv):
        argv.return_value = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ]
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        self.assertEqual(run_remote(server, "ignored", stdin_data=b"public-only\n"), "public-only\n")

    def test_remote_stdin_rejects_unbounded_payload_before_starting_ssh(self):
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")
        with self.assertRaises(ConnectorFailure) as raised:
            run_remote(server, "ignored", stdin_data=b"x" * (16 * 1024 + 1))
        self.assertEqual(raised.exception.code, "request_too_large")

    @patch("vram_radar.connectors.MAX_REMOTE_STDOUT_BYTES", 256 * 1024)
    @patch("vram_radar.connectors._run_bounded_process")
    def test_near_limit_remote_captures_share_a_global_concurrency_budget(self, run):
        condition = threading.Condition()
        release = threading.Event()
        active = 0
        maximum_active = 0

        def capture(_argv, **options):
            nonlocal active, maximum_active
            payload = b"x" * (options["stdout_limit"] - 1)
            with condition:
                active += 1
                maximum_active = max(maximum_active, active)
                if maximum_active == MAX_CONCURRENT_REMOTE_CAPTURES:
                    release.set()
                condition.notify_all()
            self.assertTrue(release.wait(5))
            with condition:
                active -= 1
            return SimpleNamespace(
                returncode=0,
                stdout=payload,
                stderr=b"",
                stdout_truncated=False,
            )

        run.side_effect = capture
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REMOTE_CAPTURES + 4) as executor:
            results = list(
                executor.map(
                    lambda _: run_remote(server, "hostname"),
                    range(MAX_CONCURRENT_REMOTE_CAPTURES + 4),
                )
            )

        self.assertEqual(len(results), MAX_CONCURRENT_REMOTE_CAPTURES + 4)
        self.assertEqual(maximum_active, MAX_CONCURRENT_REMOTE_CAPTURES)

    @patch("vram_radar.connectors._askpass_executable", return_value="safe-askpass-helper")
    @patch("vram_radar.connectors._run_bounded_process")
    def test_password_reaches_askpass_only_through_scoped_loopback_broker(self, run, _helper):
        password = "correct horse battery staple !@#"

        def exercise_broker(_argv, **options):
            environment = options["env"]
            self.assertNotIn(password, str(_argv))
            self.assertNotIn(password, str(environment))
            recovered = request_password(
                environment["VRAM_RADAR_ASKPASS_ENDPOINT"],
                environment["VRAM_RADAR_ASKPASS_NONCE"],
            )
            self.assertEqual(recovered, password)
            self.assertEqual(environment["SSH_ASKPASS"], "safe-askpass-helper")
            self.assertEqual(environment["SSH_ASKPASS_REQUIRE"], "force")
            return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"", stdout_truncated=False)

        run.side_effect = exercise_broker
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        self.assertEqual(run_remote(server, "hostname", password=password), "ok")

    @patch("vram_radar.connectors._run_bounded_process")
    def test_remote_failure_never_surfaces_protocol_stdout_or_unknown_stderr(self, run):
        canary = "RAW-COMMAND-CANARY"
        encoded = f"python train.py --token {canary}".encode("utf-8").hex()
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout=f"VRAM_RADAR_DIRECT|1\nMETA|42|OK|{encoded}\n".encode(),
            stderr=f"unexpected remote detail {canary}".encode(),
            stdout_truncated=False,
        )
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        with self.assertRaises(ConnectorFailure) as raised:
            run_remote(server, "remote script")

        message = str(raised.exception)
        self.assertEqual(message, "SSH 已连接，但服务器环境检测命令执行失败")
        self.assertNotIn(canary, message)
        self.assertNotIn(encoded, message)

    @patch("vram_radar.connectors.MAX_REMOTE_STDOUT_BYTES", 8192)
    @patch("vram_radar.connectors.ssh_argv")
    def test_remote_stdout_overflow_is_killed_and_reported_as_domain_failure(self, argv):
        argv.return_value = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 1048576); sys.stdout.flush()",
        ]
        server = ServerProfile(id="a", display_name="A", backend="direct_ssh", ssh_alias="gpu-alias")

        with self.assertRaises(ConnectorFailure) as raised:
            run_remote(server, "remote script")

        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("x", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
