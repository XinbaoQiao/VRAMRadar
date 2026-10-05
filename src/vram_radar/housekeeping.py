"""Bounded housekeeping for a VRAM Radar that stays running for weeks.

Everything here only touches VRAM Radar's own storage folders (the
``StoragePaths`` of the running home) and never raises into the caller.

What can accumulate, and its cap:

* ``logs/app.log`` -- ``RotatingFileHandler`` 1 MB x (1 + 3 backups); stray
  ``app.log.N`` beyond the backup count and other ``*.log`` files older than
  30 days are removed.
* ``cache/updates/<32 hex>`` download stages -- removed after 2 days
  (``updater.prune_stale_stages``).
* ``.<name>.<32 hex>.tmp`` leftovers of an interrupted atomic write -- removed
  after 1 day (``storage.prune_orphan_temporaries``).
* ``config/profiles/<id>.toml.invalid-*`` copies of damaged profiles -- newest
  5 per profile are kept.
* ``cache/<profile>/<server>.json`` snapshots of servers that are no longer
  configured -- removed after 90 days (configured servers are never touched).
* ``cache/webview/<pid>`` WebView2 profile of each run -- deleted by pywebview
  on a clean exit; folders of processes that are no longer running (crash,
  forced stop, power loss) are removed at startup and daily.
* ``cache/usage-trend/<profile>.json`` quota sparkline samples -- pruned to
  30 days (one point per ~30 minutes per series).
"""
from __future__ import annotations

import gc
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Iterable

DAY = 86400.0
LOG_BACKUP_COUNT = 3
LOG_MAX_BYTES = 1_000_000
STRAY_LOG_MAX_AGE = 30 * DAY
INVALID_PROFILE_KEEP = 5
SNAPSHOT_MAX_AGE = 90 * DAY
WEBVIEW_FOLDER = "webview"

LOG = logging.getLogger("vram_radar")


def _age(path: Path, now: float) -> float:
    return now - path.stat().st_mtime


def directory_size(root: Path) -> int:
    total = 0
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def prune_log_files(logs: Path, *, keep: int = LOG_BACKUP_COUNT, now: float | None = None,
                    max_age: float = STRAY_LOG_MAX_AGE) -> int:
    now = time.time() if now is None else now
    removed = 0
    try:
        entries = list(logs.iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if not path.is_file():
                continue
            match = re.fullmatch(r"app\.log\.(\d+)", path.name)
            if match:
                stale = int(match.group(1)) > keep
            elif path.name == "app.log":
                continue
            else:
                stale = path.suffix.lower() in {".log", ".txt"} and _age(path, now) > max_age
            if stale:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def prune_invalid_profile_backups(profiles: Path, *, keep: int = INVALID_PROFILE_KEEP) -> int:
    groups: dict[str, list[Path]] = {}
    try:
        for path in profiles.glob("*.toml.invalid-*"):
            if path.is_file():
                groups.setdefault(path.name.split(".toml.invalid-", 1)[0], []).append(path)
    except OSError:
        return 0
    removed = 0
    for paths in groups.values():
        paths.sort(key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
        for path in paths[keep:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def prune_snapshot_cache(folder: Path, known_ids: Iterable[str], *, max_age: float = SNAPSHOT_MAX_AGE,
                         now: float | None = None) -> int:
    now = time.time() if now is None else now
    known = set(known_ids)
    removed = 0
    try:
        candidates = list(folder.glob("*.json"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if path.is_file() and path.stem not in known and _age(path, now) > max_age:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def webview_storage_path(cache: Path, pid: int | None = None) -> Path:
    return cache / WEBVIEW_FOLDER / str(os.getpid() if pid is None else pid)


def prune_webview_sessions(cache: Path, *, current_pid: int | None = None,
                           alive: Callable[[int], bool] = pid_alive) -> int:
    root = cache / WEBVIEW_FOLDER
    current = os.getpid() if current_pid is None else current_pid
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for path in entries:
        if not path.is_dir() or not path.name.isdigit():
            continue
        pid = int(path.name)
        if pid == current or alive(pid):
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 0 if path.exists() else 1
    return removed



def _prune_usage_trend(cache: Path) -> int:
    try:
        from .usage_trend import prune_trend_files
        return prune_trend_files(cache)
    except Exception:
        return 0


def run_maintenance(paths: Any, profile_id: str, known_server_ids: Iterable[str] = (), *,
                    now: float | None = None, current_pid: int | None = None,
                    alive: Callable[[int], bool] = pid_alive) -> dict[str, Any]:
    """One light pass; every step is independent and failures are only counted."""
    from .storage import prune_orphan_temporaries
    from .updater import prune_stale_stages

    now = time.time() if now is None else now
    cache, config, logs = Path(paths.cache), Path(paths.config), Path(paths.logs)
    steps: dict[str, Callable[[], int]] = {
        "logs": lambda: prune_log_files(logs, now=now),
        "update_stages": lambda: prune_stale_stages(cache / "updates", now=now),
        "temporaries": lambda: prune_orphan_temporaries(config, now=now) + prune_orphan_temporaries(cache, now=now),
        "invalid_profiles": lambda: prune_invalid_profile_backups(config / "profiles"),
        "snapshots": lambda: prune_snapshot_cache(cache / profile_id, known_server_ids, now=now),
        "webview": lambda: prune_webview_sessions(cache, current_pid=current_pid, alive=alive),
        "usage_trend": lambda: _prune_usage_trend(cache),
    }
    summary: dict[str, Any] = {}
    for name, step in steps.items():
        try:
            summary[name] = int(step())
        except Exception as exc:  # housekeeping must never stop the app
            summary[name] = f"failed:{type(exc).__name__}"
    summary["bytes"] = {name: directory_size(folder) for name, folder in
                        (("logs", logs), ("cache", cache), ("config", config))}
    return summary


# --------------------------------------------------------------------------
# Self-health watchdog
# --------------------------------------------------------------------------
_TYPES: dict[str, Any] = {}


def _counters_type():
    """Defined once: a ctypes type per call is never freed (see audit 1)."""
    if "counters" not in _TYPES:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]
        _TYPES["counters"] = Counters
    return _TYPES["counters"]


def process_sample() -> dict[str, int]:
    """Current private memory, kernel handles and GDI/USER objects (Windows)."""
    sample: dict[str, int] = {"threads": threading.active_count()}
    if sys.platform != "win32":
        return sample
    try:
        import ctypes
        from ctypes import wintypes
        Counters = _counters_type()
        process = ctypes.c_void_p(-1)  # GetCurrentProcess() pseudo handle
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            sample["private_bytes"] = int(counters.PrivateUsage)
        handles = wintypes.DWORD()
        if ctypes.windll.kernel32.GetProcessHandleCount(process, ctypes.byref(handles)):
            sample["handles"] = int(handles.value)
        sample["gdi"] = int(ctypes.windll.user32.GetGuiResources(process, 0))
        sample["user"] = int(ctypes.windll.user32.GetGuiResources(process, 1))
    except Exception:
        pass
    return sample


def idle_seconds() -> float | None:
    """Seconds since the last user input (read-only; Windows), else None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        if "last_input" not in _TYPES:
            class LastInput(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
            _TYPES["last_input"] = LastInput
        LastInput = _TYPES["last_input"]
        info = LastInput()
        info.cbSize = ctypes.sizeof(LastInput)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _process_memory() -> dict[str, int]:
    """Private bytes and working set of this process (Windows), else {}."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        Counters = _counters_type()
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
            return {"private_bytes": int(counters.PrivateUsage), "working_set": int(counters.WorkingSetSize)}
    except Exception:
        pass
    return {}


def trim_memory(working_set: bool = True) -> dict[str, Any]:
    """Give memory back without restarting: a full Python collection, a
    compacting .NET collection (WinForms/pythonnet objects) and -- Windows --
    trimming the working set so pages that are no longer used (startup
    code, freed heaps) return to the system.  Pages still in use simply
    fault back in; nothing visible changes.  Never raises."""
    before = _process_memory()
    collected = 0
    try:
        collected = gc.collect()
    except Exception:
        pass
    try:
        from System import GC  # type: ignore[import-not-found]
        from System.Runtime import GCSettings, GCLargeObjectHeapCompactionMode  # type: ignore[import-not-found]
        GCSettings.LargeObjectHeapCompactionMode = GCLargeObjectHeapCompactionMode.CompactOnce
        GC.Collect()
        GC.WaitForPendingFinalizers()
        GC.Collect()
    except Exception:
        pass
    if working_set and sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
            kernel32.SetProcessWorkingSetSize(ctypes.c_void_p(-1), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass
    after = _process_memory()
    mb = lambda value: round(value / 1048576, 1) if isinstance(value, int) else None
    return {"gc": collected,
            "private_mb": [mb(before.get("private_bytes")), mb(after.get("private_bytes"))],
            "working_set_mb": [mb(before.get("working_set")), mb(after.get("working_set"))]}


DIAGNOSTICS_FLAG = "diagnostics.on"
DIAGNOSTICS_FILE = "diagnostics.jsonl"
DIAGNOSTICS_MAX_BYTES = 2_000_000


def diagnostics_requested(runtime: Path) -> str | None:
    """Content of ``runtime/diagnostics.on`` ("" or "trace") when present."""
    try:
        return (Path(runtime) / DIAGNOSTICS_FLAG).read_text(encoding="utf-8").strip().lower()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return ""


def start_tracing_if_requested(runtime: Path) -> bool:
    """Startup only: ``trace`` in the flag file turns on tracemalloc."""
    if diagnostics_requested(runtime) == "trace":
        import tracemalloc
        if not tracemalloc.is_tracing():
            tracemalloc.start(4)
        return True
    return False


def diagnostics_snapshot(top: int = 15) -> dict[str, Any]:
    """Memory picture for leak hunting: process counters, .NET heap, Python
    object counts by type and (when tracing) the top allocation sites."""
    sample: dict[str, Any] = {"at": round(time.time(), 1), **process_sample(), **_process_memory()}
    try:
        from System import GC  # type: ignore[import-not-found]
        sample["managed_bytes"] = int(GC.GetTotalMemory(False))
    except Exception:
        pass
    counts: dict[str, int] = {}
    objects = gc.get_objects()
    for obj in objects:
        name = type(obj).__name__
        counts[name] = counts.get(name, 0) + 1
    sample["py_objects"] = len(objects)
    del objects
    sample["py_types"] = sorted(counts.items(), key=lambda item: -item[1])[:top]
    try:
        import tracemalloc
        if tracemalloc.is_tracing():
            sample["traced_bytes"] = tracemalloc.get_traced_memory()[0]
            sample["traced_top"] = [f"{stat.size // 1024}KB {stat.traceback[0].filename.rsplit(os.sep, 1)[-1]}:{stat.traceback[0].lineno}"
                                    for stat in tracemalloc.take_snapshot().statistics("lineno")[:top]]
    except Exception:
        pass
    return sample


def write_diagnostics(runtime: Path, logs: Path) -> bool:
    """Append one snapshot to ``logs/diagnostics.jsonl`` while the flag file
    exists (off by default; the file is capped and restarted when full)."""
    if diagnostics_requested(runtime) is None:
        return False
    target = Path(logs) / DIAGNOSTICS_FILE
    try:
        if target.exists() and target.stat().st_size > DIAGNOSTICS_MAX_BYTES:
            target.unlink()
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(diagnostics_snapshot(), ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


class HealthWatchdog:
    """Decides when a clean self-restart is warranted.

    A limit must be exceeded on ``confirm`` consecutive checks, the process
    must have run for ``min_uptime`` and the last restart (persisted, so it
    survives the restart itself) must be ``min_interval`` ago.
    """

    LIMITS = {"private_bytes": 400 * 1024 * 1024, "handles": 10_000, "gdi": 5_000, "user": 5_000, "threads": 200}

    def __init__(self, state_path: Path, *, limits: dict[str, int] | None = None, confirm: int = 3,
                 min_interval: float = 6 * 3600.0, min_uptime: float = 3600.0,
                 clock: Callable[[], float] = time.time, started_at: float | None = None) -> None:
        self.state_path = Path(state_path)
        self.limits = dict(self.LIMITS if limits is None else limits)
        self.confirm = confirm
        self.min_interval = min_interval
        self.min_uptime = min_uptime
        self.clock = clock
        self.started_at = clock() if started_at is None else started_at
        self.strikes = 0
        self.logged = False

    def over_limit(self, sample: dict[str, int]) -> str | None:
        for name, limit in self.limits.items():
            value = sample.get(name)
            if isinstance(value, int) and value > limit:
                return f"{name}={value}>{limit}"
        return None

    def last_restart(self) -> float:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8")).get("last_restart")
            return float(value) if isinstance(value, (int, float)) else 0.0
        except (OSError, ValueError, AttributeError):
            return 0.0

    def check(self, sample: dict[str, int]) -> str | None:
        reason = self.over_limit(sample)
        if reason is None:
            self.strikes, self.logged = 0, False
            return None
        self.strikes += 1
        if not self.logged:
            self.logged = True
            LOG.warning("health: over limit (%s)", reason)
        now = self.clock()
        if (self.strikes < self.confirm or now - self.started_at < self.min_uptime
                or now - self.last_restart() < self.min_interval):
            return None
        return reason

    def record_restart(self) -> None:
        from .storage import atomic_write_text
        try:
            atomic_write_text(self.state_path, json.dumps({"last_restart": self.clock()}))
        except OSError:
            pass


class Housekeeper:
    """Background loop: daily maintenance and a 10-minute health check."""

    def __init__(self, maintenance: Callable[[], dict[str, Any]], *, watchdog: HealthWatchdog | None = None,
                 sampler: Callable[[], dict[str, int]] = process_sample,
                 restart: Callable[[str], bool] | None = None, is_idle: Callable[[], bool] = lambda: False,
                 first_maintenance: float = 600.0, maintenance_interval: float = DAY,
                 check_interval: float = 600.0, clock: Callable[[], float] = time.monotonic,
                 trim: Callable[[], dict[str, Any]] | None = None, first_trim: float = 180.0,
                 trim_interval: float = 3600.0, diagnostics: Callable[[], Any] | None = None) -> None:
        self.maintenance = maintenance
        self.trim = trim
        self.trim_interval = trim_interval
        self.diagnostics = diagnostics
        self.watchdog = watchdog
        self.sampler = sampler
        self.restart = restart
        self.is_idle = is_idle
        self.maintenance_interval = maintenance_interval
        self.check_interval = check_interval
        self.clock = clock
        now = clock()
        self.next_maintenance = now + first_maintenance
        self.next_check = now + check_interval
        self.next_trim = now + first_trim
        self.pending: str | None = None
        self.restarted = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def step(self) -> None:
        """One iteration (also driven directly by tests with a fake clock)."""
        now = self.clock()
        if now >= self.next_maintenance:
            self.next_maintenance = now + self.maintenance_interval
            try:
                summary = self.maintenance()
                collected = gc.collect()
                LOG.info("maintenance %s gc=%d", json.dumps(summary, sort_keys=True), collected)
            except Exception as exc:
                LOG.info("maintenance failed (%s)", type(exc).__name__)
        if self.trim is not None and now >= self.next_trim:
            # Memory is kept in check by cleanup; the watchdog below is only
            # an emergency backstop.
            self.next_trim = now + self.trim_interval
            try:
                LOG.info("memory trim %s", json.dumps(self.trim(), sort_keys=True))
            except Exception as exc:
                LOG.info("memory trim failed (%s)", type(exc).__name__)
        check_due = now >= self.next_check
        if check_due:
            self.next_check = now + self.check_interval
        if self.diagnostics is not None and check_due:
            try:
                self.diagnostics()
            except Exception:
                pass
        if self.watchdog is not None and check_due:
            try:
                reason = self.watchdog.check(self.sampler())
            except Exception:
                reason = None
            if reason:
                self.pending = reason
        if self.pending and not self.restarted and self.restart is not None and self.is_idle():
            reason, self.pending = self.pending, None
            LOG.warning("health: restarting cleanly (%s)", reason)
            self.watchdog.record_restart() if self.watchdog is not None else None
            try:
                self.restarted = bool(self.restart(reason))
            except Exception as exc:
                LOG.warning("health: restart failed (%s)", type(exc).__name__)

    def _run(self) -> None:
        while not self.stop_event.wait(min(60.0, self.check_interval)):
            self.step()

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="vram-radar-housekeeping", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()