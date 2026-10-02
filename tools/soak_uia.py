"""UI Automation memory soak: FindAll every read vs cached elements.
Usage: python tools/soak_uia.py [CHUNKS] [PER_CHUNK] -> JSON"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tools"))
import clr  # noqa: F401  (pythonnet)
from soak_memory import measure  # noqa: E402


def main(chunks=4, per=1000):
    from vram_radar import usage_surface as us
    import ctypes
    layout = us.TaskbarLayout(interval=0.0, retry=0.0, trim=None)
    layout._load()
    handle = int(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None) or 0)
    out = {"start": measure()["private_mb"]}
    mode = sys.argv[3] if len(sys.argv) > 3 else "findall"
    series = []
    reader = layout._read if mode == "cached" else getattr(layout, "_find_all", layout._read)
    for _ in range(chunks):
        t = time.perf_counter()
        for _ in range(per):
            reader(handle)
        series.append((round(measure()["private_mb"], 1), round((time.perf_counter() - t) / per * 1000, 1)))
    out.update(mode=mode, series=series, sample=sorted(reader(handle)))
    print(json.dumps(out))


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:3]]
    main(*a)
