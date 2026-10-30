"""Read-only SenseCore ACP job inventory through the user's existing SCO profile.

SCO list flags and SCO_ZONE are grounded in the archived 2026-10-23 CLI help.
This module never initializes credentials, submits jobs or estimates free VRAM.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from .models import ServerProfile
from .connectors import (
    ConnectorFailure, _REMOTE_CAPTURE_SLOTS, _run_bounded_process,
    redact_command_preview,
)

PAGE_SIZE = 100
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "CANCELED", "STOPPED", "SUSPENDED"})


def _text(value: Any, limit: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in value if ord(ch) >= 32 and ord(ch) != 127)
    return redact_command_preview(text, limit=limit)[0]


def build_list_command(server: ServerProfile) -> list[str]:
    """Only this fixed read-only subcommand can be issued by the connector."""
    return [
        server.acp_sco_path, "--profile", server.acp_profile,
        "acp", "jobs", "list", "--workspace-name=" + server.acp_workspace,
        "--aec2-name=" + server.acp_cluster, "--format=json",
        f"--page-size={PAGE_SIZE}", "--page-token=1",
    ]


def parse_jobs(payload: Any) -> list[dict[str, Any]]:
    # SCO's observed JSON output is an array. Refuse schema drift instead of
    # treating an API error object or unknown envelope as an empty queue.
    if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
        raise ConnectorFailure("acp_parse_failed", "云任务列表格式不受支持", retryable=False)
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
            raise ConnectorFailure("acp_parse_failed", "云任务返回了无效的任务标识", retryable=False)
        job_id = _text(row["name"])
        if not job_id or job_id in seen:
            raise ConnectorFailure("acp_parse_failed", "云任务返回了重复的任务标识", retryable=False)
        seen.add(job_id)
        ownership = row.get("ownership") if isinstance(row.get("ownership"), dict) else {}
        pool = row.get("resource_pool") if isinstance(row.get("resource_pool"), dict) else {}
        # GPU shape names and requested limits are not measured allocations.
        # Until a documented numeric request field is supported, show unknown.
        jobs.append({
            "job_id": job_id,
            "name": _text(row.get("display_name")) or job_id,
            "state": _text(row.get("state"), 64).upper() or "UNKNOWN",
            "user": _text(ownership.get("user_name")),
            "owner_scope": "unknown",
            "requested_gpus": None,
            "gpu_count": None,
            "cluster": _text(pool.get("name")),
            "submitted_at": _text(row.get("create_time"), 80),
            "reason": _text(row.get("reason"), 512),
            "nodes": "",
        })
    return jobs


def query_sensecore_acp(server: ServerProfile) -> dict[str, Any]:
    from .acp_transport import current_transport
    transport = current_transport(server)
    if transport is None and server.acp_protected_auth:
        raise ConnectorFailure("acp_transport_config_invalid", "受保护的云任务授权不可用",
                               retryable=False, state="security_blocked")
    input_data = None
    cwd = None
    if transport is not None:
        argv = transport.command()
        try:
            input_data = transport.request(server)
        except ValueError:
            raise ConnectorFailure("acp_transport_config_invalid", "云任务连接绑定已变更",
                                   retryable=False, state="security_blocked") from None
        cwd = transport.cwd
    else:
        argv = build_list_command(server)
        executable = shutil.which(argv[0])
        if executable is None or executable.lower().endswith((".cmd", ".bat", ".ps1", ".py")):
            raise ConnectorFailure("acp_cli_missing", "未找到 sco，请选择本机已安装的 sco", retryable=False, state="misconfigured")
        argv[0] = executable
    env = dict(os.environ)
    env["SCO_ZONE"] = server.acp_zone
    # Explicit profile owns authentication; do not accidentally inherit another
    # account's process-scoped credentials. Never inspect or copy profile files.
    for key in ("SCO_ACCESS_KEY_ID", "SCO_ACCESS_KEY_SECRET"):
        env.pop(key, None)
    try:
        with _REMOTE_CAPTURE_SLOTS:
            result = _run_bounded_process(
                argv, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                timeout=server.connect_timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
                stdout_limit=MAX_OUTPUT_BYTES, stderr_limit=16384,
                input_data=input_data, cwd=cwd,
            )
    except subprocess.TimeoutExpired as exc:
        raise ConnectorFailure("acp_timeout", "云任务查询超时", retryable=True) from exc
    except OSError as exc:
        raise ConnectorFailure("acp_cli_failed", "无法启动 sco", retryable=False, state="misconfigured") from exc
    if result.stdout_truncated:
        raise ConnectorFailure("acp_response_too_large", "云任务响应超过长度上限", retryable=False)
    if result.returncode:
        # Raw CLI output may include credentials, command/env or signed URLs.
        # Classify in memory, emit only fixed messages, never log raw stderr.
        detail = bytes(result.stderr).decode("utf-8", errors="replace").lower()
        if "sco-timeout" in detail:
            raise ConnectorFailure("acp_timeout", "云任务查询超时", retryable=True)
        if any(word in detail for word in ("unauthenticated", "unauthorized", "credential", "access key")):
            raise ConnectorFailure("acp_auth_required", "sco 配置认证失败，请在本机 sco 中完成登录", retryable=False, state="misconfigured")
        if any(word in detail for word in ("forbidden", "permission denied")):
            raise ConnectorFailure("acp_access_denied", "云任务拒绝本次查询，请核对配置名、工作区和集群", retryable=False)
        if "protected-child-refused" in detail:
            raise ConnectorFailure(
                "acp_transport_config_invalid",
                "受保护查询通道未授权或已变更",
                retryable=False,
                state="security_blocked",
            )
        raise ConnectorFailure("acp_query_failed", "云任务列表查询失败，请检查 sco 配置与平台设置", retryable=True)

    try:
        jobs = parse_jobs(json.loads(bytes(result.stdout).decode("utf-8-sig")))
    except (ValueError, UnicodeError) as exc:
        raise ConnectorFailure("acp_parse_failed", "sco 未返回有效的任务列表", retryable=False) from exc
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["state"]] = counts.get(job["state"], 0) + 1
    page_size = transport.page_size if transport is not None else PAGE_SIZE
    # One bounded page only: a full page means more workspace jobs may exist.
    list_complete = len(jobs) < page_size
    return {
        "server_id": server.id, "display_name": server.display_name,
        "backend": "sensecore_acp", "view_kind": "platform",
        "total_gpus": None, "free_gpus": None,
        "total_vram_gib": None, "free_vram_gib": None,
        "nodes": [], "gpus": [], "jobs": jobs,
        "platform": {"workspace": server.acp_workspace, "cluster": server.acp_cluster,
                     "zone": server.acp_zone, "job_scope": "workspace_visible",
                     "page_size": page_size, "list_complete": list_complete,
                     "live_vram_supported": False},
        "tasks": {"current_user": "", "active": [j for j in jobs if j["state"] not in TERMINAL_STATES],
                  "recent": [j for j in jobs if j["state"] in TERMINAL_STATES],
                  "counts": counts, "history_supported": False, "history_window_hours": None},
    }
