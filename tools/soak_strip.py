"""Sped-up soak of the native strip: thousands of ticks with changing data,
theme/style/label/language switches and menu opens; reports tick/menu cost
and GDI/USER/kernel handle, thread and memory growth.

Usage: python tools/soak_strip.py [TICKS] [MENU_OPENS]  -> JSON
Nothing is sent anywhere; consent and the user's profile are never touched.
"""
from __future__ import annotations

import copy
import ctypes
import json
import logging
import statistics
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


class _Mem(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                             "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                             "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                             "PrivateUsage")]


def resources() -> dict:
    k32, u32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    psapi = ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    me = k32.GetCurrentProcess()
    u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    count = wintypes.DWORD()
    k32.GetProcessHandleCount(me, ctypes.byref(count))
    mem = _Mem()
    mem.cb = ctypes.sizeof(mem)
    psapi.GetProcessMemoryInfo(me, ctypes.byref(mem), mem.cb)
    import gc
    gc.collect()
    try:
        from System import GC
        GC.Collect()
        GC.WaitForPendingFinalizers()
        GC.Collect()
        managed = int(GC.GetTotalMemory(True))
    except Exception:
        managed = -1
    return {"gdi": int(u32.GetGuiResources(me, 0)), "user": int(u32.GetGuiResources(me, 1)),
            "handles": int(count.value), "py_threads": threading.active_count(),
            "private_mb": round(mem.PrivateUsage / 1048576, 1), "managed_mb": round(managed / 1048576, 1)}


def run(ticks: int = 3000, opens: int = 40) -> dict:
    import webview
    from benchmark_webview_ui import FakeApi, _wait_until_ready
    from check_english_ui import provider_overview
    from vram_radar.usage_surface import BACKGROUND_STYLES, CodexUsageSurface
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    window = webview.create_window("VRAM Radar soak", width=600, height=400,
                                  url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
                                  js_api=FakeApi(), hidden=True, focus=False)
    clock = {"t": time.time()}
    state = {"enabled": True, "state": "ready", "windows": []}
    overviews = [provider_overview(n) for n in range(3)]
    variant = {"n": 0, "lang": "zh-CN"}
    display = {"codex_time_format": "decimal", "usage_labels": "icons", "usage_background": "transparent"}

    def snapshot():
        return copy.deepcopy(state)

    def providers():
        return copy.deepcopy(overviews[variant["n"] % 3])

    surface = CodexUsageSurface(window, snapshot, language=lambda: variant["lang"],
                                open_settings=lambda: None, open_home=lambda: None, refresh=lambda: None,
                                display_options=lambda: dict(display), save_display=lambda k, v: {"ok": True},
                                disable=lambda: None, quit_application=lambda: None, providers=providers,
                                save_providers=lambda ids: {"ok": True}, save_consent=lambda pid, granted: {"ok": True})
    surface.confirm_consent = lambda *a: False
    result: dict = {"ok": False}
    timeout = threading.Timer(900, window.destroy)

    def inner():
        try:
            from System import Action
            from System.Drawing import Point
            _wait_until_ready(window, time.monotonic() + 20)
            surface.start()
            invoke = lambda cb: window.native.Invoke(Action(cb))

            def step(i):
                # Simulated time: ~1 minute per tick, so countdowns and values
                # change constantly (a 3000-tick run ~ 2 days of text churn).
                now = clock["t"] + i * 60
                state["windows"] = [
                    {"id": "codex:primary", "window_minutes": 300, "remaining_percent": (100 - i) % 101,
                     "resets_at": now + 3600 * 5 - (i * 60) % 18000},
                    {"id": "codex:secondary", "window_minutes": 10080, "remaining_percent": 50 + i % 50,
                     "resets_at": now + 86400 * 3}]
                state["state"] = "error" if i % 97 == 0 else "ready"
                variant["n"] = i // 25
                if i % 200 == 100:
                    variant["lang"] = "en" if variant["lang"] != "en" else "zh-CN"
                if i % 150 == 75:
                    display["usage_labels"] = "text" if display["usage_labels"] == "icons" else "icons"
                if i % 300 == 150:
                    styles = list(BACKGROUND_STYLES)
                    display["usage_background"] = styles[(styles.index(display["usage_background"]) + 1) % len(styles)]

            for i in range(200):
                step(i)
                invoke(surface._tick)
            base = resources()
            import os, tracemalloc
            trace = os.environ.get("SOAK_TRACE") == "1"
            if trace:
                tracemalloc.start(8)
                snap0 = tracemalloc.take_snapshot()
            costs = []
            profile = None
            if os.environ.get("SOAK_PROFILE") == "1":
                import cProfile
                profile = cProfile.Profile()
            tick = surface._tick if profile is None else (lambda: (profile.enable(), surface._tick(), profile.disable()))
            for i in range(200, 200 + ticks):
                step(i)
                start = time.perf_counter()
                invoke(tick)
                costs.append((time.perf_counter() - start) * 1000)
            after_ticks = resources()
            if profile is not None:
                import io, pstats
                out = io.StringIO()
                pstats.Stats(profile, stream=out).sort_stats("tottime").print_stats(14)
                result["profile"] = [line.strip()[:150] for line in out.getvalue().splitlines() if line.strip()][-15:]
            if trace:
                diff = tracemalloc.take_snapshot().compare_to(snap0, "traceback")[:6]
                result["trace"] = [[round(d.size_diff / 1024), d.count_diff, [str(f) for f in d.traceback.format()[-6:]]] for d in diff]
                tracemalloc.stop()
            menu_ms = []
            for _ in range(opens):
                opened = threading.Event()
                handler = lambda *_: opened.set()
                surface._menu.Opened += handler
                start = time.perf_counter()
                invoke(lambda: surface._menu.Show(Point(200, 200)))
                opened.wait(2)
                menu_ms.append((time.perf_counter() - start) * 1000)
                invoke(lambda: surface._menu.Close())
                surface._menu.Opened -= handler
                time.sleep(0.05)
            final = resources()
            q = sorted(costs)
            result.update(ok=True, ticks=ticks, tick_ms={"median": round(statistics.median(q), 2),
                                                         "p95": round(q[int(len(q) * 0.95)], 2),
                                                         "max": round(q[-1], 2)},
                          menu_open_ms={"median": round(statistics.median(menu_ms), 1), "max": round(max(menu_ms), 1)},
                          base=base, after_ticks=after_ticks, final=final,
                          growth={k: round(final[k] - base[k], 1) for k in base})
        except Exception as error:
            result.update(error=f"{type(error).__name__}: {error}")
        finally:
            try:
                surface.stop()
            finally:
                timeout.cancel()
                window.destroy()

    with tempfile.TemporaryDirectory(prefix="strip-soak-") as temporary:
        timeout.start()
        try:
            webview.start(inner, private_mode=True, storage_path=temporary)
        finally:
            logging.disable(previous)
    return result


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    print(json.dumps(run(*args)))