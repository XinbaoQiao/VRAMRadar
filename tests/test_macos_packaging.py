import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.validate_macos_bundle import TLS_ENVIRONMENT_KEYS, finder_like_environment


ROOT = Path(__file__).resolve().parents[1]


class MacOSPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = (ROOT / "packaging" / "vram-radar.spec").read_text(encoding="utf-8")
        cls.build_script = (ROOT / "Build-VramRadar-macOS.sh").read_text(encoding="utf-8")
        cls.validator = (ROOT / "tools" / "validate_macos_bundle.py").read_text(encoding="utf-8")
        cls.icon_builder = (ROOT / "tools" / "build_icon.py").read_text(encoding="utf-8")
        cls.lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        cls.pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "publish-release.yml").read_text(encoding="utf-8")
        cls.signer = (ROOT / "tools" / "sign_notarize_macos.sh").read_text(encoding="utf-8")
        cls.packager = (ROOT / "tools" / "package_macos_release.sh").read_text(encoding="utf-8")
        cls.combined_packager = (ROOT / "tools" / "package_combined_macos_release.sh").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.release_notes = (ROOT / "docs" / "release-notes-v0.4.3.md").read_text(encoding="utf-8")

    def test_spec_selects_cocoa_keychain_and_app_bundle_on_macos(self) -> None:
        self.assertIn('sys.platform == "darwin"', self.spec)
        self.assertIn('"webview.platforms.cocoa"', self.spec)
        self.assertIn('"keyring.backends.macOS"', self.spec)
        self.assertIn('name="VRAM Radar.app"', self.spec)
        self.assertIn('name="VRAMRadarAskPass"', self.spec)
        self.assertIn('bundle_identifier="com.vramradar.desktop"', self.spec)
        self.assertIn('"universal2"', self.spec)
        self.assertIn('target_arch=macos_target_arch if is_macos else None', self.spec)
        self.assertIn('VRAM_RADAR_RELEASE_TAG', self.spec)
        self.assertIn('_build_info.json', self.spec)
        self.assertIn('"LSApplicationCategoryType": "public.app-category.utilities"', self.spec)

    def test_build_script_is_native_macos_only_and_frozen(self) -> None:
        self.assertIn('[[ "$(uname -s)" != "Darwin" ]]', self.build_script)
        self.assertIn("uv sync --extra build --frozen", self.build_script)
        self.assertIn("packaging/vram-radar.spec", self.build_script)
        self.assertIn("dist-macos/VRAM Radar.app", self.build_script)
        self.assertIn("--target-arch=arm64|--target-arch=x86_64|--target-arch=universal2", self.build_script)

    def test_icon_builder_emits_windows_and_macos_formats(self) -> None:
        self.assertIn('TARGET_ICO = ROOT / "packaging" / "app-icon.ico"', self.icon_builder)
        self.assertIn('TARGET_ICNS = ROOT / "packaging" / "app-icon.icns"', self.icon_builder)
        self.assertIn('format="ICNS"', self.icon_builder)

    def test_locked_environment_contains_cocoa_runtime_dependencies(self) -> None:
        for package in (
            'name = "pyobjc-core"',
            'name = "pyobjc-framework-cocoa"',
            'name = "pyobjc-framework-quartz"',
            'name = "pyobjc-framework-security"',
            'name = "pyobjc-framework-webkit"',
        ):
            self.assertIn(package, self.lock)
        self.assertIn("name = \"truststore\"", self.lock)
        self.assertIn("truststore>=0.10.4,<0.11; sys_platform == 'darwin'", self.pyproject)

    def test_bundle_validator_reaches_the_cocoa_window_without_servers(self) -> None:
        self.assertIn('"--no-auto-import"', self.validator)
        self.assertIn('run_bundle_smoke(home, "--gui-smoke"', self.validator)
        self.assertIn("validate_packaged_askpass()", self.validator)
        self.assertIn("validate_release_tag()", self.validator)
        self.assertIn("validate_packaged_update_transport(", self.validator)
        self.assertIn('"SSL_CERT_FILE"', self.validator)
        self.assertIn('"SSL_CERT_DIR"', self.validator)
        self.assertIn('"REQUESTS_CA_BUNDLE"', self.validator)
        self.assertIn('env=finder_like_environment()', self.validator)
        self.assertIn('"finder_tls_environment": "sanitized"', self.validator)
        self.assertIn('run_bundle_smoke(home, "--gui-update-smoke", timeout=60)', self.validator)
        self.assertIn('"cocoa_update_bridge_smoke_exit": 0', self.validator)
        self.assertIn('code == "update_rate_limited"', self.validator)
        self.assertIn('"rate-limited-requires-sibling-proof"', self.validator)
        self.assertIn('"--download-published-update-smoke-json"', self.validator)
        self.assertIn('"github_update_transport": update_transport_status', self.validator)
        self.assertIn('"github_update_download": update_transport_status', self.validator)
        self.assertIn("packaged macOS updater downloaded the wrong release asset", self.validator)
        self.assertIn("packaged macOS update transport did not use the published baseline", self.validator)
        self.assertIn('"expected_architectures": sorted(architectures)', self.validator)
        self.assertIn('ASKPASS_EXECUTABLE = CONTENTS / "MacOS" / "VRAMRadarAskPass"', self.validator)
        self.assertIn("executable_architectures(EXECUTABLE)", self.validator)
        self.assertIn("executable_architectures(ASKPASS_EXECUTABLE)", self.validator)
        self.assertIn("VRAM_RADAR_EXPECTED_MACOS_ARCH", self.validator)
        self.assertIn('["codesign", "--verify", "--deep", "--strict", str(BUNDLE)]', self.validator)
        self.assertIn("VRAM_RADAR_REQUIRE_DISTRIBUTION_SIGNING", self.validator)
        self.assertIn("Authority=Developer ID Application:", self.validator)
        self.assertIn('["xcrun", "stapler", "validate", str(BUNDLE)]', self.validator)
        self.assertIn('["spctl", "--assess", "--type", "execute"', self.validator)
        self.assertIn('"real_server_contacted": False', self.validator)
        self.assertIn('if sys.platform != "darwin"', self.validator)

    def test_bundle_validator_removes_shell_only_ca_state(self) -> None:
        injected = {key: f"secret-{index}" for index, key in enumerate(TLS_ENVIRONMENT_KEYS)}
        with patch.dict(os.environ, {**injected, "VRAM_RADAR_RELEASE_TAG": "v0.8.8"}, clear=True):
            environment = finder_like_environment()

        for key in TLS_ENVIRONMENT_KEYS:
            self.assertNotIn(key, environment)
        self.assertEqual(environment["VRAM_RADAR_RELEASE_TAG"], "v0.8.8")

    def test_ci_builds_validated_native_apple_silicon_and_intel_packages_without_secrets(self) -> None:
        self.assertIn("runner: macos-14", self.workflow)
        self.assertIn("runner: macos-15-intel", self.workflow)
        self.assertIn("VRAM_RADAR_RELEASE_TAG: ${{ inputs.release_tag }}", self.workflow)
        self.assertIn("VRAM_RADAR_EXPECTED_MACOS_ARCH: ${{ matrix.arch }}", self.workflow)
        self.assertIn("tools\\validate_packaged_tray.py", self.workflow)
        self.assertIn("tools\\benchmark_webview_ui.py --timeout-seconds 120", self.workflow)
        self.assertIn("tools/benchmark_webview_ui.py --timeout-seconds 120", self.workflow)
        self.assertIn("VRAMRadar-*-macos-${{ matrix.arch }}.zip", self.workflow)
        self.assertIn("bash tools/package_macos_release.sh", self.workflow)
        self.assertNotIn("secrets.MACOS_", self.workflow)
        self.assertNotIn("secrets.APPLE_", self.workflow)
        self.assertNotIn('VRAM_RADAR_REQUIRE_DISTRIBUTION_SIGNING: "1"', self.workflow)
        self.assertNotIn("bash tools/sign_notarize_macos.sh", self.workflow)
        self.assertNotIn("--prerelease", self.workflow)

    def test_unsigned_macos_packager_emits_native_archive_and_checksum(self) -> None:
        for contract in (
            '[[ "$(uname -s)" != "Darwin" ]]',
            'RELEASE_ARCH must be arm64 or x86_64',
            'dist-macos/VRAM Radar.app',
            'ditto -c -k --sequesterRsrc --keepParent',
            'shasum -a 256',
            'VRAMRadar-${release_version}-macos-${release_arch}.zip',
        ):
            self.assertIn(contract, self.packager)

    def test_combined_macos_packager_preserves_both_validated_native_apps(self) -> None:
        for contract in (
            'shasum -a 256 -c',
            'plutil -extract CFBundleShortVersionString',
            'lipo -archs',
            'VRAM Radar (Apple Silicon).app',
            'VRAM Radar (Intel).app',
            'VRAMRadar-$version-macos.zip',
            'ditto -c -k --sequesterRsrc --keepParent',
        ):
            self.assertIn(contract, self.combined_packager)

    def test_unsigned_current_distribution_boundary_is_explicit(self) -> None:
        self.assertIn("This release is not signed", self.readme)
        self.assertIn("with an Apple Developer ID", self.readme)
        self.assertIn("not notarized", self.readme)
        self.assertIn("right-click **Open**", self.readme)
        self.assertIn("未使用 Apple Developer ID 签名", self.release_notes)
        self.assertIn("未经过 Apple", self.release_notes)
        self.assertIn("右键应用并选择“打开”", self.release_notes)

    def test_signer_uses_hardened_runtime_notarytool_and_stapler(self) -> None:
        for contract in (
            "codesign --force --deep --options runtime --timestamp",
            "xcrun notarytool submit",
            "xcrun stapler staple",
            "xcrun stapler validate",
            "spctl --assess --type execute",
            "/usr/bin/base64 -D",
        ):
            self.assertIn(contract, self.signer)
        self.assertNotIn("base64 --decode", self.signer)

    def test_release_workflow_binds_tag_and_requires_exact_dynamic_asset_set(self) -> None:
        for contract in (
            'version="${RELEASE_TAG#v}"',
            'public_assets=(',
            'checksum_assets=(',
            'VRAMRadar-Setup-$version.exe.sha256',
            'VRAMRadar-$version-macos.zip.sha256',
            'sha256sum -c -- ./*.sha256',
            'public_dir="../public-release-assets"',
            'cp -- "${public_assets[@]}" "$public_dir/"',
            'git ls-remote --exit-code --tags origin "refs/tags/$RELEASE_TAG"',
            'git rev-parse "$RELEASE_TAG^{commit}"',
            'gh api --method POST "repos/$GITHUB_REPOSITORY/git/refs"',
            'cleanup_failed_draft() (',
            'restore_previous_release() (',
            'created_release_id=""',
            'repos/$GITHUB_REPOSITORY/releases/$created_release_id',
            'release_tags="$(gh api --paginate',
            'REPLACE_EXISTING_RELEASE: ${{ inputs.replace_existing_release }}',
            'gh release download "$RELEASE_TAG"',
            'gh release delete "$RELEASE_TAG"',
            'replacement_started="1"',
            'gh api --method PATCH "repos/$GITHUB_REPOSITORY/git/refs/tags/$RELEASE_TAG"',
            '-f sha="$TARGET_COMMIT"',
            '-F force=true',
            'tag_lookup_status=$?',
            'case "$tag_lookup_status" in',
            'created_release_id="$(gh api --method POST',
            '-F draft=true',
            'gh release upload "$RELEASE_TAG" public-release-assets/*',
            'uploaded_asset_names="$(gh api --paginate',
            'expected_asset_names="$(find public-release-assets',
            'trap - ERR INT TERM',
            'gh api --method PATCH "repos/$GITHUB_REPOSITORY/releases/$created_release_id"',
            '-F draft=false',
            '-f make_latest=true',
            'docs/release-notes-${RELEASE_TAG}.md',
        ):
            self.assertIn(contract, self.workflow)
        self.assertNotIn('if gh release view "$RELEASE_TAG"', self.workflow)
        self.assertNotIn('--cleanup-tag', self.workflow)
        self.assertNotIn('gh release upload "$RELEASE_TAG" release-assets/*', self.workflow)
        self.assertNotIn("VRAMRadar-0.4.0-windows", self.workflow)
        self.assertNotIn("VRAMRadar-0.4.0-macos", self.workflow)
        public_assets = self.workflow.split("public_assets=(", 1)[1].split(")", 1)[0]
        self.assertNotIn(".sha256", public_assets)
        self.assertNotIn("macos-arm64", public_assets)
        self.assertNotIn("macos-x86_64", public_assets)
        self.assertEqual(public_assets.count("VRAMRadar-"), 2)
        self.assertIn("two files users need to download", self.readme)
        self.assertNotIn("windows-x64-portable", self.workflow)
        self.assertIn("needs: [windows, macos_combined_validate]", self.workflow)
        self.assertIn("group: stable-release-v2-${{ inputs.release_tag }}", self.workflow)
        self.assertIn("bash tools/package_combined_macos_release.sh native-assets", self.workflow)
        self.assertIn("needs: [windows, macos_combined_validate]", self.workflow)
        self.assertIn("Validate final macOS package on ${{ matrix.arch }}", self.workflow)
        self.assertIn("VRAM_RADAR_MACOS_BUNDLE: combined-extracted/VRAM Radar macOS/${{ matrix.app }}", self.workflow)
        self.assertEqual(
            self.workflow.count('VRAM_RADAR_ALLOW_RATE_LIMIT_WITH_SIBLING_PROOF: "1"'),
            2,
        )
        self.assertIn("tools/validate_macos_transport_receipts.py", self.workflow)
        self.assertIn('macos-validation-${{ matrix.arch }}.json', self.workflow)
        self.assertIn("./.venv/bin/python tools/validate_macos_bundle.py", self.workflow)


if __name__ == "__main__":
    unittest.main()
