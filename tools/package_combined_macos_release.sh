#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Combined macOS release packaging must run on macOS." >&2
  exit 2
fi

release_tag="${RELEASE_TAG:-}"
if [[ ! "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "RELEASE_TAG must be a stable semantic version tag." >&2
  exit 2
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
input_dir="${1:-$project_root/native-assets}"
version="${release_tag#v}"
project_version="$(sed -nE 's/^version = "([^"]+)"/\1/p' "$project_root/pyproject.toml" | head -n 1)"
[[ "$project_version" == "$version" ]] || { echo "Release version $version does not match project version $project_version" >&2; exit 2; }
arm_archive="$input_dir/VRAMRadar-$version-macos-arm64.zip"
intel_archive="$input_dir/VRAMRadar-$version-macos-x86_64.zip"

for archive in "$arm_archive" "$intel_archive"; do
  if [[ ! -f "$archive" || ! -f "$archive.sha256" ]]; then
    echo "Validated native macOS archive is missing: $archive" >&2
    exit 2
  fi
done
(
  cd "$input_dir"
  shasum -a 256 -c "$(basename "$arm_archive").sha256"
  shasum -a 256 -c "$(basename "$intel_archive").sha256"
)

stage="$(mktemp -d "${TMPDIR:-/tmp}/vram-radar-macos-combined.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/arm64" "$stage/x86_64" "$stage/VRAM Radar macOS"
ditto -x -k "$arm_archive" "$stage/arm64"
ditto -x -k "$intel_archive" "$stage/x86_64"

arm_bundle="$stage/arm64/VRAM Radar.app"
intel_bundle="$stage/x86_64/VRAM Radar.app"
for bundle in "$arm_bundle" "$intel_bundle"; do
  [[ -d "$bundle" ]] || { echo "Expected app bundle is missing: $bundle" >&2; exit 2; }
  bundle_version="$(plutil -extract CFBundleShortVersionString raw "$bundle/Contents/Info.plist")"
  [[ "$bundle_version" == "$version" ]] || { echo "Bundle version $bundle_version does not match $version" >&2; exit 2; }
done

[[ "$(lipo -archs "$arm_bundle/Contents/MacOS/VRAMRadar")" == "arm64" ]] || { echo "Apple Silicon app has the wrong architecture" >&2; exit 2; }
[[ "$(lipo -archs "$intel_bundle/Contents/MacOS/VRAMRadar")" == "x86_64" ]] || { echo "Intel app has the wrong architecture" >&2; exit 2; }
[[ "$(lipo -archs "$arm_bundle/Contents/MacOS/VRAMRadarAskPass")" == "arm64" ]] || { echo "Apple Silicon helper has the wrong architecture" >&2; exit 2; }
[[ "$(lipo -archs "$intel_bundle/Contents/MacOS/VRAMRadarAskPass")" == "x86_64" ]] || { echo "Intel helper has the wrong architecture" >&2; exit 2; }

mv "$arm_bundle" "$stage/VRAM Radar macOS/VRAM Radar (Apple Silicon).app"
mv "$intel_bundle" "$stage/VRAM Radar macOS/VRAM Radar (Intel).app"
printf '%s\n' \
  'VRAM Radar for macOS' \
  '' \
  'Apple Silicon (M1/M2/M3/M4): open VRAM Radar (Apple Silicon).app' \
  'Intel Mac: open VRAM Radar (Intel).app' \
  '' \
  'This release is not Apple Developer ID signed or notarized.' \
  'If macOS blocks first launch, right-click the matching app and choose Open.' \
  > "$stage/VRAM Radar macOS/README.txt"

asset="$project_root/VRAMRadar-$version-macos.zip"
rm -f "$asset" "$asset.sha256"
ditto -c -k --sequesterRsrc --keepParent "$stage/VRAM Radar macOS" "$asset"
(
  cd "$project_root"
  shasum -a 256 "$(basename "$asset")" > "$(basename "$asset").sha256"
)
ls -lh "$asset" "$asset.sha256"
