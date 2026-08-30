from pathlib import Path
import base64
import copy
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from vram_radar.connectors import ConnectorFailure
from vram_radar.models import Profile
from vram_radar.ssh_keys import PreparedSshKey, SshKeySetupError
from vram_radar.storage import ProfileStore, SnapshotCache, storage_paths
from vram_radar.window_state import WindowGeometry
from vram_radar.shell import (
    ActivationServer,
    AppApi,
    InstanceAlreadyRunning,
    InstanceLock,
    WindowShutdownCoordinator,
    activation_worker,
    build_runtime,
    main,
    request_existing_instance,
    _copy_text_to_system_clipboard,
    webview_start_options,
    window_frontend_is_ready,
    window_smoke_worker,
)


CATALOG = """\
version = 2

[servers."direct-gpu"]
display_name = "4090 workstation"
ssh_alias = "direct-gpu-test"
backend = "ssh"
enabled = true

[servers."slurm-a100"]
display_name = "A100 cluster"
ssh_alias = "slurm-gpu-test"
backend = "slurm"
enabled = true
"""


class ShellApiTests(unittest.TestCase):
    @staticmethod
    def versioned_profile(api, raw):
        candidate = copy.deepcopy(raw)
        candidate["profile_revision"] = api.get_profile()["profile_revision"]
        return candidate

    @staticmethod
    def favorite_alert_profile(*, language="zh-CN", minimum_memory_gib=0):
        return Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "ui_language": language,
                "favorite_server_ids": ["gpu"],
                "favorite_alert_enabled": True,
                "favorite_alert_min_memory_gib": minimum_memory_gib,
                "servers": [{
                    "id": "gpu",
                    "display_name": "A100 Lab",
                    "backend": "direct_ssh",
                    "host": "gpu.test",
                }],
            }
        )

    @staticmethod
    def favorite_alert_snapshot(*, available=True, paused=False, in_flight=False):
        free_memory = 24 if available else 2
        return {
            "monitoring": {"paused": paused, "in_flight": in_flight},
            "servers": [{
                "server_id": "gpu",
                "display_name": "A100 Lab",
                "backend": "direct_ssh",
                "view_kind": "live-memory",
                "connection": {"state": "online"},
                "processes": {
                    "supported": True,
                    "active": [] if available else [{"allocations": [{"gpu_index": "0"}]}],
                },
                "gpus": [{
                    "gpu_index": "0",
                    "memory_total_gib": 24,
                    "memory_free_gib": free_memory,
                    "utilization_percent": 0 if available else 90,
                }],
            }],
        }

    def test_status_bridge_starts_a_coalesced_background_refresh(self):
        service = Mock()
        service.request_refresh.return_value = {"monitoring": {"in_flight": True}}
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)

        result = api.get_status(True, "gpu")

        self.assertTrue(result["monitoring"]["in_flight"])
        service.request_refresh.assert_called_once_with(force=True, server_id="gpu")
        service.refresh.assert_not_called()

        service.snapshot.return_value = {"monitoring": {"in_flight": False}}
        self.assertFalse(api.get_snapshot()["monitoring"]["in_flight"])
        service.snapshot.assert_called_once_with()

    def test_hidden_webview_refresh_returns_only_compact_monitoring_state(self):
        service = Mock()
        service.request_refresh.return_value = {
            "monitoring": {"revision": 17, "in_flight": True},
            "servers": [{"large": "payload"}],
        }
        service.snapshot.return_value = {"monitoring": {"in_flight": False}, "servers": []}
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)

        result = api.request_background_refresh()

        self.assertEqual(result, {"ok": True, "revision": 17, "in_flight": True})
        service.request_refresh.assert_called_once_with(force=False)

    def test_windows_copied_ssh_command_is_explicitly_powershell_quoted(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "ssh_alias": "gpu&whoami",
                        "ssh_config_file": "C:/SSH&A/config",
                        "identity_file": "C:/Keys/user's-key",
                        "prefer_identity_auth": True,
                    }
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())

        with patch("vram_radar.shell.sys.platform", "win32"):
            result = api.get_ssh_command("gpu")

        self.assertTrue(result["ok"])
        self.assertEqual(result["shell"], "powershell")
        self.assertEqual(result["copy_format"], "ssh-command")
        self.assertEqual(result["copy_text"], result["command"])
        self.assertIn(f"'{Path('C:/SSH&A/config')}'", result["command"])
        self.assertIn("'gpu&whoami'", result["command"])
        quoted_key = str(Path("C:/Keys/user's-key")).replace("'", "''")
        self.assertIn(f"'{quoted_key}'", result["command"])
        self.assertIn("'IdentitiesOnly=yes'", result["command"])

    def test_copied_imported_ssh_command_exposes_static_address_user_and_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName 192.0.2.81\n"
                "  User researcher\n"
                "  Port 22022\n"
                "  ProxyCommand helper --secret-must-stay-in-config\n",
                encoding="utf-8",
            )
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "servers": [
                        {
                            "id": "gpu",
                            "display_name": "GPU",
                            "backend": "direct_ssh",
                            "ssh_alias": "gpu-copy",
                            "ssh_config_file": str(config),
                            "identity_file": "~/.ssh/id_rsa",
                            "prefer_identity_auth": True,
                        }
                    ],
                }
            )
            api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())

            with patch("vram_radar.shell.sys.platform", "win32"):
                result = api.get_ssh_command("gpu")

        self.assertTrue(result["ok"])
        self.assertTrue(result["endpoint_complete"])
        self.assertEqual(
            result["endpoint"],
            {"hostname": "192.0.2.81", "user": "researcher", "port": 22022},
        )
        self.assertEqual(result["copy_format"], "openssh-config")
        self.assertEqual(
            result["copy_text"],
            "Host gpu-copy\n"
            "  HostName 192.0.2.81\n"
            "  User researcher\n"
            "  Port 22022\n"
            "  IdentityFile ~/.ssh/id_rsa\n"
            "  IdentitiesOnly yes\n"
            "  BatchMode yes\n"
            "  ClearAllForwardings yes",
        )
        self.assertNotIn("secret-must-stay-in-config", result["copy_text"])

    def test_direct_server_copy_is_a_complete_openssh_host_block(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [{
                    "id": "gpu",
                    "display_name": "GPU",
                    "backend": "direct_ssh",
                    "host": "192.0.2.90",
                    "username": "researcher",
                    "port": 22022,
                }],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())

        with patch("vram_radar.shell.sys.platform", "win32"):
            result = api.get_ssh_command("gpu")

        self.assertEqual(
            result["copy_text"],
            "Host gpu\n"
            "  HostName 192.0.2.90\n"
            "  User researcher\n"
            "  Port 22022\n"
            "  IdentitiesOnly yes\n"
            "  BatchMode yes\n"
            "  ClearAllForwardings yes",
        )
        self.assertEqual(result["copy_format"], "openssh-config")
        self.assertTrue(result["endpoint_complete"])

    def test_dynamic_ssh_config_copies_safe_alias_command_with_an_explicit_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host gpu-copy\n"
                "  HostName gpu.example\n"
                "  User researcher\n"
                "Match exec \"test -f ~/.ssh/alternate-port\"\n"
                "  Port 22022\n",
                encoding="utf-8",
            )
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "servers": [
                        {
                            "id": "gpu",
                            "display_name": "GPU",
                            "backend": "direct_ssh",
                            "ssh_alias": "gpu-copy",
                            "ssh_config_file": str(config),
                        }
                    ],
                }
            )
            api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())

            with patch("vram_radar.connectors.subprocess.Popen") as popen:
                result = api.get_ssh_command("gpu")

        popen.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertFalse(result["endpoint_complete"])
        self.assertEqual(result["copy_format"], "ssh-command")
        self.assertEqual(result["copy_text"], result["command"])
        self.assertEqual(result["resolution_reason"], "conditional_match")
        self.assertIn("gpu-copy", result["command"])
        self.assertIn("OpenSSH", result["warning"])

    def test_windows_open_terminal_passes_canonical_ssh_argv_without_shell(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU Terminal",
                        "backend": "direct_ssh",
                        "ssh_alias": "gpu&safe",
                        "ssh_config_file": "C:/SSH&A/config",
                    }
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "win32"),
            patch(
                "vram_radar.shell.shutil.which",
                return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            ),
            patch("vram_radar.shell.subprocess.Popen") as popen,
        ):
            result = api.open_terminal("gpu")

        self.assertTrue(result["ok"])
        launch = popen.call_args.args[0]
        self.assertEqual(launch[-2], "-Command")
        script = launch[-1]
        self.assertNotIn("gpu&safe", script)
        encoded = script.split("$encoded='", 1)[1].split("';", 1)[0]
        decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
        self.assertEqual(decoded[0], "ssh")
        self.assertIn("-F", decoded)
        self.assertEqual(decoded[-2:], ["--", "gpu&safe"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_windows_open_terminal_reports_missing_powershell(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "ssh_alias": "gpu&safe",
                    }
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())

        with (
            patch("vram_radar.shell.sys.platform", "win32"),
            patch("vram_radar.shell.shutil.which", return_value=None),
            patch("vram_radar.shell.subprocess.Popen") as popen,
        ):
            result = api.open_terminal("gpu")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "terminal_missing")
        self.assertIn("复制 SSH", result["error"])
        popen.assert_not_called()

    def test_windows_setup_guide_opens_plain_powershell_without_running_a_command(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "win32"),
            patch(
                "vram_radar.shell.shutil.which",
                return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            ),
            patch("vram_radar.shell.subprocess.Popen") as popen,
        ):
            result = api.open_setup_terminal("windows")

        self.assertTrue(result["ok"])
        self.assertIn("PowerShell", result["message"])
        self.assertEqual(
            popen.call_args.args[0],
            [
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NoExit",
            ],
        )
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_setup_guide_rejects_the_other_platform_without_starting_a_process(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "win32"),
            patch("vram_radar.shell.subprocess.Popen") as popen,
            patch("vram_radar.shell.subprocess.run") as run,
        ):
            result = api.open_setup_terminal("macos")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "platform_mismatch")
        popen.assert_not_called()
        run.assert_not_called()

    def test_macos_setup_guide_opens_terminal_without_running_a_command(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())
        completed = Mock(returncode=0)
        with (
            patch("vram_radar.shell.sys.platform", "darwin"),
            patch("vram_radar.shell.subprocess.run", return_value=completed) as run,
        ):
            result = api.open_setup_terminal("macos")

        self.assertTrue(result["ok"])
        self.assertIn("Return", result["message"])
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-a", "Terminal"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_macos_open_terminal_uses_posix_quoted_canonical_command(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "ssh_alias": "gpu&safe",
                        "ssh_config_file": "/srv/tester/SSH Config/config",
                    }
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "darwin"),
            patch("vram_radar.shell.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            result = api.open_terminal("gpu")

        self.assertTrue(result["ok"])
        launch = run.call_args.args[0]
        self.assertEqual(launch[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("ssh -F '/srv/tester/SSH Config/config' -- 'gpu&safe'", launch[2])
        self.assertEqual(run.call_args.kwargs["timeout"], 8)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_open_terminal_failure_is_actionable(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "darwin"),
            patch("vram_radar.shell.subprocess.run", return_value=Mock(returncode=1)),
        ):
            result = api.open_terminal("gpu")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "open_terminal_failed")
        self.assertIn("复制 SSH", result["error"])

    def test_macos_open_terminal_timeout_is_actionable(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())
        with (
            patch("vram_radar.shell.sys.platform", "darwin"),
            patch(
                "vram_radar.shell.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["osascript"], 8),
            ),
        ):
            result = api.open_terminal("gpu")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "open_terminal_failed")
        self.assertIn("复制 SSH", result["error"])

    def test_scale_bridges_validate_and_delegate_without_exposing_full_snapshot(self):
        service = Mock()
        service.recommend_many.return_value = {"ok": True, "candidates": []}
        service.get_cluster_nodes.return_value = {"ok": True, "nodes": []}
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)

        recommendation = api.recommend_resources(
            {
                "gpu_count": 8,
                "min_memory_gib": 70,
                "gpu_type": "H100",
                "partition": "gpu-large",
                "same_node": True,
                "limit": 5,
            }
        )
        page = api.get_cluster_nodes(
            "cluster",
            {
                "offset": 25,
                "limit": 75,
                "query": "node",
                "gpu_type": "H100",
                "partition": "gpu-large",
                "only_available": True,
                "issues_only": False,
                "revision": 7,
            },
        )

        self.assertTrue(recommendation["ok"])
        service.recommend_many.assert_called_once_with(
            gpu_count=8,
            min_memory_gib=70,
            gpu_type="H100",
            partition="gpu-large",
            same_node=True,
            limit=5,
        )
        self.assertTrue(page["ok"])
        service.get_cluster_nodes.assert_called_once_with(
            "cluster",
            cursor=25,
            limit=75,
            query="node",
            gpu_type="H100",
            partition="gpu-large",
            only_available=True,
            only_issues=False,
            revision=7,
        )

    def test_monitor_pause_and_notification_bridges_are_explicit_and_bounded(self):
        service = Mock()
        service.pause.return_value = {"monitoring": {"paused": True}}
        service.resume.return_value = {"monitoring": {"paused": False}}
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)
        notify = Mock(return_value=True)
        api.bind_notification_callback(notify)

        paused = api.set_monitoring_paused(True)
        resumed = api.set_monitoring_paused(False)
        shown = api.show_notification("资源可用", "H100 已空闲")
        invalid = api.show_notification("x", "y" * 2_049)

        self.assertTrue(paused["ok"])
        self.assertTrue(resumed["ok"])
        self.assertTrue(shown["ok"])
        notify.assert_called_once_with("资源可用", "H100 已空闲")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["code"], "invalid_notification")

    def test_favorite_alert_notifies_once_per_transition_and_resets_after_unavailable(self):
        service = Mock()
        service.snapshot.return_value = self.favorite_alert_snapshot(available=False)
        api = AppApi(
            self.favorite_alert_profile(language="en"),
            store=Mock(),
            paths=Mock(),
            service=service,
        )
        notify = Mock(return_value=True)
        api.bind_notification_callback(notify)

        service.snapshot.return_value = self.favorite_alert_snapshot(available=True)
        api.get_snapshot()
        api.get_snapshot()
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[0], "Favorite GPUs are available")
        self.assertIn("A100 Lab", notify.call_args.args[1])

        service.snapshot.return_value = self.favorite_alert_snapshot(available=False)
        api.get_snapshot()
        service.snapshot.return_value = self.favorite_alert_snapshot(available=True)
        api.get_snapshot()
        self.assertEqual(notify.call_count, 2)

    def test_favorite_alert_is_suppressed_while_monitoring_is_paused(self):
        service = Mock()
        service.snapshot.return_value = self.favorite_alert_snapshot(available=True, paused=True)
        api = AppApi(
            self.favorite_alert_profile(),
            store=Mock(),
            paths=Mock(),
            service=service,
        )
        notify = Mock(return_value=True)

        api.bind_notification_callback(notify)
        api.get_snapshot()

        notify.assert_not_called()

    def test_hidden_refresh_notifies_after_background_collection_finishes(self):
        service = Mock()
        service.request_refresh.return_value = {
            "monitoring": {"revision": 3, "in_flight": True},
            "servers": [],
        }
        threshold_match = self.favorite_alert_snapshot(available=True)
        threshold_match["servers"][0]["processes"]["active"] = [
            {"allocations": [{"gpu_index": "0"}]}
        ]
        threshold_match["servers"][0]["gpus"][0]["utilization_percent"] = 90
        service.snapshot.side_effect = [
            self.favorite_alert_snapshot(available=False),
            self.favorite_alert_snapshot(available=False, in_flight=True),
            threshold_match,
        ]
        api = AppApi(
            self.favorite_alert_profile(minimum_memory_gib=20),
            store=Mock(),
            paths=Mock(),
            service=service,
        )
        notified = threading.Event()
        notify = Mock(side_effect=lambda _title, _message: notified.set() or True)
        api.bind_notification_callback(notify)

        result = api.request_background_refresh()

        self.assertTrue(result["in_flight"])
        self.assertTrue(notified.wait(2))
        self.assertEqual(notify.call_count, 1)
        self.assertIn("20 GiB", notify.call_args.args[1])

    def test_directory_api_validates_server_id_and_delegates_to_service(self):
        service = Mock()
        service.inspect_account_directory.return_value = {
            "ok": True,
            "server_id": "gpu",
            "account": {"home_directory": "/srv/vram-radar-account"},
        }
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)

        invalid = api.inspect_account_directory("  ")
        result = api.inspect_account_directory(" gpu ")

        self.assertFalse(invalid["ok"])
        self.assertTrue(result["ok"])
        service.inspect_account_directory.assert_called_once_with("gpu", None, force=False)

    def test_default_directory_is_verified_then_persisted_outside_the_package(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [{
                    "id": "gpu",
                    "display_name": "GPU",
                    "backend": "direct_ssh",
                    "host": "gpu.test",
                }],
            }
        )
        account = {
            "home_directory": "/srv/vram-radar-account",
            "directory_tree": {
                "supported": True,
                "root": "/srv/vram-radar-account/projects/radar",
                "root_source": "requested",
                "entries": [],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            service.inspect_account_directory.return_value = {"ok": True, "account": account}
            api = AppApi(profile, store, paths, service)

            pinned = api.set_default_directory("gpu", "/srv/vram-radar-account/projects/radar")
            cleared = api.set_default_directory("gpu", "")

            self.assertTrue(pinned["ok"])
            self.assertEqual(
                pinned["profile"]["servers"][0]["default_work_directory"],
                "/srv/vram-radar-account/projects/radar",
            )
            self.assertTrue(cleared["ok"])
            self.assertNotIn("default_work_directory", cleared["profile"]["servers"][0])
            self.assertEqual(store.load("local").servers[0].default_work_directory, "")
            service.inspect_account_directory.assert_called_once_with(
                "gpu", "/srv/vram-radar-account/projects/radar", force=True
            )
            self.assertEqual(service.replace_profile.call_count, 2)

    def test_navigator_side_is_persisted_outside_the_package(self):
        profile = Profile.empty("local")
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(profile, store, paths, service)

            moved = api.set_navigator_side(" left ")

            self.assertTrue(moved["ok"])
            self.assertEqual(moved["profile"]["navigator_side"], "left")
            self.assertEqual(store.load("local").navigator_side, "left")
            service.replace_profile.assert_not_called()

    def test_navigator_side_rejects_unknown_position(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())

        result = api.set_navigator_side("center")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_navigator_side")

    def test_close_behavior_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(Profile.empty("local"), store, paths, service)

            changed = api.set_close_behavior(" EXIT ")
            invalid = api.set_close_behavior("hide")

            self.assertTrue(changed["ok"])
            self.assertEqual(changed["profile"]["close_behavior"], "exit")
            self.assertEqual(store.load("local").close_behavior, "exit")
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["code"], "invalid_close_behavior")

    def test_server_enabled_toggle_preserves_password_reference_and_replaces_service(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "host": "gpu.test",
                        "auth_ref": "server:local:gpu:login-password",
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(profile, store, paths, service)

            result = api.set_server_enabled("gpu", False)

            self.assertTrue(result["ok"])
            self.assertFalse(store.load("local").servers[0].enabled)
            self.assertEqual(
                store.load("local").servers[0].auth_ref,
                "server:local:gpu:login-password",
            )
            self.assertTrue(result["profile"]["servers"][0]["has_password"])
            service.replace_profile.assert_called_once()

    def test_favorite_server_can_outlive_a_temporarily_missing_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(Profile.empty("local"), store, paths, service)

            added = api.set_favorite_server("missing-server", True)
            removed = api.set_favorite_server("missing-server", False)

            self.assertTrue(added["ok"])
            self.assertEqual(added["profile"]["favorite_server_ids"], ["missing-server"])
            self.assertTrue(removed["ok"])
            self.assertEqual(store.load("local").favorite_server_ids, ())

    def test_saved_view_api_generates_safe_id_round_trips_and_deletes_by_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(Profile.empty("local"), store, paths, service)
            criteria = {
                "query": "H100 training",
                "filter": "available",
                "gpu_count": 8,
                "min_memory_gib": 70,
                "gpu_type": "H100",
                "partition": "gpu-large",
                "same_node": True,
            }

            saved = api.save_saved_view("8 卡训练", criteria)
            deleted = api.delete_saved_view("8 卡训练")

            self.assertTrue(saved["ok"])
            self.assertRegex(saved["saved_view"]["id"], r"^view-[a-f0-9]{16}$")
            self.assertEqual(saved["saved_view"]["gpu_count"], 8)
            self.assertTrue(saved["saved_view"]["same_node"])
            self.assertTrue(deleted["ok"])
            self.assertEqual(store.load("local").saved_views, ())

    def test_saved_view_api_rejects_unbounded_or_unknown_content(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())

        result = api.save_saved_view("unsafe", {"filter": "all", "command": "ssh gpu"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_saved_view")
        api.store.save.assert_not_called()

    def test_default_first_run_keeps_discovery_unsaved_until_user_review(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.shell.resolve_server_config", return_value=Path(temporary) / "servers.toml"
        ) as resolve, patch("vram_radar.shell.configure_logging", return_value=Mock()):
            _, store, profile, service = build_runtime("isolated", Path(temporary))

        self.assertEqual(profile.servers, ())
        self.assertFalse(store.profile_path("isolated").exists())
        self.assertEqual(service.snapshot()["summary"]["total_servers"], 0)
        resolve.assert_not_called()

    def test_explicit_import_merges_catalog_semantics_with_exact_openssh_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile-home"
            catalog = root / "servers.toml"
            ssh_config = root / "editor-ssh.conf"
            catalog.write_text(CATALOG, encoding="utf-8")
            ssh_config.write_text(
                "Host direct-gpu-test\n  HostName private.example\n  IdentityFile ~/.ssh/id_ed25519\n",
                encoding="utf-8",
            )
            with patch(
                "vram_radar.shell.resolve_server_config", return_value=catalog.resolve()
            ), patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[catalog.resolve(), ssh_config.resolve()],
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, _store, profile, _service = build_runtime(
                    "isolated",
                    home,
                    servers_config=catalog,
                    automatic_import_enabled=False,
                )

        direct = profile.servers[0]
        self.assertEqual(direct.backend, "direct_ssh")
        self.assertEqual(direct.ssh_alias, "direct-gpu-test")
        self.assertEqual(direct.ssh_config_file, str(ssh_config.resolve()))
        self.assertTrue(profile.auto_sync_servers)
        self.assertEqual(profile.server_config_path, str(catalog.resolve()))

    def test_existing_catalog_auto_sync_reassociates_local_openssh_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile-home"
            catalog = root / "servers.toml"
            ssh_config = root / "editor-ssh.conf"
            catalog.write_text(CATALOG, encoding="utf-8")
            ssh_config.write_text(
                "Host direct-gpu-test\n  HostName private.example\n  IdentityFile ~/.ssh/id_ed25519\n",
                encoding="utf-8",
            )
            paths = storage_paths(home)
            ProfileStore(paths).save(
                Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "isolated",
                        "display_name": "My GPUs",
                        "server_config_path": str(catalog.resolve()),
                        "auto_sync_servers": True,
                        "servers": [
                            {
                                "id": "direct-gpu",
                                "display_name": "Direct GPU",
                                "backend": "direct_ssh",
                                "ssh_alias": "direct-gpu-test",
                            }
                        ],
                    }
                )
            )
            with patch(
                "vram_radar.shell.resolve_server_config", return_value=catalog.resolve()
            ), patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[catalog.resolve(), ssh_config.resolve()],
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, _store, profile, _service = build_runtime("isolated", home)

        self.assertEqual(profile.servers[0].ssh_config_file, str(ssh_config.resolve()))
        self.assertTrue(profile.auto_sync_servers)
        self.assertEqual(profile.server_config_path, str(catalog.resolve()))

    def test_startup_auto_sync_failure_is_visible_in_the_dashboard_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            paths = storage_paths(home)
            missing = home / "missing-servers.toml"
            ProfileStore(paths).save(
                Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "isolated",
                        "display_name": "My GPUs",
                        "server_config_path": str(missing),
                        "auto_sync_servers": True,
                        "servers": [
                            {
                                "id": "kept",
                                "display_name": "Kept",
                                "backend": "direct_ssh",
                                "host": "kept.example",
                            }
                        ],
                    }
                )
            )
            with patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, _, profile, service = build_runtime("isolated", home)

        self.assertEqual([server.id for server in profile.servers], ["kept"])
        notices = service.snapshot()["notices"]
        self.assertEqual(notices[0]["code"], "server_catalog_sync_failed")
        self.assertEqual(notices[0]["severity"], "error")
        self.assertIn("自动同步失败", notices[0]["message"])

    def test_startup_auto_sync_accepts_windows_junction_backed_user_ssh_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_home = root / "profile-home"
            user_home = root / "user-home"
            ssh_link = user_home / ".ssh"
            ssh_target = root / "managed-ssh"
            ssh_target.mkdir(parents=True)
            config = ssh_target / "config"
            config.write_text(
                "Host junction-gpu\n  HostName gpu.example\n",
                encoding="utf-8",
            )
            paths = storage_paths(profile_home)
            ProfileStore(paths).save(
                Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "isolated",
                        "display_name": "My GPUs",
                        "server_config_path": str(config),
                        "auto_sync_servers": True,
                        "servers": [
                            {
                                "id": "junction-gpu",
                                "display_name": "Junction GPU",
                                "backend": "direct_ssh",
                                "ssh_alias": "junction-gpu",
                                "ssh_config_file": str(config),
                            }
                        ],
                    }
                )
            )
            original_resolve = Path.resolve
            original_readlink = os.readlink

            def resolve(path: Path, strict: bool = False) -> Path:
                try:
                    path.relative_to(ssh_link)
                except ValueError:
                    return original_resolve(path, strict=strict)
                error = OSError(448, "untrusted mount point", str(path))
                error.winerror = 448
                raise error

            def is_junction(path: str | Path) -> bool:
                return Path(path) == ssh_link

            def readlink(path: str | Path) -> str:
                if Path(path) != ssh_link:
                    return original_readlink(path)
                return str(ssh_target)

            with patch("vram_radar.server_catalog.sys.platform", "win32"), patch(
                "vram_radar.server_catalog.Path.home", return_value=user_home
            ), patch("vram_radar.server_catalog.Path.resolve", resolve), patch(
                "vram_radar.server_catalog.os.path.isjunction", side_effect=is_junction, create=True
            ), patch("vram_radar.server_catalog.os.readlink", side_effect=readlink), patch.dict(
                os.environ,
                {"HOME": str(user_home), "USERPROFILE": str(user_home)},
                clear=False,
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, store, profile, service = build_runtime("isolated", profile_home)
                persisted = store.load("isolated")

        self.assertEqual(profile.server_config_path, str(config.resolve()))
        self.assertEqual(profile.servers[0].ssh_config_file, str(config.resolve()))
        self.assertEqual(persisted, profile)
        self.assertFalse(
            any(notice["code"] == "server_catalog_sync_failed" for notice in service.snapshot()["notices"])
        )

    def test_startup_recovers_invalid_catalog_when_one_openssh_source_covers_all_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile-home"
            invalid_catalog = root / "servers.toml"
            invalid_catalog.write_text("version = 1\n", encoding="utf-8")
            ssh_config = root / "config"
            ssh_config.write_text(
                "Host gpu-a\n  HostName a.example\n"
                "Host gpu-b\n  HostName b.example\n",
                encoding="utf-8",
            )
            paths = storage_paths(home)
            ProfileStore(paths).save(
                Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "isolated",
                        "display_name": "My GPUs",
                        "server_config_path": str(invalid_catalog),
                        "auto_sync_servers": True,
                        "servers": [
                            {
                                "id": "first",
                                "display_name": "First",
                                "backend": "direct_ssh",
                                "ssh_alias": "gpu-a",
                            },
                            {
                                "id": "second",
                                "display_name": "Second",
                                "backend": "slurm_ssh",
                                "ssh_alias": "gpu-b",
                            },
                        ],
                    }
                )
            )
            with patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[invalid_catalog.resolve(), ssh_config.resolve()],
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, store, profile, service = build_runtime("isolated", home)
                persisted = store.load("isolated")

        self.assertEqual(profile.server_config_path, str(ssh_config.resolve()))
        self.assertTrue(profile.auto_sync_servers)
        self.assertEqual(
            [server.ssh_config_file for server in profile.servers],
            [str(ssh_config.resolve()), str(ssh_config.resolve())],
        )
        self.assertEqual(profile.servers[1].backend, "slurm_ssh")
        self.assertEqual(persisted, profile)
        notices = service.snapshot()["notices"]
        self.assertEqual(notices[0]["code"], "server_catalog_sync_recovered")

    def test_startup_recovers_missing_catalog_when_discovered_openssh_covers_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile-home"
            missing_catalog = root / "missing-servers.toml"
            ssh_config = root / "config"
            ssh_config.write_text("Host gpu-a\n  HostName a.example\n", encoding="utf-8")
            paths = storage_paths(home)
            ProfileStore(paths).save(
                Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "isolated",
                        "display_name": "My GPUs",
                        "server_config_path": str(missing_catalog),
                        "auto_sync_servers": True,
                        "servers": [
                            {
                                "id": "first",
                                "display_name": "First",
                                "backend": "direct_ssh",
                                "ssh_alias": "gpu-a",
                            }
                        ],
                    }
                )
            )
            with patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[ssh_config.resolve()],
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, _, profile, service = build_runtime("isolated", home)

        self.assertEqual(profile.server_config_path, str(ssh_config.resolve()))
        self.assertEqual(profile.servers[0].ssh_config_file, str(ssh_config.resolve()))
        self.assertEqual(service.snapshot()["notices"][0]["code"], "server_catalog_sync_recovered")

    def test_runtime_notice_can_be_dismissed_without_persisting_profile(self):
        service = Mock()
        service.snapshot.return_value = {"notices": []}
        store = Mock()
        api = AppApi(Profile.empty("local"), store=store, paths=Mock(), service=service)

        result = api.dismiss_notice("server_catalog_sync_recovered")

        self.assertTrue(result["ok"])
        service.clear_notice.assert_called_once_with("server_catalog_sync_recovered")
        store.save.assert_not_called()

    def test_runtime_notice_rejects_unbounded_or_invalid_code(self):
        service = Mock()
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=service)

        result = api.dismiss_notice("../server_catalog_sync_recovered")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_notice_code")
        service.clear_notice.assert_not_called()

    def test_profile_save_requires_the_current_revision(self):
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock())

        result = api.save_profile(Profile.empty("local").to_dict())

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "profile_changed")
        self.assertEqual(result["profile"]["profile_revision"], 0)
        api.store.save.assert_not_called()

    def test_profile_save_persists_language_and_refreshes_native_menu(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(Profile.empty("local"), store, paths, service)
            tray_controller = Mock()
            api.bind_tray_controller(tray_controller)
            raw = Profile.empty("local").to_dict()
            raw["ui_language"] = "en"

            result = api.save_profile(self.versioned_profile(api, raw))

            self.assertTrue(result["ok"])
            self.assertEqual(result["profile"]["ui_language"], "en")
            self.assertEqual(store.load("local").ui_language, "en")
            tray_controller.refresh_menu.assert_called_once_with()

    def test_auto_sync_save_applies_catalog_immediately_and_matches_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "profile-home"
            catalog = Path(temporary) / "servers.toml"
            ssh_config = Path(temporary) / "editor-ssh.conf"
            catalog.write_text(CATALOG, encoding="utf-8")
            ssh_config.write_text(
                "Host editor-gpu\n  HostName editor-gpu.example\n",
                encoding="utf-8",
            )
            paths = storage_paths(home)
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(Profile.empty("local"), store, paths, service)
            raw = {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "server_config_path": str(catalog),
                "auto_sync_servers": True,
                "servers": [],
            }

            with patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[catalog.resolve(), ssh_config.resolve()],
            ), patch("vram_radar.shell.configure_logging", return_value=Mock()):
                saved = api.save_profile(self.versioned_profile(api, raw))
                immediate_ids = [server["id"] for server in saved["profile"]["servers"]]
                _, _, restarted_profile, _ = build_runtime("local", home)

            self.assertTrue(saved["ok"])
            self.assertEqual(immediate_ids, ["direct-gpu", "slurm-a100", "editor-gpu"])
            self.assertEqual(
                immediate_ids,
                [server.id for server in restarted_profile.servers],
            )
            self.assertEqual(
                [server.id for server in api.profile.servers],
                [server.id for server in store.load("local").servers],
            )
            applied_profile = service.replace_profile.call_args.args[0]
            self.assertEqual(
                [server.id for server in applied_profile.servers],
                immediate_ids,
            )

    def test_deleted_imported_host_stays_ignored_across_save_restart_and_new_hosts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "profile-home"
            config = root / "config"
            config.write_text(
                "Host retired-gpu\n  HostName retired.example\n"
                "Host current-gpu\n  HostName current.example\n",
                encoding="utf-8",
            )
            paths = storage_paths(home)
            initial = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "server_config_path": str(config),
                    "auto_sync_servers": True,
                    "servers": [
                        {
                            "id": "retired-gpu",
                            "display_name": "Retired",
                            "backend": "direct_ssh",
                            "ssh_alias": "retired-gpu",
                            "ssh_config_file": str(config),
                        }
                    ],
                }
            )
            store = ProfileStore(paths)
            store.save(initial)
            api = AppApi(initial, store, paths, Mock())
            raw = initial.to_dict()
            raw["servers"] = []
            # The current UI records this only when the user presses Remove;
            # omission from an imported replacement list is not deletion intent.
            raw["ignored_ssh_aliases"] = ["retired-gpu"]

            saved = api.save_profile(self.versioned_profile(api, raw))

            self.assertTrue(saved["ok"])
            self.assertEqual(
                [server["ssh_alias"] for server in saved["profile"]["servers"]],
                ["current-gpu"],
            )
            self.assertEqual(saved["profile"]["ignored_ssh_aliases"], ["retired-gpu"])

            config.write_text(
                "Host retired-gpu\n  HostName retired.example\n"
                "Host current-gpu\n  HostName current.example\n"
                "Host brand-new-gpu\n  HostName new.example\n",
                encoding="utf-8",
            )
            with patch("vram_radar.shell.configure_logging", return_value=Mock()):
                _, _, restarted, _ = build_runtime("local", home)

        self.assertEqual(
            [server.ssh_alias for server in restarted.servers],
            ["current-gpu", "brand-new-gpu"],
        )
        self.assertEqual(restarted.ignored_ssh_aliases, ("retired-gpu",))

    def test_missing_old_row_without_explicit_remove_does_not_create_a_tombstone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.write_text("Host new-gpu\n  HostName new.example\n", encoding="utf-8")
            paths = storage_paths(root / "profile-home")
            initial = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "servers": [
                        {
                            "id": "old-gpu",
                            "display_name": "Old",
                            "backend": "direct_ssh",
                            "ssh_alias": "old-gpu",
                        }
                    ],
                }
            )
            api = AppApi(initial, ProfileStore(paths), paths, Mock())
            raw = {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "server_config_path": str(config),
                "auto_sync_servers": True,
                "servers": [
                    {
                        "id": "new-gpu",
                        "display_name": "New",
                        "backend": "direct_ssh",
                        "ssh_alias": "new-gpu",
                        "ssh_config_file": str(config),
                    }
                ],
            }

            result = api.save_profile(self.versioned_profile(api, raw))

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["ignored_ssh_aliases"], [])

    def test_server_rename_does_not_create_an_ignored_alias(self):
        initial = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [
                    {
                        "id": "old-id",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "ssh_alias": "gpu-alias",
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(initial, ProfileStore(paths), paths, Mock())
            raw = initial.to_dict()
            raw["servers"][0]["id"] = "new-id"

            result = api.save_profile(
                self.versioned_profile(api, raw),
                renamed_server_ids={"new-id": "old-id"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["ignored_ssh_aliases"], [])

    def test_manual_readd_of_ignored_alias_clears_the_tombstone(self):
        initial = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "ignored_ssh_aliases": ["gpu-alias"],
                "servers": [],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(initial, ProfileStore(paths), paths, Mock())
            raw = initial.to_dict()
            raw["servers"] = [
                {
                    "id": "restored",
                    "display_name": "Restored",
                    "backend": "direct_ssh",
                    "ssh_alias": "GPU-ALIAS",
                }
            ]

            result = api.save_profile(self.versioned_profile(api, raw))

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["ignored_ssh_aliases"], [])

    def test_auto_sync_does_not_hide_an_invalid_primary_behind_a_valid_secondary(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "profile-home"
            catalog = Path(temporary) / "servers.toml"
            ssh_config = Path(temporary) / "config"
            catalog.write_text("version = 2\n[servers.broken\n", encoding="utf-8")
            ssh_config.write_text("Host valid-secondary\n  HostName secondary.example\n", encoding="utf-8")
            paths = storage_paths(home)
            service = Mock()
            api = AppApi(Profile.empty("local"), ProfileStore(paths), paths, service)
            raw = {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "server_config_path": str(catalog),
                "auto_sync_servers": True,
                "servers": [],
            }

            with patch(
                "vram_radar.shell.resolve_server_configs",
                return_value=[catalog.resolve(), ssh_config.resolve()],
            ):
                result = api.save_profile(self.versioned_profile(api, raw))

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "profile_invalid")
        self.assertEqual(api.profile.servers, ())
        service.replace_profile.assert_not_called()

    def test_save_reports_recovery_required_when_secret_rollback_fails(self):
        class RollbackFailingSecrets:
            def __init__(self):
                self.values = {}

            def get(self, ref):
                return self.values.get(ref)

            def set(self, ref, value):
                self.values[ref] = value

            def delete(self, ref):
                raise RuntimeError("credential manager is locked")

        raw = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [
                {
                    "id": "gpu",
                    "display_name": "GPU",
                    "backend": "direct_ssh",
                    "host": "gpu.test",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = Mock()
            store.save.side_effect = OSError("profile disk is read-only")
            secrets = RollbackFailingSecrets()
            api = AppApi(
                Profile.empty("local"),
                store,
                paths,
                Mock(),
                secret_store=secrets,
            )

            result = api.save_profile(
                self.versioned_profile(api, raw),
                {"gpu": "new-password"},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "credential_rollback_failed")
        self.assertIs(result["recovery_required"], True)
        self.assertIn("回滚失败", result["error"])
        self.assertNotIn("已恢复原配置", result["error"])
        self.assertEqual(
            secrets.values,
            {"server:local:gpu:login-password": "new-password"},
        )

    def test_get_profile_waits_for_atomic_profile_and_revision_commit(self):
        entered_save = threading.Event()
        release_save = threading.Event()

        class BlockingStore:
            def save(self, profile):
                entered_save.set()
                if not release_save.wait(timeout=2):
                    raise TimeoutError("test did not release profile save")

        raw = {
            "schema_version": 1,
            "id": "local",
            "display_name": "Committed Profile",
            "servers": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(Profile.empty("local"), BlockingStore(), paths, Mock())
            save_result = {}
            read_result = {}
            reader_started = threading.Event()

            writer = threading.Thread(
                target=lambda: save_result.update(
                    api.save_profile(self.versioned_profile(api, raw))
                )
            )
            writer.start()
            self.assertTrue(entered_save.wait(timeout=1))

            def read_profile():
                reader_started.set()
                read_result.update(api.get_profile())

            reader = threading.Thread(target=read_profile)
            reader.start()
            self.assertTrue(reader_started.wait(timeout=1))
            reader.join(timeout=0.05)
            self.assertTrue(reader.is_alive())

            release_save.set()
            writer.join(timeout=2)
            reader.join(timeout=2)

        self.assertFalse(writer.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertTrue(save_result["ok"])
        self.assertEqual(save_result["profile"]["display_name"], "Committed Profile")
        self.assertEqual(save_result["profile"]["profile_revision"], 1)
        self.assertEqual(read_result["display_name"], "Committed Profile")
        self.assertEqual(read_result["profile_revision"], 1)

    def test_server_password_is_os_stored_and_never_returned_or_written_to_profile(self):
        class FakeSecrets:
            def __init__(self):
                self.values = {}

            def get(self, ref):
                return self.values.get(ref)

            def set(self, ref, value):
                self.values[ref] = value

            def delete(self, ref):
                self.values.pop(ref, None)

        class FakeService:
            secret_store = None

            def replace_profile(self, profile, cache):
                self.profile = profile
                self.cache = cache

        raw = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "refresh_seconds": 15,
            "servers": [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}],
        }
        generated_value = "not-in-profile-123!"
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            secrets = FakeSecrets()
            service = FakeService()
            api = AppApi(Profile.empty("local"), store, paths, service, secret_store=secrets)  # type: ignore[arg-type]

            saved = api.save_profile(self.versioned_profile(api, raw), {"gpu": generated_value})
            profile_text = store.profile_path("local").read_text(encoding="utf-8")

            self.assertTrue(saved["ok"])
            self.assertTrue(saved["profile"]["servers"][0]["has_password"])
            self.assertNotIn("auth_ref", saved["profile"]["servers"][0])
            self.assertNotIn(generated_value, profile_text)
            self.assertIn("auth_ref", profile_text)
            self.assertEqual(list(secrets.values.values()), [generated_value])

            cleared = api.save_profile(self.versioned_profile(api, raw), {"gpu": None})
            self.assertTrue(cleared["ok"])
            self.assertFalse(cleared["profile"]["servers"][0]["has_password"])
            self.assertFalse(secrets.values)
            self.assertNotIn("auth_ref", store.profile_path("local").read_text(encoding="utf-8"))

    def test_server_id_rename_preserves_existing_password_reference(self):
        class FakeSecrets:
            def __init__(self):
                self.values = {}

            def get(self, ref):
                return self.values.get(ref)

            def set(self, ref, value):
                self.values[ref] = value

            def delete(self, ref):
                self.values.pop(ref, None)

        class FakeService:
            secret_store = None

            def replace_profile(self, profile, cache):
                self.profile = profile
                self.cache = cache

        original = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}],
        }
        renamed = {
            **original,
            "servers": [
                {"id": "gpu-renamed", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            secrets = FakeSecrets()
            service = FakeService()
            api = AppApi(Profile.empty("local"), store, paths, service, secret_store=secrets)  # type: ignore[arg-type]
            self.assertTrue(api.save_profile(self.versioned_profile(api, original), {"gpu": "saved-password"})["ok"])

            result = api.save_profile(self.versioned_profile(api, renamed), {}, {"gpu-renamed": "gpu"})

            self.assertTrue(result["ok"])
            self.assertEqual(result["profile"]["servers"][0]["id"], "gpu-renamed")
            self.assertTrue(result["profile"]["servers"][0]["has_password"])
            self.assertEqual(secrets.values, {"server:local:gpu:login-password": "saved-password"})

    def test_endpoint_change_clears_saved_password_unless_user_reenters_it(self):
        class FakeSecrets:
            def __init__(self):
                self.values = {}

            def get(self, ref):
                return self.values.get(ref)

            def set(self, ref, value):
                self.values[ref] = value

            def delete(self, ref):
                self.values.pop(ref, None)

        class FakeService:
            secret_store = None

            def replace_profile(self, profile, cache):
                self.profile = profile
                self.cache = cache

        original = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [
                {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "old.test"}
            ],
        }
        changed = copy.deepcopy(original)
        changed["servers"][0]["host"] = "new.test"
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            secrets = FakeSecrets()
            api = AppApi(
                Profile.empty("local"),
                store,
                paths,
                FakeService(),
                secret_store=secrets,
            )  # type: ignore[arg-type]
            self.assertTrue(
                api.save_profile(self.versioned_profile(api, original), {"gpu": "old-password"})["ok"]
            )

            cleared = api.save_profile(self.versioned_profile(api, changed))

            self.assertTrue(cleared["ok"])
            self.assertFalse(cleared["profile"]["servers"][0]["has_password"])
            self.assertFalse(secrets.values)
            self.assertIn("新端点发送旧密码", cleared["warnings"][0])

            rebound = api.save_profile(
                self.versioned_profile(api, original),
                {"gpu": "confirmed-password"},
            )

            self.assertTrue(rebound["ok"])
            self.assertTrue(rebound["profile"]["servers"][0]["has_password"])
            self.assertEqual(
                secrets.values,
                {"server:local:gpu:login-password": "confirmed-password"},
            )

    def test_profile_removal_fails_closed_when_os_credential_cannot_be_deleted(self):
        class RefusingSecrets:
            def __init__(self):
                self.values = {}
                self.refuse_delete = False

            def get(self, ref):
                return self.values.get(ref)

            def set(self, ref, value):
                self.values[ref] = value

            def delete(self, ref):
                if self.refuse_delete:
                    raise RuntimeError("keychain locked")
                self.values.pop(ref, None)

        class FakeService:
            secret_store = None

            def replace_profile(self, profile, cache):
                self.profile = profile
                self.cache = cache

        configured = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}],
        }
        empty = {**configured, "servers": []}
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            secrets = RefusingSecrets()
            service = FakeService()
            api = AppApi(Profile.empty("local"), store, paths, service, secret_store=secrets)  # type: ignore[arg-type]
            self.assertTrue(api.save_profile(self.versioned_profile(api, configured), {"gpu": "saved-password"})["ok"])
            secrets.refuse_delete = True

            result = api.save_profile(self.versioned_profile(api, empty))

            self.assertFalse(result["ok"])
            self.assertEqual([server.id for server in api.profile.servers], ["gpu"])
            self.assertEqual([server.id for server in store.load("local").servers], ["gpu"])
            self.assertEqual(secrets.values, {"server:local:gpu:login-password": "saved-password"})

    def test_password_validation_rejects_newlines_before_secret_storage(self):
        secret_store = Mock()
        raw = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}],
        }
        api = AppApi(Profile.empty("local"), store=Mock(), paths=Mock(), service=Mock(), secret_store=secret_store)

        result = api.save_profile(self.versioned_profile(api, raw), {"gpu": "line1\nline2"})

        self.assertFalse(result["ok"])
        secret_store.set.assert_not_called()

    def test_profile_form_save_preserves_endpoint_owned_preferences_and_renames_favorite(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "close_behavior": "exit",
                "favorite_server_ids": ["gpu"],
                "saved_views": [{"id": "all-gpus", "name": "全部 GPU"}],
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "host": "gpu.test",
                    }
                ],
            }
        )
        raw_without_preferences = {
            "schema_version": 1,
            "id": "local",
            "display_name": "My GPUs",
            "servers": [
                {
                    "id": "gpu-renamed",
                    "display_name": "GPU",
                    "backend": "direct_ssh",
                    "host": "gpu.test",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            service = Mock()
            api = AppApi(profile, store, paths, service)

            result = api.save_profile(
                self.versioned_profile(api, raw_without_preferences),
                renamed_server_ids={"gpu-renamed": "gpu"},
            )

            self.assertTrue(result["ok"])
            persisted = store.load("local")
            self.assertEqual(persisted.close_behavior, "exit")
            self.assertEqual(persisted.favorite_server_ids, ("gpu-renamed",))
            self.assertEqual(persisted.saved_views[0]["id"], "all-gpus")

    def test_connection_probe_uses_collector_and_updates_dashboard_runtime(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "host": "sensitive.example.test",
                        "auth_ref": "server:local:gpu:login-password",
                    }
                ],
            }
        )
        service = Mock()
        service.probe_server.return_value = {"total_gpus": 4, "raw": "must-not-return"}
        api = AppApi(profile, store=Mock(), paths=Mock(), service=service)

        result = api.test_connection("gpu")

        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"], {"backend": "direct_ssh", "total_gpus": 4})
        self.assertEqual([stage["state"] for stage in result["stages"]], ["passed"] * 3)
        service.probe_server.assert_called_once_with("gpu")
        service.refresh.assert_not_called()
        self.assertNotIn("raw", str(result))

    def test_connection_probe_returns_actionable_stages_for_collection_failure(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "host": "gpu.test",
                    }
                ],
            }
        )
        service = Mock()
        service.probe_server.side_effect = ConnectorFailure(
            "command_missing",
            "服务器缺少所需的 GPU 或调度命令",
            retryable=False,
            state="misconfigured",
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=service)

        result = api.test_connection("gpu")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "command_missing")
        self.assertEqual(result["stages"][1]["state"], "passed")
        self.assertEqual(result["stages"][2]["id"], "collection")
        self.assertEqual(result["stages"][2]["state"], "failed")

    def test_ssh_key_setup_uses_saved_password_only_for_initial_deployment_then_verifies_identity(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {
                        "id": "gpu",
                        "display_name": "GPU",
                        "backend": "direct_ssh",
                        "host": "gpu.test",
                        "auth_ref": "server:local:gpu:login-password",
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            store.save(profile)
            service = Mock()
            secret_store = Mock()
            secret_store.get.return_value = "saved-password"
            api = AppApi(profile, store, paths, service, secret_store=secret_store)
            prepared = PreparedSshKey(
                private_path=Path(temporary) / "private-key",
                public_path=Path(temporary) / "private-key.pub",
                public_line="ssh-ed25519 PUBLIC-BLOB",
                generated=False,
            )
            with (
                patch("vram_radar.shell.prepare_existing_key", return_value=prepared),
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=[
                        ConnectorFailure("auth_failed", "denied", retryable=False, state="auth_required"),
                        "VRAM_RADAR_KEY_SETUP|installed|1|1|600\n",
                        "VRAM_RADAR_KEY_VERIFY|ok\n",
                    ],
                ) as remote,
            ):
                result = api.configure_ssh_key(
                    "gpu",
                    {"mode": "existing", "private_key_path": str(prepared.private_path)},
                )

            self.assertTrue(result["ok"])
            persisted = store.load("local").servers[0]
            self.assertEqual(persisted.identity_file, str(prepared.private_path))
            self.assertTrue(persisted.prefer_identity_auth)
            self.assertEqual([stage["state"] for stage in result["stages"]], ["passed"] * 4)
            self.assertNotIn("PUBLIC-BLOB", str(remote.call_args_list[0].args))
            self.assertEqual(remote.call_args_list[0].kwargs["stdin_data"], b"ssh-ed25519 PUBLIC-BLOB\n")
            self.assertEqual(remote.call_args_list[1].kwargs["password"], "saved-password")
            self.assertTrue(remote.call_args_list[2].kwargs["identities_only"])
            secret_store.get.assert_called_once_with("server:local:gpu:login-password")

    def test_ssh_key_setup_uses_saved_password_when_key_needs_unlock_or_agent_refuses(self):
        for failure_code in ("identity_passphrase_required", "ssh_agent_refused"):
            with self.subTest(failure_code=failure_code), tempfile.TemporaryDirectory() as temporary:
                profile = Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "local",
                        "display_name": "Local",
                        "servers": [
                            {
                                "id": "gpu",
                                "display_name": "GPU",
                                "backend": "direct_ssh",
                                "host": "gpu.test",
                                "auth_ref": "server:local:gpu:login-password",
                            }
                        ],
                    }
                )
                paths = storage_paths(Path(temporary))
                store = ProfileStore(paths)
                store.save(profile)
                secret_store = Mock()
                secret_store.get.return_value = "saved-password"
                prepared = PreparedSshKey(
                    private_path=Path(temporary) / "private-key",
                    public_path=Path(temporary) / "private-key.pub",
                    public_line="ssh-ed25519 PUBLIC-BLOB",
                    generated=False,
                )
                api = AppApi(profile, store, paths, Mock(), secret_store=secret_store)
                with (
                    patch("vram_radar.shell.prepare_existing_key", return_value=prepared),
                    patch(
                        "vram_radar.shell.run_remote",
                        side_effect=[
                            ConnectorFailure(
                                failure_code,
                                "key unavailable",
                                retryable=False,
                                state="auth_required",
                            ),
                            "VRAM_RADAR_KEY_SETUP|installed|1|1|600\n",
                            "VRAM_RADAR_KEY_VERIFY|ok\n",
                        ],
                    ) as remote,
                ):
                    result = api.configure_ssh_key(
                        "gpu",
                        {"mode": "existing", "private_key_path": str(prepared.private_path)},
                    )

                self.assertTrue(result["ok"])
                self.assertEqual(remote.call_args_list[1].kwargs["password"], "saved-password")
                self.assertTrue(remote.call_args_list[2].kwargs["identities_only"])

    def test_ssh_key_setup_retains_new_remote_and_local_key_after_verification_failure(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        prepared = PreparedSshKey(
            private_path=Path("C:/temporary/private-key"),
            public_path=Path("C:/temporary/private-key.pub"),
            public_line="ssh-ed25519 PUBLIC-BLOB",
            generated=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            api = AppApi(profile, store=Mock(), paths=storage_paths(Path(temporary)), service=Mock())
            with (
                patch("vram_radar.shell.prepare_generated_key", return_value=prepared),
                patch("vram_radar.shell.remove_generated_key", return_value=True) as remove,
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=[
                        "VRAM_RADAR_KEY_SETUP|installed|0|0|600\n",
                        ConnectorFailure("auth_failed", "denied", retryable=False, state="auth_required"),
                    ],
                ) as remote,
            ):
                result = api.configure_ssh_key("gpu", {"mode": "generate"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ssh_key_recovery_required")
        self.assertTrue(result["recovery_required"])
        self.assertTrue(result["local_key_retained"])
        self.assertEqual(result["stages"][-1]["id"], "recovery")
        self.assertEqual(result["stages"][-1]["state"], "failed")
        self.assertIn("未自动重写 authorized_keys", result["stages"][-1]["message"])
        self.assertEqual(remote.call_count, 2)
        remove.assert_not_called()

    def test_ssh_key_setup_keeps_generated_key_for_retry_when_remote_key_already_existed(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        prepared = PreparedSshKey(
            private_path=Path("C:/temporary/private-key"),
            public_path=Path("C:/temporary/private-key.pub"),
            public_line="ssh-ed25519 PUBLIC-BLOB",
            generated=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            api = AppApi(profile, store=Mock(), paths=storage_paths(Path(temporary)), service=Mock())
            with (
                patch("vram_radar.shell.prepare_generated_key", return_value=prepared),
                patch("vram_radar.shell.remove_generated_key") as remove,
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=[
                        "VRAM_RADAR_KEY_SETUP|already_present|1|1|600\n",
                        ConnectorFailure("auth_failed", "denied", retryable=False, state="auth_required"),
                    ],
                ),
            ):
                result = api.configure_ssh_key("gpu", {"mode": "generate"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ssh_key_verify_failed")
        self.assertFalse(result["recovery_required"])
        self.assertTrue(result["local_key_retained"])
        self.assertEqual(result["stages"][-1]["state"], "passed")
        self.assertIn("未改动远端内容", result["stages"][-1]["message"])
        remove.assert_not_called()

    def test_ssh_key_setup_reports_cleanup_failure_before_remote_deployment(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        prepared = PreparedSshKey(
            private_path=Path("C:/temporary/private-key"),
            public_path=Path("C:/temporary/private-key.pub"),
            public_line="ssh-ed25519 PUBLIC-BLOB",
            generated=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            api = AppApi(profile, store=Mock(), paths=storage_paths(Path(temporary)), service=Mock())
            with (
                patch("vram_radar.shell.prepare_generated_key", return_value=prepared),
                patch("vram_radar.shell.remove_generated_key", return_value=False),
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=ConnectorFailure(
                        "auth_failed", "denied", retryable=False, state="auth_required"
                    ),
                ),
            ):
                result = api.configure_ssh_key("gpu", {"mode": "generate"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ssh_key_local_cleanup_failed")
        self.assertTrue(result["recovery_required"])
        self.assertTrue(result["local_key_retained"])
        self.assertIn("清理失败", result["error"])

    def test_ssh_key_setup_returns_local_validation_without_contacting_server(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        api = AppApi(profile, store=Mock(), paths=Mock(), service=Mock())
        with (
            patch(
                "vram_radar.shell.prepare_existing_key",
                side_effect=SshKeySetupError("key_not_found", "找不到 SSH 私钥"),
            ),
            patch("vram_radar.shell.run_remote") as remote,
        ):
            result = api.configure_ssh_key("gpu", {"mode": "existing", "private_key_path": "missing"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "key_not_found")
        remote.assert_not_called()

    def test_ssh_key_setup_preserves_valid_remote_key_when_profile_save_fails(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        prepared = PreparedSshKey(
            private_path=Path("D:/keys/existing"),
            public_path=Path("D:/keys/existing.pub"),
            public_line="ssh-ed25519 PUBLIC-BLOB",
            generated=False,
        )
        store = Mock()
        store.save.side_effect = OSError("disk unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            api = AppApi(profile, store, storage_paths(Path(temporary)), service=Mock())
            with (
                patch("vram_radar.shell.prepare_existing_key", return_value=prepared),
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=[
                        "VRAM_RADAR_KEY_SETUP|installed|1|1|640\n",
                        "VRAM_RADAR_KEY_VERIFY|ok\n",
                    ],
                ) as remote,
            ):
                result = api.configure_ssh_key(
                    "gpu",
                    {"mode": "existing", "private_key_path": str(prepared.private_path)},
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ssh_key_setup_recovery_required")
        self.assertEqual(remote.call_count, 2)
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["stages"][-2]["id"], "profile")
        self.assertEqual(result["stages"][-1]["id"], "recovery")
        self.assertEqual(result["stages"][-1]["state"], "failed")
        self.assertIn("公钥和本地密钥被保留", result["stages"][-1]["message"])

    def test_ssh_key_setup_requires_recovery_when_local_profile_rollback_fails(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.test"}
                ],
            }
        )
        prepared = PreparedSshKey(
            private_path=Path("D:/keys/existing"),
            public_path=Path("D:/keys/existing.pub"),
            public_line="ssh-ed25519 PUBLIC-BLOB",
            generated=False,
        )
        store = Mock()
        store.save.side_effect = [None, OSError("rollback disk unavailable")]
        service = Mock()
        service.replace_profile.side_effect = OSError("runtime switch failed")
        with tempfile.TemporaryDirectory() as temporary:
            api = AppApi(profile, store, storage_paths(Path(temporary)), service=service)
            with (
                patch("vram_radar.shell.prepare_existing_key", return_value=prepared),
                patch(
                    "vram_radar.shell.run_remote",
                    side_effect=[
                        "VRAM_RADAR_KEY_SETUP|installed|1|1|600\n",
                        "VRAM_RADAR_KEY_VERIFY|ok\n",
                    ],
                ),
            ):
                result = api.configure_ssh_key(
                    "gpu",
                    {"mode": "existing", "private_key_path": str(prepared.private_path)},
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ssh_key_setup_recovery_required")
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["stages"][-1]["id"], "recovery")
        self.assertEqual(result["stages"][-1]["state"], "failed")
        self.assertEqual(store.save.call_count, 2)

    def test_redacted_diagnostics_exclude_remote_identity_paths_commands_and_raw_data(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Sensitive Lab",
                "servers": [
                    {
                        "id": "secret-server",
                        "display_name": "Secret GPU",
                        "backend": "direct_ssh",
                        "host": "sensitive.example.test",
                        "username": "secret-user",
                        "identity_file": "C:/private/id_ed25519",
                        "ssh_config_file": "C:/private/ssh-config",
                        "auth_ref": "server:local:secret-server:login-password",
                    }
                ],
            }
        )
        service = Mock()
        service.snapshot.return_value = {
            "servers": [
                {
                    "server_id": "secret-server",
                    "display_name": "Secret GPU",
                    "total_gpus": 4,
                    "free_vram_gib": 120,
                    "command": "python train.py --token secret",
                    "connection": {"state": "online"},
                }
            ]
        }
        api = AppApi(profile, store=Mock(), paths=Mock(), service=service)

        result = api.get_redacted_diagnostics()
        legacy = api.get_diagnostics()
        serialized = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["diagnostics"]["schema_version"], 2)
        self.assertEqual(result["diagnostics"]["total_gpus"], 4)
        self.assertEqual(result["diagnostics"]["servers"][0]["label"], "server_1")
        self.assertTrue(result["diagnostics"]["servers"][0]["identity_file_configured"])
        self.assertFalse(result["diagnostics"]["servers"][0]["identity_file_present"])
        for sensitive in (
            "Sensitive Lab",
            "secret-server",
            "Secret GPU",
            "sensitive.example.test",
            "secret-user",
            "C:/private",
            "python train.py",
            "login-password",
        ):
            self.assertNotIn(sensitive, serialized)
            self.assertNotIn(sensitive, legacy)

    def test_redacted_diagnostics_accept_optional_server_scope_for_webview_bridge(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "favorite_server_ids": ["gpu-a"],
                "servers": [
                    {"id": "gpu-a", "display_name": "A", "backend": "direct_ssh", "host": "a.invalid"},
                    {"id": "gpu-b", "display_name": "B", "backend": "slurm_ssh", "host": "b.invalid"},
                ],
            }
        )
        service = Mock()
        service.snapshot.return_value = {
            "servers": [
                {
                    "server_id": "gpu-a",
                    "total_gpus": 4,
                    "free_vram_gib": 80,
                    "connection": {"state": "online"},
                },
                {
                    "server_id": "gpu-b",
                    "total_gpus": 2,
                    "free_vram_gib": 20,
                    "connection": {"state": "offline"},
                },
            ]
        }
        api = AppApi(profile, store=Mock(), paths=Mock(), service=service)

        global_result = api.get_redacted_diagnostics()
        scoped_result = api.get_redacted_diagnostics("gpu-a")
        missing_result = api.get_redacted_diagnostics("missing")

        self.assertEqual(global_result["diagnostics"]["scope"], "profile")
        self.assertEqual(global_result["diagnostics"]["server_count"], 2)
        self.assertEqual(global_result["diagnostics"]["total_gpus"], 6)
        self.assertTrue(scoped_result["ok"])
        self.assertEqual(scoped_result["diagnostics"]["scope"], "server")
        self.assertEqual(scoped_result["diagnostics"]["server_count"], 1)
        self.assertEqual(scoped_result["diagnostics"]["total_gpus"], 4)
        self.assertEqual(scoped_result["diagnostics"]["free_vram_gib"], 80)
        self.assertEqual(scoped_result["diagnostics"]["backend_counts"], {"direct_ssh": 1, "slurm_ssh": 0})
        self.assertEqual(scoped_result["diagnostics"]["favorite_count"], 1)
        self.assertNotIn("gpu-a", json.dumps(scoped_result, ensure_ascii=False))
        self.assertFalse(missing_result["ok"])

    def test_copy_redacted_diagnostics_uses_native_clipboard_and_returns_full_text(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "Local",
                "servers": [
                    {"id": "gpu", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.invalid"}
                ],
            }
        )
        service = Mock()
        service.snapshot.return_value = {
            "servers": [
                {
                    "server_id": "gpu",
                    "connection": {
                        "state": "auth_required",
                        "data_origin": "none",
                        "error": {"code": "auth_failed", "retryable": False},
                    },
                }
            ]
        }
        api = AppApi(profile, store=Mock(), paths=Mock(), service=service)

        with patch("vram_radar.shell._copy_text_to_system_clipboard", return_value=True) as copy_text:
            result = api.copy_redacted_diagnostics("gpu")

        self.assertTrue(result["ok"])
        self.assertTrue(result["copied"])
        self.assertEqual(result["diagnostics"]["servers"][0]["error_code"], "auth_failed")
        self.assertIn('"local_ssh"', result["text"])
        self.assertIn('"servers"', result["text"])
        copy_text.assert_called_once_with(result["text"])

    def test_redacted_diagnostics_include_bounded_error_codes_without_server_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            paths.logs.mkdir(parents=True)
            (paths.logs / "app.log").write_text(
                "2026-08-29 WARNING server=gpu-secret code=auth_failed message=sensitive\n"
                "2026-08-29 WARNING server=gpu-secret code=auth_failed message=sensitive\n"
                "2026-08-29 WARNING server=other-secret code=ssh_timeout message=sensitive\n",
                encoding="utf-8",
            )
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "servers": [
                        {"id": "gpu-secret", "display_name": "GPU", "backend": "direct_ssh", "host": "gpu.invalid"},
                        {"id": "other-secret", "display_name": "Other", "backend": "direct_ssh", "host": "other.invalid"},
                    ],
                }
            )
            service = Mock()
            service.snapshot.return_value = {"servers": []}
            result = AppApi(profile, store=Mock(), paths=paths, service=service).get_redacted_diagnostics(
                "gpu-secret"
            )

        recent = result["diagnostics"]["recent_connection_errors"]
        self.assertEqual(recent["event_count"], 2)
        self.assertEqual(recent["by_code"], {"auth_failed": 2})
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("gpu-secret", serialized)
        self.assertNotIn("other-secret", serialized)
        self.assertNotIn("sensitive", serialized)

    def test_macos_clipboard_uses_stdin_without_putting_diagnostics_in_argv(self):
        text = '{"diagnostic":"bounded support text"}'
        with patch("vram_radar.shell.sys.platform", "darwin"), patch(
            "vram_radar.shell.subprocess.run", return_value=Mock(returncode=0)
        ) as run:
            copied = _copy_text_to_system_clipboard(text)

        self.assertTrue(copied)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["/usr/bin/pbcopy"])
        self.assertNotIn(text, " ".join(argv))
        self.assertEqual(run.call_args.kwargs["input"], text.encode("utf-8"))

    def test_open_logs_directory_uses_platform_file_manager_without_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(Profile.empty("local"), store=Mock(), paths=paths, service=Mock())
            with patch("vram_radar.shell.sys.platform", "darwin"), patch(
                "vram_radar.shell.subprocess.Popen"
            ) as popen:
                result = api.open_logs_directory()

        self.assertTrue(result["ok"])
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "open")
        self.assertEqual(len(command), 2)
        self.assertTrue(popen.call_args.kwargs["close_fds"])

    @unittest.skipUnless(os.name == "nt", "Windows byte-range lock behavior")
    def test_second_profile_lock_raises_controlled_instance_signal(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "local.lock"
            with InstanceLock(lock_path):
                with self.assertRaises(InstanceAlreadyRunning):
                    with InstanceLock(lock_path):
                        self.fail("second lock unexpectedly succeeded")

    def test_second_instance_signals_primary_and_exits_without_an_error_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "local.activation.json"
            activated = threading.Event()
            with ActivationServer(endpoint, activated.set):
                self.assertTrue(request_existing_instance(endpoint, timeout_seconds=1))
                self.assertTrue(activated.wait(1))
            self.assertFalse(endpoint.exists())

    def test_authenticated_activation_probe_reports_fixed_frontend_readiness_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "local.activation.json"
            with ActivationServer(endpoint, Mock(), on_probe=lambda: True):
                document = json.loads(endpoint.read_text(encoding="utf-8"))
                with socket.create_connection(("127.0.0.1", document["port"]), timeout=1) as connection:
                    connection.sendall(f"{document['nonce']} PROBE\n".encode("utf-8"))
                    self.assertEqual(connection.recv(32).strip(), b"READY")

                with socket.create_connection(("127.0.0.1", document["port"]), timeout=1) as connection:
                    connection.sendall(b"wrong-nonce PROBE\n")
                    self.assertEqual(connection.recv(32).strip(), b"DENIED")

    def test_frontend_probe_checks_dom_and_bridge_without_reloading(self):
        window = Mock()
        window.events.loaded.is_set.return_value = True
        window.evaluate_js.return_value = True

        self.assertTrue(window_frontend_is_ready(window))

        script = window.evaluate_js.call_args.args[0]
        self.assertIn("document.readyState", script)
        self.assertIn(".app-shell", script)
        self.assertIn("window.pywebview", script)
        self.assertNotIn("reload", script.lower())

    def test_second_instance_is_rejected_before_runtime_can_sync_the_profile(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            InstanceLock,
            "__enter__",
            side_effect=InstanceAlreadyRunning("already running"),
        ), patch("vram_radar.shell.request_existing_instance", return_value=True) as request, patch(
            "vram_radar.shell.build_runtime"
        ) as build:
            result = main(["--profile", "local", "--home", temporary])

        self.assertEqual(result, 0)
        request.assert_called_once()
        build.assert_not_called()

    def test_existing_instance_exit_request_uses_the_authenticated_local_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "local.activation.json"
            activated = threading.Event()
            exited = threading.Event()
            with ActivationServer(endpoint, activated.set, exited.set):
                self.assertTrue(request_existing_instance(endpoint, action="exit", timeout_seconds=1))
                self.assertTrue(exited.wait(1))
            self.assertFalse(activated.is_set())

    def test_existing_instance_rejects_unknown_action(self):
        with self.assertRaisesRegex(ValueError, "show or exit"):
            request_existing_instance(Path("unused"), action="restart")

    def test_quit_existing_short_circuits_before_runtime_or_server_discovery(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.shell.request_existing_instance", return_value=False
        ) as request, patch("vram_radar.shell.build_runtime") as build:
            result = main(["--profile", "local", "--home", temporary, "--quit-existing"])

        self.assertEqual(result, 0)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["action"], "exit")
        build.assert_not_called()

    def test_invalid_activation_endpoint_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "local.activation.json"
            endpoint.write_text('{"schema_version": 1, "port": 0, "nonce": "short"}', encoding="utf-8")
            self.assertFalse(request_existing_instance(endpoint, timeout_seconds=0.15))

    def test_native_desktop_backends_get_their_icon_from_the_bundle(self):
        icon_path = Path("app-icon.png")

        with patch("vram_radar.shell.sys.platform", "win32"):
            self.assertEqual(webview_start_options(False, icon_path), {"debug": False})
        with patch("vram_radar.shell.sys.platform", "darwin"):
            self.assertEqual(webview_start_options(True, icon_path), {"debug": True})
        with patch("vram_radar.shell.sys.platform", "linux"):
            self.assertEqual(
                webview_start_options(False, icon_path),
                {"debug": False, "icon": str(icon_path)},
            )

    def test_gui_smoke_waits_for_visible_window_then_closes_it(self):
        shown = threading.Event()
        shown.set()
        window = Mock()
        window.events.shown = shown
        result = {}

        window_smoke_worker(window, result, timeout_seconds=0.1)

        self.assertEqual(result, {"shown": True})
        window.destroy.assert_called_once_with()

    def test_gui_smoke_times_out_and_still_closes_the_window(self):
        window = Mock()
        window.events.shown = threading.Event()
        result = {}

        window_smoke_worker(window, result, timeout_seconds=0.01)

        self.assertFalse(result["shown"])
        self.assertIn("timeout", result["error"])
        window.destroy.assert_called_once_with()

    def test_activation_shutdown_wakeup_never_restores_a_disposed_window(self):
        stopped = threading.Event()
        requested = Mock()
        requested.wait.side_effect = lambda _timeout: (stopped.set() or True)
        window = Mock()
        window.events.shown = threading.Event()
        window.events.shown.set()

        with patch("vram_radar.shell.restore_window") as restore:
            activation_worker(
                window,
                requested,
                threading.Event(),
                stopped,
                Mock(),
            )

        restore.assert_not_called()
        requested.clear.assert_not_called()

    def test_activation_shutdown_after_clear_never_restores_a_disposed_window(self):
        stopped = threading.Event()
        requested = Mock()
        requested.wait.return_value = True
        requested.clear.side_effect = stopped.set
        window = Mock()
        window.events.shown = threading.Event()
        window.events.shown.set()

        with patch("vram_radar.shell.restore_window") as restore:
            activation_worker(
                window,
                requested,
                threading.Event(),
                stopped,
                Mock(),
            )

        restore.assert_not_called()
        requested.clear.assert_called_once_with()

    def test_shutdown_rejects_activation_after_its_final_check_before_destroy(self):
        final_check_passed = threading.Event()
        release_final_check = threading.Event()
        order = []

        class RacingStopEvent:
            def __init__(self):
                self.event = threading.Event()
                self.checks = 0

            def is_set(self):
                self.checks += 1
                value = self.event.is_set()
                if self.checks == 3:
                    final_check_passed.set()
                    release_final_check.wait(1)
                return value

            def set(self):
                self.event.set()

        requested = threading.Event()
        requested.set()
        stopped = RacingStopEvent()
        window = Mock()
        window.events.shown = threading.Event()
        window.events.shown.set()
        window.destroy.side_effect = lambda: order.append("destroy")
        shutdown = WindowShutdownCoordinator(window, requested, stopped)  # type: ignore[arg-type]
        worker = threading.Thread(
            target=activation_worker,
            args=(
                window,
                requested,
                threading.Event(),
                stopped,
                shutdown.request,
                shutdown.restore,
            ),
        )
        shutdown.bind_worker(worker)

        with patch("vram_radar.shell.restore_window", side_effect=lambda _window: order.append("restore")) as restore:
            worker.start()
            self.assertTrue(final_check_passed.wait(1))
            shutdown.request()
            window.destroy.assert_not_called()
            release_final_check.set()
            self.assertTrue(shutdown.wait(1))

        restore.assert_not_called()
        self.assertEqual(order, ["destroy"])
        self.assertFalse(worker.is_alive())

    def test_shutdown_waits_for_an_inflight_window_operation_before_destroy(self):
        entered_restore = threading.Event()
        release_restore = threading.Event()
        requested = threading.Event()
        stopped = threading.Event()
        order = []
        window = Mock()
        window.destroy.side_effect = lambda: order.append("destroy")
        shutdown = WindowShutdownCoordinator(window, requested, stopped)

        def delayed_restore(_window):
            order.append("restore-start")
            entered_restore.set()
            release_restore.wait(1)
            order.append("restore-end")

        with patch("vram_radar.shell.restore_window", side_effect=delayed_restore):
            restore_thread = threading.Thread(target=shutdown.restore)
            restore_thread.start()
            self.assertTrue(entered_restore.wait(1))
            shutdown.request()
            window.destroy.assert_not_called()
            release_restore.set()
            restore_thread.join(1)
            self.assertTrue(shutdown.wait(1))

        self.assertEqual(order, ["restore-start", "restore-end", "destroy"])

    def test_guarded_restore_uses_the_last_valid_window_geometry(self):
        requested = threading.Event()
        stopped = threading.Event()
        window = Mock()
        geometry = WindowGeometry(1120, 760)
        shutdown = WindowShutdownCoordinator(
            window,
            requested,
            stopped,
            preferred_geometry=lambda: geometry,
        )

        with patch("vram_radar.shell.restore_window") as restore:
            self.assertTrue(shutdown.restore())

        restore.assert_called_once_with(window, geometry)

    def test_shutdown_flushes_window_geometry_before_native_destroy(self):
        requested = threading.Event()
        stopped = threading.Event()
        order = []
        window = Mock()
        window.destroy.side_effect = lambda: order.append("destroy")
        shutdown = WindowShutdownCoordinator(
            window,
            requested,
            stopped,
            before_destroy=lambda: order.append("flush"),
        )

        shutdown.request()

        self.assertTrue(shutdown.wait(1))
        self.assertEqual(order, ["flush", "destroy"])

    def test_gui_smoke_can_delegate_native_close_to_lifecycle_coordinator(self):
        shown = threading.Event()
        shown.set()
        window = Mock()
        window.events.shown = shown
        request_shutdown = Mock()
        result = {}

        window_smoke_worker(
            window,
            result,
            timeout_seconds=0.1,
            request_shutdown=request_shutdown,
        )

        self.assertEqual(result, {"shown": True})
        request_shutdown.assert_called_once_with()
        window.destroy.assert_not_called()

    def test_manual_catalog_import_preserves_local_order_and_command_setting(self):
        profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "local",
                "display_name": "My GPUs",
                "servers": [
                    {
                        "id": "slurm-a100",
                        "display_name": "Old A100",
                        "backend": "slurm_ssh",
                        "ssh_alias": "old-a100",
                    },
                    {
                        "id": "direct-gpu",
                        "display_name": "Old 4090",
                        "backend": "direct_ssh",
                        "ssh_alias": "old-4090",
                        "show_other_user_commands": True,
                    },
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG, encoding="utf-8")
            api = AppApi(profile, store=None, paths=None, service=None)  # type: ignore[arg-type]

            result = api.import_server_config(str(path))

        self.assertTrue(result["ok"])
        self.assertEqual([server["id"] for server in result["servers"]], ["slurm-a100", "direct-gpu"])
        self.assertFalse(result["servers"][0]["show_other_user_commands"])
        self.assertTrue(result["servers"][1]["show_other_user_commands"])

    def test_ui_discovery_includes_user_openssh_config_without_changing_startup_discovery(self):
        source = Path("test-home") / ".ssh" / "config"
        api = AppApi(Profile.empty("local"), store=None, paths=None, service=None)  # type: ignore[arg-type]

        with patch("vram_radar.shell.resolve_server_configs", return_value=[source]) as resolve:
            result = api.discover_server_config()

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], str(source))
        self.assertEqual(result["paths"], [str(source)])
        resolve.assert_called_once_with(include_openssh=True)

    def test_no_auto_import_disables_api_discovery_without_touching_profile(self):
        profile = Profile.empty("local")
        store = Mock()
        api = AppApi(
            profile,
            store=store,
            paths=Mock(),
            service=Mock(),
            automatic_import_enabled=False,
        )

        with patch("vram_radar.shell.resolve_server_configs") as resolve_many, patch(
            "vram_radar.shell.resolve_server_config"
        ) as resolve_one, patch("vram_radar.shell.import_server_config") as import_source:
            discovered = api.discover_server_config()
            implicit_import = api.import_server_config("")

        self.assertFalse(discovered["ok"])
        self.assertEqual(discovered["code"], "automatic_import_disabled")
        self.assertEqual(discovered["paths"], [])
        self.assertIn("--no-auto-import", discovered["message"])
        self.assertFalse(implicit_import["ok"])
        self.assertEqual(implicit_import["code"], "automatic_import_disabled")
        self.assertIs(api.profile, profile)
        resolve_many.assert_not_called()
        resolve_one.assert_not_called()
        import_source.assert_not_called()
        store.save.assert_not_called()

    def test_no_auto_import_still_allows_an_explicit_manual_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG, encoding="utf-8")
            api = AppApi(
                Profile.empty("local"),
                store=Mock(),
                paths=Mock(),
                service=Mock(),
                automatic_import_enabled=False,
            )

            with patch("vram_radar.shell.resolve_server_configs") as automatic_discovery:
                result = api.import_server_config(str(path))

        self.assertTrue(result["ok"])
        self.assertEqual([server["id"] for server in result["servers"]], ["direct-gpu", "slurm-a100"])
        self.assertFalse(result["persisted"])
        automatic_discovery.assert_not_called()

    def test_no_auto_import_skips_stored_startup_sync_and_keeps_profile_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            paths = storage_paths(home)
            catalog = home / "configured-servers.toml"
            catalog.write_text(CATALOG, encoding="utf-8")
            original = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "isolated",
                    "display_name": "Isolated",
                    "server_config_path": str(catalog),
                    "auto_sync_servers": True,
                    "servers": [
                        {
                            "id": "kept",
                            "display_name": "Kept server",
                            "backend": "direct_ssh",
                            "ssh_alias": "kept-server",
                        }
                    ],
                }
            )
            persisted_store = ProfileStore(paths)
            persisted_store.save(original)
            profile_path = persisted_store.profile_path("isolated")
            profile_bytes = profile_path.read_bytes()

            with patch("vram_radar.shell.resolve_server_configs") as resolve_many, patch(
                "vram_radar.shell.resolve_server_config"
            ) as resolve_one, patch("vram_radar.shell.import_server_config") as import_source, patch(
                "vram_radar.shell.configure_logging", return_value=Mock()
            ):
                _, store, loaded, service = build_runtime(
                    "isolated",
                    home,
                    automatic_import_enabled=False,
                )

            self.assertEqual(loaded, original)
            self.assertEqual(service.snapshot()["summary"]["total_servers"], 1)
            self.assertEqual(profile_bytes, profile_path.read_bytes())
            self.assertEqual(store.load("isolated"), original)
            resolve_many.assert_not_called()
            resolve_one.assert_not_called()
            import_source.assert_not_called()

    def test_update_api_opens_only_the_maintained_latest_release_page(self):
        api = AppApi(Profile.empty("local"), store=None, paths=None, service=None)  # type: ignore[arg-type]

        release_url = "https://github.com/example-org/VRAMRadar/releases/tag/v0.4.1"
        with patch(
            "vram_radar.shell.check_latest_release",
            return_value={"ok": True, "update_available": True, "release_url": release_url},
        ), patch("vram_radar.shell.webbrowser.open", return_value=True) as open_browser:
            api.check_for_updates()
            result = api.open_latest_release()

        self.assertTrue(result["ok"])
        open_browser.assert_called_once_with(release_url)

    def test_windows_one_click_update_rechecks_downloads_verifies_and_schedules(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(Profile.empty("local"), store=None, paths=paths, service=None)  # type: ignore[arg-type]
            asset = {
                "name": "VRAMRadar-Setup-0.7.0.exe",
                "url": "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe",
                "sha256": "ab" * 32,
                "size": 10,
            }
            installer = Path(temporary) / asset["name"]
            with patch(
                "vram_radar.shell.check_latest_release",
                return_value={
                    "ok": True,
                    "update_available": True,
                    "latest_version": "0.7.0",
                    "release_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "asset": asset,
                },
            ), patch("vram_radar.shell.sys.platform", "win32"), patch(
                "vram_radar.shell.download_verified_asset", return_value=installer
            ) as download, patch(
                "vram_radar.shell.windows_update_capability", return_value=(True, None)
            ), patch("vram_radar.shell.schedule_windows_update") as schedule:
                result = api.install_latest_update()

            self.assertTrue(result["ok"])
            self.assertTrue(result["scheduled"])
            download.assert_called_once_with(asset, paths.cache / "updates")
            schedule.assert_called_once_with(
                installer,
                sha256="ab" * 32,
                version="0.7.0",
                activation_path=paths.runtime / "local.activation.json",
                restart_arguments=["--profile", "local"],
            )

    def test_macos_update_download_is_verified_before_finder_reveal(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            api = AppApi(Profile.empty("local"), store=None, paths=paths, service=None)  # type: ignore[arg-type]
            asset = {
                "name": "VRAMRadar-0.7.0-macos.zip",
                "url": "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-0.7.0-macos.zip",
                "sha256": "ab" * 32,
                "size": 10,
            }
            archive = Path(temporary) / asset["name"]
            with patch(
                "vram_radar.shell.check_latest_release",
                return_value={
                    "ok": True,
                    "update_available": True,
                    "latest_version": "0.7.0",
                    "release_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "asset": asset,
                },
            ), patch("vram_radar.shell.sys.platform", "darwin"), patch(
                "vram_radar.shell.download_verified_asset", return_value=archive
            ) as download, patch("vram_radar.shell.subprocess.Popen") as reveal:
                result = api.install_latest_update()

            self.assertTrue(result["ok"])
            self.assertFalse(result["scheduled"])
            download.assert_called_once_with(asset, paths.cache / "updates")
            reveal.assert_called_once_with(
                ["open", "-R", str(archive)],
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def test_show_release_exits_without_building_runtime(self):
        with patch("vram_radar.shell.current_release_tag", return_value="v0.4.0-macos-beta.3"), patch(
            "vram_radar.shell.build_runtime"
        ) as build_runtime, patch("builtins.print") as print_value:
            from vram_radar.shell import main

            result = main(["--show-release"])

        self.assertEqual(result, 0)
        build_runtime.assert_not_called()
        print_value.assert_called_once_with("v0.4.0-macos-beta.3")


if __name__ == "__main__":
    unittest.main()
