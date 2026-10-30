from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vram_radar.acp import build_list_command, parse_jobs, query_sensecore_acp
from vram_radar.connectors import ConnectorFailure, query_server
from vram_radar.models import ConfigError, ServerProfile
from vram_radar.server_catalog import import_server_catalog


def profile(**overrides):
    return ServerProfile.from_dict(dict(id="acp-test", display_name="ACP test", backend="sensecore_acp",
        acp_workspace="test-space", acp_cluster="test-cluster", acp_zone="cn-sh-01z",
        acp_profile="test-monitor", **overrides))


class ACPTests(unittest.TestCase):
    def test_profile_round_trip_without_ssh_or_credentials(self):
        p = profile()
        self.assertEqual(ServerProfile.from_dict(p.to_dict()), p)
        self.assertEqual(p.ssh_alias, "")
        self.assertNotIn("auth_ref", p.to_dict())
        for bad in ({"host": "gpu"}, {"acp_sco_path": "start.cmd"}):
            with self.assertRaises(ConfigError): profile(**bad)
        for key in ("acp_workspace", "acp_cluster", "acp_zone", "acp_profile"):
            raw = p.to_dict(); raw[key] = "--bad=flag"
            with self.assertRaises(ConfigError): ServerProfile.from_dict(raw)

    def test_command_is_fixed_bounded_list(self):
        argv = build_list_command(profile())
        self.assertEqual(argv[3:6], ["acp", "jobs", "list"])
        self.assertIn("--page-size=100", argv)
        self.assertIn("--page-token=1", argv)
        self.assertNotIn("--zone", argv)
        self.assertFalse(set(argv) & {"create", "copy", "stop", "delete", "init"})

    def test_job_parse_retains_unknown_memory_and_drops_sensitive_payload(self):
        jobs = parse_jobs([{"name": "pt-1", "state": "STARTING", "display_name": "test",
            "env": {"ENV_MARK": "x"}, "command": "omit-command",
            "roles": [{"resource_spec": [{"name": "h100.8.80gb"}]}]}])
        self.assertIsNone(jobs[0]["requested_gpus"])
        self.assertIsNone(jobs[0]["gpu_count"])
        self.assertNotIn("ENV_MARK", json.dumps(jobs))
        self.assertNotIn("omit-command", json.dumps(jobs))
        for bad in ({"error": "bad"}, [{}], [{"name": "same"}, {"name": "same"}]):
            with self.assertRaises(ConnectorFailure): parse_jobs(bad)

    @patch("vram_radar.acp.shutil.which", return_value="/usr/bin/sco")
    @patch("vram_radar.acp._run_bounded_process")
    def test_collection_does_not_invent_gpu_inventory_or_owned_jobs(self, run, which):
        run.return_value = SimpleNamespace(returncode=0, stdout=b'[{"name":"pt-1","state":"RUNNING"}]', stderr=b'', stdout_truncated=False)
        with patch.dict("os.environ", {"SCO_ACCESS_KEY_ID": "x", "SCO_ACCESS_KEY_SECRET": "x"}):
            result = query_server(profile())
        self.assertEqual(result["view_kind"], "platform")
        self.assertIsNone(result["free_vram_gib"])
        self.assertIsNone(result["total_gpus"])
        self.assertEqual(result["gpus"], [])
        self.assertTrue(result["platform"]["list_complete"])  # one job < page_size 100
        self.assertFalse(result["tasks"]["history_supported"])
        self.assertEqual(run.call_args.kwargs["env"]["SCO_ZONE"], "cn-sh-01z")
        self.assertNotIn("SCO_ACCESS_KEY_ID", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 60)



    @patch("vram_radar.acp.shutil.which", return_value="/usr/bin/sco")
    @patch("vram_radar.acp._run_bounded_process")
    def test_full_page_marks_list_incomplete_without_extra_probes(self, run, which):
        # Host transport may pin page_size=100; a full page must not claim completeness
        # or invent a second sco call. Truncation is why H100 looked "missing" jobs.
        rows = [{"name": f"pt-{i}", "state": "RUNNING"} for i in range(100)]
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(rows).encode(),
            stderr=b"",
            stdout_truncated=False,
        )
        result = query_sensecore_acp(profile())
        self.assertEqual(len(result["jobs"]), 100)
        self.assertEqual(result["platform"]["page_size"], 100)
        self.assertFalse(result["platform"]["list_complete"])
        self.assertEqual(run.call_count, 1)

    @patch("vram_radar.acp.shutil.which", return_value="/usr/bin/sco")
    @patch("vram_radar.acp._run_bounded_process")
    def test_failure_never_echoes_cli_details(self, run, which):
        run.return_value = SimpleNamespace(returncode=1, stdout=b'', stderr=b'Unauthenticated access_key=REDACTED', stdout_truncated=False)
        with self.assertRaises(ConnectorFailure) as ctx: query_sensecore_acp(profile())
        self.assertEqual(ctx.exception.code, "acp_auth_required")
        self.assertNotIn("REDACTED", str(ctx.exception))
        run.side_effect = subprocess.TimeoutExpired("sco", 10)
        with self.assertRaises(ConnectorFailure) as ctx: query_sensecore_acp(profile())
        self.assertEqual(ctx.exception.code, "acp_timeout")

    def test_catalog_requires_explicit_monitor_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"servers.toml"
            path.write_text('version=3\n[servers.gpu]\nbackend="ssh"\nssh_alias="gpu"\n[servers.acp]\nbackend="acp"\n[servers.cloud]\nbackend="acp"\nacp_workspace="w"\nacp_cluster="c"\nacp_zone="z"\nacp_profile="monitor"\n', encoding="utf8")
            servers, warnings = import_server_catalog(path)
            self.assertEqual([s.id for s in servers], ["gpu", "cloud"])
            self.assertEqual(servers[1].backend, "sensecore_acp")
            self.assertTrue(any("host credential profiles are not imported" in s for s in warnings))
            path.write_text(path.read_text()+'acp_protected_auth=true\n',encoding='utf8')
            servers,warnings=import_server_catalog(path)
            self.assertEqual([s.id for s in servers],["gpu"])
            self.assertTrue(any("host-only" in s for s in warnings))


if __name__ == "__main__": unittest.main()
