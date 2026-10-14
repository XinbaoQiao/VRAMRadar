import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vram_radar.connectors import ConnectorFailure, run_remote
from vram_radar.models import ServerProfile, Profile
from vram_radar.service import DashboardService, connection_fingerprint
from vram_radar.ssh_transport import HostSshTransport, current_transport, transport_scope, regular_path
from vram_radar.storage import storage_paths, SnapshotCache


class HostTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ServerProfile(id='gpu', display_name='GPU', backend='direct_ssh', ssh_alias='registered')
        self.helper = self.root / 'transport.py'; self.helper.write_text('# fixture')
        self.path = self.root / 'ssh-transport.json'
        self.binding = {'ssh_alias': 'registered', 'connection_fingerprint': connection_fingerprint(self.server),
                        'python': sys.executable, 'helper': str(self.helper), 'cwd': str(self.root),
                        'files_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (Path(sys.executable), self.helper)}}
        self.resolver = HostSshTransport(self.path, 'test')

    def register(self):
        self.path.write_text(json.dumps({'schema_version': 1, 'profiles': {'test': {'gpu': self.binding}}}))

    def test_missing_and_other_profile_have_no_transport(self):
        self.assertIsNone(self.resolver(self.server))
        self.register()
        self.assertIsNone(HostSshTransport(self.path, 'other')(self.server))

    def test_changed_registration_or_target_or_helper_fails_closed(self):
        self.register()
        for server in (replace(self.server, ssh_alias='other'), replace(self.server, port=2222)):
            with self.assertRaises(ConnectorFailure): self.resolver(server)
        self.helper.write_text('# modified')
        with self.assertRaises(ConnectorFailure): self.resolver(self.server)
        self.path.write_text('{')
        with self.assertRaises(ConnectorFailure): self.resolver(self.server)

    def test_scoped_password_transport_preserves_stdin_limits_and_cwd(self):
        self.register(); transport = self.resolver(self.server)
        result = SimpleNamespace(returncode=0, stdout=b'live', stderr=b'', stdout_truncated=False)
        with patch('vram_radar.connectors._run_bounded_process', return_value=result) as child, \
             patch('vram_radar.connectors.PasswordBroker') as broker:
            with transport_scope(self.server, transport):
                self.assertEqual(run_remote(self.server, 'bash -s', stdin_data=b'printf live'), 'live')
                self.assertIsNone(current_transport(replace(self.server, id='other')))
            self.assertIsNone(current_transport(self.server))
            args, kwargs = child.call_args
            self.assertEqual(Path(args[0][0]).resolve(), Path(sys.executable).resolve())
            self.assertEqual(args[0][1:3], ['-I', '-B'])
            self.assertEqual(Path(args[0][3]).resolve(), self.helper.resolve())
            self.assertEqual(kwargs['input_data'], b'printf live')
            self.assertEqual(Path(kwargs['cwd']).resolve(), Path(self.root).resolve())
            self.assertIsNone(kwargs['env']); broker.assert_not_called()

    def test_service_tries_key_then_host_password_transport(self):
        self.register()
        profile = Profile.from_dict({'schema_version': 1, 'id': 'test', 'display_name': 'Test',
                                     'servers': [self.server.to_dict()]})
        secret, recovery = Mock(), Mock(return_value=None)
        service = DashboardService(profile, SnapshotCache(storage_paths(self.root), 'test'),
                                   secret_store=secret, ssh_recovery=recovery, ssh_transport=self.resolver)
        phases = []
        def operation(server, **_kwargs):
            using_transport = current_transport(server) is not None
            phases.append(using_transport)
            if not using_transport:
                raise ConnectorFailure('auth_failed', 'rejected', retryable=False)
            return {'ok': True}
        self.assertEqual(service._authenticated_call(self.server, operation), {'ok': True})
        self.assertEqual(phases, [False, True])  # key/config first, then host password transport
        secret.get.assert_not_called(); recovery.assert_not_called()
        self.assertIsNone(current_transport(self.server))

    def test_stale_host_transport_does_not_block_successful_key_auth(self):
        self.register()
        profile = Profile.from_dict({'schema_version': 1, 'id': 'test', 'display_name': 'Test',
                                     'servers': [self.server.to_dict()]})
        secret, recovery = Mock(), Mock(return_value=None)
        service = DashboardService(profile, SnapshotCache(storage_paths(self.root), 'test'),
                                   secret_store=secret, ssh_recovery=recovery, ssh_transport=self.resolver)
        # Drift the registration so resolve fails closed, but key auth still works.
        self.helper.write_text('# modified')
        operation = Mock(return_value={'via': 'key'})
        self.assertEqual(service._authenticated_call(self.server, operation), {'via': 'key'})
        operation.assert_called_once_with(self.server)
        secret.get.assert_not_called(); recovery.assert_not_called()

    def test_stale_host_transport_warns_once_per_server(self):
        self.register()
        profile = Profile.from_dict({'schema_version': 1, 'id': 'test', 'display_name': 'Test',
                                     'servers': [self.server.to_dict()]})
        logger = Mock()
        service = DashboardService(profile, SnapshotCache(storage_paths(self.root), 'test'),
                                   logger=logger, ssh_recovery=Mock(return_value=None),
                                   ssh_transport=self.resolver)
        self.helper.write_text('# modified')
        operation = Mock(return_value={'via': 'key'})
        service._authenticated_call(self.server, operation)
        service._authenticated_call(self.server, operation)
        self.assertEqual(logger.warning.call_count, 1)
        # Healthy registration clears the warn latch.
        self.helper.write_text('# fixture')
        self.binding['files_sha256'][str(self.helper)] = __import__('hashlib').sha256(self.helper.read_bytes()).hexdigest()
        self.register()
        service._authenticated_call(self.server, Mock(side_effect=[
            ConnectorFailure('auth_failed', 'rejected', retryable=False),
            {'via': 'transport'},
        ]))
        self.helper.write_text('# modified again')
        service._authenticated_call(self.server, operation)
        self.assertEqual(logger.warning.call_count, 2)

    def test_stale_host_transport_surfaces_after_key_auth_fails_without_password(self):
        self.register()
        profile = Profile.from_dict({'schema_version': 1, 'id': 'test', 'display_name': 'Test',
                                     'servers': [self.server.to_dict()]})
        service = DashboardService(profile, SnapshotCache(storage_paths(self.root), 'test'),
                                   secret_store=Mock(), ssh_recovery=Mock(return_value=None), ssh_transport=self.resolver)
        self.helper.write_text('# modified')
        operation = Mock(side_effect=ConnectorFailure('auth_failed', 'rejected', retryable=False))
        with self.assertRaises(ConnectorFailure) as raised:
            service._authenticated_call(self.server, operation)
        self.assertEqual(raised.exception.code, 'host_transport_config_invalid')
        self.assertEqual(operation.call_count, 1)

    def test_context_is_not_inherited_by_another_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        self.register()
        with transport_scope(self.server, self.resolver(self.server)), ThreadPoolExecutor(1) as pool:
            self.assertIsNone(pool.submit(current_transport, self.server).result())

    def test_reparse_paths_are_rejected_and_normal_paths_are_canonical(self):
        import stat
        if sys.platform.startswith('win'):
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)):
                with self.assertRaises(ValueError): regular_path(self.helper)
        else:
            self.assertEqual(regular_path(self.helper), self.helper.resolve(strict=True))
        self.register()
        self.assertEqual(self.resolver(self.server).helper, str(self.helper.resolve(strict=True)))

    def test_explicit_unscoped_management_keeps_existing_ssh_path(self):
        self.register()
        result = SimpleNamespace(returncode=0, stdout=b'ok', stderr=b'', stdout_truncated=False)
        with patch('vram_radar.connectors._run_bounded_process', return_value=result) as child:
            self.assertEqual(run_remote(self.server, 'true'), 'ok')
            self.assertEqual(child.call_args.args[0][0], 'ssh')


if __name__ == '__main__': unittest.main()
