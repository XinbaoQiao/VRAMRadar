import base64
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch

from vram_radar.ssh_keys import (
    INSTALL_AUTHORIZED_KEY_SCRIPT,
    SshKeySetupError,
    normalize_public_key,
    prepare_existing_key,
    prepare_generated_key,
    remove_generated_key,
    rollback_authorized_key_script,
)


KEY_TYPE = b"ssh-ed25519"
PUBLIC_BLOB = base64.b64encode(struct.pack(">I", len(KEY_TYPE)) + KEY_TYPE + b"x" * 32).decode("ascii")
PUBLIC_LINE = f"ssh-ed25519 {PUBLIC_BLOB}"


class SshKeyTests(unittest.TestCase):
    def test_public_key_is_validated_and_comments_are_not_deployed(self):
        normalized = normalize_public_key(f"{PUBLIC_LINE} alice@private-host\n")

        self.assertEqual(normalized, PUBLIC_LINE)
        self.assertNotIn("alice", normalized)

    def test_public_key_rejects_multiline_and_unsupported_types(self):
        with self.assertRaises(SshKeySetupError):
            normalize_public_key(f"{PUBLIC_LINE}\n{PUBLIC_LINE}\n")
        with self.assertRaises(SshKeySetupError):
            normalize_public_key(f"ssh-dss {PUBLIC_BLOB}")

    def test_existing_key_requires_a_matching_public_key_when_derivable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "id_ed25519"
            public = root / "id_ed25519.pub"
            private.write_text("PRIVATE-CANARY", encoding="utf-8")
            public.write_text(PUBLIC_LINE + "\n", encoding="utf-8")
            if os.name != "nt":
                private.chmod(0o600)

            with patch("vram_radar.ssh_keys._derive_public_key", return_value=PUBLIC_LINE):
                prepared = prepare_existing_key(str(private), str(public))

            self.assertEqual(prepared.private_path, private.resolve())
            self.assertEqual(prepared.public_line, PUBLIC_LINE)
            self.assertFalse(prepared.generated)

            with patch(
                "vram_radar.ssh_keys._derive_public_key",
                return_value=f"ssh-ed25519 {base64.b64encode(b'x' * 40).decode('ascii')}",
            ):
                with self.assertRaisesRegex(SshKeySetupError, "不匹配"):
                    prepare_existing_key(str(private), str(public))

    def test_generated_key_never_overwrites_and_can_be_removed_exactly(self):
        def generate(argv, **_options):
            target = Path(argv[argv.index("-f") + 1])
            target.write_text("PRIVATE-CANARY", encoding="utf-8")
            Path(f"{target}.pub").write_text(PUBLIC_LINE + " generated\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        def protect(path):
            if os.name != "nt":
                path.chmod(0o600)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "keys"
            with (
                patch("vram_radar.ssh_keys._ssh_keygen", return_value="ssh-keygen"),
                patch("vram_radar.ssh_keys._run_keygen", side_effect=generate),
                patch("vram_radar.ssh_keys.protect_private_key", side_effect=protect),
            ):
                first = prepare_generated_key(root, "local", "gpu")
            self.assertTrue(first.generated)
            self.assertTrue(first.private_path.is_file())
            self.assertTrue(first.public_path and first.public_path.is_file())

            with patch("vram_radar.ssh_keys._derive_public_key", return_value=PUBLIC_LINE):
                second = prepare_generated_key(root, "local", "gpu")
            self.assertFalse(second.generated)
            self.assertEqual(second.private_path, first.private_path)

            self.assertTrue(remove_generated_key(first))
            self.assertFalse(first.private_path.exists())
            self.assertFalse(first.public_path and first.public_path.exists())

    def test_remote_scripts_are_bounded_to_public_key_and_restore_creation_state(self):
        rollback = rollback_authorized_key_script(ssh_existed=False, auth_existed=False)

        for script in (INSTALL_AUTHORIZED_KEY_SCRIPT, rollback):
            self.assertIn("authorized_keys", script)
            self.assertIn("read -r key_type key_blob", script)
            self.assertNotIn("PRIVATE-CANARY", script)
        self.assertIn("[ 0 -eq 0 ]", rollback)
        self.assertIn("rmdir", rollback)

    @unittest.skipUnless(shutil.which("bash"), "bash is required for remote-script syntax validation")
    def test_remote_scripts_parse_with_the_system_bash_contract(self):
        for script in (
            INSTALL_AUTHORIZED_KEY_SCRIPT,
            rollback_authorized_key_script(ssh_existed=True, auth_existed=True, auth_mode="640"),
        ):
            parsed = subprocess.run(
                [shutil.which("bash") or "bash", "-n", "-c", script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(parsed.returncode, 0, parsed.stderr.decode(errors="replace"))

    @unittest.skipIf(os.name == "nt", "POSIX mode check runs on native macOS/Linux validation")
    def test_generated_private_key_has_posix_user_only_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_generated_key(Path(temporary) / "keys", "local", "gpu")
            self.assertEqual(prepared.private_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(prepared.private_path.parent.stat().st_mode & 0o777, 0o700)
            remove_generated_key(prepared)

    @unittest.skipIf(os.name == "nt", "POSIX existing-key mode check runs on native macOS/Linux validation")
    def test_existing_posix_private_key_with_broad_permissions_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "id_ed25519"
            public = Path(f"{private}.pub")
            private.write_text("PRIVATE-CANARY", encoding="utf-8")
            public.write_text(PUBLIC_LINE + "\n", encoding="utf-8")
            private.chmod(0o644)
            with self.assertRaisesRegex(SshKeySetupError, "chmod 600"):
                prepare_existing_key(str(private), str(public))


if __name__ == "__main__":
    unittest.main()
