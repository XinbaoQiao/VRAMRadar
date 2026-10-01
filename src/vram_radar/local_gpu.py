"""Lightweight, read-only GPU telemetry on the desktop running Radar.

Backends, tried in order (first one that returns GPUs wins):

1. **NVML** (``nvml.dll`` / ``libnvidia-ml.so.1``) through ctypes: no child
   process, works without ``nvidia-smi`` in PATH and for non-admin users.
   Calls run on a watchdog thread; a hung driver disables NVML for the rest
   of the session instead of freezing the refresh loop.
2. **nvidia-smi** (PATH, System32, legacy NVSMI folder), bounded by a timeout
   that kills the process tree.  Output is machine CSV (not localized);
   ``[N/A]`` / ``[Not Supported]`` fields stay unknown.
3. **Windows GPU counters** for any vendor (AMD, Intel, NVIDIA without NVML,
   hybrid laptops, VMs with a GPU): adapters from DXGI (name, dedicated and
   shared memory, software adapters skipped) and system-wide usage from the
   locale-independent PDH counters ``GPU Adapter Memory`` and ``GPU Engine``.
   Integrated GPUs with little dedicated memory report shared memory.
4. **Linux amdgpu sysfs** (``mem_info_vram_*``, ``gpu_busy_percent``).

Nothing here starts compute work, changes driver state or needs elevation.
"""

from __future__ import annotations

import glob
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from .connectors import ConnectorFailure, _run_bounded_process, parse_nvidia_smi_rows
from .models import ServerProfile

GIB = 1024 ** 3
INTEGRATED_DEDICATED_LIMIT = 512 * 1024 ** 2


class BackendUnavailable(Exception):
    """This backend cannot read GPUs here; try the next one."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ----------------------------------------------------------------------------
# 1. NVML
# ----------------------------------------------------------------------------
_NVML: dict[str, Any] = {"lib": None, "initialized": False, "hung": False}
_NVML_LOCK = threading.Lock()


def _nvml_candidates() -> list[str]:
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        store = sorted(glob.glob(str(system_root / "System32" / "DriverStore" / "FileRepository"
                                     / "nv*" / "nvml.dll")))
        return [str(system_root / "System32" / "nvml.dll"),
                str(program_files / "NVIDIA Corporation" / "NVSMI" / "nvml.dll"), *store[-2:]]
    if sys.platform == "darwin":
        return []
    return ["libnvidia-ml.so.1", "libnvidia-ml.so"]


def _nvml_library():
    if _NVML["lib"] is not None:
        return _NVML["lib"]
    import ctypes
    for candidate in _nvml_candidates():
        if os.name == "nt" and not os.path.isfile(candidate):
            continue
        try:
            _NVML["lib"] = ctypes.CDLL(candidate)
            return _NVML["lib"]
        except OSError:
            continue
    raise BackendUnavailable("nvml_missing")


def _nvml_read() -> list[dict[str, Any]]:
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

    class MemoryV2(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                    ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

    class Utilization(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    lib = _nvml_library()
    with _NVML_LOCK:
        if not _NVML["initialized"]:
            init = getattr(lib, "nvmlInit_v2", None) or lib.nvmlInit
            if init() != 0:
                raise BackendUnavailable("nvml_init_failed")  # no NVIDIA GPU / driver too old
            _NVML["initialized"] = True
        try:
            count = ctypes.c_uint()
            get_count = getattr(lib, "nvmlDeviceGetCount_v2", None) or lib.nvmlDeviceGetCount
            if get_count(ctypes.byref(count)) != 0:
                raise BackendUnavailable("nvml_count_failed")
            get_handle = getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None) or lib.nvmlDeviceGetHandleByIndex
            gpus = []
            for index in range(min(count.value, 64)):
                handle = ctypes.c_void_p()
                if get_handle(ctypes.c_uint(index), ctypes.byref(handle)) != 0:
                    continue  # e.g. a GPU lost or without permission: skip it, keep others
                name = ctypes.create_string_buffer(96)
                uuid = ctypes.create_string_buffer(96)
                if lib.nvmlDeviceGetName(handle, name, ctypes.c_uint(96)) != 0:
                    name.value = b"NVIDIA GPU"
                if lib.nvmlDeviceGetUUID(handle, uuid, ctypes.c_uint(96)) != 0:
                    uuid.value = b""
                # v2 (driver R510+) excludes driver-reserved memory, matching
                # nvidia-smi; older drivers fall back to v1.
                memory = MemoryV2()
                memory.version = ctypes.sizeof(MemoryV2) | (2 << 24)
                get_v2 = getattr(lib, "nvmlDeviceGetMemoryInfo_v2", None)
                if get_v2 is None or get_v2(handle, ctypes.byref(memory)) != 0:
                    memory = Memory()
                    if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) != 0:
                        continue
                utilization = Utilization()
                busy = (float(utilization.gpu)
                        if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(utilization)) == 0 else None)
                temperature = ctypes.c_uint()
                temp = (float(temperature.value)
                        if lib.nvmlDeviceGetTemperature(handle, ctypes.c_int(0), ctypes.byref(temperature)) == 0
                        else None)
                gpus.append(_gpu_row(str(index), uuid.value.decode("utf-8", "replace") or None,
                                     name.value.decode("utf-8", "replace") or "NVIDIA GPU",
                                     memory.total, memory.used, memory.free, busy, temp))
        except BackendUnavailable:
            _nvml_reset(lib)
            raise
        except Exception as exc:
            _nvml_reset(lib)
            raise BackendUnavailable("nvml_failed") from exc
    if not gpus:
        raise BackendUnavailable("nvml_no_gpu")
    return gpus


def _nvml_reset(lib) -> None:
    try:
        lib.nvmlShutdown()
    except Exception:
        pass
    _NVML["initialized"] = False


def _nvml_query(timeout: float = 4.0) -> list[dict[str, Any]]:
    if _NVML["hung"]:
        raise BackendUnavailable("nvml_hung")
    result: dict[str, Any] = {}

    def work():
        try:
            result["gpus"] = _nvml_read()
        except BaseException as exc:  # handed back to the caller below
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="local-gpu-nvml")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        _NVML["hung"] = True  # never block a refresh on this driver again
        raise BackendUnavailable("nvml_timeout")
    if "error" in result:
        error = result["error"]
        raise error if isinstance(error, BackendUnavailable) else BackendUnavailable("nvml_failed")
    return result["gpus"]


# ----------------------------------------------------------------------------
# 2. nvidia-smi
# ----------------------------------------------------------------------------
def _nvidia_smi_path() -> str | None:
    executable = shutil.which("nvidia-smi")
    if executable:
        return executable
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    elif os.path.isfile("/usr/bin/nvidia-smi"):
        return "/usr/bin/nvidia-smi"
    return None


def _smi_query(executable: str, server: ServerProfile) -> list[dict[str, Any]]:
    try:
        result = _run_bounded_process(
            [executable,
             "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            stdin=subprocess.DEVNULL,
            timeout=min(server.connect_timeout_seconds, 5),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            env=None, stdout_limit=1024 * 1024, stderr_limit=16 * 1024,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectorFailure(
            "local_gpu_timeout", "本地 GPU 查询超时，将在下一次刷新重试", retryable=True,
        ) from exc
    except OSError as exc:
        raise ConnectorFailure(
            "local_gpu_unavailable", "暂时无法读取本地 GPU，请检查 NVIDIA 驱动", retryable=True,
        ) from exc
    if result.stdout_truncated:
        raise ConnectorFailure("parse_failed", "本地 GPU 返回的数据超过读取上限", retryable=True)
    if result.returncode != 0:
        # Driver stderr is intentionally excluded from logs and cache.
        raise ConnectorFailure(
            "local_gpu_unavailable", "本地 nvidia-smi 查询失败，请检查 NVIDIA 驱动", retryable=True,
        )
    return parse_nvidia_smi_rows(result.stdout.decode("utf-8", errors="replace"))


# ----------------------------------------------------------------------------
# 3. Windows: DXGI adapters + PDH counters (any vendor)
# ----------------------------------------------------------------------------
_LUID = re.compile(r"luid_0x([0-9a-fA-F]{1,8})_0x([0-9a-fA-F]{1,8})", re.IGNORECASE)
_ENGINE_TYPE = re.compile(r"engtype_(.+)$", re.IGNORECASE)


def luid_key(high: int, low: int) -> str:
    return f"{high & 0xFFFFFFFF:08x}_{low & 0xFFFFFFFF:08x}"


def instance_luid(name: str) -> str | None:
    match = _LUID.search(name or "")
    return luid_key(int(match.group(1), 16), int(match.group(2), 16)) if match else None


def combine_windows_counters(adapters, dedicated, shared, engines) -> list[dict[str, Any]]:
    """Pure merge of DXGI adapters with PDH instance->value maps.

    ``adapters``: [{"luid", "name", "dedicated", "shared"}] (bytes).  Usage
    maps are {instance name: value}.  Utilization follows Task Manager: sum
    per engine type, busiest type wins.
    """
    def per_luid(values):
        totals: dict[str, float] = {}
        for name, value in values.items():
            key = instance_luid(name)
            if key is not None and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                totals[key] = totals.get(key, 0.0) + float(value)
        return totals

    dedicated_used, shared_used = per_luid(dedicated), per_luid(shared)
    busy: dict[str, dict[str, float]] = {}
    for name, value in engines.items():
        key = instance_luid(name)
        kind = _ENGINE_TYPE.search(name or "")
        if key is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        group = busy.setdefault(key, {})
        engine = kind.group(1) if kind else "other"
        group[engine] = group.get(engine, 0.0) + max(0.0, float(value))
    gpus = []
    for index, adapter in enumerate(adapters):
        key = adapter["luid"]
        integrated = adapter["dedicated"] < INTEGRATED_DEDICATED_LIMIT and adapter["shared"] > 0
        if integrated:
            total, used = adapter["shared"], shared_used.get(key)
            label = adapter["name"] + " · 共享显存"
        else:
            total, used = adapter["dedicated"], dedicated_used.get(key)
            label = adapter["name"]
        if not total or used is None:
            continue  # counters missing for this adapter: never invent usage
        used = min(float(used), float(total))
        groups = busy.get(key)
        utilization = min(100.0, max(groups.values())) if groups else None
        gpus.append(_gpu_row(str(index), "LUID-" + key, label, total, used, total - used,
                             None if utilization is None else round(utilization, 1), None))
    return gpus


def _dxgi_adapters() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                    ("d4", ctypes.c_ubyte * 8)]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class Desc1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint),
                    ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                    ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]

    try:
        dxgi = ctypes.WinDLL("dxgi")
    except OSError as exc:
        raise BackendUnavailable("dxgi_missing") from exc
    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    factory = ctypes.c_void_p()
    dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    dxgi.CreateDXGIFactory1.restype = ctypes.c_long
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0 or not factory.value:
        raise BackendUnavailable("dxgi_factory_failed")

    def method(obj, index, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])

    adapters = []
    try:
        enum_adapters = method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for index in range(16):
            adapter = ctypes.c_void_p()
            if enum_adapters(factory, index, ctypes.byref(adapter)) != 0 or not adapter.value:
                break  # DXGI_ERROR_NOT_FOUND
            try:
                desc = Desc1()
                if method(adapter, 10, ctypes.POINTER(Desc1))(adapter, ctypes.byref(desc)) != 0:
                    continue
                if desc.Flags & 2 or desc.VendorId == 0x1414:  # software / Basic Render Driver
                    continue
                adapters.append({"luid": luid_key(desc.AdapterLuid.HighPart, desc.AdapterLuid.LowPart),
                                 "name": desc.Description.strip() or "GPU", "vendor": desc.VendorId,
                                 "dedicated": int(desc.DedicatedVideoMemory),
                                 "shared": int(desc.SharedSystemMemory)})
            finally:
                method(adapter, 2)(adapter)
    finally:
        method(factory, 2)(factory)
    return adapters


class _PdhSampler:
    """Keeps one PDH query open so rate counters (engine utilization) have a
    previous sample; the first read waits briefly for a second sample."""

    PATHS = {"dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
             "shared": r"\GPU Adapter Memory(*)\Shared Usage",
             "engine": r"\GPU Engine(*)\Utilization Percentage"}

    def __init__(self):
        self.lock = threading.Lock()
        self.query = None
        self.counters: dict[str, Any] = {}
        self.primed = False

    def _open(self):
        import ctypes
        from ctypes import wintypes
        pdh = ctypes.WinDLL("pdh")
        query = wintypes.HANDLE()
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
            raise BackendUnavailable("pdh_unavailable")
        counters = {}
        add = getattr(pdh, "PdhAddEnglishCounterW", None) or pdh.PdhAddCounterW  # locale-independent
        for key, path in self.PATHS.items():
            counter = wintypes.HANDLE()
            if add(query, ctypes.c_wchar_p(path), None, ctypes.byref(counter)) == 0:
                counters[key] = counter
        if "dedicated" not in counters:
            pdh.PdhCloseQuery(query)
            raise BackendUnavailable("gpu_counters_missing")  # e.g. Windows < 10 1709, VM without GPU
        self.pdh, self.query, self.counters, self.primed = pdh, query, counters, False

    def _array(self, counter) -> dict[str, float]:
        import ctypes
        from ctypes import wintypes

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", Value)]

        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        fmt = 0x00000200 | 0x00008000  # PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        status = self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if (status & 0xFFFFFFFF) != 0x800007D2 or not size.value:  # PDH_MORE_DATA
            return {}
        buffer = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buffer) != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(Item))
        return {items[i].szName: items[i].FmtValue.doubleValue for i in range(count.value)
                if items[i].szName and items[i].FmtValue.CStatus in (0, 1)}

    def read(self) -> tuple[dict, dict, dict]:
        with self.lock:
            if self.query is None:
                self._open()
            if self.pdh.PdhCollectQueryData(self.query) != 0:
                self.close()
                raise BackendUnavailable("pdh_collect_failed")
            if not self.primed:
                time.sleep(0.25)
                self.pdh.PdhCollectQueryData(self.query)
                self.primed = True
            return tuple(self._array(self.counters[key]) if key in self.counters else {}
                         for key in ("dedicated", "shared", "engine"))

    def close(self):
        if self.query is not None:
            try:
                self.pdh.PdhCloseQuery(self.query)
            except Exception:
                pass
        self.query = None


_PDH = _PdhSampler()


def _windows_query() -> list[dict[str, Any]]:
    if os.name != "nt":
        raise BackendUnavailable("not_windows")
    try:
        adapters = _dxgi_adapters()
    except BackendUnavailable:
        raise
    except Exception as exc:
        raise BackendUnavailable("dxgi_failed") from exc
    if not adapters:
        raise BackendUnavailable("no_hardware_adapter")  # RDP/VM with only Basic Render
    try:
        dedicated, shared, engines = _PDH.read()
    except BackendUnavailable:
        raise
    except Exception as exc:
        _PDH.close()
        raise BackendUnavailable("pdh_failed") from exc
    gpus = combine_windows_counters(adapters, dedicated, shared, engines)
    if not gpus:
        raise BackendUnavailable("gpu_counters_empty")
    return gpus


# ----------------------------------------------------------------------------
# 4. Linux amdgpu sysfs
# ----------------------------------------------------------------------------
def _sysfs_query(root: str = "/sys/class/drm") -> list[dict[str, Any]]:
    gpus = []
    for device in sorted(glob.glob(os.path.join(root, "card[0-9]*", "device"))):
        def number(name):
            try:
                with open(os.path.join(device, name), encoding="ascii") as handle:
                    return float(handle.read().strip())
            except (OSError, ValueError):
                return None
        total, used = number("mem_info_vram_total"), number("mem_info_vram_used")
        if not total or used is None:
            continue
        temperature = None
        for sensor in glob.glob(os.path.join(device, "hwmon", "hwmon*", "temp1_input")):
            try:
                with open(sensor, encoding="ascii") as handle:
                    temperature = float(handle.read().strip()) / 1000
                break
            except (OSError, ValueError):
                continue
        gpus.append(_gpu_row(str(len(gpus)), None, "AMD GPU", total, used, total - used,
                             number("gpu_busy_percent"), temperature))
    if not gpus:
        raise BackendUnavailable("no_sysfs_gpu")
    return gpus


# ----------------------------------------------------------------------------
def _gpu_row(index, uuid, name, total_bytes, used_bytes, free_bytes, utilization, temperature):
    return {"gpu_index": index, "gpu_uuid": uuid, "gpu_type": name,
            "memory_total_gib": round(float(total_bytes) / GIB, 2),
            "memory_used_gib": round(float(used_bytes) / GIB, 2),
            "memory_free_gib": round(max(0.0, float(free_bytes)) / GIB, 2),
            "utilization_percent": utilization, "temperature_c": temperature}


def query_local_gpu(server: ServerProfile) -> dict[str, Any]:
    gpus, source, smi_failure = None, None, None
    try:
        gpus, source = _nvml_query(), "NVML"
    except BackendUnavailable:
        pass
    if gpus is None:
        executable = _nvidia_smi_path()
        if executable is not None:
            try:
                gpus, source = _smi_query(executable, server), "nvidia-smi"
            except ConnectorFailure as exc:
                smi_failure = exc
    if gpus is None and os.name == "nt":
        try:
            gpus, source = _windows_query(), "Windows GPU counters"
        except BackendUnavailable:
            pass
    if gpus is None and sys.platform.startswith("linux"):
        try:
            gpus, source = _sysfs_query(), "sysfs"
        except BackendUnavailable:
            pass
    if gpus is None:
        if smi_failure is not None:
            raise smi_failure
        raise ConnectorFailure(
            "local_gpu_missing",
            "未检测到可读取的本地 GPU：NVIDIA 需要安装驱动；AMD/Intel 需要 Windows 10 1709 及以上的 GPU 性能计数器",
            retryable=False, state="misconfigured",
        )
    for gpu in gpus:
        for key in ("memory_total_gib", "memory_used_gib", "memory_free_gib",
                    "utilization_percent", "temperature_c"):
            value = gpu[key]
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ConnectorFailure("parse_failed", "本地 GPU 返回了无效的指标", retryable=True)
    note = ("本地模式仅采集显存、GPU 利用率和温度。" if source != "Windows GPU counters" else
            "本地模式读取 Windows GPU 计数器：显存与利用率（无温度）。")
    return {
        "server_id": server.id, "display_name": server.display_name, "backend": "local",
        "view_kind": "live-memory", "total_gpus": len(gpus),
        "total_vram_gib": round(sum(gpu["memory_total_gib"] for gpu in gpus), 2),
        "free_vram_gib": round(sum(gpu["memory_free_gib"] for gpu in gpus), 2),
        "gpus": gpus,
        "processes": {
            "supported": False, "source": "local " + source, "active": [],
            "warning": note,
        },
    }
