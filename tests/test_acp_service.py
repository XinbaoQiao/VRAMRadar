import unittest
from unittest.mock import Mock, patch

from vram_radar.models import Profile
from vram_radar.service import DashboardService, _prepare_runtime_payload, connection_fingerprint, recommend_resources, favorite_resource_matches
from vram_radar.shell import AppApi


def profile():
    return Profile.from_dict({'schema_version': 1, 'id': 'test', 'display_name': 'Test', 'servers': [{
        'id': 'acp', 'display_name': 'ACP', 'backend': 'sensecore_acp',
        'acp_workspace': 'workspace', 'acp_cluster': 'cluster',
        'acp_zone': 'cn-sh-01g', 'acp_sco_path': 'sco', 'acp_profile': 'test',
    }]})


class AcpServiceTests(unittest.TestCase):
    def test_platform_never_falls_back_to_ssh_auth(self):
        p = profile()
        transport = Mock(side_effect=AssertionError('must not resolve SSH transport'))
        recovery = Mock(side_effect=AssertionError('must not recover SSH keys'))
        cache = Mock(); cache.load.return_value = None
        service = DashboardService(p, cache=cache, ssh_transport=transport, ssh_recovery=recovery)
        operation = Mock(return_value={'view_kind': 'platform', 'jobs': []})
        self.assertEqual(service._authenticated_call(p.servers[0], operation)['view_kind'], 'platform')
        operation.assert_called_once_with(p.servers[0])
        transport.assert_not_called()

    def test_platform_fingerprint_does_not_read_ssh_config(self):
        with patch('vram_radar.service.openssh_config_dependency_fingerprint', side_effect=AssertionError):
            self.assertEqual(len(connection_fingerprint(profile().servers[0])), 64)

    def test_platform_not_a_free_gpu_or_recommendation(self):
        row = {'server_id': 'acp', 'view_kind': 'platform', 'jobs': [], 'connection': {'state': 'online'},
               'total_gpus': None, 'total_vram_gib': None, 'free_vram_gib': None}
        self.assertEqual(_prepare_runtime_payload(row), row)
        self.assertEqual(favorite_resource_matches({'servers': [row]}, ['acp']), [])
        result = recommend_resources({'servers': [row]})
        self.assertFalse(result.get('candidates'))
        with self.assertRaises(ValueError):
            _prepare_runtime_payload({'view_kind': 'platform', 'jobs': 'invalid'})

    def test_shell_blocks_ssh_actions_and_reports_platform_probe(self):
        service = Mock()
        service.probe_server.return_value = {'view_kind': 'platform', 'jobs': [], 'total_gpus': None}
        api = AppApi(profile(), store=Mock(), paths=Mock(), service=service)
        for action in [api.get_ssh_command, api.open_terminal, api.trust_host_key, api._configure_ssh_key]:
            self.assertEqual(action('acp')['code'], 'platform_ssh_unavailable')
        result = api.test_connection('acp')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['summary']['total_gpus'])
        self.assertEqual(result['stages'][1]['label'], '云任务')

    def test_page_disappearance_never_becomes_slurm_completion(self):
        self.assertEqual(AppApi._active_tasks({'backend': 'sensecore_acp', 'tasks': {
            'current_user': 'me', 'active': [{'job_id': 'pt-1', 'user': 'me'}]}}), {})

    def test_platform_only_summary_is_unknown_and_directory_has_no_io(self):
        cache = Mock(); cache.load.return_value = None
        directory = Mock(side_effect=AssertionError('ACP must not run SSH directory query'))
        service = DashboardService(profile(), cache=cache, directory_query=directory)
        runtime = service.states['acp']; runtime.state = 'online'
        runtime.payload = {'view_kind': 'platform', 'jobs': [], 'total_gpus': None}
        summary = service.snapshot()['summary']
        self.assertIsNone(summary['total_gpus'])
        self.assertIsNone(summary['free_vram_gib'])
        self.assertEqual(summary['unknown_capacity_servers'], 1)
        self.assertEqual(service.inspect_account_directory('acp')['code'], 'platform_directory_unavailable')
        directory.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class PlatformRefreshSecondsTests(unittest.TestCase):
    def test_acp_stable_page_waits_longer_than_profile_interval(self):
        from vram_radar.service import platform_refresh_seconds
        self.assertEqual(platform_refresh_seconds(15, backend="sensecore_acp", payload_changed=False), 120)
        self.assertEqual(platform_refresh_seconds(15, backend="sensecore_acp", payload_changed=True), 60)
        self.assertEqual(platform_refresh_seconds(15, backend="direct_ssh", payload_changed=False), 15)
        self.assertEqual(platform_refresh_seconds(90, backend="sensecore_acp", payload_changed=False), 600)

    def test_acp_retry_table_starts_slower_than_ssh(self):
        from vram_radar.service import ACP_RETRY_SECONDS, RETRY_SECONDS
        self.assertGreaterEqual(ACP_RETRY_SECONDS[0], 60)
        self.assertGreater(ACP_RETRY_SECONDS[0], RETRY_SECONDS[0])
        self.assertGreaterEqual(ACP_RETRY_SECONDS[-1], 900)
