"""Lightweight, read-only GPU telemetry on the desktop running Radar."""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .connectors import ConnectorFailure, _run_bounded_process, parse_nvidia_smi_rows
from .models import ServerProfile


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
    return None


def query_local_gpu(server: ServerProfile) -> dict[str, Any]:
    executable = _nvidia_smi_path()
    if executable is None:
        raise ConnectorFailure(
            "local_nvidia_smi_missing", "找不到本地 nvidia-smi，请检查 NVIDIA 驱动是否已安装",
            retryable=False, state="misconfigured",
        )
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
    gpus = parse_nvidia_smi_rows(result.stdout.decode("utf-8", errors="replace"))
    for gpu in gpus:
        for key in ("memory_total_gib", "memory_used_gib", "memory_free_gib",
                    "utilization_percent", "temperature_c"):
            value = gpu[key]
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ConnectorFailure("parse_failed", "本地 GPU 返回了无效的指标", retryable=True)
    return {
        "server_id": server.id, "display_name": server.display_name, "backend": "local",
        "view_kind": "live-memory", "total_gpus": len(gpus),
        "total_vram_gib": round(sum(gpu["memory_total_gib"] for gpu in gpus), 2),
        "free_vram_gib": round(sum(gpu["memory_free_gib"] for gpu in gpus), 2),
        "gpus": gpus,
        "processes": {
            "supported": False, "source": "local nvidia-smi", "active": [],
            "warning": "本地模式仅采集显存、GPU 利用率和温度。",
        },
    }
