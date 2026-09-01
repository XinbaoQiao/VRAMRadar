import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch, sentinel
from urllib.error import URLError

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
    @staticmethod
    def asset(data: bytes) -> tuple[dict[str, object], str]:
        url = "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe"
        return ({
            "name": "VRAMRadar-Setup-0.7.0.exe",
            "url": url,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }, url)

    def test_macos_download_uses_native_system_trust(self):
        data = b"official macOS archive"
        asset, url = self.asset(data)
        opener = Mock(return_value=FakeDownload(data, url))
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.updater._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updater._macos_system_ca_file_context",
        ) as fallback:
            result = download_verified_asset(
                asset,
                Path(temporary),
                opener=opener,
                platform_name="darwin",
            )
            downloaded = result.read_bytes()

        self.assertEqual(downloaded, data)
        self.assertIs(opener.call_args.kwargs["context"], sentinel.native_context)
        fallback.assert_not_called()

    def test_macos_native_tls_failure_uses_ca_file_fallback(self):
        data = b"official macOS archive"
        asset, url = self.asset(data)
        certificate_error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        opener = Mock(side_effect=[certificate_error, FakeDownload(data, url)])
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.updater._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updater._macos_system_ca_file_context",
            return_value=sentinel.ca_file_context,
        ):
            result = download_verified_asset(
                asset,
                Path(temporary),
                opener=opener,
                platform_name="darwin",
            )
            downloaded = result.read_bytes()

        self.assertEqual(downloaded, data)
        self.assertEqual(opener.call_count, 2)
        self.assertIs(opener.call_args_list[1].kwargs["context"], sentinel.ca_file_context)

    def test_macos_non_tls_failure_does_not_retry(self):
        data = b"official macOS archive"
        asset, _url = self.asset(data)
        opener = Mock(side_effect=URLError("network unavailable"))
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.updater._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updater._macos_system_ca_file_context",
        ) as fallback:
            with self.assertRaises(URLError):
                download_verified_asset(
                    asset,
                    Path(temporary),
                    opener=opener,
                    platform_name="darwin",
                )
            self.assertEqual(list(Path(temporary).iterdir()), [])

        opener.assert_called_once()
        fallback.assert_not_called()

    def test_macos_exhausted_tls_trust_is_actionable_and_cleans_stage(self):
        data = b"official macOS archive"
        asset, _url = self.asset(data)
        certificate_error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        opener = Mock(side_effect=[certificate_error, certificate_error])
        with tempfile.TemporaryDirectory() as temporary, patch(
            "vram_radar.updater._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updater._macos_system_ca_file_context",
            return_value=sentinel.ca_file_context,
        ):
            with self.assertRaisesRegex(RuntimeError, "无法验证 GitHub 下载服务器"):
                download_verified_asset(
                    asset,
                    Path(temporary),
                    opener=opener,
                    platform_name="darwin",
                )
            self.assertEqual(list(Path(temporary).iterdir()), [])

        self.assertEqual(opener.call_count, 2)

    def test_validation_update_keeps_installer_out_of_user_registration(self):
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
                "restart_arguments": ["--profile", "test", "--home", str(root / "home"), "--no-auto-import"],
                "validation_mode": True,
            }
            plan_path = stage / "update-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0)

            def run_command(command, **_kwargs):
                if command[0] == str(installer.resolve()):
                    install_root.mkdir()
                    executable.write_bytes(b"updated application")
                    (install_root / ".vram-radar-installed").write_text(
                        "updated marker",
                        encoding="utf-8",
                    )
                return completed

            with patch("vram_radar.update_helper._wait_for_exit", return_value=True), patch(
                "vram_radar.update_helper.subprocess.run",
                side_effect=run_command,
            ) as run, patch("vram_radar.update_helper.subprocess.Popen"):
                run_update(plan_path)

        installer_command = run.call_args_list[1].args[0]
        self.assertIn("/VRAMRADARVALIDATION=1", installer_command)

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

    def test_download_reports_real_monotonic_byte_progress(self):
        data = b"0123456789" * 200_000
        url = "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe"
        asset = {
            "name": "VRAMRadar-Setup-0.7.0.exe",
            "url": url,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        progress: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temporary:
            download_verified_asset(
                asset,
                Path(temporary),
                opener=lambda _request, timeout: FakeDownload(data, url),
                progress_callback=lambda downloaded, total: progress.append((downloaded, total)),
            )

        self.assertEqual(progress[0], (0, len(data)))
        self.assertEqual(progress[-1], (len(data), len(data)))
        self.assertEqual([item[0] for item in progress], sorted(item[0] for item in progress))

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
