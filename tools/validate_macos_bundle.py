from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from uuid import uuid4

from vram_radar import __version__
from vram_radar.askpass import PasswordBroker


ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = (ROOT / "work").resolve()
BUNDLE = Path(os.environ.get("VRAM_RADAR_MACOS_BUNDLE", ROOT / "dist-macos" / "VRAM Radar.app")).resolve()
CONTENTS = BUNDLE / "Contents"
EXECUTABLE = CONTENTS / "MacOS" / "VRAMRadar"
ASKPASS_EXECUTABLE = CONTENTS / "MacOS" / "VRAMRadarAskPass"
RESOURCES = CONTENTS / "Resources"
EXPECTED_BUNDLE_ID = "com.vramradar.desktop"


def safe_cleanup(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != WORK_ROOT or not resolved.name.startswith("macos-bundle-smoke-"):
        raise RuntimeError(f"refusing to remove unexpected smoke path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def assert_bundle_metadata() -> dict[str, object]:
    info_path = CONTENTS / "Info.plist"
    if not info_path.is_file() or not EXECUTABLE.is_file() or not ASKPASS_EXECUTABLE.is_file():
        raise RuntimeError(f"macOS bundle is incomplete: {BUNDLE}")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    expected = {
        "CFBundleIdentifier": EXPECTED_BUNDLE_ID,
        "CFBundleExecutable": "VRAMRadar",
        "CFBundleIconFile": "app-icon.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
    }
    mismatches = {key: {"expected": value, "actual": info.get(key)} for key, value in expected.items() if info.get(key) != value}
    if mismatches:
        raise RuntimeError("macOS bundle metadata mismatch: " + json.dumps(mismatches, ensure_ascii=False))
    return info


def assert_packaged_assets() -> None:
    pairs = (
        (ROOT / "src" / "vram_radar" / "web" / "app.js", RESOURCES / "vram_radar" / "web" / "app.js"),
        (ROOT / "src" / "vram_radar" / "web" / "localization.js", RESOURCES / "vram_radar" / "web" / "localization.js"),
        (ROOT / "src" / "vram_radar" / "web" / "app.css", RESOURCES / "vram_radar" / "web" / "app.css"),
        (ROOT / "src" / "vram_radar" / "web" / "index.html", RESOURCES / "vram_radar" / "web" / "index.html"),
        (
            ROOT / "src" / "vram_radar" / "assets" / "app-icon.png",
            RESOURCES / "vram_radar" / "assets" / "app-icon.png",
        ),
    )
    for source, packaged in pairs:
        if not packaged.is_file() or source.read_bytes() != packaged.read_bytes():
            raise RuntimeError(f"packaged asset does not match source: {source.name}")


def executable_architectures(path: Path) -> list[str]:
    result = subprocess.run(
        ["lipo", "-archs", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    architectures = result.stdout.strip().split()
    if result.returncode != 0 or not architectures:
        raise RuntimeError(f"could not identify packaged macOS executable architectures: {path.name}")
    return architectures


def validate_macos_code() -> list[str]:
    architectures = executable_architectures(EXECUTABLE)
    askpass_architectures = executable_architectures(ASKPASS_EXECUTABLE)
    if set(askpass_architectures) != set(architectures):
        raise RuntimeError("main app and askpass helper architectures do not match")
    expected = os.environ.get("VRAM_RADAR_EXPECTED_MACOS_ARCH", "").strip()
    expected_architectures = {"arm64", "x86_64"} if expected == "universal2" else ({expected} if expected else set())
    if expected_architectures and set(architectures) != expected_architectures:
        raise RuntimeError(
            f"packaged architectures {architectures} do not match expected target {sorted(expected_architectures)}"
        )
    signing_result = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(BUNDLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    if signing_result.returncode != 0:
        raise RuntimeError("packaged macOS code-sign verification failed")
    return architectures


def validate_distribution_signing() -> None:
    signature = subprocess.run(
        ["codesign", "-d", "--verbose=4", str(BUNDLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    signature_details = signature.stdout + signature.stderr
    if signature.returncode != 0 or "Authority=Developer ID Application:" not in signature_details:
        raise RuntimeError("macOS release is not signed with a Developer ID Application identity")

    stapler = subprocess.run(
        ["xcrun", "stapler", "validate", str(BUNDLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    if stapler.returncode != 0:
        raise RuntimeError("macOS release does not contain a valid stapled notarization ticket")

    gatekeeper = subprocess.run(
        ["spctl", "--assess", "--type", "execute", "--verbose=4", str(BUNDLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    if gatekeeper.returncode != 0:
        raise RuntimeError("Gatekeeper rejected the final macOS release bundle")


def validate_packaged_askpass() -> None:
    password = uuid4().hex + uuid4().hex
    with PasswordBroker(password) as broker:
        environment = os.environ.copy()
        environment.update(broker.environment)
        if password in repr(environment):
            raise RuntimeError("password leaked into packaged askpass environment")
        result = subprocess.run(
            [str(ASKPASS_EXECUTABLE), "OpenSSH password prompt"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
    if result.returncode != 0 or result.stdout != password or result.stderr:
        raise RuntimeError("packaged askpass private loopback exchange failed")


def run_bundle_smoke(home: Path, *extra_args: str, timeout: float) -> None:
    command = [
        str(EXECUTABLE),
        "--home",
        str(home),
        "--profile",
        "macos-bundle-smoke",
        "--no-auto-import",
        *extra_args,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"packaged macOS smoke failed with exit code {result.returncode}")


def validate_release_tag() -> str:
    expected = os.environ.get("VRAM_RADAR_RELEASE_TAG", f"v{__version__}").strip()
    result = subprocess.run(
        [str(EXECUTABLE), "--show-release"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    actual = result.stdout.strip()
    if result.returncode != 0 or result.stderr or actual != expected:
        raise RuntimeError(f"packaged release tag {actual!r} does not match expected {expected!r}")
    return actual


def validate_packaged_update_transport(release_tag: str) -> tuple[str, str]:
    result = subprocess.run(
        [str(EXECUTABLE), "--check-updates-json"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("packaged macOS update transport returned invalid JSON") from exc
    if result.returncode != 0 or result.stderr or not payload.get("ok"):
        code = payload.get("code") if isinstance(payload, dict) else None
        allow_rate_limit_with_sibling_proof = (
            os.environ.get("VRAM_RADAR_ALLOW_RATE_LIMIT_WITH_SIBLING_PROOF") == "1"
        )
        if code == "update_rate_limited" and allow_rate_limit_with_sibling_proof:
            return "", "rate-limited-requires-sibling-proof"
        raise RuntimeError(f"packaged macOS update transport failed: {code or 'unknown'}")
    expected_version = release_tag.removeprefix("v")
    if payload.get("current_version") != expected_version:
        raise RuntimeError("packaged macOS update transport used the wrong release identity")
    return str(payload.get("latest_version") or ""), "passed"


def main() -> int:
    if sys.platform != "darwin" or os.name != "posix":
        raise RuntimeError("macOS bundle validation must run on macOS")

    run_id = uuid4().hex
    home = WORK_ROOT / f"macos-bundle-smoke-{run_id}"
    home.mkdir(parents=True)
    try:
        info = assert_bundle_metadata()
        assert_packaged_assets()
        architectures = validate_macos_code()
        distribution_signing_required = os.environ.get("VRAM_RADAR_REQUIRE_DISTRIBUTION_SIGNING") == "1"
        if distribution_signing_required:
            validate_distribution_signing()
        validate_packaged_askpass()
        release_tag = validate_release_tag()
        latest_checked_version, update_transport_status = validate_packaged_update_transport(
            release_tag
        )
        run_bundle_smoke(home, "--show-paths", timeout=20)
        run_bundle_smoke(home, "--gui-smoke", timeout=45)
        profile_path = home / "config" / "profiles" / "macos-bundle-smoke.toml"
        if profile_path.exists():
            raise RuntimeError("empty packaged smoke unexpectedly persisted a Profile")
        print(
            json.dumps(
                {
                    "ok": True,
                    "bundle": str(BUNDLE),
                    "bundle_identifier": info["CFBundleIdentifier"],
                    "version": info["CFBundleShortVersionString"],
                    "release_tag": release_tag,
                    "architectures": architectures,
                    "askpass_architectures": architectures,
                    "codesign_verify": "passed",
                    "developer_id_signing": "passed" if distribution_signing_required else "not-required",
                    "notarization_ticket": "passed" if distribution_signing_required else "not-required",
                    "gatekeeper_assessment": "passed" if distribution_signing_required else "not-required",
                    "packaged_askpass": "passed",
                    "expected_architectures": sorted(architectures),
                    "github_update_transport": update_transport_status,
                    "latest_checked_version": latest_checked_version,
                    "assets_match_source": True,
                    "show_paths_exit": 0,
                    "cocoa_window_smoke_exit": 0,
                    "real_server_contacted": False,
                },
                indent=2,
            )
        )
        return 0
    finally:
        safe_cleanup(home)


if __name__ == "__main__":
    raise SystemExit(main())
