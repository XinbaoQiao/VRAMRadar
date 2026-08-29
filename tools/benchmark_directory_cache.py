"""Repeatable, server-free benchmark for working-directory cache freshness.

The fake readers model a bounded full-tree SSH round trip and a lightweight
root-version round trip. No Profile from the current user is loaded and no
network connection is attempted.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import tempfile
import time
import tracemalloc

from vram_radar.models import Profile
from vram_radar.service import (
    DIRECTORY_CACHE_FRESH_SECONDS,
    DashboardService,
    MAX_DIRECTORY_CACHE_ROOTS,
)
from vram_radar.storage import SnapshotCache, storage_paths


def _profile() -> Profile:
    return Profile.from_dict(
        {
            "schema_version": 1,
            "id": "benchmark",
            "display_name": "Directory benchmark",
            "refresh_seconds": 15,
            "servers": [
                {
                    "id": "synthetic",
                    "display_name": "Synthetic server",
                    "backend": "direct_ssh",
                    "host": "synthetic.invalid",
                    "username": "benchmark",
                }
            ],
        }
    )


def _measure(operation):
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    result = operation()
    return {
        "wall_ms": round((time.perf_counter() - wall_start) * 1_000, 3),
        "cpu_ms": round((time.process_time() - cpu_start) * 1_000, 3),
        "cache_state": result["cache"]["state"],
        "compact_unchanged": bool(result.get("unchanged") and "account" not in result),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay-ms", type=float, default=60.0)
    parser.add_argument("--probe-delay-ms", type=float, default=2.0)
    parser.add_argument("--entries", type=int, default=160)
    parser.add_argument("--stress-roots", type=int, default=112)
    parser.add_argument("--stress-passes", type=int, default=4)
    args = parser.parse_args()
    entry_count = max(1, min(160, args.entries))
    delay_seconds = max(0.0, args.delay_ms) / 1_000
    active_delay = [delay_seconds]
    directory_calls = 0
    version_calls = 0
    now = [0.0]
    versions: dict[str, str] = {}

    def directory_query(_server, *, root_path=None, **_kwargs):
        nonlocal directory_calls
        directory_calls += 1
        if active_delay[0]:
            time.sleep(active_delay[0])
        root = root_path or "/srv/benchmark/code"
        version = versions.setdefault(root, "version-1")
        return {
            "username": "benchmark",
            "home_directory": "/srv/benchmark",
            "directory_tree": {
                "supported": True,
                "root": root,
                "root_source": "requested" if root_path else "auto",
                "version_token": version,
                "entries": [
                    {
                        "name": f"project-{index:03d}",
                        "path": f"project-{index:03d}",
                        "absolute_path": f"{root}/project-{index:03d}",
                        "parent_absolute_path": root,
                        "kind": "directory",
                        "has_more": True,
                        "size_bytes": 0,
                        "modified_at": "",
                    }
                    for index in range(entry_count)
                ],
            },
        }

    def directory_version_query(_server, *, root_path, **_kwargs):
        nonlocal version_calls
        version_calls += 1
        probe_delay = max(0.0, args.probe_delay_ms) / 1_000
        if probe_delay:
            time.sleep(probe_delay)
        return {
            "supported": True,
            "root": root_path,
            "version_token": versions.get(root_path, "version-1"),
        }

    with tempfile.TemporaryDirectory(prefix="vram-radar-directory-benchmark-") as temporary:
        service = DashboardService(
            _profile(),
            SnapshotCache(storage_paths(Path(temporary)), "benchmark"),
            directory_query=directory_query,
            directory_version_query=directory_version_query,
            clock=lambda: now[0],
        )
        root = "/srv/benchmark/code"
        tracemalloc.start()
        first = _measure(lambda: service.inspect_account_directory("synthetic", root))
        now[0] = 1.0
        second = _measure(lambda: service.inspect_account_directory("synthetic", root))
        now[0] = DIRECTORY_CACHE_FRESH_SECONDS + 1
        unchanged_validation = _measure(
            lambda: service.inspect_account_directory("synthetic", root)
        )
        versions[root] = "version-2"
        now[0] += DIRECTORY_CACHE_FRESH_SECONDS + 1
        changed_refresh = _measure(
            lambda: service.inspect_account_directory("synthetic", root)
        )
        forced = _measure(
            lambda: service.inspect_account_directory("synthetic", root, force=True)
        )
        child = _measure(
            lambda: service.inspect_account_directory("synthetic", f"{root}/project-000")
        )
        child_reopen = _measure(
            lambda: service.inspect_account_directory("synthetic", f"{root}/project-000")
        )
        measured_directory_calls = directory_calls
        measured_version_calls = version_calls
        before_stress_bytes, _ = tracemalloc.get_traced_memory()
        active_delay[0] = 0.0
        stress_roots = max(0, args.stress_roots)
        stress_passes = max(1, args.stress_passes)
        stress_measurements: list[dict[str, float | int]] = []
        for pass_index in range(stress_passes):
            stress_start = time.perf_counter()
            for root_index in range(
                pass_index * stress_roots,
                (pass_index + 1) * stress_roots,
            ):
                service.inspect_account_directory(
                    "synthetic",
                    f"{root}/stress-{root_index:04d}",
                )
            wall_ms = round((time.perf_counter() - stress_start) * 1_000, 3)
            gc.collect()
            pass_bytes, pass_peak_bytes = tracemalloc.get_traced_memory()
            stress_measurements.append(
                {
                    "pass": pass_index + 1,
                    "wall_ms": wall_ms,
                    "cache_roots": len(service._directory_cache),
                    "python_current_kib": round(pass_bytes / 1024, 2),
                    "python_peak_kib": round(pass_peak_bytes / 1024, 2),
                }
            )
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        output = {
            "synthetic_only": True,
            "remote_connections": 0,
            "entry_count": entry_count,
            "simulated_round_trip_ms": args.delay_ms,
            "simulated_probe_round_trip_ms": args.probe_delay_ms,
            "first_load": first,
            "second_open": second,
            "unchanged_validation": unchanged_validation,
            "changed_refresh": changed_refresh,
            "forced_refresh": forced,
            "child_expand": child,
            "child_reopen": child_reopen,
            "measured_directory_query_calls": measured_directory_calls,
            "expected_measured_directory_query_calls": 4,
            "measured_version_query_calls": measured_version_calls,
            "expected_measured_version_query_calls": 2,
            "stress_roots_requested_per_pass": stress_roots,
            "stress_passes": stress_passes,
            "stress_measurements": stress_measurements,
            "total_directory_query_calls": directory_calls,
            "total_version_query_calls": version_calls,
            "cache_roots": len(service._directory_cache),
            "cache_root_limit": MAX_DIRECTORY_CACHE_ROOTS,
            "python_cache_before_stress_kib": round(before_stress_bytes / 1024, 2),
            "python_cache_current_kib": round(current_bytes / 1024, 2),
            "python_cache_peak_kib": round(peak_bytes / 1024, 2),
            "python_cache_last_pass_delta_kib": round(
                stress_measurements[-1]["python_current_kib"]
                - stress_measurements[-2]["python_current_kib"],
                2,
            ) if len(stress_measurements) > 1 else 0.0,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        expected_roots = min(
            MAX_DIRECTORY_CACHE_ROOTS,
            2 + (stress_roots * stress_passes),
        )
        return 0 if (
            measured_directory_calls == 4
            and measured_version_calls == 2
            and unchanged_validation["cache_state"] == "validated"
            and unchanged_validation["compact_unchanged"]
            and changed_refresh["cache_state"] == "refreshed"
            and len(service._directory_cache) == expected_roots
        ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
