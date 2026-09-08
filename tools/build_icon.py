from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "vram_radar" / "assets" / "app-icon.png"
TARGET_SVG = ROOT / "src" / "vram_radar" / "web" / "brand.svg"
TARGET_ICO = ROOT / "packaging" / "app-icon.ico"
TARGET_ICNS = ROOT / "packaging" / "app-icon.icns"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

# One geometric mark for the dashboard, desktop, taskbar, tray, and Dock.
# Work in a 64-unit grid and supersample before exporting small native sizes.
BACKGROUND = "#183e3a"
RING = "#e5f3ed"
SIGNAL = "#85d9bc"


def render_icon(size: int) -> Image.Image:
    scale = max(4, (size * 4 + 63) // 64)
    image = Image.new("RGBA", (64 * scale, 64 * scale))
    draw = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(value * scale) for value in values)

    def dot(x, y, radius, fill):
        draw.ellipse(box((x - radius, y - radius, x + radius, y + radius)), fill=fill)

    draw.rounded_rectangle(box((4, 4, 60, 60)), radius=14 * scale, fill=BACKGROUND)
    draw.arc(box((15, 15, 49, 49)), start=0, end=270, fill=RING, width=5 * scale)
    # Pillow strokes sit inside their bounds; use the centerline radius here.
    # The vector has the same visible arc envelope and rounded caps.
    dot(46.5, 32, 2.5, RING)
    dot(32, 17.5, 2.5, RING)
    draw.line(box((32, 32, 39.5, 24.5)), fill=SIGNAL, width=5 * scale)
    dot(32, 32, 2.5, SIGNAL)
    dot(39.5, 24.5, 2.5, SIGNAL)
    dot(46, 18, 3.5, SIGNAL)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def vector_icon() -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect x="4" y="4" width="56" height="56" rx="14" fill="{BACKGROUND}"/>
  <path d="M46.5 32a14.5 14.5 0 1 1-14.5-14.5" stroke="{RING}" stroke-width="5" stroke-linecap="round"/>
  <path class="signal-sweep" d="m32 32 7.5-7.5" stroke="{SIGNAL}" stroke-width="5" stroke-linecap="round"/>
  <circle cx="46" cy="18" r="3.5" fill="{SIGNAL}"/>
</svg>
'''


def main() -> None:
    TARGET_ICO.parent.mkdir(parents=True, exist_ok=True)
    rgba = render_icon(1024)
    rgba.save(SOURCE)
    TARGET_SVG.write_text(vector_icon(), encoding="utf-8")
    rgba.save(TARGET_ICO, format="ICO", sizes=[(size, size) for size in SIZES],
              append_images=[render_icon(size) for size in SIZES])
    (SOURCE.parent / "taskbar-light-radar.ico").write_bytes(TARGET_ICO.read_bytes())
    rgba.save(TARGET_ICNS, format="ICNS")
    print(TARGET_ICO)
    print(TARGET_ICNS)


if __name__ == "__main__":
    main()
