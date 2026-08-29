#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS signing and notarization must run on macOS." >&2
  exit 2
fi

required=(
  MACOS_CERTIFICATE_P12
  MACOS_CERTIFICATE_PASSWORD
  MACOS_SIGNING_IDENTITY
  APPLE_ID
  APPLE_TEAM_ID
  APPLE_APP_SPECIFIC_PASSWORD
  RELEASE_ARCH
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Required macOS release value is missing: $name" >&2
    exit 2
  fi
done
if [[ "$RELEASE_ARCH" != "arm64" && "$RELEASE_ARCH" != "x86_64" ]]; then
  echo "RELEASE_ARCH must be arm64 or x86_64." >&2
  exit 2
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bundle="$project_root/dist-macos/VRAM Radar.app"
if [[ ! -d "$bundle" ]]; then
  echo "macOS app bundle is missing: $bundle" >&2
  exit 2
fi

release_version="$(
  cd "$project_root"
  ./.venv/bin/python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
)"
asset="$project_root/VRAMRadar-${release_version}-macos-${RELEASE_ARCH}.zip"
scratch_root="${RUNNER_TEMP:-$project_root/work}"
scratch="$scratch_root/vram-radar-sign-${RELEASE_ARCH}-$$"
certificate_path="$scratch/developer-id.p12"
keychain_path="$scratch/release.keychain-db"
notary_zip="$scratch/notary-upload.zip"
notary_result="$scratch/notary-result.json"
keychain_password="$(openssl rand -hex 24)"

cleanup() {
  security delete-keychain "$keychain_path" >/dev/null 2>&1 || true
  rm -f "$certificate_path" "$keychain_path" "$notary_zip" "$notary_result"
  rmdir "$scratch" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mkdir -p "$scratch"
printf '%s' "$MACOS_CERTIFICATE_P12" | /usr/bin/base64 -D > "$certificate_path"

security create-keychain -p "$keychain_password" "$keychain_path"
security set-keychain-settings -lut 21600 "$keychain_path"
security unlock-keychain -p "$keychain_password" "$keychain_path"
security import "$certificate_path" -k "$keychain_path" -P "$MACOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain_path" >/dev/null
security list-keychains -d user -s "$keychain_path"
security find-identity -v -p codesigning "$keychain_path" | grep -F "$MACOS_SIGNING_IDENTITY" >/dev/null

codesign --force --deep --options runtime --timestamp --keychain "$keychain_path" --sign "$MACOS_SIGNING_IDENTITY" "$bundle"
codesign --verify --deep --strict --verbose=2 "$bundle"

ditto -c -k --sequesterRsrc --keepParent "$bundle" "$notary_zip"
xcrun notarytool submit "$notary_zip" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --wait \
  --output-format json > "$notary_result"
notary_status="$("$project_root/.venv/bin/python" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", ""))' "$notary_result")"
if [[ "$notary_status" != "Accepted" ]]; then
  cat "$notary_result" >&2
  echo "Apple notarization was not accepted." >&2
  exit 2
fi

xcrun stapler staple "$bundle"
xcrun stapler validate "$bundle"
codesign --verify --deep --strict --verbose=2 "$bundle"
spctl --assess --type execute --verbose=4 "$bundle"

rm -f "$asset" "$asset.sha256"
ditto -c -k --sequesterRsrc --keepParent "$bundle" "$asset"
(
  cd "$project_root"
  shasum -a 256 "$(basename "$asset")" > "$(basename "$asset").sha256"
)
ls -lh "$asset" "$asset.sha256"
