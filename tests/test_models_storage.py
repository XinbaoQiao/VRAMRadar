from pathlib import Path
import tempfile
import unittest

from vram_radar.models import ConfigError, Profile
from vram_radar.storage import ProfileStore, SnapshotCache, storage_paths


PROFILE = {
    "schema_version": 1,
    "id": "lab",
    "display_name": "Lab GPUs",
    "refresh_seconds": 15,
    "servers": [
        {
            "id": "gpu-1",
            "display_name": "GPU One",
            "backend": "direct_ssh",
            "host": "gpu.example.test",
            "port": 22,
            "username": "alice",
            "show_other_user_commands": True,
        }
    ],
}


class ModelStorageTests(unittest.TestCase):
    def test_profile_round_trip_uses_explicit_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = storage_paths(Path(temporary))
            store = ProfileStore(paths)
            profile = Profile.from_dict(PROFILE)
            path = store.save(profile)
            self.assertTrue(path.resolve().is_relative_to(Path(temporary).resolve()))
            self.assertEqual(store.load("lab"), profile)
            self.assertEqual(store.list_profiles(), ["lab"])
            self.assertTrue(store.load("lab").servers[0].show_other_user_commands)

    def test_profile_round_trip_preserves_per_server_openssh_source(self):
        raw = dict(PROFILE)
        raw["servers"] = [dict(PROFILE["servers"][0], ssh_config_file="~/.ssh/editor.conf")]

        profile = Profile.from_dict(raw)

        self.assertEqual(profile.servers[0].ssh_config_file, "~/.ssh/editor.conf")
        self.assertEqual(profile.to_dict()["servers"][0]["ssh_config_file"], "~/.ssh/editor.conf")

    def test_profile_round_trip_preserves_default_work_directory(self):
        raw = dict(PROFILE)
        raw["servers"] = [
            dict(PROFILE["servers"][0], default_work_directory="/srv/vram-radar-account/projects/radar")
        ]

        profile = Profile.from_dict(raw)

        self.assertEqual(profile.servers[0].default_work_directory, "/srv/vram-radar-account/projects/radar")
        self.assertEqual(
            profile.to_dict()["servers"][0]["default_work_directory"],
            "/srv/vram-radar-account/projects/radar",
        )

    def test_profile_round_trip_preserves_key_first_authentication(self):
        raw = dict(PROFILE)
        raw["servers"] = [
            dict(PROFILE["servers"][0], identity_file="~/.ssh/id_ed25519", prefer_identity_auth=True)
        ]

        profile = Profile.from_dict(raw)

        self.assertTrue(profile.servers[0].prefer_identity_auth)
        self.assertTrue(profile.to_dict()["servers"][0]["prefer_identity_auth"])

    def test_profile_round_trip_preserves_navigator_side_and_defaults_right(self):
        left = Profile.from_dict(dict(PROFILE, navigator_side="left"))
        default = Profile.from_dict(PROFILE)

        self.assertEqual(left.navigator_side, "left")
        self.assertEqual(left.to_dict()["navigator_side"], "left")
        self.assertEqual(default.navigator_side, "right")

    def test_profile_rejects_unknown_navigator_side(self):
        with self.assertRaisesRegex(ConfigError, "navigator_side must be left or right"):
            Profile.from_dict(dict(PROFILE, navigator_side="center"))

    def test_legacy_profile_defaults_new_local_usability_preferences(self):
        profile = Profile.from_dict(PROFILE)

        self.assertEqual(profile.schema_version, 1)
        self.assertEqual(profile.close_behavior, "tray")
        self.assertEqual(profile.ui_language, "zh-CN")
        self.assertEqual(profile.favorite_server_ids, ())
        self.assertTrue(profile.favorite_alert_enabled)
        self.assertEqual(profile.favorite_alert_min_memory_gib, 0.0)
        self.assertEqual(profile.ignored_ssh_aliases, ())
        self.assertEqual(profile.saved_views, ())

    def test_new_defaults_enable_alerts_and_other_user_summaries_without_overriding_opt_outs(self):
        default_profile = Profile.from_dict(
            {
                "schema_version": 1,
                "id": "default-on",
                "display_name": "Default on",
                "servers": [
                    {
                        "id": "gpu-default",
                        "display_name": "GPU default",
                        "backend": "direct_ssh",
                        "host": "gpu-default.example.test",
                    }
                ],
            }
        )
        opted_out = Profile.from_dict(
            {
                **default_profile.to_dict(),
                "favorite_alert_enabled": False,
                "servers": [
                    {
                        **default_profile.to_dict()["servers"][0],
                        "show_other_user_commands": False,
                    }
                ],
            }
        )

        self.assertTrue(default_profile.favorite_alert_enabled)
        self.assertTrue(default_profile.servers[0].show_other_user_commands)
        self.assertFalse(opted_out.favorite_alert_enabled)
        self.assertFalse(opted_out.servers[0].show_other_user_commands)

    def test_ui_language_round_trip_and_validation(self):
        english = Profile.from_dict(dict(PROFILE, ui_language="en"))

        self.assertEqual(english.ui_language, "en")
        self.assertEqual(english.to_dict()["ui_language"], "en")
        self.assertEqual(Profile.from_dict(english.to_dict()), english)
        with self.assertRaisesRegex(ConfigError, "ui_language must be zh-CN or en"):
            Profile.from_dict(dict(PROFILE, ui_language="fr"))

    def test_ignored_ssh_aliases_round_trip_without_changing_profile_schema(self):
        raw = dict(PROFILE, ignored_ssh_aliases=["retired-gpu", "Legacy.A100"])

        profile = Profile.from_dict(raw)
        serialized = profile.to_dict()

        self.assertEqual(profile.schema_version, 1)
        self.assertEqual(profile.ignored_ssh_aliases, ("retired-gpu", "Legacy.A100"))
        self.assertEqual(serialized["ignored_ssh_aliases"], ["retired-gpu", "Legacy.A100"])
        self.assertEqual(Profile.from_dict(serialized), profile)

    def test_ignored_ssh_aliases_are_bounded_unique_and_validated_as_ssh_tokens(self):
        with self.assertRaisesRegex(ConfigError, "must be an array"):
            Profile.from_dict(dict(PROFILE, ignored_ssh_aliases="retired-gpu"))
        with self.assertRaisesRegex(ConfigError, "non-empty SSH token"):
            Profile.from_dict(dict(PROFILE, ignored_ssh_aliases=[""]))
        with self.assertRaisesRegex(ConfigError, "SSH token"):
            Profile.from_dict(dict(PROFILE, ignored_ssh_aliases=["-oProxyCommand=bad"]))
        with self.assertRaisesRegex(ConfigError, "unique, ignoring case"):
            Profile.from_dict(dict(PROFILE, ignored_ssh_aliases=["Legacy.A100", "legacy.a100"]))
        with self.assertRaisesRegex(ConfigError, "cannot contain more than 4096"):
            Profile.from_dict(
                dict(PROFILE, ignored_ssh_aliases=[f"retired-{index}" for index in range(4097)])
            )

    def test_local_usability_preferences_round_trip_and_allow_missing_favorite(self):
        raw = dict(
            PROFILE,
            close_behavior="exit",
            favorite_server_ids=["gpu-1", "temporarily-missing"],
            favorite_alert_enabled=True,
            favorite_alert_min_memory_gib=18.5,
            saved_views=[
                {
                    "id": "eight-h100",
                    "name": "8 张 H100",
                    "query": "training",
                    "filter": "available",
                    "gpu_count": 8,
                    "min_memory_gib": 70,
                    "gpu_type": "H100",
                    "partition": "gpu-large",
                    "same_node": True,
                }
            ],
        )

        profile = Profile.from_dict(raw)
        serialized = profile.to_dict()

        self.assertEqual(profile.close_behavior, "exit")
        self.assertEqual(profile.favorite_server_ids, ("gpu-1", "temporarily-missing"))
        self.assertTrue(profile.favorite_alert_enabled)
        self.assertEqual(profile.favorite_alert_min_memory_gib, 18.5)
        self.assertEqual(profile.saved_views[0]["gpu_count"], 8)
        self.assertEqual(profile.saved_views[0]["min_memory_gib"], 70.0)
        self.assertEqual(Profile.from_dict(serialized), profile)

    def test_profile_rejects_invalid_local_preferences(self):
        with self.assertRaisesRegex(ConfigError, "close_behavior must be tray or exit"):
            Profile.from_dict(dict(PROFILE, close_behavior="hide"))
        with self.assertRaisesRegex(ConfigError, "favorite server id"):
            Profile.from_dict(dict(PROFILE, favorite_server_ids=["unsafe/id"]))
        with self.assertRaisesRegex(ConfigError, "must be unique"):
            Profile.from_dict(dict(PROFILE, favorite_server_ids=["gpu-1", "gpu-1"]))
        with self.assertRaisesRegex(ConfigError, "favorite_alert_enabled"):
            Profile.from_dict(dict(PROFILE, favorite_alert_enabled="yes"))
        for value in (True, float("nan"), -1, 1001):
            with self.subTest(favorite_alert_min_memory_gib=value):
                with self.assertRaisesRegex(ConfigError, "favorite_alert_min_memory_gib"):
                    Profile.from_dict(dict(PROFILE, favorite_alert_min_memory_gib=value))

    def test_saved_views_are_bounded_and_reject_unsafe_or_oversized_fields(self):
        base = {
            "id": "training",
            "name": "训练资源",
            "query": "",
            "filter": "all",
            "gpu_count": 1,
            "min_memory_gib": 0,
            "gpu_type": "",
            "partition": "",
            "same_node": False,
        }
        with self.assertRaisesRegex(ConfigError, "unsupported fields"):
            Profile.from_dict(dict(PROFILE, saved_views=[{**base, "command": "ssh secret"}]))
        with self.assertRaisesRegex(ConfigError, "gpu_count must be"):
            Profile.from_dict(dict(PROFILE, saved_views=[{**base, "gpu_count": 10001}]))
        with self.assertRaisesRegex(ConfigError, "min_memory_gib must be"):
            Profile.from_dict(dict(PROFILE, saved_views=[{**base, "min_memory_gib": float("nan")}]))
        with self.assertRaisesRegex(ConfigError, "same_node must be true or false"):
            Profile.from_dict(dict(PROFILE, saved_views=[{**base, "same_node": "yes"}]))
        with self.assertRaisesRegex(ConfigError, "contains control characters"):
            Profile.from_dict(dict(PROFILE, saved_views=[{**base, "query": "line1\nline2"}]))
        with self.assertRaisesRegex(ConfigError, "cannot contain more than 32"):
            Profile.from_dict(
                dict(
                    PROFILE,
                    saved_views=[{**base, "id": f"view-{index}"} for index in range(33)],
                )
            )

    def test_profile_rejects_non_absolute_default_work_directory(self):
        raw = dict(PROFILE)
        raw["servers"] = [dict(PROFILE["servers"][0], default_work_directory="projects/radar")]

        with self.assertRaisesRegex(ConfigError, "absolute remote directory"):
            Profile.from_dict(raw)

    def test_profile_rejects_duplicate_server_ids(self):
        raw = dict(PROFILE)
        raw["servers"] = [PROFILE["servers"][0], PROFILE["servers"][0]]
        with self.assertRaisesRegex(ConfigError, "unique"):
            Profile.from_dict(raw)

    def test_profile_requires_host_or_alias(self):
        raw = dict(PROFILE)
        raw["servers"] = [{"id": "gpu", "display_name": "GPU", "backend": "direct_ssh"}]
        with self.assertRaisesRegex(ConfigError, "ssh_alias or host"):
            Profile.from_dict(raw)

    def test_profile_rejects_option_like_ssh_tokens_and_boolean_numbers(self):
        for field in ("ssh_alias", "host", "username"):
            server = dict(PROFILE["servers"][0])
            server["ssh_alias"] = "gpu"
            server[field] = "-oProxyCommand=bad"
            raw = dict(PROFILE, servers=[server])
            with self.subTest(field=field), self.assertRaisesRegex(ConfigError, "SSH token"):
                Profile.from_dict(raw)

        for field in ("port", "connect_timeout_seconds"):
            raw = dict(PROFILE, servers=[dict(PROFILE["servers"][0], **{field: True})])
            with self.subTest(field=field), self.assertRaises(ConfigError):
                Profile.from_dict(raw)

        with self.assertRaisesRegex(ConfigError, "refresh_seconds"):
            Profile.from_dict(dict(PROFILE, refresh_seconds=True))

    def test_profile_rejects_non_finite_or_boolean_gpu_memory(self):
        for value in (True, float("nan"), float("inf"), 1001):
            raw = dict(
                PROFILE,
                servers=[
                    dict(
                        PROFILE["servers"][0],
                        backend="slurm_ssh",
                        gpu_memory_gib={"A100": value},
                    )
                ],
            )
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, "gpu_memory_gib"):
                Profile.from_dict(raw)

    def test_profile_rejects_control_characters_in_local_catalog_path(self):
        with self.assertRaisesRegex(ConfigError, "control characters"):
            Profile.from_dict(dict(PROFILE, server_config_path="D:/ssh/config\nother"))

    def test_profile_auto_sync_requires_a_catalog_path(self):
        raw = dict(PROFILE, auto_sync_servers=True)
        with self.assertRaisesRegex(ConfigError, "requires server_config_path"):
            Profile.from_dict(raw)

    def test_profile_auto_sync_rejects_non_boolean_value(self):
        raw = dict(PROFILE, auto_sync_servers="false", server_config_path="servers.toml")
        with self.assertRaisesRegex(ConfigError, "must be true or false"):
            Profile.from_dict(raw)

    def test_cache_rejects_wrong_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            path = cache.path_for("gpu-1")
            path.parent.mkdir(parents=True)
            path.write_text('{"schema_version":1,"server_id":"other","last_success_at":"x","payload":{}}', encoding="utf-8")
            self.assertIsNone(cache.load("gpu-1", connection_fingerprint="endpoint-a"))

    def test_cache_round_trip_preserves_detailed_task_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SnapshotCache(storage_paths(Path(temporary)), "lab")
            payload = {"tasks": {"active": [{"job_id": "42", "name": "实验 对照组|seed=3"}]}}

            cache.save(
                "gpu-1",
                "2026-08-26T00:00:00+00:00",
                payload,
                connection_fingerprint="endpoint-a",
            )

            self.assertEqual(
                cache.load("gpu-1", connection_fingerprint="endpoint-a")["payload"],
                payload,
            )


if __name__ == "__main__":
    unittest.main()
