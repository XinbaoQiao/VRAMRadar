"""Regenerate the web UI's quota gradient table (app.js) from quota_colors.

Usage: python tools/gen_quota_gradient.py [--check]   (exit 1 when stale)
"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vram_radar.quota_colors import js_table

BEGIN, END = "// BEGIN quota-gradient (tools/gen_quota_gradient.py)", "// END quota-gradient"


def block() -> str:
    return f"{BEGIN}\nconst QUOTA_GRADIENT = {json.dumps(js_table(), separators=(',', ':'))};\n{END}"


def main() -> int:
    path = ROOT / "src/vram_radar/web/app.js"
    text = path.read_text(encoding="utf-8")
    a, b = text.index(BEGIN), text.index(END) + len(END)
    fresh = text[:a] + block() + text[b:]
    if "--check" in sys.argv:
        return 0 if fresh == text else 1
    path.write_bytes(fresh.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())