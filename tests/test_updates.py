import json
import ssl
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import ANY, patch, sentinel

from vram_radar.updates import (
    LATEST_RELEASE_API,
    RELEASES_API,
    _MACOS_SYSTEM_CA_FILE,
    _macos_system_ca_file_context,
    check_latest_release,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.payload


class UpdateCheckTests(unittest.TestCase):
    def test_macos_default_opener_uses_native_system_trust(self):
        payload = {
            "tag_name": "v0.8.3",
            "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.8.3",
            "draft": False,
            "prerelease": False,
        }
        with patch(
            "vram_radar.updates._macos_system_trust_context",
            return_value=sentinel.context,
        ) as system_trust, patch(
            "vram_radar.updates.urlopen",
            return_value=FakeResponse(payload),
        ) as open_release:
            result = check_latest_release(
                "0.8.4",
                current_tag="v0.8.4",
                platform_name="darwin",
            )

        self.assertTrue(result["ok"])
        system_trust.assert_called_once_with()
        open_release.assert_called_once_with(ANY, timeout=4.0, context=sentinel.context)

    def test_macos_native_tls_failure_uses_explicit_system_ca_file_fallback(self):
        payload = {
            "tag_name": "v0.8.8",
            "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.8.8",
            "draft": False,
            "prerelease": False,
        }

        def open_release(_request, *, timeout, context):
            self.assertEqual(timeout, 4.0)
            if context is sentinel.native_context:
                raise URLError(ssl.SSLCertVerificationError(1, "native trust failed"))
            self.assertIs(context, sentinel.ca_file_context)
            return FakeResponse(payload)

        with patch(
            "vram_radar.updates._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updates._macos_system_ca_file_context",
            return_value=sentinel.ca_file_context,
        ) as ca_fallback, patch(
            "vram_radar.updates.urlopen",
            side_effect=open_release,
        ) as urlopen_mock, self.assertLogs("vram_radar", level="WARNING"):
            result = check_latest_release(
                "0.8.7",
                current_tag="v0.8.7",
                platform_name="darwin",
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        ca_fallback.assert_called_once_with()
        self.assertEqual(urlopen_mock.call_count, 2)

    def test_macos_non_tls_failure_does_not_retry_with_ca_file(self):
        with patch(
            "vram_radar.updates._macos_system_trust_context",
            return_value=sentinel.native_context,
        ), patch(
            "vram_radar.updates._macos_system_ca_file_context",
            return_value=sentinel.ca_file_context,
        ) as ca_fallback, patch(
            "vram_radar.updates.urlopen",
            side_effect=URLError("offline"),
        ):
            result = check_latest_release(
                "0.8.7",
                current_tag="v0.8.7",
                platform_name="darwin",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "update_network_failed")
        ca_fallback.assert_not_called()

    def test_macos_missing_native_trust_uses_explicit_ca_file(self):
        payload = {
            "tag_name": "v0.8.8",
            "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.8.8",
            "draft": False,
            "prerelease": False,
        }
        with patch(
            "vram_radar.updates._macos_system_trust_context",
            side_effect=RuntimeError("truststore unavailable"),
        ), patch(
            "vram_radar.updates._macos_system_ca_file_context",
            return_value=sentinel.ca_file_context,
        ) as ca_fallback, patch(
            "vram_radar.updates.urlopen",
            return_value=FakeResponse(payload),
        ) as open_release, self.assertLogs("vram_radar", level="WARNING"):
            result = check_latest_release(
                "0.8.7",
                current_tag="v0.8.7",
                platform_name="darwin",
            )

        self.assertTrue(result["ok"])
        ca_fallback.assert_called_once_with()
        open_release.assert_called_once_with(
            ANY,
            timeout=4.0,
            context=sentinel.ca_file_context,
        )

    def test_macos_ca_file_context_does_not_depend_on_environment(self):
        with patch("vram_radar.updates.ssl.create_default_context", return_value=sentinel.context) as create:
            context = _macos_system_ca_file_context()

        self.assertIs(context, sentinel.context)
        create.assert_called_once_with(cafile=_MACOS_SYSTEM_CA_FILE)

    def test_tls_failure_is_actionable_and_logged_without_exception_text(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            raise URLError(ssl.SSLCertVerificationError(1, "private-proxy.example"))

        with self.assertLogs("vram_radar", level="WARNING") as logs:
            result = check_latest_release("0.8.3", current_tag="v0.8.3", opener=opener)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "update_tls_failed")
        self.assertEqual(result["error"], "无法验证 GitHub 的 HTTPS 证书")
        self.assertNotIn("private-proxy.example", "\n".join(logs.output))

    def test_rate_limit_reports_bounded_http_status(self):
        def opener(request, *, timeout):
            raise HTTPError(request.full_url, 429, "limited", {}, None)

        result = check_latest_release("0.8.3", current_tag="v0.8.3", opener=opener)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "update_rate_limited")
        self.assertEqual(result["http_status"], 429)

    def test_newer_stable_release_is_reported(self):
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                {
                    "tag_name": "v0.4.1",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.4.1",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-08-29T00:00:00Z",
                }
            )

        result = check_latest_release("0.4.0", current_tag="v0.4.0", opener=opener)

        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "0.4.1")
        self.assertEqual(requests[0][0].full_url, LATEST_RELEASE_API)
        self.assertEqual(requests[0][0].get_header("User-agent"), "VRAMRadar/0.4.0")
        self.assertIsNone(requests[0][0].get_header("Authorization"))
        self.assertEqual(requests[0][1], 4.0)

    def test_same_or_older_release_does_not_prompt(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                {
                    "tag_name": "v0.3.9",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.3.9",
                    "draft": False,
                    "prerelease": False,
                }
            )

        result = check_latest_release("0.4.0", opener=opener)

        self.assertTrue(result["ok"])
        self.assertFalse(result["update_available"])

    def test_same_tag_replacement_prompts_when_exact_release_commit_changed(self):
        installed_commit = "1" * 40
        replacement_commit = "2" * 40

        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                {
                    "tag_name": "v0.7.0",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "target_commitish": replacement_commit,
                    "draft": False,
                    "prerelease": False,
                }
            )

        result = check_latest_release(
            "0.7.0",
            current_tag="v0.7.0",
            current_commit=installed_commit,
            opener=opener,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        self.assertTrue(result["replacement_available"])
        self.assertEqual(result["current_build"], installed_commit)
        self.assertEqual(result["latest_build"], replacement_commit)

    def test_same_tag_does_not_prompt_without_an_exact_changed_commit(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                {
                    "tag_name": "v0.7.0",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "target_commitish": "main",
                    "draft": False,
                    "prerelease": False,
                }
            )

        result = check_latest_release(
            "0.7.0",
            current_tag="v0.7.0",
            current_commit="1" * 40,
            opener=opener,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["update_available"])
        self.assertFalse(result["replacement_available"])

    def test_untrusted_release_url_fails_closed(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                {
                    "tag_name": "v9.9.9",
                    "html_url": "https://example.com/download",
                    "draft": False,
                    "prerelease": False,
                }
            )

        result = check_latest_release("0.4.0", opener=opener)

        self.assertFalse(result["ok"])
        self.assertFalse(result["update_available"])

    def test_prerelease_payload_is_not_treated_as_latest_stable(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                {
                    "tag_name": "v0.5.0-beta.1",
                    "html_url": "https://github.com/example-org/VRAMRadar/releases/tag/v0.5.0-beta.1",
                    "draft": False,
                    "prerelease": True,
                }
            )

        result = check_latest_release("0.4.0", opener=opener)

        self.assertFalse(result["ok"])
        self.assertFalse(result["update_available"])

    def test_packaged_macos_beta_reports_a_newer_beta(self):
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                [
                    {
                        "tag_name": "v0.4.0-macos-beta.4",
                        "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.4.0-macos-beta.4",
                        "draft": False,
                        "prerelease": True,
                        "published_at": "2026-08-30T00:00:00Z",
                    },
                    {
                        "tag_name": "v0.4.0-macos-beta.3",
                        "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.4.0-macos-beta.3",
                        "draft": False,
                        "prerelease": True,
                    },
                    {
                        "tag_name": "v0.5.0-preview.1",
                        "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.5.0-preview.1",
                        "draft": False,
                        "prerelease": True,
                    },
                ]
            )

        result = check_latest_release(
            "0.4.0",
            current_tag="v0.4.0-macos-beta.3",
            opener=opener,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "0.4.0-macos-beta.4")
        self.assertEqual(result["current_version"], "0.4.0-macos-beta.3")
        self.assertEqual(requests[0][0].full_url, RELEASES_API)

    def test_packaged_macos_beta_does_not_prompt_for_itself(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(
                [
                    {
                        "tag_name": "v0.4.0-macos-beta.3",
                        "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.4.0-macos-beta.3",
                        "draft": False,
                        "prerelease": True,
                    },
                    {
                        "tag_name": "v0.3.9",
                        "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.3.9",
                        "draft": False,
                        "prerelease": False,
                    },
                ]
            )

        result = check_latest_release("0.4.0", current_tag="v0.4.0-macos-beta.3", opener=opener)

        self.assertTrue(result["ok"])
        self.assertFalse(result["update_available"])

    def test_official_windows_asset_is_returned_only_with_exact_digest_url_and_size(self):
        digest = "ab" * 32

        def opener(_request, *, timeout):
            return FakeResponse(
                {
                    "tag_name": "v0.7.0",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "VRAMRadar-Setup-0.7.0.exe",
                            "browser_download_url": "https://github.com/example-owner/VRAMRadar/releases/download/v0.7.0/VRAMRadar-Setup-0.7.0.exe",
                            "digest": f"sha256:{digest}",
                            "size": 12345,
                        }
                    ],
                }
            )

        result = check_latest_release("0.6.1", current_tag="v0.6.1", opener=opener, platform_name="win32")

        self.assertEqual(result["asset"]["sha256"], digest)
        self.assertEqual(result["asset"]["size"], 12345)

    def test_asset_with_untrusted_download_url_is_not_executable(self):
        def opener(_request, *, timeout):
            return FakeResponse(
                {
                    "tag_name": "v0.7.0",
                    "html_url": "https://github.com/example-owner/VRAMRadar/releases/tag/v0.7.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "VRAMRadar-Setup-0.7.0.exe",
                            "browser_download_url": "https://example.com/VRAMRadar-Setup-0.7.0.exe",
                            "digest": f"sha256:{'ab' * 32}",
                            "size": 12345,
                        }
                    ],
                }
            )

        result = check_latest_release("0.6.1", current_tag="v0.6.1", opener=opener, platform_name="win32")
        self.assertTrue(result["update_available"])
        self.assertIsNone(result["asset"])


if __name__ == "__main__":
    unittest.main()
