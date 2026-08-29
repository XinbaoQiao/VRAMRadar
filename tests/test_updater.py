import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vram_radar.update_helper import run_update
from vram_radar.updater import download_verified_asset


class FakeDownload:
    def __init__(self, data: bytes, url: str) -> None:
        self.data = data
        self.url = url
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def geturl(self):
        return self.url

    def read(self, amount: int) -> bytes:
        chunk = self.data[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk


class UpdateDownloadTests(unittest.TestCase):
    def test_verified_asset_is_committed_only_after_size_and_hash_match(self):
        data = b"official installer bytes"
        url = "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe"
        asset = {
            "name": "VRAMRadar-Setup-0.7.0.exe",
            "url": url,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = download_verified_asset(
                asset,
                Path(temporary),
                opener=lambda _request, timeout: FakeDownload(data, url),
            )
            self.assertEqual(result.read_bytes(), data)
            self.assertFalse(result.with_suffix(result.suffix + ".part").exists())

    def test_hash_mismatch_removes_partial_stage(self):
        data = b"tampered"
        url = "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe"
        asset = {
            "name": "VRAMRadar-Setup-0.7.0.exe",
            "url": url,
            "size": len(data),
            "sha256": "00" * 32,
        }
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_verified_asset(
                    asset,
                    Path(temporary),
                    opener=lambda _request, timeout: FakeDownload(data, url),
                )
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_redirect_outside_github_is_rejected(self):
        data = b"installer"
        url = "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe"
        asset = {
            "name": "VRAMRadar-Setup-0.7.0.exe",
            "url": url,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "非 GitHub"):
                download_verified_asset(
                    asset,
                    Path(temporary),
                    opener=lambda _request, timeout: FakeDownload(data, "https://evil.example/update.exe"),
                )

    def test_failed_installer_restores_and_relaunches_previous_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_root = root / "install"
            install_root.mkdir()
            executable = install_root / "VRAMRadar.exe"
            executable.write_bytes(b"old application")
            (install_root / ".vram-radar-installed").write_text("old marker", encoding="utf-8")
            stage = root / "stage"
            stage.mkdir()
            installer = stage / "VRAMRadar-Setup-0.7.0.exe"
            installer.write_bytes(b"verified installer")
            plan = {
                "schema_version": 1,
                "pid": 123,
                "app_executable": str(executable),
                "install_root": str(install_root),
                "installer": str(installer),
                "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "version": "0.7.0",
                "activation_path": str(root / "activation.json"),
                "restart_arguments": ["--profile", "test"],
            }
            plan_path = stage / "update-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            completed_shutdown = subprocess.CompletedProcess([], 0)
            completed_installer = subprocess.CompletedProcess([], 5)
            with patch("vram_radar.update_helper._wait_for_exit", return_value=True), patch(
                "vram_radar.update_helper.subprocess.run",
                side_effect=[completed_shutdown, completed_installer],
            ), patch("vram_radar.update_helper.subprocess.Popen") as restart:
                with self.assertRaisesRegex(RuntimeError, "exit code 5"):
                    run_update(plan_path)

            self.assertEqual(executable.read_bytes(), b"old application")
            self.assertEqual(
                (install_root / ".vram-radar-installed").read_text(encoding="utf-8"),
                "old marker",
            )
            self.assertEqual(list(root.glob("install.update-backup-*")), [])
            restart.assert_called_once_with(
                [str(executable.resolve()), "--profile", "test"],
                close_fds=True,
            )


if __name__ == "__main__":
    unittest.main()
