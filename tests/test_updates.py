import json
import unittest

from vram_radar.updates import LATEST_RELEASE_API, RELEASES_API, check_latest_release


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
