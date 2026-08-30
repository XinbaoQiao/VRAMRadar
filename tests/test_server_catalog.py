from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from vram_radar.models import ConfigError, Profile
from vram_radar.server_catalog import (
    _system_ssh_config_candidates,
    import_openssh_config,
    import_server_catalog,
    import_server_config,
    openssh_config_dependency_fingerprint,
    profile_from_server_config,
    profile_from_server_configs,
    resolve_server_config,
    resolve_server_configs,
    server_config_candidates,
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

[servers."slurm-a100".slurm.partitions."gpu"]
gpu_memory_gb = 40
gpu_types = ["A100-40G"]
"""


class ServerCatalogTests(unittest.TestCase):
    def test_windows_untrusted_junction_uses_kernel_reported_ssh_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            ssh_link = home / ".ssh"
            ssh_target = root / "managed-ssh"
            fragment = ssh_target / "config.d" / "gpu.conf"
            config = ssh_target / "config"
            fragment.parent.mkdir(parents=True)
            config.write_text("Include config.d/*.conf\n", encoding="utf-8")
            fragment.write_text(
                "Host junction-gpu\n  HostName gpu.example\n",
                encoding="utf-8",
            )
            original_resolve = Path.resolve

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
                    raise OSError("not a reparse point")
                return str(ssh_target)

            environment = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(root / "xdg"),
                "APPDATA": "",
                "LOCALAPPDATA": "",
                "VRAM_RADAR_SERVERS_CONFIG": "",
            }
            with patch("vram_radar.server_catalog.sys.platform", "win32"), patch(
                "vram_radar.server_catalog.Path.home", return_value=home
            ), patch("vram_radar.server_catalog.Path.resolve", resolve), patch(
                "vram_radar.server_catalog.os.path.isjunction", side_effect=is_junction, create=True
            ), patch("vram_radar.server_catalog.os.readlink", side_effect=readlink), patch(
                "vram_radar.server_catalog._candidate_roots", return_value=[]
            ), patch(
                "vram_radar.server_catalog._remote_ssh_config_candidates", return_value=[]
            ), patch(
                "vram_radar.server_catalog._system_ssh_config_candidates", return_value=[]
            ), patch.dict(os.environ, environment, clear=False):
                candidates = server_config_candidates(include_openssh=True)
                existing = resolve_server_configs(include_openssh=True)
                servers, warnings = import_openssh_config(config)
            expected_config = original_resolve(config)
            expected_fragment = original_resolve(fragment)

        self.assertIn(expected_config, candidates)
        self.assertIn(expected_config, existing)
        self.assertIn(expected_fragment, candidates)
        self.assertEqual([server.ssh_alias for server in servers], ["junction-gpu"])
        self.assertTrue(any("Include" in warning for warning in warnings))

    def test_import_maps_server_backends_and_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG, encoding="utf-8")
            servers, warnings = import_server_catalog(path)
            by_id = {server.id: server for server in servers}
            self.assertEqual(warnings, [])
            self.assertEqual(by_id["direct-gpu"].backend, "direct_ssh")
            self.assertEqual(by_id["slurm-a100"].backend, "slurm_ssh")
            self.assertEqual(by_id["slurm-a100"].gpu_memory_gib["A100-40G"], 40)

            profile, _ = profile_from_server_config(Profile.empty("local"), path)
            self.assertTrue(profile.auto_sync_servers)
            self.assertEqual(profile.server_config_path, str(path.resolve()))
            self.assertEqual([server.id for server in profile.servers], ["direct-gpu", "slurm-a100"])

            customized = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "servers": [
                        {
                            "id": "slurm-a100",
                            "display_name": "Old A100 name",
                            "backend": "slurm_ssh",
                            "ssh_alias": "old-a100-alias",
                        },
                        {
                            "id": "direct-gpu",
                            "display_name": "Old 4090 name",
                            "backend": "direct_ssh",
                            "ssh_alias": "old-4090-alias",
                            "show_other_user_commands": True,
                            "default_work_directory": "/srv/vram-radar-account/projects/radar",
                            "prefer_identity_auth": True,
                        }
                    ],
                }
            )
            synchronized, _ = profile_from_server_config(customized, path)
            self.assertEqual([server.id for server in synchronized.servers], ["slurm-a100", "direct-gpu"])
            self.assertEqual(synchronized.servers[0].display_name, "A100 cluster")
            self.assertTrue(synchronized.servers[1].show_other_user_commands)
            self.assertEqual(
                synchronized.servers[1].default_work_directory,
                "/srv/vram-radar-account/projects/radar",
            )
            self.assertTrue(synchronized.servers[1].prefer_identity_auth)

    def test_explicit_path_is_resolved_and_missing_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG, encoding="utf-8")
            self.assertEqual(resolve_server_config(path), path.resolve())
            with self.assertRaisesRegex(ConfigError, "不存在"):
                resolve_server_config(Path(temporary) / "missing.toml")

    def test_explicit_config_path_expands_cross_platform_home_variables(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".ssh" / "config"
            config.parent.mkdir(parents=True)
            config.write_text("Host gpu\n  HostName gpu.example\n", encoding="utf-8")
            values = (
                "$HOME/.ssh/config",
                "${HOME}/.ssh/config",
                "$env:USERPROFILE/.ssh/config",
                "%USERPROFILE%/.ssh/config",
            )
            with patch("vram_radar.server_catalog.Path.home", return_value=home), patch.dict(
                os.environ,
                {"HOME": "", "USERPROFILE": ""},
                clear=False,
            ):
                for value in values:
                    with self.subTest(value=value):
                        self.assertEqual(resolve_server_config(value), config.resolve())

    def test_catalog_with_credentials_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG + '\n[servers."direct-gpu".auth]\ntoken = "x"\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "凭据字段"):
                import_server_catalog(path)

    def test_catalog_with_prefixed_credential_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(CATALOG + '\n[servers."direct-gpu".auth]\naccess_token = "x"\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "access_token"):
                import_server_catalog(path)

    def test_discovery_supports_portable_harness_workspace_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "harness" / "config" / "servers.toml"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(CATALOG, encoding="utf-8")
            with patch("vram_radar.server_catalog._candidate_roots", return_value=[root]), patch.dict(
                os.environ, {"VRAM_RADAR_SERVERS_CONFIG": ""}, clear=False
            ):
                canonical_catalog = catalog.resolve()
                self.assertIn(canonical_catalog, server_config_candidates())
                self.assertEqual(resolve_server_config(), canonical_catalog)

    def test_ui_discovery_can_fall_back_to_user_openssh_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            ssh_config = home / ".ssh" / "config"
            ssh_config.parent.mkdir()
            ssh_config.write_text("Host gpu-mac\n  HostName 192.0.2.10\n", encoding="utf-8")
            with patch("vram_radar.server_catalog._candidate_roots", return_value=[]), patch(
                "vram_radar.server_catalog.Path.home", return_value=home
            ), patch("vram_radar.server_catalog._system_ssh_config_candidates", return_value=[]), patch.dict(
                os.environ, {"VRAM_RADAR_SERVERS_CONFIG": ""}, clear=False
            ):
                self.assertIsNone(resolve_server_config())
                self.assertEqual(resolve_server_config(include_openssh=True), ssh_config.resolve())

    def test_openssh_config_imports_only_concrete_host_aliases(self):
        document = """\
Host *
  ServerAliveInterval 30
Host gpu-mac gpu-backup # two concrete aliases
  HostName 192.0.2.10
  User tester
  IdentityFile ~/.ssh/id_private
Host !blocked *.example.com exact-host
  HostName 192.0.2.20
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".ssh" / "config"
            path.parent.mkdir()
            path.write_text(document, encoding="utf-8")

            servers, warnings = import_openssh_config(path)
            aliases = [server.ssh_alias for server in servers]

        self.assertEqual(aliases, ["gpu-mac", "gpu-backup", "exact-host"])
        self.assertTrue(all(server.backend == "direct_ssh" for server in servers))
        self.assertTrue(all(not server.username and not server.identity_file for server in servers))
        self.assertTrue(all(server.ssh_config_file == str(path.resolve()) for server in servers))
        self.assertIn("无法判断直连或 Slurm", warnings[0])

    def test_openssh_import_accepts_equals_separated_host_and_include_directives(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".ssh" / "custom-equals.conf"
            first_fragment = home / ".ssh" / "conf.d" / "first.conf"
            second_fragment = home / ".ssh" / "conf.d" / "second.conf"
            first_fragment.parent.mkdir(parents=True)
            path.write_text(
                "Include = conf.d/first.conf\n"
                "Include=conf.d/second.conf\n"
                "Host=equals-root\n"
                "Host = equals-spaced\n",
                encoding="utf-8",
            )
            # Reset to an unconditional block before control returns to the
            # parent file. OpenSSH carries Host/Match state across Include
            # boundaries, so this keeps the second Include genuinely global.
            first_fragment.write_text("Host=equals-first\nHost *\n", encoding="utf-8")
            second_fragment.write_text("Host = equals-second\n", encoding="utf-8")

            with patch("vram_radar.server_catalog.Path.home", return_value=home):
                # Use the generic dispatcher as well as the OpenSSH parser: a
                # custom editor path must be recognized even when its first
                # directive uses the legal ``keyword=value`` form.
                servers, warnings = import_server_config(path)

        self.assertEqual(
            [server.ssh_alias for server in servers],
            ["equals-first", "equals-second", "equals-root", "equals-spaced"],
        )
        self.assertTrue(any("Include" in warning for warning in warnings))

    def test_openssh_import_skips_includes_in_conditional_host_and_match_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".ssh" / "config"
            global_fragment = home / ".ssh" / "global.conf"
            host_fragment = home / ".ssh" / "host-only.conf"
            match_fragment = home / ".ssh" / "match-only.conf"
            path.parent.mkdir(parents=True)
            path.write_text(
                "Include global.conf\n"
                "Host base-server\n"
                "  Include host-only.conf\n"
                "Match user somebody\n"
                "  Include match-only.conf\n",
                encoding="utf-8",
            )
            global_fragment.write_text("Host global-server\n", encoding="utf-8")
            host_fragment.write_text("Host leaked-from-host-condition\n", encoding="utf-8")
            match_fragment.write_text("Host leaked-from-match-condition\n", encoding="utf-8")

            with patch("vram_radar.server_catalog.Path.home", return_value=home):
                servers, warnings = import_openssh_config(path)

        self.assertEqual(
            [server.ssh_alias for server in servers],
            ["global-server", "base-server"],
        )
        self.assertTrue(
            any("Include" in warning and ("条件" in warning or "跳过" in warning) for warning in warnings)
        )

    def test_repeated_include_preserves_the_fragments_exit_host_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            ssh_root = home / ".ssh"
            ssh_root.mkdir(parents=True)
            config = ssh_root / "config"
            fragment = ssh_root / "scope.conf"
            conditional = ssh_root / "must-not-import.conf"
            fragment.write_text("Host scoped-server\n", encoding="utf-8")
            conditional.write_text("Host leaked-server\n", encoding="utf-8")
            config.write_text(
                "Include scope.conf\n"
                "Host *\n"
                "Include scope.conf\n"
                "Include must-not-import.conf\n",
                encoding="utf-8",
            )

            with patch("vram_radar.server_catalog.Path.home", return_value=home):
                servers, warnings = import_openssh_config(config)

        self.assertEqual([server.ssh_alias for server in servers], ["scoped-server"])
        self.assertTrue(any("条件" in warning or "跳过" in warning for warning in warnings))

    def test_openssh_include_expands_home_environment_and_percent_d(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            ssh_root = home / ".ssh"
            fragments = ssh_root / "fragments"
            fragments.mkdir(parents=True)
            config = ssh_root / "config"
            (fragments / "home.conf").write_text("Host home-env\n", encoding="utf-8")
            (fragments / "token.conf").write_text("Host home-token\n", encoding="utf-8")
            config.write_text(
                "Include $HOME/.ssh/fragments/home.conf\n"
                "Host *\n"
                "Include %d/.ssh/fragments/token.conf\n",
                encoding="utf-8",
            )

            with patch("vram_radar.server_catalog.Path.home", return_value=home), patch.dict(
                os.environ,
                {"HOME": "", "USERPROFILE": ""},
                clear=False,
            ):
                servers, _warnings = import_openssh_config(config)

        self.assertEqual([server.ssh_alias for server in servers], ["home-env", "home-token"])

    def test_conditional_include_changes_the_connection_dependency_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragment = root / "conditional.conf"
            fragment.write_text("IdentityFile ~/.ssh/id_a\n", encoding="utf-8")
            config = root / "config"
            config.write_text(
                f"Host gpu\n  HostName gpu.example\n  Include {fragment.as_posix()}\n",
                encoding="utf-8",
            )
            before = openssh_config_dependency_fingerprint(config)

            fragment.write_text("IdentityFile ~/.ssh/id_b\n", encoding="utf-8")
            after = openssh_config_dependency_fingerprint(config)

            self.assertNotEqual(before, after)

    def test_openssh_fingerprint_canonicalization_failure_is_nonfatal(self):
        with patch(
            "vram_radar.server_catalog._resolved",
            side_effect=OSError(448, "untrusted mount point"),
        ):
            self.assertEqual(
                openssh_config_dependency_fingerprint("~/.ssh/config"),
                "unreadable",
            )

    def test_tilde_openssh_path_is_dispatched_without_toml_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".ssh" / "config"
            path.parent.mkdir()
            path.write_text("Host mac-a100\n  HostName cluster.example\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"HOME": str(home), "USERPROFILE": str(home)},
                clear=False,
            ):
                servers, _warnings = import_server_config("~/.ssh/config")

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].ssh_alias, "mac-a100")

    def test_openssh_import_resolves_includes_and_stops_cycles(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".ssh" / "config"
            fragment = home / ".ssh" / "conf.d" / "gpu.conf"
            fragment.parent.mkdir(parents=True)
            path.write_text("Include conf.d/*\nHost *\n  User tester\n", encoding="utf-8")
            fragment.write_text("Include config\nHost included-gpu\n  HostName example\n", encoding="utf-8")
            with patch("vram_radar.server_catalog.Path.home", return_value=home):
                servers, warnings = import_openssh_config(path)

        self.assertEqual([server.ssh_alias for server in servers], ["included-gpu"])
        self.assertTrue(any("Include" in warning for warning in warnings))

    def test_nested_relative_include_is_always_rooted_at_user_ssh_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".ssh" / "config"
            first = home / ".ssh" / "conf.d" / "one.conf"
            expected = home / ".ssh" / "two.conf"
            wrong_parent_relative = first.parent / "two.conf"
            first.parent.mkdir(parents=True)
            path.write_text("Include conf.d/one.conf\n", encoding="utf-8")
            first.write_text("Include two.conf\n", encoding="utf-8")
            expected.write_text("Host openssh-expected\n", encoding="utf-8")
            wrong_parent_relative.write_text("Host wrong-parent-relative\n", encoding="utf-8")

            with patch("vram_radar.server_catalog.Path.home", return_value=home):
                servers, _warnings = import_openssh_config(path)

        self.assertEqual([server.ssh_alias for server in servers], ["openssh-expected"])

    def test_system_openssh_relative_include_is_rooted_at_system_config_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            program_data = Path(temporary) / "ProgramData"
            system_root = program_data / "ssh"
            config = system_root / "ssh_config"
            fragment = system_root / "ssh_config.d" / "gpu.conf"
            fragment.parent.mkdir(parents=True)
            config.write_text("Include ssh_config.d/*.conf\n", encoding="utf-8")
            fragment.write_text("Host system-managed-gpu\n", encoding="utf-8")

            with patch(
                "vram_radar.server_catalog._system_ssh_config_candidates",
                return_value=[config.resolve(), fragment.resolve()],
            ):
                servers, warnings = import_openssh_config(config)

        self.assertEqual([server.ssh_alias for server in servers], ["system-managed-gpu"])
        self.assertTrue(any("Include" in warning for warning in warnings))

    def test_openssh_include_preserves_windows_backslashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config"
            fragment = root / "config.d" / "gpu.conf"
            fragment.parent.mkdir()
            fragment.write_text("Host windows-gpu\n", encoding="utf-8")
            path.write_text(f'Include "{fragment}"\n', encoding="utf-8")

            servers, _warnings = import_openssh_config(path)

        self.assertEqual([server.ssh_alias for server in servers], ["windows-gpu"])

    def test_cross_platform_discovery_merges_standard_editor_and_fragment_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            appdata = root / "AppData" / "Roaming"
            user_config = home / ".ssh" / "config"
            fragment = home / ".ssh" / "config.d" / "lab.conf"
            xdg_config = root / "xdg" / "ssh" / "config"
            editor_config = root / "editor-ssh.conf"
            settings = appdata / "Code" / "User" / "settings.json"
            for path in (user_config, fragment, xdg_config, editor_config, settings):
                path.parent.mkdir(parents=True, exist_ok=True)
            for path in (user_config, fragment, xdg_config, editor_config):
                path.write_text("Host test-host\n", encoding="utf-8")
            settings.write_text(
                '{"remote.SSH.configFile": "' + str(editor_config).replace("\\", "\\\\") + '"}',
                encoding="utf-8",
            )
            environment = {
                "VRAM_RADAR_SERVERS_CONFIG": "",
                "USERPROFILE": str(home),
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(root / "xdg"),
                "APPDATA": str(appdata),
                "LOCALAPPDATA": "",
            }
            with patch("vram_radar.server_catalog._candidate_roots", return_value=[]), patch(
                "vram_radar.server_catalog.Path.home", return_value=home
            ), patch("vram_radar.server_catalog._system_ssh_config_candidates", return_value=[]), patch.dict(
                os.environ, environment, clear=False
            ):
                candidates = server_config_candidates(include_openssh=True)
                existing = resolve_server_configs(include_openssh=True)

        for expected in (user_config, fragment, xdg_config, editor_config):
            self.assertIn(expected.resolve(), candidates)
            self.assertIn(expected.resolve(), existing)
        self.assertTrue(all(path == path.resolve() for path in candidates))
        self.assertFalse(any(path.name == "known_hosts" for path in candidates))

    def test_editor_jsonc_comments_do_not_supply_remote_ssh_config_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            appdata = root / "AppData" / "Roaming"
            ignored_line = root / "ignored-line-comment.conf"
            ignored_block = root / "ignored-block-comment.conf"
            selected = root / "selected.conf"
            settings = appdata / "Code" / "User" / "settings.json"
            settings.parent.mkdir(parents=True)
            for path in (ignored_line, ignored_block, selected):
                path.write_text("Host test-host\n", encoding="utf-8")
            settings.write_text(
                "{\n"
                f"  // \"remote.SSH.configFile\": {json.dumps(str(ignored_line))},\n"
                f"  /* \"remote.SSH.configFile\": {json.dumps(str(ignored_block))} */,\n"
                f"  \"remote.SSH.configFile\": {json.dumps(str(selected))}\n"
                "}\n",
                encoding="utf-8",
            )
            environment = {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": "",
                "HOME": str(home),
                "USERPROFILE": str(home),
            }
            with patch("vram_radar.server_catalog._candidate_roots", return_value=[]), patch(
                "vram_radar.server_catalog.Path.home", return_value=home
            ), patch("vram_radar.server_catalog._system_ssh_config_candidates", return_value=[]), patch.dict(
                os.environ, environment, clear=False
            ):
                candidates = server_config_candidates(include_openssh=True)

        self.assertIn(selected.resolve(), candidates)
        self.assertNotIn(ignored_line.resolve(), candidates)
        self.assertNotIn(ignored_block.resolve(), candidates)

    def test_installed_openssh_adds_adjacent_config_paths_without_executing(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "bin" / "ssh"
            config = binary.parent / "ssh_config"
            binary.parent.mkdir()
            binary.write_text("", encoding="utf-8")
            config.write_text("Host managed-gpu\n", encoding="utf-8")
            with patch("vram_radar.server_catalog.shutil.which", return_value=str(binary)), patch.dict(
                os.environ, {"PROGRAMDATA": "", "ProgramData": "", "ProgramFiles": ""}, clear=False
            ):
                candidates = _system_ssh_config_candidates()

        self.assertIn(config.resolve(), candidates)

    def test_editor_insiders_environment_path_is_discovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            appdata = root / "AppData" / "Roaming"
            config = root / "custom" / "ssh-config"
            settings = appdata / "Code - Insiders" / "User" / "settings.json"
            config.parent.mkdir(parents=True)
            settings.parent.mkdir(parents=True)
            config.write_text("Host insiders-gpu\n", encoding="utf-8")
            settings.write_text(
                json.dumps({"remote.SSH.configFile": "${env:VRAM_RADAR_TEST_SSH_CONFIG}"}),
                encoding="utf-8",
            )
            environment = {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": "",
                "HOME": str(home),
                "USERPROFILE": str(home),
                "VRAM_RADAR_TEST_SSH_CONFIG": str(config),
            }
            with patch("vram_radar.server_catalog._candidate_roots", return_value=[]), patch(
                "vram_radar.server_catalog.Path.home", return_value=home
            ), patch("vram_radar.server_catalog._system_ssh_config_candidates", return_value=[]), patch.dict(
                os.environ, environment, clear=False
            ):
                candidates = server_config_candidates(include_openssh=True)

        self.assertIn(config.resolve(), candidates)

    def test_multiple_sources_merge_and_deduplicate_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "servers.toml"
            ssh_config = root / "ssh-config"
            later_config = root / "later-ssh-config"
            catalog.write_text(CATALOG, encoding="utf-8")
            ssh_config.write_text(
                "Host direct-gpu-test mac-only\n  HostName example\n",
                encoding="utf-8",
            )
            later_config.write_text(
                "Host direct-gpu-test\n  HostName lower-priority.example\n",
                encoding="utf-8",
            )
            synchronized, warnings = profile_from_server_configs(
                Profile.empty("local"), [catalog, ssh_config, later_config]
            )

        self.assertEqual(
            [server.ssh_alias for server in synchronized.servers],
            ["direct-gpu-test", "slurm-gpu-test", "mac-only"],
        )
        self.assertEqual(synchronized.servers[0].backend, "direct_ssh")
        self.assertEqual(synchronized.servers[0].ssh_config_file, str(ssh_config.resolve()))
        self.assertFalse(synchronized.auto_sync_servers)
        self.assertEqual(synchronized.server_config_path, "")
        self.assertTrue(any("已关联 OpenSSH 配置来源" in warning for warning in warnings))

    def test_multiple_sources_keep_distinct_aliases_when_cleaned_ids_collide(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first-ssh.conf"
            second = root / "second-ssh.conf"
            first.write_text("Host gpu@lab\n", encoding="utf-8")
            second.write_text("Host gpu:lab\n", encoding="utf-8")

            synchronized, warnings = profile_from_server_configs(Profile.empty("local"), [first, second])

        self.assertEqual(
            [server.ssh_alias for server in synchronized.servers],
            ["gpu@lab", "gpu:lab"],
        )
        self.assertEqual(
            [server.id for server in synchronized.servers],
            ["gpu-lab", "gpu-lab-2"],
        )
        self.assertEqual(
            len({server.id.casefold() for server in synchronized.servers}),
            len(synchronized.servers),
        )
        self.assertFalse(any("重复" in warning for warning in warnings))

    def test_ignored_ssh_alias_stays_deleted_while_new_hosts_are_imported(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host retired-gpu\n  HostName retired.example\n"
                "Host new-gpu\n  HostName new.example\n",
                encoding="utf-8",
            )
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "ignored_ssh_aliases": ["RETIRED-GPU"],
                    "servers": [],
                }
            )

            synchronized, warnings = profile_from_server_configs(profile, [config])

        self.assertEqual([server.ssh_alias for server in synchronized.servers], ["new-gpu"])
        self.assertEqual(synchronized.ignored_ssh_aliases, ("RETIRED-GPU",))
        self.assertTrue(any("主动移除" in warning for warning in warnings))

    def test_valid_config_with_only_ignored_hosts_is_a_successful_empty_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text("Host retired-gpu\n  HostName retired.example\n", encoding="utf-8")
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "ignored_ssh_aliases": ["retired-gpu"],
                    "servers": [],
                }
            )

            synchronized, warnings = profile_from_server_configs(profile, [config])

        self.assertEqual(synchronized.servers, ())
        self.assertTrue(synchronized.auto_sync_servers)
        self.assertEqual(synchronized.server_config_path, str(config.resolve()))
        self.assertTrue(any("主动移除" in warning for warning in warnings))

    def test_active_alias_defensively_wins_over_a_stale_tombstone(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text("Host restored-gpu\n  HostName restored.example\n", encoding="utf-8")
            profile = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "Local",
                    "ignored_ssh_aliases": ["restored-gpu"],
                    "servers": [
                        {
                            "id": "restored",
                            "display_name": "Restored",
                            "backend": "direct_ssh",
                            "ssh_alias": "RESTORED-GPU",
                        }
                    ],
                }
            )

            synchronized, _warnings = profile_from_server_configs(profile, [config])

        self.assertEqual([server.id for server in synchronized.servers], ["restored"])

    def test_catalog_rejects_non_boolean_enabled_without_coercing_it(self):
        document = """\
version = 2

[servers."valid"]
display_name = "Valid"
ssh_alias = "valid"
backend = "ssh"
enabled = true

[servers."invalid"]
display_name = "Invalid"
ssh_alias = "invalid"
backend = "ssh"
enabled = "false"
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.toml"
            path.write_text(document, encoding="utf-8")

            servers, warnings = import_server_catalog(path)

        self.assertEqual([server.id for server in servers], ["valid"])
        self.assertTrue(any("invalid" in warning and "enabled" in warning for warning in warnings))

    def test_catalog_resync_preserves_local_key_and_config_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "servers.toml"
            ssh_config = root / "editor-ssh.conf"
            identity = root / "id_ed25519"
            catalog.write_text(CATALOG, encoding="utf-8")
            ssh_config.write_text("Host direct-gpu-test\n", encoding="utf-8")
            identity.write_text("private-key-placeholder", encoding="utf-8")
            reviewed = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "servers": [
                        {
                            "id": "direct-gpu",
                            "display_name": "Reviewed direct GPU",
                            "backend": "direct_ssh",
                            "ssh_alias": "direct-gpu-test",
                            "username": "reviewed-user",
                            "identity_file": str(identity),
                            "ssh_config_file": str(ssh_config),
                            "connect_timeout_seconds": 37,
                        }
                    ],
                }
            )

            synchronized, _warnings = profile_from_server_config(reviewed, catalog)

        server = synchronized.servers[0]
        self.assertEqual(server.username, "reviewed-user")
        self.assertEqual(server.identity_file, str(identity))
        self.assertEqual(server.ssh_config_file, str(ssh_config))
        self.assertEqual(server.connect_timeout_seconds, 37)

    def test_openssh_resync_preserves_reviewed_slurm_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".ssh" / "config"
            path.parent.mkdir()
            path.write_text("Host mac-a100\n  HostName cluster.example\n", encoding="utf-8")
            reviewed = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "servers": [
                        {
                            "id": "mac-a100",
                            "display_name": "A100 scheduler",
                            "backend": "slurm_ssh",
                            "ssh_alias": "old-alias",
                        }
                    ],
                }
            )

            synchronized, _warnings = profile_from_server_config(reviewed, path)

        self.assertEqual(synchronized.servers[0].backend, "slurm_ssh")
        self.assertEqual(synchronized.servers[0].display_name, "A100 scheduler")
        self.assertEqual(synchronized.servers[0].ssh_alias, "mac-a100")
        self.assertEqual(synchronized.servers[0].ssh_config_file, str(path.resolve()))

    def test_resync_matches_existing_server_ids_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".ssh" / "config"
            path.parent.mkdir()
            path.write_text("Host gpu-a\n  HostName gpu-a.example\n", encoding="utf-8")
            reviewed = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "local",
                    "display_name": "My GPUs",
                    "servers": [
                        {
                            "id": "GPU-A",
                            "display_name": "Reviewed GPU",
                            "backend": "direct_ssh",
                            "host": "legacy.example",
                        }
                    ],
                }
            )

            synchronized, warnings = profile_from_server_config(reviewed, path)

        self.assertEqual([server.id for server in synchronized.servers], ["GPU-A"])
        self.assertEqual(synchronized.servers[0].ssh_alias, "gpu-a")
        self.assertTrue(any("大小写不敏感 ID" in warning for warning in warnings))
        Profile.from_dict(synchronized.to_dict(), expected_id="local")


if __name__ == "__main__":
    unittest.main()
