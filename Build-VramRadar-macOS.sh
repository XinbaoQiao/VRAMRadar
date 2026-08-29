#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skip_sync=0
target_arch=""

for argument in "$@"; do
  case "$argument" in
    --skip-sync)
      skip_sync=1
      ;;
    --target-arch=arm64|--target-arch=x86_64|--target-arch=universal2)
      target_arch="${argument#*=}"
      ;;
    *)
      echo "Unknown argument: $argument" >&2
      echo "Usage: bash Build-VramRadar-macOS.sh [--skip-sync] [--target-arch=arm64|x86_64|universal2]" >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS .app must be built on macOS; PyInstaller cannot cross-build it from Windows or Linux." >&2
  exit 2
fi

if [[ "$skip_sync" -eq 0 ]]; then
  uv sync --extra build --frozen --project "$project_root"
fi

python="$project_root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "The maintained macOS build environment is incomplete. Run uv sync --extra build --frozen first." >&2
  exit 2
fi

"$python" "$project_root/tools/build_icon.py"
if [[ -n "$target_arch" ]]; then
  export VRAM_RADAR_MACOS_TARGET_ARCH="$target_arch"
fi
"$python" -m PyInstaller --noconfirm \
  --distpath "$project_root/dist-macos" \
  --workpath "$project_root/build-macos" \
  "$project_root/packaging/vram-radar.spec"

echo "$project_root/dist-macos/VRAM Radar.app"
