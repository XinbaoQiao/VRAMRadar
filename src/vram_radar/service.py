from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .connectors import (
    MAX_CONCURRENT_REMOTE_CAPTURES,
    PASSWORD_FALLBACK_AUTH_CODES,
    ConnectorFailure,
    query_account_directory,
    query_account_directory_version,
    query_server,
    resolve_identity_path,
    resolve_ssh_config_path,
)
from .models import Profile, ServerProfile
from .server_catalog import openssh_config_dependency_fingerprint
from .storage import SnapshotCache


RETRY_SECONDS = (15, 30, 60, 120, 300)
# Beyond these bounds, serializing every Slurm node on every dashboard refresh
# produces a material UI and IPC cost. Either dimension independently enables
# compact mode so dense eight-GPU clusters are covered well before 1,000 GPUs.
LARGE_CLUSTER_NODE_THRESHOLD = 64
LARGE_CLUSTER_GPU_THRESHOLD = 256
MAX_CLUSTER_PAGE_SIZE = 250
MAX_REFRESH_WORKERS = MAX_CONCURRENT_REMOTE_CAPTURES
MAX_DIRECTORY_CACHE_ROOTS = 96
DIRECTORY_SINGLE_FLIGHT_WAIT_SECONDS = 180
# A reopened tree within this window performs no remote I/O. Afterwards the
# service probes only the root metadata. A periodic deep refresh bounds
# staleness for in-place file size/mtime changes that some filesystems do not
# propagate to their parent directory timestamp.
DIRECTORY_CACHE_FRESH_SECONDS = 15.0
DIRECTORY_CACHE_DEEP_REFRESH_SECONDS = 120.0
DIRECTORY_CACHE_PROBE_RETRY_SECONDS = 15.0

_ISSUE_STATE_TOKENS = (
    "down",
    "drain",
    "drng",
    "fail",
    "error",
    "unknown",
    "unk",
    "maint",
    "invalid",
    "inval",
    "reboot",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def future_utc(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded_integer(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name}必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是整数") from exc
    if parsed != value and not (isinstance(value, str) and str(parsed) == value.strip()):
        raise ValueError(f"{name}必须是整数")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name}必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _memory_requirement(value: Any) -> float:
    try:
        required = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("所需单卡显存必须是数字") from exc
    if not 0 <= required <= 1000:
        raise ValueError("所需单卡显存必须在 0 到 1000 GiB 之间")
    return required


def _node_has_issue(node: dict[str, Any]) -> bool:
    state = str(node.get("state") or "").casefold()
    return not state or any(token in state for token in _ISSUE_STATE_TOKENS)


def _per_gpu_memory(node: dict[str, Any]) -> float:
    per_gpu = node.get("memory_per_gpu_gib")
    free_units = int(node.get("free_gpus") or 0)
    if per_gpu is None and free_units:
        total_free = node.get("free_vram_gib")
        per_gpu = float(total_free) / free_units if total_free is not None else 0
    return float(per_gpu or 0)


def _candidate_pool(
    server: dict[str, Any],
    allocations: list[dict[str, Any]],
    *,
    requested_gpu_count: int,
    available_units: int,
    same_node: bool,
) -> dict[str, Any]:
    selected = allocations
    minimum_memory = min((float(item["available_memory_gib"]) for item in selected), default=0)
    selected_memory = round(
        sum(float(item["available_memory_gib"]) * int(item["selected_units"]) for item in selected), 2
    )
    gpu_types = sorted({str(item.get("gpu_type") or "") for item in selected if item.get("gpu_type")})
    partitions = sorted({str(item.get("partition") or "") for item in selected if item.get("partition")})
    return {
        "server_id": server["server_id"],
        "display_name": server.get("display_name") or server["server_id"],
        "backend": server.get("backend") or "",
        "gpu_count": requested_gpu_count,
        "available_units": available_units,
        "minimum_memory_gib": minimum_memory,
        "selected_memory_gib": selected_memory,
        "gpu_types": gpu_types,
        "partitions": partitions,
        "same_node": same_node,
        "fragmented": len(selected) > 1,
        "allocations": selected,
    }


def recommend_resources(
    snapshot: dict[str, Any],
    gpu_count: int = 1,
    min_memory_gib: float = 0,
    gpu_type: str = "",
    partition: str = "",
    same_node: bool = True,
    limit: int = 10,
) -> dict[str, Any]:
    """Return explicit read-only GPU pools without hiding cross-node fragmentation."""

    requested_count = _bounded_integer(gpu_count, name="GPU 数量", minimum=1, maximum=10000)
    required_memory = _memory_requirement(min_memory_gib)
    result_limit = _bounded_integer(limit, name="结果数量", minimum=1, maximum=100)
    type_filter = str(gpu_type or "").strip().casefold()
    partition_filter = str(partition or "").strip().casefold()
    require_same_node = bool(same_node)
    candidates: list[dict[str, Any]] = []

    for server in snapshot.get("servers", []):
        if server.get("connection", {}).get("state") != "online":
            continue
        view_kind = server.get("view_kind")
        if view_kind == "live-memory":
            if partition_filter:
                continue
            eligible: list[dict[str, Any]] = []
            for gpu in server.get("gpus", []):
                candidate_type = str(gpu.get("gpu_type") or "")
                available = float(gpu.get("memory_free_gib") or 0)
                if available < required_memory or (type_filter and type_filter not in candidate_type.casefold()):
                    continue
                eligible.append(
                    {
                        "location": f"GPU {gpu.get('gpu_index', '?')}",
                        "gpu_index": str(gpu.get("gpu_index", "?")),
                        "gpu_type": candidate_type,
                        "partition": "",
                        "available_memory_gib": available,
                        "available_units": 1,
                        "selected_units": 1,
                    }
                )
            eligible.sort(key=lambda item: (-item["available_memory_gib"], item["gpu_index"]))
            if len(eligible) >= requested_count:
                candidates.append(
                    _candidate_pool(
                        server,
                        eligible[:requested_count],
                        requested_gpu_count=requested_count,
                        available_units=len(eligible),
                        same_node=True,
                    )
                )
            continue

        if view_kind != "scheduler":
            continue
        eligible_nodes: list[dict[str, Any]] = []
        for node in server.get("nodes", []):
            free_units = int(node.get("free_gpus") or 0)
            candidate_type = str(node.get("gpu_type") or "")
            candidate_partition = str(node.get("partition") or "")
            per_gpu = _per_gpu_memory(node)
            if (
                free_units < 1
                or _node_has_issue(node)
                or per_gpu < required_memory
                or (type_filter and type_filter not in candidate_type.casefold())
                or (partition_filter and partition_filter not in candidate_partition.casefold())
            ):
                continue
            eligible_nodes.append(
                {
                    "location": str(node.get("node") or "Slurm node"),
                    "node": str(node.get("node") or ""),
                    "gpu_type": candidate_type,
                    "partition": candidate_partition,
                    "available_memory_gib": per_gpu,
                    "available_units": free_units,
                }
            )

        if require_same_node:
            for node in eligible_nodes:
                if node["available_units"] < requested_count:
                    continue
                allocation = dict(node)
                allocation["selected_units"] = requested_count
                candidates.append(
                    _candidate_pool(
                        server,
                        [allocation],
                        requested_gpu_count=requested_count,
                        available_units=node["available_units"],
                        same_node=True,
                    )
                )
            continue

        # Slurm allocations cannot safely be described as one pool across
        # unrelated partitions or GPU types. Build deterministic pools only
        # inside a matching partition/type/memory lane and label fragmentation.
        lanes: dict[tuple[str, str, float], list[dict[str, Any]]] = {}
        for node in eligible_nodes:
            lane = (
                str(node["partition"]),
                str(node["gpu_type"]),
                float(node["available_memory_gib"]),
            )
            lanes.setdefault(lane, []).append(node)
        for lane_nodes in lanes.values():
            lane_nodes.sort(key=lambda item: (-item["available_units"], item["location"]))
            available_units = sum(int(item["available_units"]) for item in lane_nodes)
            if available_units < requested_count:
                continue
            remaining = requested_count
            allocations: list[dict[str, Any]] = []
            for node in lane_nodes:
                selected_units = min(remaining, int(node["available_units"]))
                if selected_units:
                    allocation = dict(node)
                    allocation["selected_units"] = selected_units
                    allocations.append(allocation)
                    remaining -= selected_units
                if not remaining:
                    break
            candidates.append(
                _candidate_pool(
                    server,
                    allocations,
                    requested_gpu_count=requested_count,
                    available_units=available_units,
                    same_node=len(allocations) == 1,
                )
            )

    candidates.sort(
        key=lambda item: (
            -item["minimum_memory_gib"],
            item["fragmented"],
            len(item["allocations"]),
            -item["available_units"],
            item["server_id"],
            item["allocations"][0]["location"],
        )
    )
    criteria = {
        "gpu_count": requested_count,
        "min_memory_gib": required_memory,
        "gpu_type": str(gpu_type or "").strip(),
        "partition": str(partition or "").strip(),
        "same_node": require_same_node,
        "limit": result_limit,
    }
    if not candidates:
        return {
            "ok": False,
            "recommendation_only": True,
            "criteria": criteria,
            "candidates": [],
            "candidate_count": 0,
            "returned_count": 0,
            "reason": "当前没有在线资源池满足这些条件",
        }
    returned = candidates[:result_limit]
    return {
        "ok": True,
        "recommendation_only": True,
        "criteria": criteria,
        "candidates": returned,
        "candidate_count": len(candidates),
        "returned_count": len(returned),
        "reason": "按单卡可用显存、是否需要跨节点以及可用 GPU 数排序",
    }


def favorite_resource_matches(
    snapshot: dict[str, Any],
    favorite_server_ids: tuple[str, ...] | list[str] | set[str],
    min_memory_gib: float = 0,
) -> list[dict[str, Any]]:
    """Find live favorites with an idle GPU or enough free VRAM.

    Direct SSH considers a GPU idle only when the stable process sample has no
    allocation on it, at least 95% of its memory is free, and utilization is at
    most 5%. Slurm uses the scheduler's free-GPU count. A positive memory
    threshold is an additional OR condition. Stale or cached payloads never
    qualify because only ``online`` runtime state is accepted.
    """

    required_memory = _memory_requirement(min_memory_gib)
    favorites = {str(server_id) for server_id in favorite_server_ids}
    if not favorites:
        return []
    matches: list[dict[str, Any]] = []
    for server in snapshot.get("servers", []):
        if (
            str(server.get("server_id") or "") not in favorites
            or server.get("connection", {}).get("state") != "online"
        ):
            continue
        view_kind = server.get("view_kind")
        idle_units = 0
        memory_units = 0
        best_free_memory = 0.0
        if view_kind == "live-memory":
            processes = server.get("processes") or {}
            occupied_indices = {
                str(allocation.get("gpu_index"))
                for process in processes.get("active", [])
                if isinstance(process, dict)
                for allocation in process.get("allocations", [])
                if isinstance(allocation, dict) and allocation.get("gpu_index") is not None
            }
            process_sample_supported = processes.get("supported") is True
            for gpu in server.get("gpus", []):
                if not isinstance(gpu, dict):
                    continue
                try:
                    free_memory = max(0.0, float(gpu.get("memory_free_gib") or 0))
                    total_memory = max(0.0, float(gpu.get("memory_total_gib") or 0))
                    utilization = gpu.get("utilization_percent")
                    utilization_ok = utilization is None or float(utilization) <= 5
                except (TypeError, ValueError):
                    continue
                raw_gpu_index = gpu.get("gpu_index")
                gpu_index = "" if raw_gpu_index is None else str(raw_gpu_index)
                idle = bool(
                    process_sample_supported
                    and gpu_index not in occupied_indices
                    and total_memory > 0
                    and free_memory >= total_memory * 0.95
                    and utilization_ok
                )
                memory_match = required_memory > 0 and free_memory >= required_memory
                idle_units += int(idle)
                memory_units += int(memory_match)
                if idle or memory_match:
                    best_free_memory = max(best_free_memory, free_memory)
        elif view_kind == "scheduler":
            rows = server.get("nodes") or server.get("node_groups") or []
            for row in rows:
                if not isinstance(row, dict) or _node_has_issue(row):
                    continue
                try:
                    free_units = max(0, int(row.get("free_gpus") or 0))
                    per_gpu_memory = _per_gpu_memory(row)
                except (TypeError, ValueError):
                    continue
                if free_units < 1:
                    continue
                idle_units += free_units
                if required_memory > 0 and per_gpu_memory >= required_memory:
                    memory_units += free_units
                best_free_memory = max(best_free_memory, per_gpu_memory)
        if idle_units < 1 and memory_units < 1:
            continue
        matches.append(
            {
                "server_id": str(server.get("server_id") or ""),
                "display_name": str(server.get("display_name") or server.get("server_id") or ""),
                "backend": str(server.get("backend") or ""),
                "idle_units": idle_units,
                "memory_units": memory_units,
                "available_memory_gib": round(best_free_memory, 2),
            }
        )
    matches.sort(key=lambda item: (-item["available_memory_gib"], item["server_id"]))
    return matches


def _aggregate_node_groups(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    task_ids: dict[tuple[str, str, str], set[str]] = {}
    anonymous_task_counts: dict[tuple[str, str, str], int] = {}
    unknown_total_vram: set[tuple[str, str, str]] = set()
    unknown_free_vram: set[tuple[str, str, str]] = set()
    for node in nodes:
        partition = str(node.get("partition") or "")
        gpu_type = str(node.get("gpu_type") or "")
        state = str(node.get("state") or "unknown")
        key = (partition, gpu_type, state)
        if key not in grouped:
            digest = hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()[:16]
            grouped[key] = {
                "group_key": f"node-group-{digest}",
                "partition": partition,
                "gpu_type": gpu_type,
                "state": state,
                "node_count": 0,
                "available_nodes": 0,
                "issue_nodes": 0,
                "total_gpus": 0,
                "free_gpus": 0,
                "total_vram_gib": 0.0,
                "free_vram_gib": 0.0,
                "task_count": 0,
            }
            task_ids[key] = set()
            anonymous_task_counts[key] = 0
        group = grouped[key]
        group["node_count"] += 1
        group["available_nodes"] += int(int(node.get("free_gpus") or 0) > 0 and not _node_has_issue(node))
        group["issue_nodes"] += int(_node_has_issue(node))
        group["total_gpus"] += int(node.get("total_gpus") or 0)
        group["free_gpus"] += int(node.get("free_gpus") or 0)
        total_vram = node.get("total_vram_gib")
        free_vram = node.get("free_vram_gib")
        if total_vram is None:
            unknown_total_vram.add(key)
        else:
            group["total_vram_gib"] += float(total_vram)
        if free_vram is None:
            unknown_free_vram.add(key)
        else:
            group["free_vram_gib"] += float(free_vram)
        for task in node.get("tasks", []):
            task_id = str(task.get("job_id") or task.get("pid") or "").strip()
            if task_id:
                task_ids[key].add(task_id)
            else:
                anonymous_task_counts[key] += 1

    result: list[dict[str, Any]] = []
    for key, group in grouped.items():
        group["task_count"] = len(task_ids[key]) + anonymous_task_counts[key]
        group["total_vram_gib"] = (
            None if key in unknown_total_vram else round(float(group["total_vram_gib"]), 2)
        )
        group["free_vram_gib"] = (
            None if key in unknown_free_vram else round(float(group["free_vram_gib"]), 2)
        )
        result.append(group)
    result.sort(
        key=lambda item: (
            str(item["partition"]).casefold(),
            str(item["gpu_type"]).casefold(),
            str(item["state"]).casefold(),
            item["group_key"],
        )
    )
    return result


def _prepare_runtime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("server payload must be an object")
    prepared = dict(payload)
    view_kind = prepared.get("view_kind")
    if view_kind not in {"scheduler", "live-memory"}:
        raise ValueError("server payload has an unsupported view kind")
    if view_kind != "scheduler":
        gpus = prepared.get("gpus", [])
        if not isinstance(gpus, list) or len(gpus) > 16_384 or not all(isinstance(item, dict) for item in gpus):
            raise ValueError("server payload has an invalid GPU list")
        return prepared
    raw_nodes = prepared.get("nodes", [])
    if (
        not isinstance(raw_nodes, list)
        or len(raw_nodes) > 131_072
        or not all(isinstance(item, dict) for item in raw_nodes)
    ):
        raise ValueError("server payload has an invalid node list")
    nodes = list(raw_nodes)
    prepared["node_count"] = len(nodes)
    prepared["large_cluster"] = (
        len(nodes) > LARGE_CLUSTER_NODE_THRESHOLD
        or int(prepared.get("total_gpus") or 0) > LARGE_CLUSTER_GPU_THRESHOLD
    )
    if prepared["large_cluster"]:
        prepared["node_groups"] = _aggregate_node_groups(nodes)
    else:
        prepared.pop("node_groups", None)
    return prepared


def connection_fingerprint(server: ServerProfile) -> str:
    """Return a non-secret hash of every setting that changes collected data."""

    payload = server.to_dict()
    for local_only in ("display_name", "enabled", "auth_ref", "default_work_directory"):
        payload.pop(local_only, None)
    config_path = (
        resolve_ssh_config_path(server)
        if server.ssh_config_file
        else str(Path.home() / ".ssh" / "config")
    )
    payload["ssh_config_dependency"] = openssh_config_dependency_fingerprint(config_path)
    if server.identity_file:
        try:
            stat = Path(resolve_identity_path(server)).stat()
            payload["identity_file_stamp"] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            payload["identity_file_stamp"] = "missing"
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _accepts_keyword(operation: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = inspect.signature(operation).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


@dataclass
class RuntimeState:
    server: ServerProfile
    state: str = "connecting"
    data_origin: str = "none"
    payload: dict[str, Any] | None = None
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    error: dict[str, Any] | None = None
    failure_count: int = 0
    next_attempt_monotonic: float = 0.0
    retry_at: str | None = None
    payload_revision: int = 0
    cache_dirty: bool = False


@dataclass(frozen=True)
class _DirectoryCacheKey:
    server_id: str
    connection_fingerprint: str
    root_source: str
    root_path: str


@dataclass
class _DirectoryCacheEntry:
    account: dict[str, Any]
    stored_at_monotonic: float
    validated_at_monotonic: float
    freshness_token: str


@dataclass
class _DirectoryFlight:
    event: threading.Event
    force: bool
    response: dict[str, Any] | None = None


class DashboardService:
    def __init__(
        self,
        profile: Profile,
        cache: SnapshotCache,
        *,
        query: Callable[..., dict[str, Any]] = query_server,
        directory_query: Callable[..., dict[str, Any]] = query_account_directory,
        directory_version_query: Callable[..., dict[str, Any]] = query_account_directory_version,
        secret_store: object | None = None,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
        startup_notices: list[dict[str, str]] | None = None,
    ) -> None:
        self.profile = profile
        self.cache = cache
        self.query = query
        self.directory_query = directory_query
        self.directory_version_query = directory_version_query
        self.secret_store = secret_store
        self.clock = clock
        self.logger = logger or logging.getLogger("vram_radar")
        self._notices = [
            {
                "code": str(notice.get("code") or "startup_warning")[:64],
                "severity": (
                    str(notice.get("severity") or "warning")
                    if str(notice.get("severity") or "warning") in {"warning", "error"}
                    else "warning"
                ),
                "message": str(notice.get("message") or "")[:1000],
            }
            for notice in (startup_notices or [])
            if isinstance(notice, dict) and str(notice.get("message") or "").strip()
        ]
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self._paused = False
        self._refresh_in_flight = False
        self._refresh_thread: threading.Thread | None = None
        self._pending_force_all = False
        self._pending_server_ids: dict[str, bool] = {}
        self._revision = 0
        self._last_updated_at = utc_now()
        self._last_updated_monotonic = self.clock()
        # Directory browsing is deliberately isolated from the monitoring
        # snapshot cache. It is on-demand, bounded in memory, and protected by
        # a separate lock so slow tree requests never serialize fleet refresh.
        self._directory_cache_lock = threading.Lock()
        self._directory_cache: OrderedDict[_DirectoryCacheKey, _DirectoryCacheEntry] = OrderedDict()
        self._directory_inflight: dict[_DirectoryCacheKey, _DirectoryFlight] = {}
        self.states = self._load_initial_states(profile)

    def _touch_locked(self, *, data_changed: bool = True) -> None:
        self._revision += 1
        if data_changed:
            self._last_updated_at = utc_now()
            self._last_updated_monotonic = self.clock()

    def _set_in_flight_locked(self, value: bool) -> None:
        if self._refresh_in_flight != value:
            self._refresh_in_flight = value
            self._touch_locked(data_changed=False)

    def pause(self) -> dict[str, Any]:
        with self.lock:
            if not self._paused:
                self._paused = True
                self._touch_locked(data_changed=False)
        return self.snapshot()

    def resume(self) -> dict[str, Any]:
        with self.lock:
            if self._paused:
                self._paused = False
                self._touch_locked(data_changed=False)
        return self.snapshot()

    def is_paused(self) -> bool:
        with self.lock:
            return self._paused

    def clear_notice(self, code: str) -> None:
        """Clear a startup/configuration notice after its owning action succeeds."""

        with self.lock:
            retained = [notice for notice in self._notices if notice.get("code") != code]
            if retained != self._notices:
                self._notices = retained
                self._touch_locked(data_changed=False)

    def _load_initial_state(self, server: ServerProfile) -> RuntimeState:
        runtime = RuntimeState(server=server)
        try:
            cached = self.cache.load(
                server.id,
                connection_fingerprint=connection_fingerprint(server),
            )
            if cached:
                prepared_cached = _prepare_runtime_payload(cached["payload"])
                runtime.state = "stale"
                runtime.data_origin = "cache"
                runtime.payload = prepared_cached
                runtime.last_success_at = cached["last_success_at"]
                runtime.payload_revision = 1
        except Exception as exc:
            # Cache is replaceable evidence. A semantically corrupt or
            # future-version payload must never prevent the desktop from
            # opening or make a new endpoint inherit old server data.
            self.logger.warning("discarding invalid cache for %s: %s", server.id, exc)
        if not server.enabled:
            runtime.state = "disabled"
            runtime.next_attempt_monotonic = float("inf")
        return runtime

    def _load_initial_states(self, profile: Profile) -> dict[str, RuntimeState]:
        return {server.id: self._load_initial_state(server) for server in profile.servers}

    def replace_profile(self, profile: Profile, cache: SnapshotCache) -> None:
        with self.refresh_lock, self.lock:
            previous_states = self.states
            previous_defaults = {
                server_id: runtime.server.default_work_directory
                for server_id, runtime in previous_states.items()
            }
            self.profile = profile
            self.cache = cache
            self._pending_force_all = False
            self._pending_server_ids.clear()
            retained: dict[str, RuntimeState] = {}
            for server in profile.servers:
                previous = previous_states.get(server.id)
                if (
                    previous is None
                    or connection_fingerprint(previous.server) != connection_fingerprint(server)
                ):
                    retained[server.id] = self._load_initial_state(server)
                    continue
                was_enabled = previous.server.enabled
                previous.server = server
                if not server.enabled:
                    previous.state = "disabled"
                    previous.next_attempt_monotonic = float("inf")
                    previous.retry_at = None
                elif not was_enabled:
                    previous.state = "stale" if previous.payload is not None else "connecting"
                    previous.error = None
                    previous.failure_count = 0
                    previous.next_attempt_monotonic = 0.0
                    previous.retry_at = None
                retained[server.id] = previous
            self.states = retained
            active_fingerprints = {
                server_id: connection_fingerprint(runtime.server)
                for server_id, runtime in retained.items()
            }
            changed_defaults = {
                server.id
                for server in profile.servers
                if previous_defaults.get(server.id) != server.default_work_directory
            }
            with self._directory_cache_lock:
                for key in list(self._directory_cache):
                    if (
                        active_fingerprints.get(key.server_id) != key.connection_fingerprint
                        or key.server_id in changed_defaults
                        and key.root_source in {"auto", "pinned"}
                    ):
                        self._directory_cache.pop(key, None)
            self._touch_locked(data_changed=False)

    def _due(self, runtime: RuntimeState, *, force: bool) -> bool:
        if not runtime.server.enabled:
            return False
        if force:
            return True
        return self.clock() >= runtime.next_attempt_monotonic

    def _record_success(self, server_id: str, payload: dict[str, Any]) -> None:
        timestamp = utc_now()
        prepared_payload = _prepare_runtime_payload(payload)
        with self.lock:
            runtime = self.states[server_id]
            payload_changed = runtime.payload != prepared_payload
            connection_recovered = (
                runtime.state != "online"
                or runtime.data_origin != "live"
                or runtime.error is not None
                or runtime.failure_count != 0
                or runtime.retry_at is not None
            )
            runtime.state = "online"
            runtime.data_origin = "live"
            runtime.payload = prepared_payload
            if payload_changed:
                runtime.payload_revision += 1
            runtime.last_success_at = timestamp
            runtime.error = None
            runtime.failure_count = 0
            runtime.retry_at = None
            runtime.next_attempt_monotonic = self.clock() + self.profile.refresh_seconds
            self._touch_locked()
            should_persist = payload_changed or connection_recovered or runtime.cache_dirty
            server_for_cache = runtime.server
        if should_persist:
            try:
                self.cache.save(
                    server_id,
                    timestamp,
                    prepared_payload,
                    connection_fingerprint=connection_fingerprint(server_for_cache),
                )
            except OSError:
                with self.lock:
                    current = self.states.get(server_id)
                    if current is runtime:
                        runtime.cache_dirty = True
                self.logger.exception("could not persist cache for %s", server_id)
            else:
                with self.lock:
                    current = self.states.get(server_id)
                    if current is runtime:
                        runtime.cache_dirty = False

    def _record_failure(self, server_id: str, failure: ConnectorFailure) -> None:
        occurred = utc_now()
        with self.lock:
            runtime = self.states[server_id]
            runtime.failure_count += 1
            delay = RETRY_SECONDS[min(runtime.failure_count - 1, len(RETRY_SECONDS) - 1)] if failure.retryable else None
            runtime.next_attempt_monotonic = self.clock() + delay if delay is not None else float("inf")
            runtime.retry_at = future_utc(delay) if delay is not None else None
            runtime.state = "stale" if runtime.payload is not None and failure.state == "offline" else failure.state
            runtime.data_origin = "cache" if runtime.payload is not None else "none"
            runtime.error = {
                "code": failure.code,
                "message": str(failure),
                "retryable": failure.retryable,
                "occurred_at": occurred,
                "retry_at": runtime.retry_at,
            }
            self._touch_locked()
            self.logger.warning("server=%s code=%s message=%s", server_id, failure.code, failure)

    def refresh(
        self,
        *,
        force: bool = False,
        server_id: str | None = None,
        _manage_in_flight: bool = True,
    ) -> dict[str, Any]:
        # A paused periodic poll must not queue behind an already-running SSH
        # collection. It serves the latest immutable view immediately.
        with self.lock:
            if self._paused and not force and server_id is None:
                return self.snapshot()
        with self.refresh_lock:
            with self.lock:
                skip_paused_refresh = self._paused and not force and server_id is None
                if not skip_paused_refresh:
                    if _manage_in_flight:
                        self._set_in_flight_locked(True)
                    candidates = [
                        runtime
                        for candidate_id, runtime in self.states.items()
                        if (server_id is None or candidate_id == server_id) and self._due(runtime, force=force)
                    ]
                    attempt_at = utc_now()
                    for runtime in candidates:
                        runtime.last_attempt_at = attempt_at
                else:
                    candidates = []
            try:
                if candidates:
                    worker_count = min(MAX_REFRESH_WORKERS, len(candidates))
                    with ThreadPoolExecutor(
                        max_workers=worker_count, thread_name_prefix="vram-radar"
                    ) as executor:
                        futures = {executor.submit(self._query, runtime.server): runtime.server.id for runtime in candidates}
                        for future in as_completed(futures):
                            # Drop completed futures immediately. Otherwise the
                            # mapping retains every parsed fleet payload until
                            # the final slow server completes.
                            candidate_id = futures.pop(future)
                            try:
                                self._record_success(candidate_id, future.result())
                            except ConnectorFailure as exc:
                                self._record_failure(candidate_id, exc)
                            except Exception as exc:  # Defensive boundary around third-party SSH/process behavior.
                                self.logger.exception("unexpected connector failure for %s", candidate_id)
                                self._record_failure(
                                    candidate_id,
                                    ConnectorFailure(
                                        "unknown", str(exc).strip() or type(exc).__name__, retryable=True
                                    ),
                                )
            finally:
                if _manage_in_flight:
                    with self.lock:
                        self._set_in_flight_locked(False)
        return self.snapshot()

    def request_refresh(self, *, force: bool = False, server_id: str | None = None) -> dict[str, Any]:
        """Start at most one background refresh and return the latest snapshot immediately."""

        with self.lock:
            if self._paused and not force and server_id is None:
                return self.snapshot()
            if self._refresh_in_flight:
                if force and server_id is None:
                    self._pending_force_all = True
                    self._pending_server_ids.clear()
                elif server_id is not None and not self._pending_force_all:
                    self._pending_server_ids[server_id] = (
                        self._pending_server_ids.get(server_id, False) or force
                    )
                return self.snapshot()
            self._set_in_flight_locked(True)

            def run() -> None:
                next_force = force
                next_server_id = server_id
                while True:
                    try:
                        self.refresh(
                            force=next_force,
                            server_id=next_server_id,
                            _manage_in_flight=False,
                        )
                    except Exception:  # Keep the desktop loop alive at this asynchronous boundary.
                        self.logger.exception("unexpected background refresh failure")
                    with self.lock:
                        if self._pending_force_all:
                            self._pending_force_all = False
                            self._pending_server_ids.clear()
                            next_force = True
                            next_server_id = None
                            continue
                        if self._pending_server_ids:
                            next_server_id, next_force = self._pending_server_ids.popitem()
                            continue
                        self._set_in_flight_locked(False)
                        break

            thread = threading.Thread(target=run, name="vram-radar-refresh", daemon=True)
            self._refresh_thread = thread
            try:
                thread.start()
            except Exception:
                self._refresh_thread = None
                self._pending_force_all = False
                self._pending_server_ids.clear()
                self._set_in_flight_locked(False)
                raise RuntimeError("无法启动后台服务器刷新线程")
        return self.snapshot()

    def _authenticated_call(
        self,
        server: ServerProfile,
        operation: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        # Always try the user's existing non-interactive OpenSSH path first.
        # A Profile can outlive a Keychain/Credential Manager item after a
        # cross-machine migration; a stale auth_ref must never block a valid
        # config IdentityFile, ssh-agent, ProxyJump, or newly deployed key.
        try:
            if server.prefer_identity_auth and server.identity_file:
                if _accepts_keyword(operation, "identities_only"):
                    return operation(server, identities_only=True)
                return operation(server)
            return operation(server)
        except ConnectorFailure as identity_failure:
            # Password is a fallback only for an authentication rejection.
            # DNS, host-key, proxy and collector failures must retain their
            # original classification and must not cause a second connection.
            if (
                identity_failure.code not in PASSWORD_FALLBACK_AUTH_CODES
                or not server.auth_ref
            ):
                raise
        getter = getattr(self.secret_store, "get", None)
        if getter is None:
            raise ConnectorFailure(
                "password_unavailable",
                "服务器密码不可用，请在本地配置中重新保存",
                retryable=False,
                state="auth_required",
            )
        try:
            password = getter(server.auth_ref)
        except Exception as exc:
            raise ConnectorFailure(
                "password_unavailable",
                "无法从系统凭据存储读取服务器密码",
                retryable=False,
                state="auth_required",
            ) from exc
        if not password:
            raise ConnectorFailure(
                "password_unavailable",
                "服务器密码不存在，请在本地配置中重新输入",
                retryable=False,
                state="auth_required",
            )
        return operation(server, password=password)

    def _query(self, server: ServerProfile) -> dict[str, Any]:
        return self._authenticated_call(server, self.query)

    def probe_server(self, server_id: str) -> dict[str, Any]:
        """Run the exact monitoring collector and synchronously update runtime state."""

        with self.refresh_lock:
            with self.lock:
                runtime = self.states.get(server_id)
                if runtime is None:
                    raise ConnectorFailure(
                        "server_not_found", "找不到这台服务器", retryable=False, state="misconfigured"
                    )
                if not runtime.server.enabled:
                    raise ConnectorFailure(
                        "server_disabled", "这台服务器已停用", retryable=False, state="disabled"
                    )
                server = runtime.server
                runtime.last_attempt_at = utc_now()
                self._touch_locked(data_changed=False)
            try:
                payload = self._query(server)
                self._record_success(server_id, payload)
                return payload
            except ConnectorFailure as exc:
                self._record_failure(server_id, exc)
                raise

    def _directory_cache_metadata(
        self,
        entry: _DirectoryCacheEntry,
        state: str,
        *,
        now: float | None = None,
        retry_after: float | None = None,
    ) -> dict[str, Any]:
        observed = self.clock() if now is None else now
        age = max(0.0, observed - entry.stored_at_monotonic)
        validated_age = max(0.0, observed - entry.validated_at_monotonic)
        deep_remaining = max(0.0, DIRECTORY_CACHE_DEEP_REFRESH_SECONDS - age)
        if retry_after is None:
            revalidate_after = min(
                max(0.0, DIRECTORY_CACHE_FRESH_SECONDS - validated_age),
                deep_remaining,
            )
        else:
            revalidate_after = min(max(0.0, retry_after), deep_remaining)
        return {
            "state": state,
            "age_seconds": age,
            "revalidate_after_seconds": revalidate_after,
        }

    def inspect_account_directory(
        self,
        server_id: str,
        root_path: str | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Return one lazy directory level with bounded freshness and memory.

        A recently validated entry is a zero-I/O hit. Once its short freshness
        window expires, one single-flight owner probes only the root directory
        metadata; an unchanged token renews the entry without serializing the
        tree. A changed token, an explicit force, or the periodic deep-refresh
        deadline performs the bounded one-level scan. Directory inspection is
        auxiliary evidence and never overwrites monitoring state.
        """

        with self.lock:
            runtime = self.states.get(server_id)
            if runtime is None:
                return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
            if not runtime.server.enabled:
                return {"ok": False, "error": "这台服务器已停用", "code": "server_disabled"}
            server = runtime.server
            selected_root = root_path if root_path is not None else (server.default_work_directory or None)
            root_source = "requested" if root_path is not None else (
                "pinned" if server.default_work_directory else "auto"
            )
        # SSH Include dependency hashing can read several local files. Keep it
        # outside the dashboard lock so a large config cannot block snapshots.
        fingerprint = connection_fingerprint(server)
        with self.lock:
            current = self.states.get(server_id)
            if current is None or current.server is not server or not current.server.enabled:
                return {
                    "ok": False,
                    "error": "读取前服务器配置已变化，请重新展开",
                    "code": "configuration_changed",
                    "retryable": True,
                }
            key = _DirectoryCacheKey(
                server_id=server.id,
                connection_fingerprint=fingerprint,
                root_source=root_source,
                root_path=selected_root or "",
            )

        cached_for_owner: _DirectoryCacheEntry | None = None
        scan_required = True
        with self._directory_cache_lock:
            for cached_key in list(self._directory_cache):
                if (
                    cached_key.server_id == server.id
                    and cached_key.connection_fingerprint != fingerprint
                ):
                    self._directory_cache.pop(cached_key, None)
            cached = self._directory_cache.get(key)
            if cached is not None and not force:
                self._directory_cache.move_to_end(key)
                now = self.clock()
                validated_age = max(0.0, now - cached.validated_at_monotonic)
                stored_age = max(0.0, now - cached.stored_at_monotonic)
                if (
                    validated_age < DIRECTORY_CACHE_FRESH_SECONDS
                    and stored_age < DIRECTORY_CACHE_DEEP_REFRESH_SECONDS
                ):
                    return {
                        "ok": True,
                        "server_id": server.id,
                        "account": deepcopy(cached.account),
                        "cache": self._directory_cache_metadata(cached, "hit", now=now),
                    }
                cached_for_owner = cached
                scan_required = (
                    stored_age >= DIRECTORY_CACHE_DEEP_REFRESH_SECONDS
                    or not cached.freshness_token
                )
            flight = self._directory_inflight.get(key)
            owns_flight = flight is None
            force_after_flight = bool(force and flight is not None and not flight.force)
            if flight is None:
                flight = _DirectoryFlight(event=threading.Event(), force=force)
                self._directory_inflight[key] = flight

        if not owns_flight:
            if not flight.event.wait(DIRECTORY_SINGLE_FLIGHT_WAIT_SECONDS):
                return {
                    "ok": False,
                    "error": "等待文件夹读取超时，请重试",
                    "code": "directory_wait_timeout",
                    "retryable": True,
                }
            if force_after_flight:
                # A user-triggered refresh that arrives after an ordinary read
                # must observe a new scan started after that action. Once the
                # ordinary flight releases, recurse through the same single-
                # flight gate so concurrent forced followers still coalesce.
                return self.inspect_account_directory(server_id, root_path, force=True)
            with self._directory_cache_lock:
                terminal = deepcopy(flight.response) if flight.response is not None else None
                if terminal is not None and not terminal.get("ok"):
                    return terminal
                if terminal is not None and terminal.get("unchanged"):
                    return terminal
                cached = self._directory_cache.get(key)
                if cached is not None:
                    self._directory_cache.move_to_end(key)
                    return {
                        "ok": True,
                        "server_id": server.id,
                        "account": deepcopy(cached.account),
                        "cache": self._directory_cache_metadata(
                            cached, "refreshed" if force else "hit"
                        ),
                    }
                if terminal is not None:
                    terminal["cache"]["state"] = "refreshed" if force else "hit"
                    return terminal
            return {
                "ok": False,
                "error": "文件夹读取未完成，请重试",
                "code": "directory_query_incomplete",
                "retryable": True,
            }

        response: dict[str, Any] = {
            "ok": False,
            "error": "无法读取账号文件夹结构",
            "code": "unknown",
            "retryable": True,
        }
        try:
            if cached_for_owner is not None and not scan_required and not force:
                cached_root = str(
                    cached_for_owner.account.get("directory_tree", {}).get("root") or ""
                )
                try:
                    version = self._authenticated_call(
                        server,
                        lambda target, *, password=None, identities_only=False: (
                            self.directory_version_query(
                                target,
                                **{
                                    "password": password,
                                    "root_path": cached_root,
                                    **(
                                        {"identities_only": True}
                                        if identities_only
                                        and _accepts_keyword(
                                            self.directory_version_query, "identities_only"
                                        )
                                        else {}
                                    ),
                                },
                            )
                        ),
                    )
                except ConnectorFailure as exc:
                    # Cached directory evidence remains useful when a cheap
                    # probe is temporarily unavailable. Its original full-scan
                    # timestamp is not renewed, so the deep deadline still
                    # forces a bounded rescan instead of allowing infinite age.
                    with self._directory_cache_lock:
                        current_cached = self._directory_cache.get(key)
                        if current_cached is cached_for_owner:
                            self._directory_cache.move_to_end(key)
                            response = {
                                "ok": True,
                                "server_id": server.id,
                                "unchanged": True,
                                "cache": self._directory_cache_metadata(
                                    current_cached,
                                    "stale_hit",
                                    retry_after=DIRECTORY_CACHE_PROBE_RETRY_SECONDS,
                                ),
                                "freshness_warning": str(exc),
                            }
                            return response
                    raise
                probe_token = str(version.get("version_token") or "")
                probe_root = str(version.get("root") or "")
                if (
                    bool(version.get("supported"))
                    and probe_root == cached_root
                    and probe_token == cached_for_owner.freshness_token
                ):
                    with self.lock:
                        latest = self.states.get(server_id)
                        current_server = latest.server if latest is not None else None
                    # As above, local OpenSSH Include hashing stays outside the
                    # main dashboard lock so a freshness probe cannot delay
                    # monitoring snapshots or UI status calls.
                    current_fingerprint = (
                        connection_fingerprint(current_server)
                        if current_server is not None
                        else ""
                    )
                    with self.lock:
                        latest = self.states.get(server_id)
                        unchanged_configuration = (
                            latest is not None
                            and latest.server is server
                            and current_server is server
                            and latest.server.enabled
                            and current_fingerprint == fingerprint
                        )
                        if unchanged_configuration:
                            with self._directory_cache_lock:
                                current_cached = self._directory_cache.get(key)
                                if current_cached is cached_for_owner:
                                    current_cached.validated_at_monotonic = self.clock()
                                    self._directory_cache.move_to_end(key)
                                    response = {
                                        "ok": True,
                                        "server_id": server.id,
                                        "unchanged": True,
                                        "cache": self._directory_cache_metadata(
                                            current_cached, "validated"
                                        ),
                                    }
                                    return response
                    response = {
                        "ok": False,
                        "error": "校验期间服务器配置已变化，请重新展开",
                        "code": "configuration_changed",
                        "retryable": True,
                    }
                    return response

            account = self._authenticated_call(
                server,
                lambda target, *, password=None, identities_only=False: self.directory_query(
                    target,
                    **{
                        "password": password,
                        "root_path": selected_root,
                        "root_source": root_source,
                        **(
                            {"identities_only": True}
                            if identities_only and _accepts_keyword(self.directory_query, "identities_only")
                            else {}
                        ),
                    },
                ),
            )
            with self.lock:
                current = self.states.get(server_id)
                if current is None or current.server is not server or not current.server.enabled:
                    current_server = None
                    current_root = None
                    current_source = ""
                else:
                    current_server = current.server
                    current_root = (
                        root_path
                        if root_path is not None
                        else (current.server.default_work_directory or None)
                    )
                    current_source = "requested" if root_path is not None else (
                        "pinned" if current.server.default_work_directory else "auto"
                    )
            current_fingerprint = (
                connection_fingerprint(current_server) if current_server is not None else ""
            )
            with self.lock:
                latest = self.states.get(server_id)
                unchanged = (
                    latest is not None
                    and latest.server is server
                    and current_server is server
                    and latest.server.enabled
                    and current_fingerprint == fingerprint
                    and current_root == selected_root
                    and current_source == root_source
                )
                if unchanged:
                    stored_at = self.clock()
                    cache_entry = _DirectoryCacheEntry(
                        account=deepcopy(account),
                        stored_at_monotonic=stored_at,
                        validated_at_monotonic=stored_at,
                        freshness_token=str(
                            account.get("directory_tree", {}).get("version_token") or ""
                        ),
                    )
                    # Match replace_profile's main-lock -> directory-lock
                    # ordering so validation and insertion are one transaction.
                    with self._directory_cache_lock:
                        self._directory_cache[key] = cache_entry
                        self._directory_cache.move_to_end(key)
                        while len(self._directory_cache) > MAX_DIRECTORY_CACHE_ROOTS:
                            self._directory_cache.popitem(last=False)
            if unchanged:
                response = {
                    "ok": True,
                    "server_id": server.id,
                    "account": deepcopy(account),
                    "cache": self._directory_cache_metadata(
                        cache_entry,
                        "refreshed" if force or cached_for_owner is not None else "miss",
                        now=stored_at,
                    ),
                }
            else:
                response = {
                    "ok": False,
                    "error": "读取期间服务器配置已变化，请重新展开",
                    "code": "configuration_changed",
                    "retryable": True,
                }
        except ConnectorFailure as exc:
            response = {
                "ok": False,
                "error": str(exc),
                "code": exc.code,
                "retryable": exc.retryable,
            }
        except Exception:
            self.logger.exception("unexpected directory inspection failure for %s", server.id)
        finally:
            # Publish the terminal response before waking followers and always
            # release the flight, including connector/parser exceptions.
            with self._directory_cache_lock:
                flight.response = deepcopy(response)
                if self._directory_inflight.get(key) is flight:
                    self._directory_inflight.pop(key, None)
                flight.event.set()
        return response

    def snapshot(self, *, include_cluster_nodes: bool = False) -> dict[str, Any]:
        with self.lock:
            servers = [
                self._serialize(runtime, include_cluster_nodes=include_cluster_nodes)
                for runtime in self.states.values()
            ]
            online = [item for item in servers if item["connection"]["state"] == "online"]
            online_with_gpus = [item for item in online if int(item.get("total_gpus") or 0) > 0]
            capacity_totals = [item.get("total_vram_gib") for item in online_with_gpus]
            total_capacity_known = all(value is not None for value in capacity_totals)
            total_vram_gib = (
                round(sum(float(value) for value in capacity_totals), 2)
                if total_capacity_known
                else None
            )
            free_capacity = [item.get("free_vram_gib") for item in online_with_gpus]
            free_capacity_known = all(value is not None for value in free_capacity)
            free_vram_gib = (
                round(sum(float(value) for value in free_capacity), 2)
                if free_capacity_known
                else None
            )
            data_age_seconds = max(0.0, self.clock() - self._last_updated_monotonic)
            return {
                "app": "vram-radar",
                "schema_version": 1,
                "profile": {
                    "id": self.profile.id,
                    "display_name": self.profile.display_name,
                    "refresh_seconds": self.profile.refresh_seconds,
                },
                "fetched_at": utc_now(),
                "monitoring": {
                    "paused": self._paused,
                    "in_flight": self._refresh_in_flight,
                    "revision": self._revision,
                    "data_updated_at": self._last_updated_at,
                    "data_age_seconds": round(data_age_seconds, 3),
                },
                "summary": {
                    "revision": self._revision,
                    "data_updated_at": self._last_updated_at,
                    "data_age_seconds": round(data_age_seconds, 3),
                    "total_servers": len(servers),
                    "online_servers": len(online),
                    "stale_servers": sum(item["connection"]["state"] == "stale" for item in servers),
                    "total_gpus": sum(int(item.get("total_gpus") or 0) for item in online),
                    "total_vram_gib": total_vram_gib,
                    "free_vram_gib": free_vram_gib,
                },
                "notices": [dict(notice) for notice in self._notices],
                "servers": servers,
            }

    def recommend_many(
        self,
        gpu_count: int = 1,
        min_memory_gib: float = 0,
        gpu_type: str = "",
        partition: str = "",
        same_node: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        return recommend_resources(
            self.snapshot(include_cluster_nodes=True),
            gpu_count=gpu_count,
            min_memory_gib=min_memory_gib,
            gpu_type=gpu_type,
            partition=partition,
            same_node=same_node,
            limit=limit,
        )

    def get_cluster_nodes(
        self,
        server_id: str,
        cursor: int = 0,
        limit: int = 100,
        query: str = "",
        gpu_type: str = "",
        partition: str = "",
        only_available: bool = False,
        only_issues: bool = False,
        revision: int | None = None,
    ) -> dict[str, Any]:
        try:
            page_cursor = max(0, int(cursor))
        except (TypeError, ValueError) as exc:
            raise ValueError("分页位置必须是整数") from exc
        try:
            page_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("每页数量必须是整数") from exc
        page_limit = min(MAX_CLUSTER_PAGE_SIZE, max(1, page_limit))
        text_filter = str(query or "").strip().casefold()
        type_filter = str(gpu_type or "").strip().casefold()
        partition_filter = str(partition or "").strip().casefold()
        with self.lock:
            runtime = self.states.get(str(server_id))
            if runtime is None:
                return {
                    "ok": False,
                    "code": "server_not_found",
                    "error": "找不到这台服务器",
                    "server_id": str(server_id),
                    "nodes": [],
                }
            payload = runtime.payload or {}
            if payload.get("view_kind") != "scheduler":
                return {
                    "ok": False,
                    "code": "not_scheduler",
                    "error": "这台服务器不是 Slurm 集群",
                    "server_id": str(server_id),
                    "nodes": [],
                }
            current_revision = runtime.payload_revision
            if revision is not None:
                try:
                    requested_revision = int(revision)
                except (TypeError, ValueError) as exc:
                    raise ValueError("节点快照版本必须是整数") from exc
                if requested_revision != current_revision:
                    return {
                        "ok": False,
                        "code": "snapshot_changed",
                        "error": "节点快照已经更新，请从第一页重新加载",
                        "server_id": str(server_id),
                        "revision": current_revision,
                        "nodes": [],
                    }
            all_nodes = list(payload.get("nodes") or [])

        filtered: list[dict[str, Any]] = []
        for node in all_nodes:
            candidate_type = str(node.get("gpu_type") or "")
            candidate_partition = str(node.get("partition") or "")
            searchable = " ".join(
                (
                    str(node.get("node") or ""),
                    candidate_type,
                    candidate_partition,
                    str(node.get("state") or ""),
                )
            ).casefold()
            if text_filter and text_filter not in searchable:
                continue
            if type_filter and type_filter not in candidate_type.casefold():
                continue
            if partition_filter and partition_filter not in candidate_partition.casefold():
                continue
            if only_available and (int(node.get("free_gpus") or 0) < 1 or _node_has_issue(node)):
                continue
            if only_issues and not _node_has_issue(node):
                continue
            filtered.append(dict(node))

        total = len(filtered)
        page = filtered[page_cursor : page_cursor + page_limit]
        next_cursor = page_cursor + len(page)
        if next_cursor >= total:
            next_cursor = None
        with self.lock:
            latest = self.states.get(str(server_id))
            if latest is not runtime or latest.payload_revision != current_revision:
                return {
                    "ok": False,
                    "code": "snapshot_changed",
                    "error": "节点快照已经更新，请从第一页重新加载",
                    "server_id": str(server_id),
                    "revision": latest.payload_revision if latest is not None else None,
                    "nodes": [],
                }
        return {
            "ok": True,
            "server_id": str(server_id),
            "revision": current_revision,
            "node_count": len(all_nodes),
            "total": total,
            "cursor": page_cursor,
            "limit": page_limit,
            "returned": len(page),
            "next_cursor": next_cursor,
            "nodes": page,
        }

    @staticmethod
    def _serialize(runtime: RuntimeState, *, include_cluster_nodes: bool = False) -> dict[str, Any]:
        payload = dict(runtime.payload or {})
        # Current Profile identity is authoritative. Cached collector data must
        # never make a changed endpoint look like the old server.
        payload["server_id"] = runtime.server.id
        payload["display_name"] = runtime.server.display_name
        payload["backend"] = runtime.server.backend
        if payload.get("view_kind") == "scheduler":
            nodes = list(payload.get("nodes") or [])
            payload.setdefault("node_count", len(nodes))
            payload.setdefault(
                "large_cluster",
                len(nodes) > LARGE_CLUSTER_NODE_THRESHOLD
                or int(payload.get("total_gpus") or 0) > LARGE_CLUSTER_GPU_THRESHOLD,
            )
            if payload["large_cluster"]:
                if "node_groups" not in payload:
                    payload["node_groups"] = _aggregate_node_groups(nodes)
                if not include_cluster_nodes:
                    payload.pop("nodes", None)
        payload["connection"] = {
            "state": runtime.state,
            "data_origin": runtime.data_origin,
            "usable_for_summary": runtime.state == "online",
            "last_attempt_at": runtime.last_attempt_at,
            "last_success_at": runtime.last_success_at,
            "retry_at": runtime.retry_at,
            "error": runtime.error,
            "data_revision": runtime.payload_revision,
        }
        return payload
