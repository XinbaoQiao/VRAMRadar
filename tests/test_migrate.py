"""Offline migrate package: QR token, file, optional sealed secrets."""
from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
import tempfile

from vram_radar.migrate import (
    FORMAT,
    TOKEN_PREFIX,
    assert_bundle_usable,
    build_bundle,
    bundle_to_token,
    encode_bundle_text,
    get_lan_transfer,
    materialize_profile,
    merge_profiles,
    open_secrets,
    parse_migrate_text,
    post_import_check,
    preview_bundle,
    rebind_secrets_to_profile,
    seal_secrets,
    secrets_for_import,
    token_fits_qr,
)
from vram_radar.models import ConfigError, Profile, ServerProfile
from vram_radar.shell import AppApi
from vram_radar.storage import ProfileStore, storage_paths


class MemorySecretStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, auth_ref: str) -> str | None:
        return self._data.get(auth_ref)

    def set(self, auth_ref: str, secret: str) -> None:
        self._data[auth_ref] = secret

    def delete(self, auth_ref: str) -> None:
        self._data.pop(auth_ref, None)


def _profile_with_server() -> Profile:
    server = ServerProfile(
        id="lab",
        display_name="Lab GPU",
        backend="direct_ssh",
        host="gpu.example.org",
        username="alice",
        identity_file="~/.ssh/id_ed25519",
        auth_ref="server:default:lab:login-password",
    )
    base = Profile.empty("default")
    return replace(
        base,
        display_name="迁移测试",
        ui_language="en",
        refresh_seconds=20,
        servers=(server,),
        favorite_alert_enabled=False,
    )


class MigratePackageTests(unittest.TestCase):
    def test_default_export_strips_auth_ref_and_round_trips_token(self):
        profile = _profile_with_server()
        bundle = build_bundle(profile)
        self.assertEqual(bundle["format"], FORMAT)
        self.assertFalse(bundle["includes_secrets"])
        self.assertEqual(bundle["password_slots"], 1)
        self.assertEqual(bundle["password_count"], 0)
        self.assertNotIn("auth_ref", bundle["profile"]["servers"][0])
        payload = bundle_to_token(bundle)
        self.assertTrue(payload.startswith(TOKEN_PREFIX))
        self.assertTrue(token_fits_qr(payload))
        parsed = parse_migrate_text(payload)
        restored = materialize_profile(parsed, target_profile_id="default")
        self.assertEqual(restored.display_name, "迁移测试")
        self.assertEqual(restored.ui_language, "en")
        self.assertEqual(restored.servers[0].host, "gpu.example.org")
        self.assertEqual(restored.servers[0].auth_ref, "")
        self.assertEqual(restored.servers[0].identity_file, "~/.ssh/id_ed25519")

    def test_file_json_round_trip(self):
        profile = _profile_with_server()
        bundle = build_bundle(profile)
        text = encode_bundle_text(bundle)
        parsed = parse_migrate_text(text)
        self.assertEqual(preview_bundle(parsed)["server_count"], 1)

    def test_sealed_secrets_round_trip(self):
        sealed = seal_secrets("correct-horse", {"server:default:lab:login-password": "s3cret"})
        opened = open_secrets("correct-horse", sealed)
        self.assertEqual(opened["server:default:lab:login-password"], "s3cret")
        with self.assertRaises(ConfigError):
            open_secrets("wrong-passphrase", sealed)

    def test_export_with_secrets_requires_passphrase_and_store(self):
        profile = _profile_with_server()
        store = MemorySecretStore()
        store.set("server:default:lab:login-password", "p@ss")
        with self.assertRaises(ConfigError):
            build_bundle(profile, secret_store=store, include_secrets=True)
        bundle = build_bundle(
            profile,
            secret_store=store,
            include_secrets=True,
            passphrase="long-enough",
        )
        self.assertTrue(bundle["includes_secrets"])
        self.assertEqual(bundle["profile"]["servers"][0]["auth_ref"], "server:default:lab:login-password")
        secrets = secrets_for_import(bundle, "long-enough")
        self.assertEqual(secrets["server:default:lab:login-password"], "p@ss")
        # Clear package must never embed plaintext password.
        blob = encode_bundle_text(bundle)
        self.assertNotIn("p@ss", blob)

    def test_rebind_secrets_to_new_profile_id(self):
        profile = replace(_profile_with_server(), id="old")
        profile = replace(
            profile,
            servers=(replace(profile.servers[0], auth_ref="server:old:lab:login-password"),),
        )
        rebound, secrets = rebind_secrets_to_profile(
            profile,
            {"server:old:lab:login-password": "pw"},
            target_profile_id="default",
        )
        self.assertEqual(rebound.servers[0].auth_ref, "server:default:lab:login-password")
        self.assertEqual(secrets["server:default:lab:login-password"], "pw")


class MigrateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = storage_paths(home=Path(self._tmp.name))
        self.store = ProfileStore(self.paths)
        self.secrets = MemorySecretStore()
        profile = _profile_with_server()
        self.store.save(profile)
        self.secrets.set("server:default:lab:login-password", "hunter2")

        class FakeService:
            def __init__(self) -> None:
                self.profile = profile
                self.replaced = 0

            def replace_profile(self, next_profile, cache) -> None:
                self.profile = next_profile
                self.replaced += 1

            def clear_notice(self, _code: str) -> None:
                return None

        self.service = FakeService()
        self.api = AppApi(
            profile=profile,
            store=self.store,
            paths=self.paths,
            service=self.service,
            secret_store=self.secrets,
            automatic_import_enabled=False,
        )
        # Avoid usage/cache side effects that need full shell wiring.
        self.api._configure_usage = lambda _profile: None  # type: ignore[method-assign]
        self.api._apply_cache_cleanup_schedule = lambda: None  # type: ignore[method-assign]
        self.api._reset_favorite_alerts_if_changed = lambda *_a, **_k: None  # type: ignore[method-assign]
        self.api._reset_task_alerts_if_servers_changed = lambda *_a, **_k: None  # type: ignore[method-assign]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_export_and_import_without_secrets(self):
        exported = self.api.export_migrate_bundle(False, "")
        self.assertTrue(exported["ok"])
        self.assertTrue(exported["qr_eligible"])
        self.assertFalse(exported["includes_secrets"])
        # Wipe local profile first.
        empty = Profile.empty("default")
        self.store.save(empty)
        self.api.profile = empty
        self.service.profile = empty
        self.secrets.delete("server:default:lab:login-password")
        imported = self.api.import_migrate_bundle(exported["token"], "")
        self.assertTrue(imported["ok"], imported)
        self.assertEqual(self.api.profile.servers[0].display_name, "Lab GPU")
        self.assertEqual(self.api.profile.servers[0].auth_ref, "")
        self.assertIsNone(self.secrets.get("server:default:lab:login-password"))

    def test_export_and_import_with_sealed_secrets(self):
        exported = self.api.export_migrate_bundle(True, "migrate-pass")
        self.assertTrue(exported["ok"], exported)
        self.assertTrue(exported["includes_secrets"])
        self.secrets.delete("server:default:lab:login-password")
        empty = Profile.empty("default")
        self.store.save(empty)
        self.api.profile = empty
        imported = self.api.import_migrate_bundle(exported["file_text"], "migrate-pass")
        self.assertTrue(imported["ok"], imported)
        self.assertEqual(self.api.profile.servers[0].auth_ref, "server:default:lab:login-password")
        self.assertEqual(self.secrets.get("server:default:lab:login-password"), "hunter2")


if __name__ == "__main__":
    unittest.main()


class MigrateUiPresenceTests(unittest.TestCase):
    def test_settings_html_has_migrate_panel_and_vendor_qr(self):
        root = Path(__file__).resolve().parents[1] / "src" / "vram_radar" / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        self.assertIn("id=\"migrate-panel\"", html)
        self.assertIn('data-settings-pane="migrate"', html)
        self.assertIn("id=\"settings-pane-migrate\"", html)
        # Migrate is its own left-nav pane, not nested under Servers.
        self.assertNotIn('id="migrate-panel" class="migrate-panel settings-group"', html)
        self.assertLess(html.index('data-settings-pane="updates"'), html.index('data-settings-pane="migrate"'))
        self.assertLess(html.index('data-settings-pane="migrate"'), html.index('data-settings-pane="diagnostics"'))
        self.assertTrue((root / "vendor" / "qrcode.min.js").is_file())
        self.assertTrue((root / "vendor" / "jsQR.js").is_file())
        js = (root / "app.js").read_text(encoding="utf-8")
        # QR libs load on demand via ensureMigrateQrLibs (RAM trim); not static script tags.
        self.assertIn("vendor/qrcode.min.js", js)
        self.assertIn("vendor/jsQR.js", js)
        self.assertIn("ensureMigrateQrLibs", js)
        self.assertIn("export_migrate_bundle", js)
        self.assertIn("import_migrate_bundle", js)
        self.assertNotIn("ui.migratePanel.open", js)
        self.assertIn("migrate-copy-image", html)
        self.assertIn("migrate-paste-image", html)
        self.assertIn("migrate-save-image", html)
        self.assertIn("migrate-include-secrets", html)
        self.assertIn("migrate-include-keys", html)
        self.assertIn("migrate-include-prefs", html)
        self.assertIn("migrate-server-list", html)
        self.assertIn("migrate-ttl", html)
        self.assertIn("migrate-lan-start", html)
        self.assertIn("migrate-import-confirm", html)
        self.assertIn("migrate-mode-merge", html)
        self.assertIn("migrate-passphrase", html)
        self.assertNotIn("migrate-import-text", html)
        self.assertNotIn("migrate-generate", html)
        self.assertIn("ensureMigrateQrForPane", js)
        self.assertIn("stageMigrateImportText", js)
        self.assertIn("startLanMigrateTransfer", js)
        self.assertIn("list_migrate_export_targets", js)
        self.assertIn("copyMigrateQrImage", js)


    def test_compact_export_omits_default_prefs(self):
        profile = _profile_with_server()
        bundle = build_bundle(profile)
        raw = bundle["profile"]
        self.assertNotIn("navigator_width", raw)
        self.assertNotIn("server_config_path", raw)
        self.assertNotIn("ignored_ssh_aliases", raw)
        server = raw["servers"][0]
        self.assertNotIn("enabled", server)  # default True omitted
        self.assertNotIn("port", server)  # default 22 omitted
        qr_payload = bundle_to_token(bundle)
        self.assertLess(len(qr_payload), 900)
        self.assertTrue(token_fits_qr(qr_payload))


class MigrateSelectiveAndExpiryTests(unittest.TestCase):
    def test_selective_server_export(self):
        profile = _profile_with_server()
        other = replace(
            profile.servers[0],
            id="other",
            display_name="Other",
            host="other.example.org",
            auth_ref="",
        )
        profile = replace(profile, servers=(profile.servers[0], other))
        bundle = build_bundle(profile, server_ids=["other"], include_preferences=False)
        self.assertEqual(len(bundle["profile"]["servers"]), 1)
        self.assertEqual(bundle["profile"]["servers"][0]["id"], "other")
        self.assertFalse(bundle.get("includes_preferences"))
        self.assertNotIn("ui_language", bundle["profile"])

    def test_sealed_bundle_expires(self):
        profile = _profile_with_server()
        store = MemorySecretStore()
        store.set("server:default:lab:login-password", "p@ss")
        bundle = build_bundle(
            profile,
            secret_store=store,
            include_secrets=True,
            passphrase="long-enough",
            created_at=1_700_000_000,
            ttl_seconds=600,
            package_id="pkg-1",
        )
        self.assertEqual(bundle["expires_at"], 1_700_000_000 + 600)
        self.assertEqual(bundle["package_id"], "pkg-1")
        with self.assertRaises(ConfigError):
            assert_bundle_usable(bundle, now=1_700_000_000 + 601)
        assert_bundle_usable(bundle, now=1_700_000_000 + 10)
        with self.assertRaises(ConfigError):
            assert_bundle_usable(bundle, now=1_700_000_000 + 10, consumed_ids={"pkg-1"})

    def test_merge_profiles_keeps_local_server(self):
        current = _profile_with_server()
        incoming_server = replace(
            current.servers[0],
            id="newbox",
            display_name="New",
            host="new.example.org",
            auth_ref="",
        )
        incoming = replace(current, display_name="Imported", servers=(incoming_server,), ui_language="en")
        merged = merge_profiles(current, incoming, apply_preferences=True)
        ids = {s.id for s in merged.servers}
        self.assertEqual(ids, {"lab", "newbox"})
        self.assertEqual(merged.display_name, "Imported")
        self.assertEqual(merged.ui_language, "en")

    def test_post_import_check_flags_missing_password_and_key(self):
        profile = _profile_with_server()
        store = MemorySecretStore()
        check = post_import_check(profile, store)
        self.assertEqual(check["missing_pw_count"], 1)
        self.assertEqual(check["missing_key_count"], 1)
        store.set("server:default:lab:login-password", "x")
        check2 = post_import_check(replace(profile, servers=(replace(profile.servers[0], identity_file=""),)), store)
        self.assertEqual(check2["missing_pw_count"], 0)
        self.assertEqual(check2["missing_key_count"], 0)

    def test_lan_transfer_round_trip_once(self):
        profile = _profile_with_server()
        bundle = build_bundle(profile)
        payload = encode_bundle_text(bundle)
        transfer = get_lan_transfer()
        transfer.stop()
        status = transfer.start(payload, ttl_seconds=120, max_downloads=1)
        self.assertTrue(status["active"])
        self.assertTrue(status["url"].startswith("http://"))
        # Fetch via loopback in case LAN IP guess is firewalled in CI.
        local_url = f"http://127.0.0.1:{status['port']}/m/{status['token']}"
        fetched = transfer.fetch(local_url)
        self.assertIn("vramradar-migrate/v1", fetched)
        with self.assertRaises(ConfigError):
            transfer.fetch(status["url"])
        transfer.stop()


class MigrateApiExtendedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = storage_paths(home=Path(self._tmp.name))
        self.store = ProfileStore(self.paths)
        self.secrets = MemorySecretStore()
        profile = _profile_with_server()
        self.store.save(profile)
        self.secrets.set("server:default:lab:login-password", "hunter2")

        class FakeService:
            def __init__(self) -> None:
                self.profile = profile
                self.replaced = 0

            def replace_profile(self, next_profile, cache) -> None:
                self.profile = next_profile
                self.replaced += 1

            def clear_notice(self, _code: str) -> None:
                return None

        self.service = FakeService()
        self.api = AppApi(
            profile=profile,
            store=self.store,
            paths=self.paths,
            service=self.service,
            secret_store=self.secrets,
            automatic_import_enabled=False,
        )
        self.api._configure_usage = lambda _profile: None  # type: ignore[method-assign]
        self.api._apply_cache_cleanup_schedule = lambda: None  # type: ignore[method-assign]
        self.api._reset_favorite_alerts_if_changed = lambda *_a, **_k: None  # type: ignore[method-assign]
        self.api._reset_task_alerts_if_servers_changed = lambda *_a, **_k: None  # type: ignore[method-assign]

    def tearDown(self) -> None:
        try:
            get_lan_transfer().stop()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_merge_import_keeps_existing_server(self):
        exported = self.api.export_migrate_bundle(False, "", False, ["lab"], True, None)
        self.assertTrue(exported["ok"], exported)
        # Add a local-only server, then merge import of lab-only package.
        local = replace(
            self.api.profile.servers[0],
            id="localonly",
            display_name="Local",
            host="local.example",
            auth_ref="",
            identity_file="",
        )
        self.api.profile = replace(self.api.profile, servers=(local,))
        self.store.save(self.api.profile)
        imported = self.api.import_migrate_bundle(exported["token"], "", "merge", True)
        self.assertTrue(imported["ok"], imported)
        ids = {s.id for s in self.api.profile.servers}
        self.assertEqual(ids, {"localonly", "lab"})
        self.assertIn("check", imported)

    def test_sealed_import_consumes_package_id(self):
        exported = self.api.export_migrate_bundle(True, "migrate-pass", False, None, True, 1800)
        self.assertTrue(exported["ok"], exported)
        self.assertTrue(exported["package_id"])
        first = self.api.import_migrate_bundle(exported["file_text"], "migrate-pass", "overwrite", True)
        self.assertTrue(first["ok"], first)
        second = self.api.preview_migrate_payload(exported["file_text"])
        self.assertFalse(second["ok"])

