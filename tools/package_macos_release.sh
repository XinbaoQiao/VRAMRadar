#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS release packaging must run on macOS." >&2
  exit 2
fi

release_arch="${RELEASE_ARCH:-}"
if [[ "$release_arch" != "arm64" && "$release_arch" != "x86_64" ]]; then
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
asset="$project_root/VRAMRadar-${release_version}-macos-${release_arch}.zip"

rm -f "$asset" "$asset.sha256"
ditto -c -k --sequesterRsrc --keepParent "$bundle" "$asset"
(
  cd "$project_root"
  shasum -a 256 "$(basename "$asset")" > "$(basename "$asset").sha256"
)
ls -lh "$asset" "$asset.sha256"
