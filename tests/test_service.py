from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from vram_radar.connectors import ConnectorFailure
from vram_radar.models import Profile
from vram_radar.service import (
    DashboardService,
    connection_fingerprint,
    favorite_resource_matches,
    recommend_resources,
)
from vram_radar.storage import SnapshotCache, storage_paths


def profile() -> Profile:
    return Profile.from_dict(
        {
            "schema_version": 1,
            "id": "lab",
            "display_name": "Lab",
            "refresh_seconds": 15,
            "servers": [
                {"id": "online", "display_name": "Online", "backend": "direct_ssh", "host": "online.test"},
                {"id": "offline", "display_name": "Offline", "backend": "direct_ssh", "host": "offline.test"},
            ],
        }
    )


def payload(server_id: str, free: float = 20) -> dict:
    return {
        "server_id": server_id,
        "display_name": server_id.title(),
        "backend": "direct_ssh",
        "view_kind": "live-memory",
        "host": f"{server_id}.test",
        "total_gpus": 1,
        "free_vram_gib": free,
        "total_vram_gib": 24,
        "gpus": [{"memory_total_gib": 24, "memory_free_gib": free}],
    }


def one_server_profile(*, backend: str = "direct_ssh") -> Profile:
    return Profile.from_dict(
        {
            "schema_version": 1,
            "id": "cluster",
            "display_name": "Cluster",
            "refresh_seconds": 15,
            "servers": [
                {"id": "cluster", "display_name": "Cluster", "backend": backend, "host": "cluster.test"}
            ],
        }
    )


def scheduler_payload(node_count: int = 1000) -> dict:
    nodes = []
    for index in range(node_count):
        issue = index % 50 == 0
        free_gpus = 0 if issue else 4
        nodes.append(
            {
                "node": f"gpu-{index:04d}",
                "partition": "GPU-LARGE" if index % 2 == 0 else "GPU-SMALL",
                "state": "down" if issue else "idle",
                "gpu_type": "H100-80G" if index % 3 == 0 else "A100-40G",
                "memory_per_gpu_gib": 80 if index % 3 == 0 else 40,
                "total_gpus": 8,
                "allocated_gpus": 8 - free_gpus,
                "free_gpus": free_gpus,
                "total_vram_gib": 640 if index % 3 == 0 else 320,
                "free_vram_gib": free_gpus * (80 if index % 3 == 0 else 40),
                "tasks": ([{"job_id": f"job-{index}", "state": "running"}] if index % 25 == 0 else []),
            }
        )
    return {
        "server_id": "cluster",
        "display_name": "Cluster",
        "backend": "slurm_ssh",
        "view_kind": "scheduler",
        "total_gpus": node_count * 8,
        "free_gpus": sum(node["free_gpus"] for node in nodes),
        "total_vram_gib": sum(node["total_vram_gib"] for node in nodes),
        "free_vram_gib": sum(node["free_vram_gib"] for node in nodes),
        "nodes": nodes,
        "tasks": {"current_user": "alice", "active": [], "recent": []},
    }


class ServiceTests(unittest.TestCase):
    def test_favorite_resource_matches_live_direct_idle_or_memory_threshold(self):
        snapshot = {
            "servers": [
                {
                    "server_id": "favorite",
                    "display_name": "Favorite GPU",
                    "backend": "direct_ssh",
                    "view_kind": "live-memory",
                    "connection": {"state": "online"},
                    "processes": {
                        "supported": True,
                        "active": [{"allocations": [{"gpu_index": 1}]}],
                    },
                    "gpus": [
                        {
                            "gpu_index": 0,
                            "memory_total_gib": 24,
                            "memory_free_gib": 24,
                            "utilization_percent": 0,
                        },
                        {
                            "gpu_index": 1,
                            "memory_total_gib": 24,
                            "memory_free_gib": 12,
                            "utilization_percent": 90,
                        },
                    ],
                },
                {
                    "server_id": "not-favorite",
                    "view_kind": "live-memory",
                    "connection": {"state": "online"},
                    "processes": {"supported": True, "active": []},
                    "gpus": [{"gpu_index": "0", "memory_total_gib": 80, "memory_free_gib": 80}],
                },
                {
                    "server_id": "stale-favorite",
                    "view_kind": "live-memory",
                    "connection": {"state": "stale"},
                    "processes": {"supported": True, "active": []},
                    "gpus": [{"gpu_index": "0", "memory_total_gib": 80, "memory_free_gib": 80}],
                },
            ]
        }

        matches = favorite_resource_matches(
            snapshot,
            {"favorite", "stale-favorite"},
            min_memory_gib=10,
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["server_id"], "favorite")
        self.assertEqual(matches[0]["idle_units"], 1)
        self.assertEqual(matches[0]["memory_units"], 2)
        self.assertEqual(matches[0]["available_memory_gib"], 24)

    def test_favorite_resource_requires_stable_process_sample_for_direct_idle(self):
        snapshot = {
            "servers": [{
                "server_id": "favorite",
                "view_kind": "live-memory",
                "connection": {"state": "online"},
                "processes": {"supported": False, "active": []},
                "gpus": [{
                    "gpu_index": "0",
                    "memory_total_gib": 24,
                    "memory_free_gib": 24,
                    "utilization_percent": 0,
                }],
            }]
        }

        self.assertEqual(favorite_resource_matches(snapshot, {"favorite"}), [])
        self.assertEqual(
            favorite_resource_matches(snapshot, {"favorite"}, min_memory_gib=20)[0]["memory_units"],
            1,
        )

    def test_favorite_resource_matches_large_scheduler_groups_and_skips_issues(self):
        snapshot = {
            "servers": [{
                "server_id": "cluster",
                "display_name": "Large Cluster",
                "backend": "slurm",
                "view_kind": "scheduler",
                "connection": {"state": "online"},
                "node_groups": [
                    {
                        "state": "idle",
                        "free_gpus": 8,
                        "free_vram_gib": 640,
                    },
                    {
                        "state": "down",
                        "free_gpus": 4,
                        "memory_per_gpu_gib": 80,
                    },
                ],
            }]
        }

        matches = favorite_resource_matches(snapshot, {"cluster"}, min_memory_gib=70)

        self.assertEqual(matches[0]["idle_units"], 8)
        self.assertEqual(matches[0]["memory_units"], 8)
        self.assertEqual(matches[0]["available_memory_gib"], 80)

    def test_saved_password_is_loaded_only_for_its_server_query(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "auth_ref": "server:lab:secured:login-password",
                    }
                ],
            }
        )
        calls = []

        class Secrets:
            def get(self, ref):
                self.ref = ref
                return "stored-password"

        def query(server, *, password=None, identities_only=False):
            calls.append((server.id, password, identities_only))
            if password is None:
                raise ConnectorFailure("auth_failed", "key rejected", retryable=False, state="auth_required")
            return payload(server.id)

        with tempfile.TemporaryDirectory() as temporary:
            secrets = Secrets()
            service = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
                secret_store=secrets,
            )
            snapshot = service.refresh(force=True)

        self.assertEqual(calls, [("secured", None, False), ("secured", "stored-password", False)])
        self.assertEqual(secrets.ref, "server:lab:secured:login-password")
        self.assertEqual(snapshot["servers"][0]["connection"]["state"], "online")

    def test_configured_identity_is_tried_before_saved_password_fallback(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "identity_file": "/local/private-key",
                        "prefer_identity_auth": True,
                        "auth_ref": "server:lab:secured:login-password",
                    }
                ],
            }
        )
        calls = []

        class Secrets:
            def get(self, _ref):
                return "stored-password"

        def query(server, *, password=None, identities_only=False):
            calls.append((server.id, password, identities_only))
            if password is None:
                raise ConnectorFailure("auth_failed", "key rejected", retryable=False, state="auth_required")
            return payload(server.id)

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
                secret_store=Secrets(),
            ).refresh(force=True)

        self.assertEqual(calls, [("secured", None, True), ("secured", "stored-password", False)])
        self.assertEqual(snapshot["servers"][0]["connection"]["state"], "online")

    def test_saved_password_falls_back_when_private_key_needs_passphrase_or_agent_refuses(self):
        for failure_code in ("identity_passphrase_required", "ssh_agent_refused"):
            with self.subTest(failure_code=failure_code), tempfile.TemporaryDirectory() as temporary:
                secured = Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "lab",
                        "display_name": "Lab",
                        "servers": [
                            {
                                "id": "secured",
                                "display_name": "Secured",
                                "backend": "direct_ssh",
                                "host": "secured.test",
                                "identity_file": "/local/private-key",
                                "prefer_identity_auth": True,
                                "auth_ref": "server:lab:secured:login-password",
                            }
                        ],
                    }
                )
                calls = []

                class Secrets:
                    def get(self, _ref):
                        return "stored-password"

                def query(server, *, password=None, identities_only=False):
                    calls.append((server.id, password, identities_only))
                    if password is None:
                        raise ConnectorFailure(
                            failure_code,
                            "key path could not authenticate",
                            retryable=False,
                            state="auth_required",
                        )
                    return payload(server.id)

                snapshot = DashboardService(
                    secured,
                    SnapshotCache(storage_paths(Path(temporary)), "lab"),
                    query=query,
                    secret_store=Secrets(),
                ).refresh(force=True)

                self.assertEqual(
                    calls,
                    [("secured", None, True), ("secured", "stored-password", False)],
                )
                self.assertEqual(snapshot["servers"][0]["connection"]["state"], "online")

    def test_key_specific_auth_error_is_preserved_without_saved_password(self):
        for failure_code in ("identity_passphrase_required", "ssh_agent_refused"):
            with self.subTest(failure_code=failure_code), tempfile.TemporaryDirectory() as temporary:
                secured = Profile.from_dict(
                    {
                        "schema_version": 1,
                        "id": "lab",
                        "display_name": "Lab",
                        "servers": [
                            {
                                "id": "secured",
                                "display_name": "Secured",
                                "backend": "direct_ssh",
                                "host": "secured.test",
                                "identity_file": "/local/private-key",
                                "prefer_identity_auth": True,
                            }
                        ],
                    }
                )
                query = Mock(
                    side_effect=ConnectorFailure(
                        failure_code,
                        "key path could not authenticate",
                        retryable=False,
                        state="auth_required",
                    )
                )

                snapshot = DashboardService(
                    secured,
                    SnapshotCache(storage_paths(Path(temporary)), "lab"),
                    query=query,
                ).refresh(force=True)

                query.assert_called_once_with(secured.servers[0], identities_only=True)
                connection = snapshot["servers"][0]["connection"]
                self.assertEqual(connection["state"], "auth_required")
                self.assertEqual(connection["error"]["code"], failure_code)

    def test_configured_identity_success_does_not_read_saved_password(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "identity_file": "/local/private-key",
                        "prefer_identity_auth": True,
                        "auth_ref": "must-not-be-read",
                    }
                ],
            }
        )

        class Secrets:
            def get(self, _ref):
                raise AssertionError("password fallback should not be read")

        with tempfile.TemporaryDirectory() as temporary:
            query = Mock(side_effect=lambda server, **_kwargs: payload(server.id))
            snapshot = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
                secret_store=Secrets(),
            ).refresh(force=True)

        self.assertEqual(snapshot["servers"][0]["connection"]["state"], "online")
        query.assert_called_once_with(secured.servers[0], identities_only=True)

    def test_stale_password_reference_does_not_block_working_agent_authentication(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "auth_ref": "stale-password-reference",
                    }
                ],
            }
        )

        class Secrets:
            def get(self, _ref):
                raise AssertionError("working ssh-agent authentication must not read a stale password")

        query = Mock(side_effect=lambda server, **_kwargs: payload(server.id))
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
                secret_store=Secrets(),
            ).refresh(force=True)

        query.assert_called_once_with(secured.servers[0])
        self.assertEqual(snapshot["servers"][0]["connection"]["state"], "online")

    def test_missing_os_password_is_reported_only_after_key_and_agent_authentication_fails(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "auth_ref": "missing",
                    }
                ],
            }
        )

        class Secrets:
            def get(self, _ref):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            query = Mock(
                side_effect=ConnectorFailure(
                    "auth_failed", "key and agent rejected", retryable=False, state="auth_required"
                )
            )
            service = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
                secret_store=Secrets(),
            )
            snapshot = service.refresh(force=True)

        query.assert_called_once_with(secured.servers[0])
        connection = snapshot["servers"][0]["connection"]
        self.assertEqual(connection["state"], "auth_required")
        self.assertEqual(connection["error"]["code"], "password_unavailable")

    def test_directory_inspection_is_on_demand_and_reuses_scoped_password(self):
        secured = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [
                    {
                        "id": "secured",
                        "display_name": "Secured",
                        "backend": "direct_ssh",
                        "host": "secured.test",
                        "auth_ref": "server:lab:secured:login-password",
                    }
                ],
            }
        )
        calls = []

        class Secrets:
            def get(self, _ref):
                return "stored-password"

        def directory_query(
            server, *, password=None, identities_only=False, root_path=None, root_source="auto"
        ):
            calls.append((server.id, password, identities_only, root_path, root_source))
            if password is None:
                raise ConnectorFailure("auth_failed", "key rejected", retryable=False, state="auth_required")
            return {
                "username": "alice",
                "home_directory": "/srv/vram-radar-account",
                "directory_tree": {"supported": True, "entries": []},
            }

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                secured,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=lambda _server, **_kwargs: payload("secured"),
                directory_query=directory_query,
                secret_store=Secrets(),
            )
            self.assertEqual(calls, [])
            result = service.inspect_account_directory("secured")

        self.assertTrue(result["ok"])
        self.assertEqual(result["account"]["home_directory"], "/srv/vram-radar-account")
        self.assertEqual(
            calls,
            [
                ("secured", None, False, None, "auto"),
                ("secured", "stored-password", False, None, "auto"),
            ],
        )

    def test_directory_inspection_uses_persisted_work_directory_by_default(self):
        pinned = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "lab",
                "display_name": "Lab",
                "servers": [{
                    "id": "gpu",
                    "display_name": "GPU",
                    "backend": "direct_ssh",
                    "host": "gpu.test",
                    "default_work_directory": "/srv/vram-radar-account/projects/radar",
                }],
            }
        )
        directory_query = Mock(return_value={"home_directory": "/srv/vram-radar-account", "directory_tree": {}})
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                pinned,
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                directory_query=directory_query,
            )

            result = service.inspect_account_directory("gpu")

        self.assertTrue(result["ok"])
        directory_query.assert_called_once_with(
            pinned.servers[0],
            password=None,
            root_path="/srv/vram-radar-account/projects/radar",
            root_source="pinned",
        )

    def test_directory_cache_reports_miss_hit_and_forced_refresh_without_shared_mutation(self):
        now = [10.0]
        calls: list[str | None] = []

        def directory_query(_server, *, root_path=None, **_kwargs):
            calls.append(root_path)
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root_path or "/srv/alice/code",
                    "entries": [{"name": f"query-{len(calls)}"}],
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
                clock=lambda: now[0],
            )
            first = service.inspect_account_directory("cluster", "/srv/alice/code")
            first["account"]["directory_tree"]["entries"][0]["name"] = "caller-mutation"
            now[0] = 13.25
            second = service.inspect_account_directory("cluster", "/srv/alice/code")
            third = service.inspect_account_directory("cluster", "/srv/alice/code", force=True)

        self.assertEqual(first["cache"]["state"], "miss")
        self.assertEqual(first["cache"]["age_seconds"], 0.0)
        self.assertEqual(first["cache"]["revalidate_after_seconds"], 15.0)
        self.assertEqual(second["cache"]["state"], "hit")
        self.assertEqual(second["cache"]["age_seconds"], 3.25)
        self.assertEqual(second["account"]["directory_tree"]["entries"][0]["name"], "query-1")
        self.assertEqual(third["cache"]["state"], "refreshed")
        self.assertEqual(third["cache"]["age_seconds"], 0.0)
        self.assertEqual(third["account"]["directory_tree"]["entries"][0]["name"], "query-2")
        self.assertEqual(calls, ["/srv/alice/code", "/srv/alice/code"])

    def test_directory_cache_probes_after_deadline_and_rescans_only_on_change(self):
        now = [0.0]
        token = ["version-1"]
        scan_calls = 0
        probe_calls = 0

        def directory_query(_server, *, root_path=None, **_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            root = root_path or "/srv/alice/code"
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root,
                    "version_token": token[0],
                    "entries": [{"name": f"scan-{scan_calls}"}],
                },
            }

        def version_query(_server, *, root_path, **_kwargs):
            nonlocal probe_calls
            probe_calls += 1
            return {"supported": True, "root": root_path, "version_token": token[0]}

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
                directory_version_query=version_query,
                clock=lambda: now[0],
            )
            first = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 14.0
            fresh_hit = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 16.0
            validated = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 20.0
            renewed_hit = service.inspect_account_directory("cluster", "/srv/alice/code")
            token[0] = "version-2"
            now[0] = 32.0
            changed = service.inspect_account_directory("cluster", "/srv/alice/code")

        self.assertEqual(first["cache"]["state"], "miss")
        self.assertEqual(fresh_hit["cache"]["state"], "hit")
        self.assertTrue(validated["unchanged"])
        self.assertNotIn("account", validated)
        self.assertEqual(validated["cache"]["state"], "validated")
        self.assertEqual(renewed_hit["account"]["directory_tree"]["entries"][0]["name"], "scan-1")
        self.assertEqual(changed["account"]["directory_tree"]["entries"][0]["name"], "scan-2")
        self.assertEqual((scan_calls, probe_calls), (2, 2))

    def test_directory_cache_deep_deadline_bounds_staleness_when_probe_fails(self):
        now = [0.0]
        scan_calls = 0
        probe_calls = 0

        def directory_query(_server, *, root_path=None, **_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root_path or "/srv/alice/code",
                    "version_token": "version-1",
                    "entries": [{"name": f"scan-{scan_calls}"}],
                },
            }

        def failed_probe(_server, **_kwargs):
            nonlocal probe_calls
            probe_calls += 1
            raise ConnectorFailure("probe_failed", "stat unavailable", retryable=True)

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
                directory_version_query=failed_probe,
                clock=lambda: now[0],
            )
            service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 16.0
            fallback = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 121.0
            deep_refresh = service.inspect_account_directory("cluster", "/srv/alice/code")

        self.assertTrue(fallback["unchanged"])
        self.assertEqual(fallback["cache"]["state"], "stale_hit")
        self.assertEqual(fallback["cache"]["age_seconds"], 16.0)
        self.assertEqual(deep_refresh["cache"]["state"], "refreshed")
        self.assertEqual(deep_refresh["account"]["directory_tree"]["entries"][0]["name"], "scan-2")
        self.assertEqual((scan_calls, probe_calls), (2, 1))

    def test_recent_probe_cannot_extend_the_absolute_deep_refresh_deadline(self):
        now = [0.0]
        scan_calls = 0
        probe_calls = 0

        def directory_query(_server, *, root_path=None, **_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root_path or "/srv/alice/code",
                    "version_token": "stable",
                    "entries": [{"name": f"scan-{scan_calls}"}],
                },
            }

        def version_query(_server, *, root_path, **_kwargs):
            nonlocal probe_calls
            probe_calls += 1
            return {"supported": True, "root": root_path, "version_token": "stable"}

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
                directory_version_query=version_query,
                clock=lambda: now[0],
            )
            service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 119.0
            validated = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 119.5
            short_hit = service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 120.0
            deep_refresh = service.inspect_account_directory("cluster", "/srv/alice/code")

        self.assertEqual(validated["cache"]["state"], "validated")
        self.assertEqual(validated["cache"]["revalidate_after_seconds"], 1.0)
        self.assertEqual(short_hit["cache"]["state"], "hit")
        self.assertEqual(short_hit["cache"]["revalidate_after_seconds"], 0.5)
        self.assertEqual(deep_refresh["cache"]["state"], "refreshed")
        self.assertEqual(
            deep_refresh["account"]["directory_tree"]["entries"][0]["name"],
            "scan-2",
        )
        self.assertEqual((scan_calls, probe_calls), (2, 1))

    def test_stale_directory_validation_is_single_flight_and_returns_no_duplicate_tree(self):
        now = [0.0]
        probe_started = threading.Event()
        release_probe = threading.Event()
        scan_calls = 0
        probe_calls = 0

        def directory_query(_server, *, root_path=None, **_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root_path or "/srv/alice/code",
                    "version_token": "stable",
                    "entries": [],
                },
            }

        def version_query(_server, *, root_path, **_kwargs):
            nonlocal probe_calls
            probe_calls += 1
            probe_started.set()
            self.assertTrue(release_probe.wait(5))
            return {"supported": True, "root": root_path, "version_token": "stable"}

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
                directory_version_query=version_query,
                clock=lambda: now[0],
            )
            service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 16.0
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [
                    executor.submit(
                        service.inspect_account_directory, "cluster", "/srv/alice/code"
                    )
                    for _ in range(6)
                ]
                self.assertTrue(probe_started.wait(5))
                time.sleep(0.05)
                release_probe.set()
                results = [future.result(timeout=5) for future in futures]

        self.assertEqual((scan_calls, probe_calls), (1, 1))
        self.assertTrue(all(result.get("unchanged") for result in results))
        self.assertTrue(all("account" not in result for result in results))

    def test_directory_probe_result_is_rejected_after_connection_changes_in_flight(self):
        now = [0.0]
        probe_started = threading.Event()
        release_probe = threading.Event()

        def directory_query(server, *, root_path=None, **_kwargs):
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": root_path or "/srv/alice/code",
                    "version_token": f"revision:{server.host}",
                    "entries": [],
                },
            }

        def version_query(server, *, root_path, **_kwargs):
            probe_started.set()
            self.assertTrue(release_probe.wait(5))
            return {
                "supported": True,
                "root": root_path,
                "version_token": f"revision:{server.host}",
            }

        original = one_server_profile()
        changed_raw = original.to_dict()
        changed_raw["servers"][0]["host"] = "replacement.test"
        changed = Profile.from_dict(changed_raw)
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            service = DashboardService(
                original,
                SnapshotCache(paths, "cluster"),
                directory_query=directory_query,
                directory_version_query=version_query,
                clock=lambda: now[0],
            )
            service.inspect_account_directory("cluster", "/srv/alice/code")
            now[0] = 16.0
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    service.inspect_account_directory, "cluster", "/srv/alice/code"
                )
                self.assertTrue(probe_started.wait(5))
                service.replace_profile(changed, SnapshotCache(paths, "cluster"))
                release_probe.set()
                result = pending.result(timeout=5)
            replacement = service.inspect_account_directory("cluster", "/srv/alice/code")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "configuration_changed")
        self.assertEqual(
            replacement["account"]["directory_tree"]["version_token"],
            "revision:replacement.test",
        )

    def test_directory_cache_single_flight_shares_one_remote_query(self):
        started = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def directory_query(_server, **_kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            self.assertTrue(release.wait(5))
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {"root": "/srv/alice/code", "entries": []},
            }

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
            )
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [
                    executor.submit(service.inspect_account_directory, "cluster", "/srv/alice/code")
                    for _ in range(6)
                ]
                self.assertTrue(started.wait(5))
                release.set()
                results = [future.result(timeout=5) for future in futures]

        self.assertEqual(calls, 1)
        self.assertEqual(sum(item["cache"]["state"] == "miss" for item in results), 1)
        self.assertEqual(sum(item["cache"]["state"] == "hit" for item in results), 5)

    def test_forced_directory_refresh_waits_for_an_ordinary_flight_then_scans_again(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def directory_query(_server, **_kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
                current_call = calls
            if current_call == 1:
                first_started.set()
                self.assertTrue(release_first.wait(5))
            elif current_call == 2:
                second_started.set()
                self.assertTrue(release_second.wait(5))
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {
                    "root": "/srv/alice/code",
                    "entries": [{"name": f"query-{current_call}"}],
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=directory_query,
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                ordinary = executor.submit(
                    service.inspect_account_directory, "cluster", "/srv/alice/code"
                )
                self.assertTrue(first_started.wait(5))
                forced = executor.submit(
                    lambda: service.inspect_account_directory(
                        "cluster", "/srv/alice/code", force=True
                    )
                )
                time.sleep(0.05)
                self.assertEqual(calls, 1)
                release_first.set()
                self.assertTrue(second_started.wait(5))
                self.assertFalse(forced.done())
                release_second.set()
                ordinary_result = ordinary.result(timeout=5)
                forced_result = forced.result(timeout=5)

        self.assertEqual(calls, 2)
        self.assertEqual(ordinary_result["cache"]["state"], "miss")
        self.assertEqual(forced_result["cache"]["state"], "refreshed")
        self.assertEqual(
            forced_result["account"]["directory_tree"]["entries"][0]["name"],
            "query-2",
        )

    def test_directory_single_flight_releases_waiters_after_connector_failure(self):
        started = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def directory_query(_server, **_kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            self.assertTrue(release.wait(5))
            raise ConnectorFailure("ssh_timeout", "directory timed out", retryable=True)

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                directory_query=lambda _server, **_kwargs: {
                    "username": "alice",
                    "home_directory": "/srv/alice",
                    "directory_tree": {"root": "/srv/alice/code", "entries": []},
                },
            )
            service.inspect_account_directory("cluster", "/srv/alice/code")
            service.directory_query = directory_query
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        lambda: service.inspect_account_directory(
                            "cluster", "/srv/alice/code", force=True
                        )
                    )
                    for _ in range(4)
                ]
                self.assertTrue(started.wait(5))
                # Give the simultaneously submitted followers time to join the
                # still-blocked flight before releasing its terminal failure.
                time.sleep(0.1)
                release.set()
                results = [future.result(timeout=5) for future in futures]
            retry = service.inspect_account_directory("cluster", "/srv/alice/code", force=True)

        self.assertEqual(calls, 2)
        self.assertTrue(all(item["code"] == "ssh_timeout" for item in results))
        self.assertEqual(retry["code"], "ssh_timeout")

    def test_directory_cache_is_lru_bounded_and_connection_changes_invalidate_only_that_server(self):
        calls: list[tuple[str, str | None]] = []

        def directory_query(server, *, root_path=None, **_kwargs):
            calls.append((server.host, root_path))
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {"root": root_path or "/srv/alice", "entries": []},
            }

        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.service.MAX_DIRECTORY_CACHE_ROOTS", 2
        ):
            paths = storage_paths(Path(temporary))
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(paths, "cluster"),
                directory_query=directory_query,
            )
            service.inspect_account_directory("cluster", "/srv/alice/a")
            service.inspect_account_directory("cluster", "/srv/alice/b")
            self.assertEqual(
                service.inspect_account_directory("cluster", "/srv/alice/a")["cache"]["state"],
                "hit",
            )
            service.inspect_account_directory("cluster", "/srv/alice/c")
            evicted = service.inspect_account_directory("cluster", "/srv/alice/b")

            unchanged_raw = one_server_profile().to_dict()
            unchanged_raw["servers"][0]["display_name"] = "Renamed"
            service.replace_profile(Profile.from_dict(unchanged_raw), SnapshotCache(paths, "cluster"))
            retained = service.inspect_account_directory("cluster", "/srv/alice/b")

            changed_raw = unchanged_raw
            changed_raw["servers"][0]["host"] = "replacement.test"
            service.replace_profile(Profile.from_dict(changed_raw), SnapshotCache(paths, "cluster"))
            invalidated = service.inspect_account_directory("cluster", "/srv/alice/b")

        self.assertEqual(evicted["cache"]["state"], "miss")
        self.assertEqual(retained["cache"]["state"], "hit")
        self.assertEqual(invalidated["cache"]["state"], "miss")
        self.assertEqual(calls[-1], ("replacement.test", "/srv/alice/b"))

    def test_directory_result_is_rejected_when_auth_reference_changes_in_flight(self):
        started = threading.Event()
        release = threading.Event()
        seen_auth_refs: list[str] = []

        def directory_query(server, **_kwargs):
            seen_auth_refs.append(server.auth_ref)
            if len(seen_auth_refs) == 1:
                started.set()
                self.assertTrue(release.wait(5))
            return {
                "username": "alice",
                "home_directory": "/srv/alice",
                "directory_tree": {"root": "/srv/alice/code", "entries": []},
            }

        original_raw = one_server_profile().to_dict()
        original_raw["servers"][0]["auth_ref"] = "credential:old"
        original = Profile.from_dict(original_raw)
        changed_raw = original.to_dict()
        changed_raw["servers"][0]["auth_ref"] = "credential:new"
        changed = Profile.from_dict(changed_raw)

        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            service = DashboardService(
                original,
                SnapshotCache(paths, "cluster"),
                directory_query=directory_query,
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    service.inspect_account_directory, "cluster", "/srv/alice/code"
                )
                self.assertTrue(started.wait(5))
                service.replace_profile(changed, SnapshotCache(paths, "cluster"))
                release.set()
                rejected = pending.result(timeout=5)
            current = service.inspect_account_directory("cluster", "/srv/alice/code")

        self.assertEqual(rejected["code"], "configuration_changed")
        self.assertEqual(current["cache"]["state"], "miss")
        self.assertEqual(seen_auth_refs, ["credential:old", "credential:new"])

    def test_directory_failure_does_not_downgrade_the_monitoring_connection_state(self):
        cases = [
            ("ssh_timeout", True, "offline"),
            ("parse_failed", True, "offline"),
            ("auth_failed", False, "auth_required"),
            ("host_key_changed", False, "security_blocked"),
        ]
        for code, retryable, state in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                failure = ConnectorFailure(code, "directory failed", retryable=retryable, state=state)
                service = DashboardService(
                    one_server_profile(),
                    SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                    query=lambda server: payload(server.id),
                    directory_query=Mock(side_effect=failure),
                )
                before = service.refresh(force=True)["servers"][0]
                result = service.inspect_account_directory("cluster")
                after = service.snapshot()["servers"][0]

                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], code)
                self.assertEqual(after["connection"]["state"], "online")
                self.assertIsNone(after["connection"]["error"])
                self.assertEqual(
                    after["connection"]["data_revision"],
                    before["connection"]["data_revision"],
                )

    def test_directory_inspection_rejects_unknown_server_without_remote_query(self):
        directory_query = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                profile(),
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                directory_query=directory_query,
            )
            result = service.inspect_account_directory("missing")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "server_not_found")
        directory_query.assert_not_called()

    def test_snapshot_preserves_profile_server_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(profile(), SnapshotCache(storage_paths(Path(temporary)), "lab"))

            snapshot = service.snapshot()

            self.assertEqual([item["server_id"] for item in snapshot["servers"]], ["online", "offline"])
            self.assertFalse(snapshot["monitoring"]["paused"])
            self.assertFalse(snapshot["monitoring"]["in_flight"])
            self.assertEqual(snapshot["summary"]["revision"], snapshot["monitoring"]["revision"])

    def test_semantically_corrupt_cache_payload_is_ignored_without_blocking_startup(self):
        class CorruptCache:
            def load(self, _server_id, **_kwargs):
                return {
                    "schema_version": 2,
                    "server_id": "cluster",
                    "connection_fingerprint": "synthetic",
                    "last_success_at": "2026-08-29T00:00:00Z",
                    "payload": {"view_kind": "scheduler", "nodes": 1},
                }

            def save(self, *_args, **_kwargs):
                raise AssertionError("startup must not rewrite an invalid cache entry")

        service = DashboardService(one_server_profile(backend="slurm_ssh"), CorruptCache())

        server = service.snapshot()["servers"][0]
        self.assertEqual(server["connection"]["state"], "connecting")
        self.assertEqual(server["connection"]["data_origin"], "none")
        self.assertNotIn("nodes", server)

    def test_cache_from_the_same_server_id_but_an_old_endpoint_is_not_loaded(self):
        original = one_server_profile()
        moved = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "cluster",
                "display_name": "Cluster",
                "servers": [
                    {
                        "id": "cluster",
                        "display_name": "Cluster",
                        "backend": "direct_ssh",
                        "host": "replacement.test",
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "cluster")
            DashboardService(original, cache, query=lambda server: payload(server.id)).refresh(force=True)

            restarted = DashboardService(moved, cache)
            server = restarted.snapshot()["servers"][0]

        self.assertEqual(server["connection"]["state"], "connecting")
        self.assertEqual(server["connection"]["data_origin"], "none")
        self.assertNotIn("gpus", server)

    def test_connection_fingerprint_tracks_relative_ssh_files_under_user_ssh_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            ssh_directory = home / ".ssh"
            ssh_directory.mkdir()
            identity = ssh_directory / "id_ed25519"
            config = ssh_directory / "config.work"
            identity.write_text("private-a", encoding="utf-8")
            config.write_text("Host gpu\n  HostName one.example\n", encoding="utf-8")
            server = Profile.from_dict(
                {
                    "schema_version": 1,
                    "id": "lab",
                    "display_name": "Lab",
                    "servers": [
                        {
                            "id": "gpu",
                            "display_name": "GPU",
                            "backend": "direct_ssh",
                            "ssh_alias": "gpu",
                            "identity_file": "id_ed25519",
                            "ssh_config_file": "config.work",
                        }
                    ],
                }
            ).servers[0]
            with patch("vram_radar.connectors.Path.home", return_value=home):
                before = connection_fingerprint(server)
                identity.write_text("private-key-b-longer", encoding="utf-8")
                after_key_rotation = connection_fingerprint(server)
                config.write_text("Host gpu\n  HostName two.example\n", encoding="utf-8")
                after_config_change = connection_fingerprint(server)

        self.assertNotEqual(before, after_key_rotation)
        self.assertNotEqual(after_key_rotation, after_config_change)

    def test_connection_probe_success_updates_the_same_runtime_state_used_by_the_dashboard(self):
        query = Mock(side_effect=lambda server: payload(server.id, 17.5))
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=query,
            )

            result = service.probe_server("cluster")
            server = service.snapshot()["servers"][0]

        self.assertEqual(result["server_id"], "cluster")
        self.assertEqual(server["connection"]["state"], "online")
        self.assertEqual(server["connection"]["data_origin"], "live")
        self.assertEqual(server["free_vram_gib"], 17.5)
        query.assert_called_once_with(one_server_profile().servers[0])

    def test_identical_success_keeps_data_revision_and_avoids_redundant_cache_write(self):
        class RecordingCache:
            def __init__(self):
                self.saves = []

            def load(self, *_args, **_kwargs):
                return None

            def save(self, *args, **kwargs):
                self.saves.append((args, kwargs))

        cache = RecordingCache()
        service = DashboardService(
            one_server_profile(),
            cache,
            query=lambda server: payload(server.id),
        )

        first = service.refresh(force=True)
        second = service.refresh(force=True)
        service.query = Mock(
            side_effect=ConnectorFailure("ssh_timeout", "timeout", retryable=True, state="offline")
        )
        failed = service.refresh(force=True)
        failed_revision = failed["monitoring"]["revision"]
        service.query = lambda server: payload(server.id)
        recovered = service.refresh(force=True)

        self.assertEqual(first["servers"][0]["connection"]["data_revision"], 1)
        self.assertEqual(second["servers"][0]["connection"]["data_revision"], 1)
        self.assertEqual(len(cache.saves), 2)
        self.assertEqual(failed["servers"][0]["connection"]["state"], "stale")
        self.assertEqual(recovered["servers"][0]["connection"]["data_revision"], 1)
        self.assertEqual(recovered["servers"][0]["connection"]["state"], "online")
        self.assertGreater(recovered["monitoring"]["revision"], failed_revision)

    def test_identical_success_retries_a_transient_snapshot_cache_write_failure(self):
        class FlakyCache:
            def __init__(self):
                self.save_calls = 0

            def load(self, *_args, **_kwargs):
                return None

            def save(self, *_args, **_kwargs):
                self.save_calls += 1
                if self.save_calls == 1:
                    raise OSError("temporary disk failure")

        cache = FlakyCache()
        service = DashboardService(
            one_server_profile(),
            cache,
            query=lambda server: payload(server.id),
        )

        first = service.refresh(force=True)
        second = service.refresh(force=True)
        third = service.refresh(force=True)

        self.assertEqual(cache.save_calls, 2)
        self.assertEqual(first["servers"][0]["connection"]["data_revision"], 1)
        self.assertEqual(second["servers"][0]["connection"]["data_revision"], 1)
        self.assertEqual(third["servers"][0]["connection"]["data_revision"], 1)

    def test_non_connection_profile_changes_preserve_live_runtime_state(self):
        original = profile()
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            cache = SnapshotCache(paths, "lab")
            service = DashboardService(original, cache, query=lambda server: payload(server.id))
            service.refresh(force=True)
            changed_raw = original.to_dict()
            changed_raw["servers"][0]["display_name"] = "Renamed"
            changed_raw["servers"][0]["default_work_directory"] = "/srv/code"
            changed = Profile.from_dict(changed_raw)

            service.replace_profile(changed, SnapshotCache(paths, "lab"))
            servers = service.snapshot()["servers"]

        self.assertEqual([item["connection"]["state"] for item in servers], ["online", "online"])
        self.assertEqual(servers[0]["display_name"], "Renamed")
        self.assertIn("gpus", servers[0])

    def test_connection_identity_change_resets_only_the_changed_server(self):
        original = profile()
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            cache = SnapshotCache(paths, "lab")
            service = DashboardService(original, cache, query=lambda server: payload(server.id))
            service.refresh(force=True)
            changed_raw = original.to_dict()
            changed_raw["servers"][0]["host"] = "replacement.test"
            changed = Profile.from_dict(changed_raw)

            service.replace_profile(changed, SnapshotCache(paths, "lab"))
            servers = service.snapshot()["servers"]

        self.assertEqual(servers[0]["connection"]["state"], "connecting")
        self.assertEqual(servers[1]["connection"]["state"], "online")

    def test_connection_probe_failure_updates_runtime_before_returning_the_error(self):
        failure = ConnectorFailure(
            "auth_failed",
            "Permission denied (publickey)",
            retryable=False,
            state="auth_required",
        )
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=Mock(side_effect=failure),
            )

            with self.assertRaises(ConnectorFailure) as captured:
                service.probe_server("cluster")
            server = service.snapshot()["servers"][0]

        self.assertEqual(captured.exception.code, "auth_failed")
        self.assertEqual(server["connection"]["state"], "auth_required")
        self.assertEqual(server["connection"]["error"]["code"], "auth_failed")

    def test_pause_skips_only_periodic_fleet_refresh_and_preserves_latest_revision(self):
        query = Mock(side_effect=lambda server: payload(server.id))
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                profile(), SnapshotCache(storage_paths(Path(temporary)), "lab"), query=query
            )
            paused = service.pause()
            paused_revision = paused["monitoring"]["revision"]

            skipped = service.refresh()
            service.refresh(server_id="online")
            service.refresh(force=True)
            resumed = service.resume()

        self.assertTrue(paused["monitoring"]["paused"])
        self.assertEqual(skipped["monitoring"]["revision"], paused_revision)
        self.assertEqual(query.call_count, 3)
        self.assertFalse(resumed["monitoring"]["paused"])
        self.assertGreater(resumed["monitoring"]["revision"], paused_revision)

    def test_request_refresh_queues_one_forced_followup_and_coalesces_duplicates(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def query(server):
            calls.append(server.id)
            started.set()
            self.assertTrue(release.wait(5))
            return payload(server.id)

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=query,
            )
            first = service.request_refresh(force=True)
            self.assertTrue(started.wait(5))
            second = service.request_refresh(force=True)
            third = service.request_refresh(force=True)
            self.assertTrue(first["monitoring"]["in_flight"])
            self.assertTrue(second["monitoring"]["in_flight"])
            self.assertTrue(third["monitoring"]["in_flight"])
            self.assertEqual(calls, ["cluster"])
            service.pause()
            pause_started = time.monotonic()
            paused_snapshot = service.refresh()
            pause_elapsed = time.monotonic() - pause_started
            self.assertTrue(paused_snapshot["monitoring"]["paused"])
            self.assertLess(pause_elapsed, 0.5)
            release.set()
            deadline = time.monotonic() + 5
            while service.snapshot()["monitoring"]["in_flight"] and time.monotonic() < deadline:
                time.sleep(0.01)
            completed = service.snapshot()

        self.assertFalse(completed["monitoring"]["in_flight"])
        self.assertEqual(calls, ["cluster", "cluster"])
        self.assertEqual(completed["servers"][0]["connection"]["state"], "online")

    def test_refresh_thread_start_failure_clears_in_flight_state_for_a_later_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=lambda server: payload(server.id),
            )
            with patch("vram_radar.service.threading.Thread.start", side_effect=RuntimeError("no thread")):
                with self.assertRaisesRegex(RuntimeError, "后台服务器刷新线程"):
                    service.request_refresh(force=True)
            self.assertFalse(service.snapshot()["monitoring"]["in_flight"])

            service.request_refresh(force=True)
            deadline = time.time() + 2
            while service.snapshot()["monitoring"]["in_flight"] and time.time() < deadline:
                time.sleep(0.01)
            final = service.snapshot()

        self.assertEqual(final["servers"][0]["connection"]["state"], "online")

    def test_targeted_forced_refresh_requested_during_an_active_refresh_is_not_lost(self):
        first_started = threading.Event()
        release_first = threading.Event()
        target_completed = threading.Event()
        calls = []

        def query(server):
            calls.append(server.id)
            if server.id == "online":
                first_started.set()
                self.assertTrue(release_first.wait(5))
            else:
                target_completed.set()
            return payload(server.id)

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                profile(),
                SnapshotCache(storage_paths(Path(temporary)), "lab"),
                query=query,
            )
            service.request_refresh(force=True, server_id="online")
            self.assertTrue(first_started.wait(5))

            service.request_refresh(force=True, server_id="offline")
            service.request_refresh(force=True, server_id="offline")
            self.assertEqual(calls, ["online"])
            release_first.set()

            self.assertTrue(target_completed.wait(5))
            deadline = time.monotonic() + 5
            while service.snapshot()["monitoring"]["in_flight"] and time.monotonic() < deadline:
                time.sleep(0.01)
            completed = service.snapshot()

        self.assertEqual(calls, ["online", "offline"])
        self.assertFalse(completed["monitoring"]["in_flight"])
        self.assertTrue(all(server["connection"]["state"] == "online" for server in completed["servers"]))

    def test_refresh_worker_pool_scales_above_four_but_stays_bounded(self):
        fleet = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "fleet",
                "display_name": "Fleet",
                "servers": [
                    {
                        "id": f"gpu-{index}",
                        "display_name": f"GPU {index}",
                        "backend": "direct_ssh",
                        "host": f"gpu-{index}.test",
                    }
                    for index in range(20)
                ],
            }
        )
        release = threading.Event()
        condition = threading.Condition()
        active = 0
        maximum_active = 0

        def query(server):
            nonlocal active, maximum_active
            with condition:
                active += 1
                maximum_active = max(maximum_active, active)
                condition.notify_all()
            self.assertTrue(release.wait(5))
            with condition:
                active -= 1
            return payload(server.id)

        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                fleet, SnapshotCache(storage_paths(Path(temporary)), "fleet"), query=query
            )
            worker = threading.Thread(target=lambda: service.refresh(force=True), daemon=True)
            worker.start()
            with condition:
                condition.wait_for(lambda: maximum_active >= 8, timeout=5)
            release.set()
            worker.join(5)

        self.assertFalse(worker.is_alive())
        self.assertGreater(maximum_active, 4)
        self.assertLessEqual(maximum_active, 8)

    def test_recommendation_uses_online_single_device_capacity_and_gpu_preference(self):
        snapshot = {
            "servers": [
                {
                    "server_id": "a100",
                    "display_name": "A100",
                    "backend": "slurm_ssh",
                    "view_kind": "scheduler",
                    "nodes": [
                        {
                            "node": "a100-1",
                            "partition": "GPU-LARGE",
                            "gpu_type": "A100-40G",
                            "memory_per_gpu_gib": 40,
                            "free_gpus": 2,
                            "state": "idle",
                        }
                    ],
                    "connection": {"state": "online"},
                },
                {
                    "server_id": "4090",
                    "display_name": "4090",
                    "backend": "direct_ssh",
                    "view_kind": "live-memory",
                    "gpus": [
                        {"gpu_index": "0", "gpu_type": "NVIDIA GeForce RTX 4090", "memory_free_gib": 23.5}
                    ],
                    "connection": {"state": "online"},
                },
                {
                    "server_id": "stale",
                    "display_name": "Stale",
                    "backend": "direct_ssh",
                    "view_kind": "live-memory",
                    "gpus": [{"gpu_index": "0", "gpu_type": "RTX 6000", "memory_free_gib": 99}],
                    "connection": {"state": "stale"},
                },
            ]
        }

        result = recommend_resources(snapshot, gpu_count=1, min_memory_gib=20)
        self.assertEqual({item["server_id"] for item in result["candidates"]}, {"a100", "4090"})
        preferred = recommend_resources(snapshot, gpu_count=1, min_memory_gib=20, gpu_type="4090")
        self.assertEqual(preferred["candidates"][0]["server_id"], "4090")
        self.assertTrue(preferred["recommendation_only"])

    def test_multi_gpu_recommendation_returns_explicit_direct_and_slurm_pools(self):
        snapshot = {
            "servers": [
                {
                    "server_id": "workstation",
                    "display_name": "Workstation",
                    "backend": "direct_ssh",
                    "view_kind": "live-memory",
                    "gpus": [
                        {
                            "gpu_index": str(index),
                            "gpu_type": "RTX 4090",
                            "memory_free_gib": 22 - index,
                        }
                        for index in range(4)
                    ],
                    "connection": {"state": "online"},
                },
                {
                    "server_id": "slurm",
                    "display_name": "Slurm",
                    "backend": "slurm_ssh",
                    "view_kind": "scheduler",
                    "nodes": [
                        {
                            "node": "h100-a",
                            "partition": "GPU-LARGE",
                            "state": "idle",
                            "gpu_type": "H100-80G",
                            "memory_per_gpu_gib": 80,
                            "free_gpus": 2,
                        },
                        {
                            "node": "h100-b",
                            "partition": "GPU-LARGE",
                            "state": "idle",
                            "gpu_type": "H100-80G",
                            "memory_per_gpu_gib": 80,
                            "free_gpus": 2,
                        },
                        {
                            "node": "h100-whole",
                            "partition": "GPU-ONE-NODE",
                            "state": "idle",
                            "gpu_type": "H100-80G",
                            "memory_per_gpu_gib": 80,
                            "free_gpus": 4,
                        },
                    ],
                    "connection": {"state": "online"},
                },
            ]
        }

        direct = recommend_resources(snapshot, gpu_count=2, min_memory_gib=18, gpu_type="4090")
        same_node = recommend_resources(
            snapshot,
            gpu_count=4,
            min_memory_gib=70,
            gpu_type="h100",
            partition="one-node",
            same_node=True,
        )
        fragmented = recommend_resources(
            snapshot,
            gpu_count=4,
            min_memory_gib=70,
            gpu_type="h100",
            partition="large",
            same_node=False,
        )

        self.assertTrue(direct["ok"])
        self.assertEqual(direct["candidates"][0]["backend"], "direct_ssh")
        self.assertEqual(len(direct["candidates"][0]["allocations"]), 2)
        self.assertTrue(direct["candidates"][0]["same_node"])
        self.assertEqual(len(same_node["candidates"][0]["allocations"]), 1)
        self.assertTrue(same_node["candidates"][0]["same_node"])
        self.assertEqual(sum(item["selected_units"] for item in fragmented["candidates"][0]["allocations"]), 4)
        self.assertTrue(fragmented["candidates"][0]["fragmented"])
        self.assertFalse(fragmented["candidates"][0]["same_node"])

    def test_multi_gpu_same_node_never_combines_fragmented_capacity(self):
        snapshot = {
            "servers": [
                {
                    "server_id": "slurm",
                    "display_name": "Slurm",
                    "backend": "slurm_ssh",
                    "view_kind": "scheduler",
                    "nodes": [
                        {
                            "node": f"node-{index}",
                            "partition": "GPU",
                            "state": "idle",
                            "gpu_type": "A100",
                            "memory_per_gpu_gib": 40,
                            "free_gpus": 2,
                        }
                        for index in range(3)
                    ],
                    "connection": {"state": "online"},
                }
            ]
        }

        result = recommend_resources(snapshot, gpu_count=4, same_node=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["candidates"], [])

    def test_large_cluster_snapshot_is_compact_but_paging_and_recommendation_use_runtime_nodes(self):
        large_payload = scheduler_payload(1000)
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(backend="slurm_ssh"),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=lambda _server: large_payload,
            )
            snapshot = service.refresh(force=True)
            repeated = service.snapshot()
            clamped = service.get_cluster_nodes("cluster", cursor=0, limit=1000)
            page = service.get_cluster_nodes(
                "cluster", cursor=0, limit=100, gpu_type="H100", partition="GPU-LARGE", only_available=True
            )
            next_page = service.get_cluster_nodes(
                "cluster",
                cursor=page["next_cursor"],
                limit=25,
                gpu_type="H100",
                partition="GPU-LARGE",
                only_available=True,
                revision=page["revision"],
            )
            issues = service.get_cluster_nodes("cluster", limit=50, only_issues=True)
            recommendation = service.recommend_many(
                gpu_count=4,
                min_memory_gib=70,
                gpu_type="H100",
                partition="GPU-LARGE",
                same_node=True,
                limit=3,
            )
            service.query = lambda _server: scheduler_payload(1001)
            service.refresh(force=True)
            stale_page = service.get_cluster_nodes(
                "cluster", cursor=page["next_cursor"], limit=25, revision=page["revision"]
            )

        server = snapshot["servers"][0]
        self.assertTrue(server["large_cluster"])
        self.assertEqual(server["node_count"], 1000)
        self.assertNotIn("nodes", server)
        self.assertEqual(sum(group["node_count"] for group in server["node_groups"]), 1000)
        self.assertEqual(sum(group["total_gpus"] for group in server["node_groups"]), 8000)
        self.assertEqual(
            [group["group_key"] for group in server["node_groups"]],
            [group["group_key"] for group in repeated["servers"][0]["node_groups"]],
        )
        self.assertEqual(clamped["limit"], 250)
        self.assertEqual(clamped["returned"], 250)
        self.assertEqual(clamped["next_cursor"], 250)
        self.assertEqual(page["total"], 160)
        self.assertEqual(page["returned"], 100)
        self.assertEqual(page["next_cursor"], 100)
        self.assertEqual(next_page["returned"], 25)
        self.assertEqual(next_page["cursor"], 100)
        self.assertFalse(stale_page["ok"])
        self.assertEqual(stale_page["code"], "snapshot_changed")
        self.assertGreater(stale_page["revision"], page["revision"])
        self.assertEqual(issues["total"], 20)
        self.assertTrue(all(node["state"] == "down" for node in issues["nodes"]))
        self.assertTrue(recommendation["ok"])
        self.assertEqual(recommendation["returned_count"], 3)
        self.assertTrue(all(candidate["same_node"] for candidate in recommendation["candidates"]))

    def test_dense_cluster_enters_compact_mode_before_one_thousand_gpus(self):
        dense_payload = scheduler_payload(40)
        self.assertEqual(dense_payload["total_gpus"], 320)
        with tempfile.TemporaryDirectory() as temporary:
            service = DashboardService(
                one_server_profile(backend="slurm_ssh"),
                SnapshotCache(storage_paths(Path(temporary)), "cluster"),
                query=lambda _server: dense_payload,
            )
            server = service.refresh(force=True)["servers"][0]

        self.assertTrue(server["large_cluster"])
        self.assertEqual(server["node_count"], 40)
        self.assertNotIn("nodes", server)

    def test_one_server_failure_does_not_hide_online_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")

            def query(server):
                if server.id == "offline":
                    raise ConnectorFailure("ssh_timeout", "timeout", retryable=True)
                return payload(server.id)

            snapshot = DashboardService(profile(), cache, query=query).refresh(force=True)
            by_id = {item["server_id"]: item for item in snapshot["servers"]}
            self.assertEqual(by_id["online"]["connection"]["state"], "online")
            self.assertEqual(by_id["offline"]["connection"]["state"], "offline")
            self.assertEqual(snapshot["summary"]["online_servers"], 1)
            self.assertEqual(snapshot["summary"]["free_vram_gib"], 20)
            self.assertEqual(snapshot["summary"]["total_vram_gib"], 24)

    def test_summary_marks_total_capacity_unknown_when_an_online_source_omits_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")

            def query(server):
                result = payload(server.id)
                if server.id == "online":
                    result["total_vram_gib"] = None
                else:
                    raise ConnectorFailure("ssh_timeout", "timeout", retryable=True)
                return result

            snapshot = DashboardService(profile(), cache, query=query).refresh(force=True)

        self.assertIsNone(snapshot["summary"]["total_vram_gib"])
        self.assertEqual(snapshot["summary"]["free_vram_gib"], 20)

    def test_cached_failure_is_stale_and_excluded_from_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            DashboardService(
                profile(),
                cache,
                query=lambda server: payload(server.id, 23.5),
            ).refresh(force=True)

            def query(server):
                if server.id == "offline":
                    raise ConnectorFailure("ssh_timeout", "timeout", retryable=True)
                return payload(server.id, 10)

            snapshot = DashboardService(profile(), cache, query=query).refresh(force=True)
            by_id = {item["server_id"]: item for item in snapshot["servers"]}
            self.assertEqual(by_id["offline"]["connection"]["state"], "stale")
            self.assertEqual(by_id["offline"]["connection"]["data_origin"], "cache")
            self.assertFalse(by_id["offline"]["connection"]["usable_for_summary"])
            self.assertEqual(snapshot["summary"]["free_vram_gib"], 10)
            self.assertEqual(snapshot["summary"]["total_vram_gib"], 24)
            self.assertEqual(snapshot["summary"]["stale_servers"], 1)

    def test_non_retryable_security_failure_stops_auto_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")

            def query(_server):
                raise ConnectorFailure("host_key_changed", "changed", retryable=False, state="security_blocked")

            service = DashboardService(profile(), cache, query=query)
            snapshot = service.refresh(force=True)
            self.assertTrue(all(item["connection"]["state"] == "security_blocked" for item in snapshot["servers"]))
            self.assertTrue(all(item["connection"]["retry_at"] is None for item in snapshot["servers"]))

    def test_security_failure_remains_high_priority_with_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            DashboardService(
                profile(),
                cache,
                query=lambda server: payload(server.id, 18),
            ).refresh(force=True)

            def query(_server):
                raise ConnectorFailure("host_key_changed", "changed", retryable=False, state="security_blocked")

            snapshot = DashboardService(profile(), cache, query=query).refresh(force=True)
            by_id = {item["server_id"]: item for item in snapshot["servers"]}
            self.assertEqual(by_id["online"]["connection"]["state"], "security_blocked")
            self.assertEqual(by_id["online"]["connection"]["data_origin"], "cache")
            self.assertFalse(by_id["online"]["connection"]["usable_for_summary"])


if __name__ == "__main__":
    unittest.main()
