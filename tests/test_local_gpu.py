from dataclasses import replace
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vram_radar.connectors import ConnectorFailure, query_server, run_remote
from vram_radar.models import ConfigError, Profile, ServerProfile
from vram_radar.server_catalog import profile_from_server_config
from vram_radar.storage import ProfileStore, storage_paths


GPU_ROWS = b'0, GPU-test, NVIDIA GeForce RTX 5060 Laptop GPU, 8151, 512, 7374, 11, 42\n'


class LocalGpuTests(unittest.TestCase):
    def setUp(self):
        self.server = ServerProfile.from_dict({
            "id": "local-5060", "display_name": "Local GPU", "backend": "local",
        })

    def query(self, result=None):
        result = result or SimpleNamespace(returncode=0, stdout=GPU_ROWS,
                                          stderr=b'', stdout_truncated=False)
        with patch('vram_radar.local_gpu._nvidia_smi_path', return_value='nvidia-smi'), \
             patch('vram_radar.local_gpu._run_bounded_process', return_value=result) as capture, \
             patch('vram_radar.connectors.run_remote', side_effect=AssertionError('no SSH')):
            return query_server(self.server), capture.call_args

    def test_read_only_local_dispatch_and_vram(self):
        snapshot, call = self.query()
        self.assertEqual(snapshot['backend'], 'local')
        self.assertEqual(snapshot['view_kind'], 'live-memory')
        self.assertEqual(snapshot['gpus'][0]['gpu_uuid'], 'GPU-test')
        self.assertEqual(snapshot['total_vram_gib'], 7.96)
        self.assertEqual(snapshot['free_vram_gib'], 7.2)
        self.assertEqual(snapshot['gpus'][0]['utilization_percent'], 11)
        self.assertNotIn('account', snapshot)
        self.assertFalse(snapshot['processes']['supported'])
        self.assertEqual(len(call.args[0]), 3)
        self.assertTrue(call.args[0][1].startswith('--query-gpu='))
        self.assertLessEqual(call.kwargs['timeout'], 5)
        self.assertLessEqual(call.kwargs['stdout_limit'], 1024 * 1024)
        self.assertEqual(call.kwargs['stdin'], subprocess.DEVNULL)

    def test_driver_metrics_na_stay_unknown(self):
        snapshot, _ = self.query(SimpleNamespace(returncode=0,
            stdout=GPU_ROWS.replace(b'11, 42', b'N/A, N/A'), stderr=b'', stdout_truncated=False))
        self.assertIsNone(snapshot['gpus'][0]['utilization_percent'])
        self.assertIsNone(snapshot['gpus'][0]['temperature_c'])

    def test_driver_missing(self):
        with patch('vram_radar.local_gpu._nvidia_smi_path', return_value=None):
            with self.assertRaises(ConnectorFailure) as caught:
                query_server(self.server)
        self.assertEqual(caught.exception.code, 'local_nvidia_smi_missing')

    def test_query_timeout_is_retryable(self):
        with patch('vram_radar.local_gpu._nvidia_smi_path', return_value='nvidia-smi'), \
             patch('vram_radar.local_gpu._run_bounded_process',
                   side_effect=subprocess.TimeoutExpired('nvidia-smi', 5)):
            with self.assertRaises(ConnectorFailure) as caught:
                query_server(self.server)
        self.assertEqual(caught.exception.code, 'local_gpu_timeout')
        self.assertTrue(caught.exception.retryable)

    def test_driver_error_does_not_expose_stderr(self):
        with self.assertRaises(ConnectorFailure) as caught:
            self.query(SimpleNamespace(returncode=1, stdout=b'', stderr=b'secret=value',
                                       stdout_truncated=False))
        self.assertNotIn('secret', str(caught.exception))

    def test_overflow_cannot_become_capacity(self):
        with self.assertRaises(ConnectorFailure):
            self.query(SimpleNamespace(returncode=0, stdout=GPU_ROWS, stderr=b'',
                                       stdout_truncated=True))

    def test_nonfinite_metrics_cannot_become_capacity(self):
        with self.assertRaises(ConnectorFailure):
            self.query(SimpleNamespace(returncode=0, stdout=GPU_ROWS.replace(b'7374', b'nan'),
                                       stderr=b'', stdout_truncated=False))

    def test_local_profiles_reject_ssh_configuration(self):
        for field, value in [('host', 'example.invalid'), ('auth_ref', 'saved-password'),
                             ('auto_detect_backend', True)]:
            with self.subTest(field=field), self.assertRaises(ConfigError):
                ServerProfile.from_dict(dict(self.server.to_dict(), **{field: value}))

    def test_local_profile_persists_without_ssh_address(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(storage_paths(Path(directory)))
            profile = replace(Profile.empty('test'), servers=(self.server,))
            store.save(profile)
            self.assertEqual(store.load('test').servers[0], self.server)

    def test_local_monitor_cannot_execute_remote_commands(self):
        with patch('vram_radar.connectors._run_bounded_process') as capture:
            with self.assertRaises(ConnectorFailure):
                run_remote(self.server, 'true')
            capture.assert_not_called()

    def test_server_import_preserves_manually_added_local_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'servers.toml'
            config.write_text('version=3\n[servers.remote]\nbackend="ssh"\nssh_alias="remote"\n',
                              encoding='utf-8')
            profile = replace(Profile.empty('test'), servers=(self.server,))
            imported, _ = profile_from_server_config(profile, config)
            self.assertIn(self.server, imported.servers)

    def test_connection_test_never_claims_local_ssh_authentication(self):
        from vram_radar.shell import AppApi
        api = AppApi.__new__(AppApi)
        api.profile = replace(Profile.empty('test'), servers=(self.server,))
        api.service = Mock()
        payload, _ = self.query()
        api.service.probe_server.return_value = payload
        success = api.test_connection(self.server.id)
        self.assertTrue(success['ok'])
        self.assertFalse(any('SSH' in str(stage) for stage in success['stages']))
        api.service.probe_server.side_effect = ConnectorFailure('parse_failed', 'Invalid local metrics', retryable=True)
        failure = api.test_connection(self.server.id)
        self.assertFalse(failure['ok'])
        self.assertFalse(any('SSH' in str(stage) for stage in failure['stages']))


if __name__ == '__main__':
    unittest.main()
