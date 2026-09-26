from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from vram_radar.models import Profile
from vram_radar.server_catalog import import_openssh_config
from vram_radar.service import DashboardService
from vram_radar.shell import AppApi
from vram_radar.storage import ProfileStore, SnapshotCache, storage_paths


def gpu_payload(server_id):
    return {
        "server_id": server_id, "view_kind": "live-memory", "total_gpus": 1,
        "total_vram_gib": 24, "used_vram_gib": 12, "free_vram_gib": 12,
        "gpus": [{"gpu_index": "0", "gpu_uuid": "GPU-fixture",
                  "memory_total_gib": 24, "memory_free_gib": 12}],
    }


def duplicate_profile(*, credential=False):
    return Profile.from_dict({
        "schema_version": 1, "id": "lab", "display_name": "Lab", "servers": [
            {"id": "primary", "display_name": "Primary", "backend": "direct_ssh",
             "ssh_alias": "primary"},
            {"id": "secondary", "display_name": "Secondary", "backend": "direct_ssh",
             "ssh_alias": "secondary", "auto_imported": True,
             "auth_ref": "fixture-reference" if credential else ""},
        ],
    })


class AliasSafetyTests(unittest.TestCase):
    def test_distinct_authentication_routes_and_case_sensitive_users_are_preserved(self):
        for left, right in (
            ("IdentityFile key-a", "IdentityFile key-b"),
            ("CertificateFile cert-a", "CertificateFile cert-b"),
            ("IdentitiesOnly yes", "IdentitiesOnly no"),
            ("IdentityAgent agent-a", "IdentityAgent agent-b"),
            ("User Alice", "User alice"),
            ("IdentityFile key-%n", "IdentityFile key-%n"),
        ):
            with self.subTest(left=left, right=right), tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / "config"
                config.write_text(
                    f"Host route-a\n  {left}\n  HostName gpu.example\n  Port 22\n  User alice\n"
                    f"Host route-b\n  {right}\n  HostName gpu.example\n  Port 22\n  User alice\n",
                    encoding="utf-8",
                )
                servers, _ = import_openssh_config(config)
                self.assertEqual([server.ssh_alias for server in servers], ["route-a", "route-b"])

    def test_multi_source_import_choices_preserve_the_selected_config_on_disk(self):
        for selection in ("route-b", "keep_all"):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                configs = []
                for alias in ("route-a", "route-b"):
                    config = root / alias
                    config.write_text(
                        f"Host {alias}\n  HostName gpu.example\n  User alice\n  Port 22\n",
                        encoding="utf-8",
                    )
                    configs.append(str(config.resolve()))
                paths = storage_paths(root / "home")
                profile = Profile.empty("lab")
                store = ProfileStore(paths)
                service = DashboardService(profile, SnapshotCache(paths, "lab"))
                api = AppApi(profile, store, paths, service)
                imported = api.import_server_config(configs)
                self.assertTrue(imported["ok"], imported)
                self.assertFalse(imported["auto_sync"])
                candidate = api.get_profile()
                candidate.update(servers=imported["servers"],
                                 pending_alias_choices=imported["pending_alias_choices"])
                saved = api.save_profile(candidate)
                self.assertTrue(saved["ok"], saved)
                choice = imported["pending_alias_choices"][0]
                applied = api.apply_alias_choice(choice["id"], selection)
                self.assertTrue(applied["ok"], applied)
                loaded = store.load("lab")
                routes = {server.ssh_alias: server.ssh_config_file for server in loaded.servers}
                self.assertEqual(routes["route-b"], configs[1])
                if selection == "keep_all":
                    self.assertEqual(routes["route-a"], configs[0])
                else:
                    self.assertNotIn("route-a", routes)
                self.assertEqual(loaded.pending_alias_choices, ())

    def test_runtime_dedupe_preserves_saved_password_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = duplicate_profile(credential=True)
            paths = storage_paths(Path(temporary))
            service = DashboardService(profile, SnapshotCache(paths, "lab"))
            for server in profile.servers:
                service._record_success(server.id, gpu_payload(server.id))
            service._dedupe_auto_imported_by_gpu_uuids("secondary")
            self.assertEqual(service.profile, profile)
            self.assertEqual(service.profile.servers[1].auth_ref, "fixture-reference")

    def test_runtime_dedupe_preserves_distinct_private_key_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config"
            config.write_text(
                "Host primary\n  IdentityFile key-a\n  HostName gpu.example\n  User alice\n  Port 22\n"
                "Host secondary\n  IdentityFile key-b\n  HostName gpu.example\n  User alice\n  Port 22\n",
                encoding="utf-8",
            )
            raw = duplicate_profile().to_dict()
            for server in raw["servers"]:
                server["ssh_config_file"] = str(config)
            profile = Profile.from_dict(raw)
            service = DashboardService(profile, SnapshotCache(storage_paths(Path(temporary)), "lab"))
            for server in profile.servers:
                service._record_success(server.id, gpu_payload(server.id))
            service._dedupe_auto_imported_by_gpu_uuids("secondary")
            self.assertEqual(service.profile, profile)

    def test_password_save_marks_an_imported_route_as_user_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            profile = duplicate_profile()
            service = DashboardService(profile, SnapshotCache(paths, "lab"))
            secret_store = Mock()
            secret_store.get.return_value = None
            api = AppApi(profile, ProfileStore(paths), paths, service, secret_store=secret_store)
            saved = api.save_profile(api.get_profile(), {"secondary": "generated-fixture-secret"})
            self.assertTrue(saved["ok"], saved)
            self.assertFalse(api.profile.servers[1].auto_imported)
            self.assertTrue(api.profile.servers[1].auth_ref)

    def test_refresh_does_not_wait_on_editor_or_overwrite_its_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            profile = duplicate_profile()
            store = ProfileStore(paths)
            service = DashboardService(profile, SnapshotCache(paths, "lab"),
                                       query=lambda server: gpu_payload(server.id))
            api = AppApi(profile, store, paths, service)
            finished = threading.Event()
            failures = []

            def refresh():
                try:
                    service.refresh(force=True)
                except Exception as exc:
                    failures.append(exc)
                finally:
                    finished.set()

            worker = threading.Thread(target=refresh, daemon=True)
            with api._profile_mutation_lock:
                worker.start()
                completed_during_edit = finished.wait(2)
                if completed_during_edit:
                    candidate = api.get_profile()
                    candidate["display_name"] = "Saved while refreshing"
                    self.assertTrue(api.save_profile(candidate)["ok"])
            worker.join(2)
            self.assertTrue(completed_during_edit, "refresh deadlocked on editor mutation")
            self.assertFalse(failures)
            service.refresh(force=True)
            self.assertEqual(store.load("lab"), api.profile)
            self.assertEqual(service.profile, api.profile)
            self.assertEqual(api.profile.display_name, "Saved while refreshing")
            self.assertEqual([server.id for server in api.profile.servers], ["primary"])

    def test_failed_or_cached_route_does_not_remove_a_live_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = duplicate_profile()
            service = DashboardService(profile, SnapshotCache(storage_paths(Path(temporary)), "lab"))
            for server in profile.servers:
                service._record_success(server.id, gpu_payload(server.id))
            for state, origin in (("offline", "live"), ("connecting", "cache"), ("disabled", "live")):
                with self.subTest(state=state):
                    service.states["primary"].state = state
                    service.states["primary"].data_origin = origin
                    service._dedupe_auto_imported_by_gpu_uuids("secondary")
                    self.assertEqual(len(service.profile.servers), 2)


if __name__ == "__main__":
    unittest.main()
