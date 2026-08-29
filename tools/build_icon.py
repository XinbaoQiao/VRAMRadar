from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "vram_radar" / "assets" / "app-icon.png"
TARGET_ICO = ROOT / "packaging" / "app-icon.ico"
TARGET_ICNS = ROOT / "packaging" / "app-icon.icns"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def main() -> None:
    TARGET_ICO.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        rgba = image.convert("RGBA")
        rgba.save(TARGET_ICO, format="ICO", sizes=[(size, size) for size in SIZES])
        rgba.save(TARGET_ICNS, format="ICNS")
    print(TARGET_ICO)
    print(TARGET_ICNS)


if __name__ == "__main__":
    main()
