"""Sample the running VRAMRadar taskbar strip rect/visibility (diagnostics).

Usage: python tools/sample_strip_position.py SECONDS [INTERVAL] [OUT.jsonl]
Prints a summary: samples, moves, hides, shows, distinct rects.
"""
import ctypes, json, sys, time
from ctypes import wintypes

u = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def pids(name="VRAMRadar.exe"):
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"')}


def strip_window(targets):
    found = []
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in targets:
            buf = ctypes.create_unicode_buffer(128)
            u.GetWindowTextW(hwnd, buf, 128)
            if "VRAM Radar" in buf.value and buf.value.startswith("Codex"):
                found.append(hwnd)
        return True
    u.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def fg_class():
    h = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(80)
    u.GetClassNameW(h, buf, 80)
    return buf.value


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
    out = open(sys.argv[3], "w", encoding="utf-8") if len(sys.argv) > 3 else None
    targets = pids()
    end = time.time() + seconds
    last = None
    stats = {"samples": 0, "moves": 0, "resizes": 0, "hides": 0, "shows": 0, "missing": 0}
    rects = {}
    while time.time() < end:
        hwnd = strip_window(targets) if targets else None
        if not hwnd:
            stats["missing"] += 1
            targets = pids()
            time.sleep(interval)
            continue
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        cur = (bool(u.IsWindowVisible(hwnd)), r.left, r.top, r.right, r.bottom)
        stats["samples"] += 1
        rects[cur[1:]] = rects.get(cur[1:], 0) + 1
        if last is not None and cur != last:
            event = []
            if last[0] and not cur[0]:
                stats["hides"] += 1; event.append("hide")
            if cur[0] and not last[0]:
                stats["shows"] += 1; event.append("show")
            if (cur[1], cur[2]) != (last[1], last[2]):
                stats["moves"] += 1; event.append("move")
            if (cur[3]-cur[1], cur[4]-cur[2]) != (last[3]-last[1], last[4]-last[2]):
                stats["resizes"] += 1; event.append("resize")
            if out:
                out.write(json.dumps({"t": time.strftime("%H:%M:%S"), "event": event, "from": last,
                                      "to": cur, "fg": fg_class()}) + "\n"); out.flush()
        last = cur
        time.sleep(interval)
    stats["distinct_rects"] = len(rects)
    stats["top_rects"] = sorted(rects.items(), key=lambda kv: -kv[1])[:4]
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
