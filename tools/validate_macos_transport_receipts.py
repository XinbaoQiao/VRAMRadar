from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_ARCHITECTURES = {"arm64", "x86_64"}
ALLOWED_STATUSES = {"passed", "rate-limited-requires-sibling-proof"}


def validate_receipts(directory: Path, release_tag: str) -> dict[str, object]:
    receipts = sorted(directory.glob("macos-validation-*.json"))
    if len(receipts) != 2:
        raise RuntimeError("expected exactly two native macOS validation receipts")

    observed_architectures: set[str] = set()
    statuses: dict[str, str] = {}
    for receipt_path in receipts:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        architectures = payload.get("expected_architectures")
        if not isinstance(architectures, list) or len(architectures) != 1:
            raise RuntimeError(f"invalid architecture receipt: {receipt_path.name}")
        architecture = architectures[0]
        if architecture not in EXPECTED_ARCHITECTURES or architecture in observed_architectures:
            raise RuntimeError(f"unexpected or duplicate architecture: {architecture}")
        if payload.get("release_tag") != release_tag:
            raise RuntimeError(f"release tag mismatch: {receipt_path.name}")
        status = payload.get("github_update_transport")
        if status not in ALLOWED_STATUSES:
            raise RuntimeError(f"invalid update transport status: {status}")
        observed_architectures.add(architecture)
        statuses[architecture] = status

    if observed_architectures != EXPECTED_ARCHITECTURES:
        raise RuntimeError("native macOS architecture receipts are incomplete")
    if "passed" not in statuses.values():
        raise RuntimeError("at least one native macOS package must pass the real GitHub transport")
    return {"release_tag": release_tag, "transport_by_architecture": statuses}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--release-tag", required=True)
    args = parser.parse_args()
    print(json.dumps(validate_receipts(args.directory, args.release_tag), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
