"""Memory soak by component: where does private memory grow?

Runs the real strip (hidden WebView host, fake data, no network) and then
each periodic piece in isolation -- strip ticks, the taskbar layout read
(UI Automation), the weather screen capture, the layout refresh on the UI
thread and on short-lived worker threads (as the live app does) -- and
reports private / managed / Python-traced growth per phase after a full
Python + .NET collection.

Usage: python tools/soak_memory.py [N]  -> JSON (N iterations per phase)
"""
from __future__ import annotations

import gc
import json
import logging
import sys
import threading
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from soak_strip import resources as _raw_resources  # noqa: E402


def measure() -> dict:
    gc.collect()
    try:
        from System import GC
        GC.Collect()
        GC.WaitForPendingFinalizers()
        GC.Collect()
    except Exception:
        pass
    sample = _raw_resources()
    sample["py_mb"] = round(tracemalloc.get_traced_memory()[0] / 1048576, 2) if tracemalloc.is_tracing() else None
    return sample


def run(n: int = 1000) -> dict:
    import copy
    import webview
    from benchmark_webview_ui import FakeApi, _wait_until_ready
    from check_english_ui import provider_overview
    from vram_radar import usage_surface as us
    logging.disable(logging.CRITICAL)
    window = webview.create_window("VRAM Radar memory soak", width=600, height=400,
                                  url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
                                  js_api=FakeApi(), hidden=True, focus=False)
    state = {"enabled": True, "state": "ready", "windows": []}
    overviews = [provider_overview(k) for k in range(3)]
    variant = {"n": 0}
    display = {"codex_time_format": "decimal", "usage_labels": "icons", "usage_background": "transparent"}
    surface = us.CodexUsageSurface(window, lambda: copy.deepcopy(state), language=lambda: "zh-CN",
                                   open_settings=lambda: None, open_home=lambda: None, refresh=lambda: None,
                                   display_options=lambda: dict(display), save_display=lambda k, v: {"ok": True},
                                   disable=lambda: None, quit_application=lambda: None,
                                   providers=lambda: copy.deepcopy(overviews[variant["n"] % 3]),
                                   save_providers=lambda ids: {"ok": True},
                                   save_consent=lambda pid, granted: {"ok": True})
    surface.confirm_consent = lambda *a: False
    result: dict = {"ok": False, "n": n, "phases": []}
    timeout = threading.Timer(1800, window.destroy)

    def inner():
        try:
            from System import Action
            _wait_until_ready(window, time.monotonic() + 20)
            surface.start()
            invoke = lambda cb: window.native.Invoke(Action(cb))
            invoke(lambda: (surface.timer.Stop(), surface._z_timer.Stop()))
            t0 = time.time()

            def data(i):
                now = t0 + i * 60
                state["windows"] = [
                    {"id": "codex:primary", "window_minutes": 300, "remaining_percent": (100 - i) % 101,
                     "resets_at": now + 3600 * 5 - (i * 60) % 18000},
                    {"id": "codex:secondary", "window_minutes": 10080, "remaining_percent": 50 + i % 50,
                     "resets_at": now + 86400 * 3}]
                variant["n"] = i // 25

            for i in range(300):
                data(i)
                invoke(surface._tick)
            tracemalloc.start(6)
            geometry = us.windows_taskbar_geometry()
            import ctypes
            bar_handle = int(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None) or 0)
            bar = geometry[0]
            own = (surface.form.Left, surface.form.Top, surface.form.Right, surface.form.Bottom)
            sync_layout = us.TaskbarLayout(interval=0.0, retry=0.0)
            sync_layout.elements(bar_handle, bar, own)

            def threaded_refresh():
                th = threading.Thread(target=lambda: sync_layout._refresh((bar_handle, tuple(bar)), bar_handle, own))
                th.start()
                th.join()

            widgets = (sync_layout._elements or {}).get("WidgetsButton")
            rect = (widgets[0], widgets[1], widgets[0] + 400, widgets[3]) if widgets else (0, bar[1], 400, bar[3])
            phases = [
                ("ticks", lambda i: (data(300 + i), invoke(surface._tick))),
                ("uia_read", lambda i: sync_layout._read(bar_handle)),
                ("capture", lambda i: us.capture_screen(rect)),
                ("refresh_ui_thread", lambda i: invoke(lambda: sync_layout._refresh((bar_handle, tuple(bar)), bar_handle, own))),
                ("refresh_new_thread", lambda i: threaded_refresh()),
                # Both live WinForms timers at ~60 Hz (real handlers: tick + z-order walk).
                ("timers_fast", lambda i: (
                    invoke(lambda: (setattr(surface.timer, "Interval", 16), setattr(surface._z_timer, "Interval", 16),
                                    surface.timer.Start(), surface._z_timer.Start())) if i == 0 else None,
                    time.sleep(0.016),
                    invoke(lambda: (surface.timer.Stop(), surface._z_timer.Stop())) if i == n - 1 else None)),
                ("thread_only", lambda i: (lambda th: (th.start(), th.join()))(threading.Thread(target=lambda: None))),
            ]
            import os
            wanted = [w for w in os.environ.get("SOAK_PHASES", "").split(",") if w]
            repeat = int(os.environ.get("SOAK_REPEAT", "1"))
            phases = [p for p in phases if not wanted or p[0] in wanted] * repeat
            before = measure()
            result["start"] = before
            for name, step in phases:
                start = time.perf_counter()
                for i in range(n):
                    step(i)
                after = measure()
                result["phases"].append({"phase": name, "s": round(time.perf_counter() - start, 1),
                                         **{k: round(after[k] - before[k], 2) for k in
                                            ("private_mb", "managed_mb", "py_mb", "gdi", "user", "handles")
                                            if isinstance(after.get(k), (int, float)) and isinstance(before.get(k), (int, float))}})
                before = after
            top = tracemalloc.take_snapshot().statistics("lineno")[:5]
            result["py_top"] = [f"{s.size // 1024}KB {s.traceback[0].filename.rsplit(chr(92), 1)[-1]}:{s.traceback[0].lineno}" for s in top]
            result["end"] = before
            result["ok"] = True
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
        finally:
            try:
                surface.stop()
            finally:
                timeout.cancel()
                window.destroy()

    import tempfile
    with tempfile.TemporaryDirectory(prefix="mem-soak-") as temporary:
        timeout.start()
        webview.start(inner, private_mode=True, storage_path=temporary)
    return result


if __name__ == "__main__":
    print(json.dumps(run(int(sys.argv[1]) if len(sys.argv) > 1 else 1000)))
