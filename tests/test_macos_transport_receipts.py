import json
from pathlib import Path
import tempfile
import unittest

from tools.validate_macos_transport_receipts import validate_receipts


class MacOSTransportReceiptTests(unittest.TestCase):
    def write_receipt(self, root: Path, architecture: str, status: str) -> None:
        payload = {
            "expected_architectures": [architecture],
            "release_tag": "v0.8.5",
            "github_update_transport": status,
        }
        (root / f"macos-validation-{architecture}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_one_real_pass_can_support_one_explicit_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.write_receipt(root, "arm64", "rate-limited-requires-sibling-proof")
            self.write_receipt(root, "x86_64", "passed")

            result = validate_receipts(root, "v0.8.5")

        self.assertEqual(result["transport_by_architecture"]["x86_64"], "passed")

    def test_two_rate_limits_fail_without_real_transport_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.write_receipt(root, "arm64", "rate-limited-requires-sibling-proof")
            self.write_receipt(root, "x86_64", "rate-limited-requires-sibling-proof")

            with self.assertRaisesRegex(RuntimeError, "at least one"):
                validate_receipts(root, "v0.8.5")

    def test_wrong_release_tag_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.write_receipt(root, "arm64", "passed")
            self.write_receipt(root, "x86_64", "passed")

            with self.assertRaisesRegex(RuntimeError, "release tag mismatch"):
                validate_receipts(root, "v0.8.6")


if __name__ == "__main__":
    unittest.main()
