from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vram_radar.acp import query_sensecore_acp
from vram_radar.acp_transport import HostAcpTransport, current_transport, transport_scope
from vram_radar.connectors import ConnectorFailure
from vram_radar.models import ServerProfile
from vram_radar.service import DashboardService, connection_fingerprint


class AcpTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/"work")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.python = self.root/"python.exe";self.python.write_bytes(b"fixture-runtime")
        self.helper = self.root/"monitor.py";self.helper.write_bytes(b"# fixture helper")
        self.server = ServerProfile.from_dict(dict(id="ACP", backend="sensecore_acp", acp_workspace="w",
            acp_cluster="c", acp_zone="z", acp_profile="harness", acp_protected_auth=True))
        self.binding = dict(python=str(self.python),helper=str(self.helper),cwd=str(self.root),
            credential_profile="radar-acp",connection_fingerprint=connection_fingerprint(self.server),
            files_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.python,self.helper)})
        self.path=self.root/"acp-transport.json"
        self.write()

    def write(self):
        self.path.write_text(json.dumps(dict(schema_version=1,profiles={"default":{"ACP":self.binding}})),encoding="utf8")

    def test_exact_readonly_protocol(self):
        transport=HostAcpTransport(self.path,"default")(self.server)
        self.assertEqual(transport.command(),[str(self.python),"-I","-B",str(self.helper)])
        req=json.loads(transport.request(self.server))
        self.assertEqual(req["action"],"jobs-list")
        self.assertEqual(req["credential_profile"],"radar-acp")
        self.assertEqual(req["page_size"],100)
        self.assertEqual(req["page_token"],"1")
        self.assertFalse(set(req)&{"command","argv","url","access_key","secret_key"})

    def test_missing_binding_is_not_authorization(self):
        ordinary=replace(self.server,acp_protected_auth=False)
        self.assertIsNone(HostAcpTransport(self.path,"another")(ordinary))
        self.assertIsNone(HostAcpTransport(self.root/"absent.json","default")(ordinary))
        for resolver in (HostAcpTransport(self.path,"another"),HostAcpTransport(self.root/"absent.json","default")):
            with self.assertRaises(ConnectorFailure) as ctx:resolver(self.server)
            self.assertEqual(ctx.exception.state,"security_blocked")

    @patch("vram_radar.acp.shutil.which",side_effect=AssertionError("no direct fallback"))
    def test_protected_flag_without_bridge_blocks_service_and_direct_call(self,which):
        for resolver in (None,lambda server:None):
            with self.assertRaises(ConnectorFailure) as ctx:
                DashboardService._authenticated_call(SimpleNamespace(acp_transport=resolver),self.server,query_sensecore_acp)
            self.assertEqual(ctx.exception.state,"security_blocked")
        with self.assertRaises(ConnectorFailure) as ctx:query_sensecore_acp(self.server)
        self.assertEqual(ctx.exception.state,"security_blocked")

    def test_extra_pin_is_rejected_before_reading_any_pinned_file(self):
        self.binding["files_sha256"][str(self.root/"private-file")]='0'*64
        self.write()
        with patch("vram_radar.acp_transport.hashlib.file_digest",side_effect=AssertionError("no file hashing")):
            with self.assertRaises(ConnectorFailure):HostAcpTransport(self.path,"default")(self.server)

    def test_protected_flag_round_trips_and_binds_fingerprint(self):
        self.assertTrue(ServerProfile.from_dict(self.server.to_dict()).acp_protected_auth)
        self.assertNotEqual(connection_fingerprint(self.server),connection_fingerprint(replace(self.server,acp_protected_auth=False)))

    def test_changed_helper_or_connection_is_security_blocked(self):
        resolver=HostAcpTransport(self.path,"default")
        with self.assertRaises(ConnectorFailure) as ctx:resolver(replace(self.server,acp_cluster="other"))
        self.assertEqual(ctx.exception.state,"security_blocked")
        self.helper.write_bytes(b"# changed")
        with self.assertRaises(ConnectorFailure) as ctx:resolver(self.server)
        self.assertEqual(ctx.exception.code,"acp_transport_config_invalid")

    def test_extra_command_is_rejected(self):
        self.binding["argv"]=["delete"]
        self.write()
        with self.assertRaises(ConnectorFailure):HostAcpTransport(self.path,"default")(self.server)

    def test_page_size_is_bounded_host_metadata(self):
        for size in (True, 1, 500, "10", None):
            self.binding["page_size"]=size
            self.write()
            with self.assertRaises(ConnectorFailure):HostAcpTransport(self.path,"default")(self.server)
        self.binding["page_size"]=10
        self.write()
        transport=HostAcpTransport(self.path,"default")(self.server)
        self.assertEqual(json.loads(transport.request(self.server))["page_size"],10)
        with patch("vram_radar.acp._run_bounded_process",return_value=SimpleNamespace(returncode=0,stdout=b'[]',stderr=b'',stdout_truncated=False)):
            with transport_scope(self.server,transport):
                self.assertEqual(query_sensecore_acp(self.server)["platform"]["page_size"],10)
        # 60 is the stealth-safe sco page under the ~4MiB gRPC cap (70+ fails).
        self.binding["page_size"]=60
        self.write()
        transport=HostAcpTransport(self.path,"default")(self.server)
        self.assertEqual(json.loads(transport.request(self.server))["page_size"],60)
        with patch("vram_radar.acp._run_bounded_process",return_value=SimpleNamespace(returncode=0,stdout=b'[]',stderr=b'',stdout_truncated=False)):
            with transport_scope(self.server,transport):
                self.assertEqual(query_sensecore_acp(self.server)["platform"]["page_size"],60)

    def test_scope_only_binds_exact_connection_and_resets(self):
        transport=HostAcpTransport(self.path,"default")(self.server)
        with transport_scope(self.server,transport):
            self.assertIs(current_transport(self.server),transport)
            self.assertIsNone(current_transport(replace(self.server,acp_zone="other")))
        self.assertIsNone(current_transport(self.server))

    @patch("vram_radar.acp._run_bounded_process")
    @patch("vram_radar.acp.shutil.which",side_effect=AssertionError("direct SCO fallback forbidden"))
    def test_service_bridge_uses_stdin_and_no_ssh_or_fallback(self,which,run):
        run.return_value=SimpleNamespace(returncode=0,stdout=b'[{"name":"pt-test","state":"RUNNING"}]',stderr=b'',stdout_truncated=False)
        service=SimpleNamespace(acp_transport=HostAcpTransport(self.path,"default"))
        result=DashboardService._authenticated_call(service,self.server,query_sensecore_acp)
        self.assertEqual(result["view_kind"],"platform")
        self.assertEqual(run.call_args.args[0],[str(self.python),"-I","-B",str(self.helper)])
        self.assertEqual(run.call_args.kwargs["stdin"],subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["cwd"],str(self.root))
        self.assertEqual(json.loads(run.call_args.kwargs["input_data"])["action"],"jobs-list")
        self.assertIsNone(current_transport(self.server))

    def test_invalid_registration_does_not_execute_operation(self):
        self.binding["credential_profile"]="--bad"
        self.write()
        service=SimpleNamespace(acp_transport=HostAcpTransport(self.path,"default"))
        called=[]
        with self.assertRaises(ConnectorFailure):
            DashboardService._authenticated_call(service,self.server,lambda s:called.append(s))
        self.assertEqual(called,[])


if __name__=="__main__":unittest.main()
