"""Synthetic, server-free pywebview benchmark for the VRAM Radar UI.

The benchmark loads the real ``web/index.html`` in a hidden pywebview window,
but injects only a small ``FakeApi`` whose Profile and snapshots live in memory.
It never opens the user's Profile, reads SSH configuration, or contacts a
server.  The process exits non-zero when an incremental-rendering contract is
broken, and always destroys its hidden window on completion or timeout.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any


SERVER_COUNT = 120
RENDER_ITERATIONS = 100
DIRECTORY_ENTRY_COUNT = 160


def _empty_profile() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile_revision": 1,
        "id": "webview-benchmark",
        "display_name": "Synthetic benchmark",
        "refresh_seconds": 3_600,
        "server_config_path": "",
        "auto_sync_servers": False,
        "ignored_ssh_aliases": [],
        "navigator_side": "right",
        "close_behavior": "exit",
        "ui_language": "zh-CN",
        "servers": [],
    }


def _empty_snapshot() -> dict[str, Any]:
    now = "2026-08-30T00:00:00Z"
    return {
        "profile": {"refresh_seconds": 3_600},
        "fetched_at": now,
        "notices": [],
        "monitoring": {
            "revision": 1,
            "in_flight": False,
            "paused": False,
            "data_updated_at": now,
        },
        "summary": {
            "revision": 1,
            "free_vram_gib": 0,
            "total_vram_gib": 0,
            "online_servers": 0,
            "total_servers": 0,
            "total_gpus": 0,
            "data_updated_at": now,
        },
        "servers": [],
    }


class FakeApi:
    """Minimum startup API for the benchmark; every response is synthetic."""

    def __init__(self) -> None:
        self._profile = _empty_profile()
        self._snapshot = _empty_snapshot()
        self.calls: dict[str, int] = {
            "get_profile": 0,
            "get_status": 0,
            "get_snapshot": 0,
            "check_for_updates": 0,
            "request_background_refresh": 0,
        }
        self._lock = threading.Lock()

    def _count(self, name: str) -> None:
        with self._lock:
            self.calls[name] += 1

    def get_profile(self) -> dict[str, Any]:
        self._count("get_profile")
        return copy.deepcopy(self._profile)

    def get_status(self, _force: bool = False, _server_id: str | None = None) -> dict[str, Any]:
        self._count("get_status")
        return copy.deepcopy(self._snapshot)

    def get_snapshot(self) -> dict[str, Any]:
        self._count("get_snapshot")
        return copy.deepcopy(self._snapshot)

    def check_for_updates(self) -> dict[str, Any]:
        self._count("check_for_updates")
        return {"ok": True, "update_available": False}

    def request_background_refresh(self) -> dict[str, Any]:
        self._count("request_background_refresh")
        return {"ok": True, "accepted": False, "synthetic": True}


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _filetime_seconds(value: wintypes.FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return ticks / 10_000_000


def _windows_process_tree_metrics(root_pid: int) -> dict[str, Any] | None:
    """Read CPU time and working set for this benchmark's process tree.

    The implementation is read-only and uses Win32 process snapshots, so the
    measurement includes WebView2 children without requiring psutil, WMI, or a
    shell subprocess.  Inaccessible short-lived children are counted and
    skipped rather than failing the UI benchmark.
    """

    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    snapshot_flag = 0x00000002
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel32.CreateToolhelp32Snapshot(snapshot_flag, 0)
    if not handle or int(handle) == invalid_handle:
        return None
    parents: dict[int, int] = {}
    names: dict[int, str] = {}
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        available = bool(kernel32.Process32FirstW(handle, ctypes.byref(entry)))
        while available:
            pid = int(entry.th32ProcessID)
            parents[pid] = int(entry.th32ParentProcessID)
            names[pid] = str(entry.szExeFile)
            entry.dwSize = ctypes.sizeof(entry)
            available = bool(kernel32.Process32NextW(handle, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(handle)

    process_ids = {int(root_pid)}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in process_ids and pid not in process_ids:
                process_ids.add(pid)
                changed = True

    query_limited = 0x1000
    vm_read = 0x0010
    working_set = 0
    peak_working_set = 0
    cpu_seconds = 0.0
    sampled = 0
    inaccessible = 0
    sampled_names: dict[str, int] = {}
    for pid in sorted(process_ids):
        process = kernel32.OpenProcess(query_limited | vm_read, False, pid)
        if not process:
            inaccessible += 1
            continue
        try:
            memory = _ProcessMemoryCounters()
            memory.cb = ctypes.sizeof(memory)
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            memory_ok = bool(
                psapi.GetProcessMemoryInfo(
                    process, ctypes.byref(memory), ctypes.sizeof(memory)
                )
            )
            time_ok = bool(
                kernel32.GetProcessTimes(
                    process,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
            )
            if not memory_ok and not time_ok:
                inaccessible += 1
                continue
            sampled += 1
            name = names.get(pid, "unknown")
            sampled_names[name] = sampled_names.get(name, 0) + 1
            if memory_ok:
                working_set += int(memory.WorkingSetSize)
                peak_working_set += int(memory.PeakWorkingSetSize)
            if time_ok:
                cpu_seconds += _filetime_seconds(kernel) + _filetime_seconds(user)
        finally:
            kernel32.CloseHandle(process)
    return {
        "root_pid": int(root_pid),
        "discovered_process_count": len(process_ids),
        "sampled_process_count": sampled,
        "inaccessible_process_count": inaccessible,
        "working_set_mib": round(working_set / 1024 / 1024, 3),
        "peak_working_set_sum_mib": round(peak_working_set / 1024 / 1024, 3),
        "cpu_seconds": round(cpu_seconds, 6),
        "processes": sampled_names,
    }


BENCHMARK_JAVASCRIPT = r"""
(() => {
  const metricCopy = () => ({
    fullRenders: Number(window.__VRAM_RADAR_PERF__?.fullRenders || 0),
    serverCardCreates: Number(window.__VRAM_RADAR_PERF__?.serverCardCreates || 0),
    directoryRepaints: Number(window.__VRAM_RADAR_PERF__?.directoryRepaints || 0),
    navigatorBuilds: Number(window.__VRAM_RADAR_PERF__?.navigatorBuilds || 0),
  });
  const metricDelta = (after, before) => Object.fromEntries(
    Object.keys(after).map(key => [key, after[key] - before[key]]),
  );
  const heap = () => {
    const value = performance.memory;
    if (!value) return null;
    return {
      used_mib: Number((value.usedJSHeapSize / 1024 / 1024).toFixed(3)),
      total_mib: Number((value.totalJSHeapSize / 1024 / 1024).toFixed(3)),
      limit_mib: Number((value.jsHeapSizeLimit / 1024 / 1024).toFixed(3)),
    };
  };
  const forceLayout = element => {
    const bounds = element.getBoundingClientRect();
    return bounds.width + bounds.height + element.offsetHeight;
  };
  const round = value => Number(value.toFixed(3));
  try {
    window.clearInterval(refreshTimer);
    window.clearTimeout(refreshPollTimer);
    refreshTimer = null;
    refreshPollTimer = null;
    refreshPollGeneration += 1;
    if (ui.dialog.open) ui.dialog.close();

    const now = '2026-08-30T00:00:00Z';
    const servers = Array.from({length: 120}, (_, index) => {
      const suffix = String(index).padStart(3, '0');
      const server = {
        server_id: `synthetic-${suffix}`,
        display_name: `Synthetic GPU ${suffix}`,
        backend: 'direct_ssh',
        view_kind: 'live-memory',
        host: `synthetic-${suffix}.invalid`,
        total_gpus: 1,
        free_vram_gib: 60,
        total_vram_gib: 80,
        gpus: [{
          gpu_index: 0,
          gpu_type: index % 2 ? 'NVIDIA A100-SXM4-80GB' : 'NVIDIA H100 80GB HBM3',
          memory_used_gib: 20,
          memory_free_gib: 60,
          memory_total_gib: 80,
          utilization_percent: index % 100,
          temperature_c: 42 + (index % 20),
        }],
        processes: {
          supported: true,
          current_user: 'benchmark',
          active: [],
          metadata_limited: false,
        },
        account: {
          username: 'benchmark',
          home_directory: `/srv/benchmark/work-${suffix}`,
        },
        connection: {
          state: 'online',
          data_origin: 'live',
          data_revision: 7,
          last_success_at: now,
          retry_at: null,
          error: null,
        },
      };
      if (index === 0) {
        server.backend = 'slurm_ssh';
        server.view_kind = 'scheduler';
        server.nodes = [
          {
            node: 'gpu-r2080-01', partition: 'gpu-medium', gpu_type: 'R2080 × 8',
            memory_per_gpu_gib: 12, total_gpus: 8, free_gpus: 0,
            allocated_gpus: 8, total_vram_gib: 96, free_vram_gib: 0,
            tasks: ['1001'], state: 'alloc',
          },
          {
            node: 'gpu-titanx-01', partition: 'gpu-medium', gpu_type: 'TITANX × 8',
            memory_per_gpu_gib: 12, total_gpus: 8, free_gpus: 4,
            allocated_gpus: 4, total_vram_gib: 96, free_vram_gib: 48,
            tasks: [], state: 'mix',
          },
          {
            node: 'gpu-h100-01', partition: 'gpu-large', gpu_type: 'H100-80G',
            memory_per_gpu_gib: 80, total_gpus: 8, free_gpus: 8,
            allocated_gpus: 0, total_vram_gib: 640, free_vram_gib: 640,
            tasks: [], state: 'idle',
          },
        ];
        server.tasks = {
          current_user: 'benchmark', history_supported: true, history_window_hours: 24,
          counts: {PENDING: 1, RUNNING: 1, COMPLETED: 1},
          active: [
            {job_id: '1001', name: 'training', user: 'benchmark', state: 'RUNNING', nodes: 'gpu-a100-01', elapsed: '01:02:03', submitted_at: now, time_limit: '04:00:00', gpu_count: 4},
            {job_id: '1002', name: 'queued', user: 'other-user', state: 'PENDING', reason: 'Resources', elapsed: '00:00', submitted_at: now, time_limit: '02:00:00', gpu_count: 2},
          ],
          recent: [{job_id: '999', name: 'completed', user: 'benchmark', state: 'COMPLETED', nodes: 'gpu-a100-01', elapsed: '00:30:00', ended_at: now, gpu_count: 1}],
        };
      } else if (index === 1) {
        server.backend = 'slurm_ssh';
        server.view_kind = 'scheduler';
        server.large_cluster = {total_nodes: 1000};
        server.node_groups = [{gpu_type: 'H100-80G', partition: 'gpu-large', total_nodes: 1000, total_gpus: 8000, free_gpus: 2400, issue_nodes: 2}];
        server.tasks = {current_user: 'benchmark', history_supported: false, counts: {}, active: [], recent: []};
      } else if (index === 2) {
        server.connection = {
          state: 'auth_required', data_origin: 'none', data_revision: 7,
          last_success_at: null, retry_at: null,
          error: {message: 'Authentication failed', code: 'authentication_failed', retryable: false},
        };
      } else if (index === 3) {
        server.connection = {
          state: 'stale', data_origin: 'cache', data_revision: 7,
          last_success_at: now, retry_at: new Date(Date.now() + 299_000).toISOString(),
          error: {message: '服务器连接超时', code: 'ssh_timeout', retryable: true},
        };
        server.processes.active = [{
          pid: '7321', user: 'other-user', owner_scope: 'other',
          name: 'ns2dtime_GNOT_L4_seed2023 · train.py',
          command_preview: 'python -u train.py --gpu 0', command_truncated: true,
          allocations: [{gpu_index: 0, memory_used_gib: 2.47}],
          memory_used_gib: 2.47, cpu_percent: 12.5, elapsed_seconds: 3600, started_at: now,
        }];
        server.cpu = {
          supported: true, logical_cores: 16, load_average: [0.42, 0.37, 0.31], sampled_at: now,
        };
      }
      return server;
    });
    const profile = {
      schema_version: 1,
      profile_revision: 2,
      id: 'webview-benchmark',
      display_name: 'Synthetic benchmark',
      refresh_seconds: 3600,
      server_config_path: '',
      auto_sync_servers: false,
      ignored_ssh_aliases: [],
      navigator_side: 'right',
      close_behavior: 'exit',
      ui_language: 'zh-CN',
      task_completion_watches: [],
      servers: servers.map(server => ({
        id: server.server_id,
        display_name: server.display_name,
        backend: server.backend,
        ssh_alias: server.server_id,
        host: server.host,
        port: 22,
        port_override: false,
        username: 'benchmark',
        identity_file: '',
        ssh_config_file: '',
        enabled: true,
        show_other_user_commands: false,
        connect_timeout_seconds: 10,
      })),
    };
    const snapshot = {
      profile: {refresh_seconds: 3600},
      fetched_at: now,
      notices: [],
      monitoring: {
        revision: 7,
        in_flight: false,
        paused: false,
        data_updated_at: now,
      },
      summary: {
        revision: 7,
        free_vram_gib: 7200,
        total_vram_gib: 9600,
        online_servers: 120,
        total_servers: 120,
        total_gpus: 120,
        data_updated_at: now,
      },
      notifications: {
        unread_count: 2,
        latest_sequence: 2,
        read_sequence: 0,
        events: [{
          sequence: 1,
          kind: 'task_completed',
          title: '任务已完成',
          message: 'synthetic-training 已结束。',
          label: 'synthetic-training',
          language: 'zh-CN',
          created_at: now,
        }, {
          sequence: 2,
          kind: 'update_available',
          title: '发现新版本',
          message: 'VRAM Radar 0.9.0 已可下载。',
          latest_version: '0.9.0',
          language: 'zh-CN',
          created_at: now,
        }],
      },
      servers,
    };

    currentProfile = profile;
    profileServerListReference = null;
    profileServersById = new Map();
    syncProfileConvenienceState(profile);
    currentSnapshot = null;
    lastRenderedRevision = null;
    lastSummaryRenderSignature = '';
    lastNavigatorRenderSignature = '';
    activeServerId = '';
    serverFleetPageOffset = 0;
    serverNavigatorFilter = 'all';
    serverNavigatorQuery = '';
    serverNavigationCards = [];
    serverNavigationCardsById = new Map();
    serverNavigatorItems = new Map();
    serverNavigatorPositions = new Map();
    renderedServerCardSignatures.clear();
    directoryTrees.clear();
    directoryRequestTokens.clear();
    openClusters.clear();
    openDirectoryNodes.clear();
    openTaskGroups.clear();
    openContextNotes.clear();
    ui.list.replaceChildren();
    ui.summary.replaceChildren();
    ui.editorList.replaceChildren();
    ui.serverNavigatorList.replaceChildren();
    const heapBefore = heap();

    const initialMetricsBefore = metricCopy();
    const initialStart = performance.now();
    render(snapshot);
    forceLayout(ui.list);
    const bellAlwaysVisible = !ui.taskAlertIndicator.hidden && ui.taskAlertCount.textContent === '2';
    ui.taskAlertIndicator.click();
    const notificationCenterOpens = !ui.notificationCenter.hidden
      && ui.taskAlertIndicator.getAttribute('aria-expanded') === 'true'
      && ui.notificationList.querySelectorAll('.notification-item').length === 2
      && ui.notificationList.textContent.includes('0.9.0')
      && ui.notificationList.querySelectorAll('.notification-update-action').length === 1
      && ui.clearNotifications && !ui.clearNotifications.disabled
      && ui.clearNotifications.textContent.includes('清空通知');
    latestUpdateProgress = {
      state: 'running',
      phase: 'downloading',
      downloaded_bytes: 4,
      total_bytes: 10,
      percent: 40,
      message: '',
    };
    renderNotificationCenter(currentSnapshot);
    const updateProgress = ui.notificationList.querySelector('.update-download-progress');
    const updateDownloadProgressIsVisibleAndDeterminate = Boolean(
      updateProgress
      && Number(updateProgress.value) === 40
      && ui.notificationList.querySelector('.install-latest-update')?.getAttribute('aria-busy') === 'true'
      && ui.notificationList.querySelector('.update-download-percent')?.textContent.includes('40%')
    );
    latestUpdateProgress = {state: 'idle', phase: 'idle'};
    renderNotificationCenter(currentSnapshot);
    document.body.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    const notificationCenterCloses = ui.notificationCenter.hidden
      && ui.taskAlertIndicator.getAttribute('aria-expanded') === 'false';
    const initialWall = performance.now() - initialStart;
    const initialMetricsAfter = metricCopy();
    const visibleCardsAfterInitial = ui.list.querySelectorAll(':scope > .server-card').length;
    const firstCard = ui.list.querySelector('.server-card');
    const initialDomNodes = document.getElementsByTagName('*').length;

    const repeatMetricsBefore = metricCopy();
    const repeatStart = performance.now();
    for (let index = 0; index < 100; index += 1) render(snapshot);
    forceLayout(ui.list);
    const repeatWall = performance.now() - repeatStart;
    const repeatMetricsAfter = metricCopy();
    const firstCardAfterRepeatedRender = ui.list.querySelector('.server-card');

    const taskWatchButtonBefore = ui.list.querySelector('[data-task-key].task-watch-toggle');
    const taskWatchKey = taskWatchButtonBefore?.dataset.taskKey || '';
    currentProfile = {
      ...currentProfile,
      profile_revision: currentProfile.profile_revision + 1,
      task_completion_watches: [{
        server_id: 'synthetic-000', task_key: taskWatchKey, task_kind: 'slurm',
        task_id: '1001', label: 'training', owner: 'benchmark', owner_scope: 'mine',
      }],
    };
    render(snapshot);
    const taskWatchButtonAfterAdd = ui.list.querySelector(`[data-task-key="${taskWatchKey}"]`);
    document.querySelector('[data-server-navigator-filter="watches"]').click();
    const watchedPanel = ui.serverNavigatorList.querySelector('.navigator-task-watches');
    const watchedPanelStartsCollapsed = Boolean(watchedPanel) && !watchedPanel.open;
    watchedPanel?.querySelector('summary')?.click();
    const watchedPanelExpands = Boolean(watchedPanel?.open)
      && watchedPanel.querySelectorAll('.remove-task-watch').length === 1
      && Boolean(watchedPanel.querySelector('.clear-task-watches'));
    if (watchedPanel) watchedPanel.open = false;
    document.activeElement?.blur();
    ui.serverNavigator.classList.remove('dragging');
    applyServerNavigatorSide('right');
    forceLayout(ui.serverNavigator);
    const watchedMarker = watchedPanel?.querySelector('.navigator-watch-marker');
    const watchRightRailBounds = ui.serverNavigator.getBoundingClientRect();
    const watchRightMarkerBounds = watchedMarker?.getBoundingClientRect();
    const watchedMarkerVisibleRight = Boolean(watchRightMarkerBounds)
      && watchRightMarkerBounds.left >= watchRightRailBounds.left - 0.5
      && watchRightMarkerBounds.right <= watchRightRailBounds.right + 0.5;
    applyServerNavigatorSide('left');
    forceLayout(ui.serverNavigator);
    const watchLeftRailBounds = ui.serverNavigator.getBoundingClientRect();
    const watchLeftMarkerBounds = watchedMarker?.getBoundingClientRect();
    const watchedMarkerVisibleLeft = Boolean(watchLeftMarkerBounds)
      && watchLeftMarkerBounds.left >= watchLeftRailBounds.left - 0.5
      && watchLeftMarkerBounds.right <= watchLeftRailBounds.right + 0.5;
    applyServerNavigatorSide('right');
    const watchedManagementIsOutsideSettings = !document.getElementById('task-completion-watch-list');
    currentProfile = {
      ...currentProfile,
      profile_revision: currentProfile.profile_revision + 1,
      task_completion_watches: [],
    };
    render(snapshot);
    const taskWatchButtonAfterRemove = ui.list.querySelector(`[data-task-key="${taskWatchKey}"]`);
    const taskWatchStateRepaintsImmediately = Boolean(taskWatchKey)
      && taskWatchButtonBefore?.getAttribute('aria-pressed') === 'false'
      && taskWatchButtonAfterAdd?.getAttribute('aria-pressed') === 'true'
      && taskWatchButtonAfterRemove?.getAttribute('aria-pressed') === 'false';

    const appShell = document.querySelector('.app-shell');
    const mainContent = document.querySelector('main');
    const appBounds = appShell.getBoundingClientRect();
    ui.serverNavigator.classList.remove('dragging');
    applyServerNavigatorSide('right');
    forceLayout(appShell);
    const rightCollapsedMainBounds = mainContent.getBoundingClientRect();
    const rightRailBounds = ui.serverNavigator.getBoundingClientRect();
    const rightCollapsedTrackWidth = appBounds.right - rightCollapsedMainBounds.right;
    ui.serverNavigator.classList.add('dragging');
    forceLayout(appShell);
    const rightExpandedMainBounds = mainContent.getBoundingClientRect();
    const expandedPanelBounds = ui.serverNavigator.querySelector('.server-navigator-panel').getBoundingClientRect();
    ui.serverNavigator.classList.remove('dragging');
    applyServerNavigatorSide('left');
    forceLayout(appShell);
    const leftCollapsedMainBounds = mainContent.getBoundingClientRect();
    const leftRailBounds = ui.serverNavigator.getBoundingClientRect();
    const leftCollapsedTrackWidth = leftCollapsedMainBounds.left - appBounds.left;
    ui.serverNavigator.classList.add('dragging');
    forceLayout(appShell);
    const leftExpandedMainBounds = mainContent.getBoundingClientRect();
    applyServerNavigatorSide('right');
    ui.serverNavigator.classList.remove('dragging');

    const directoryRoot = '/srv/benchmark/code';
    const expandedRoot = `${directoryRoot}/project-000`;
    const directoryEntries = [
      ...Array.from({length: 80}, (_, index) => {
        const name = `project-${String(index).padStart(3, '0')}`;
        return {
          name,
          path: name,
          absolute_path: `${directoryRoot}/${name}`,
          parent_absolute_path: directoryRoot,
          kind: 'directory',
          has_more: false,
          size_bytes: 0,
          modified_at: now,
        };
      }),
      ...Array.from({length: 80}, (_, index) => {
        const name = `module-${String(index).padStart(3, '0')}.py`;
        return {
          name,
          path: `project-000/${name}`,
          absolute_path: `${expandedRoot}/${name}`,
          parent_absolute_path: expandedRoot,
          kind: 'file',
          has_more: false,
          size_bytes: 1024 + index,
          modified_at: now,
        };
      }),
    ];
    const directoryAccount = {
      username: 'benchmark',
      home_directory: '/srv/benchmark',
      directory_tree: {
        supported: true,
        root: directoryRoot,
        root_source: 'auto',
        entries: directoryEntries,
        truncated: false,
      },
    };
    const directoryState = directoryStateFromAccount(
      directoryAccount,
      {state: 'hit', age_seconds: 0.25},
    );
    directoryState.loadedRoots.add(expandedRoot);
    directoryState.loadedRootOrder.push(expandedRoot);
    directoryTrees.set(servers[0].server_id, directoryState);
    openClusters.add(`${servers[0].server_id}:account-directory`);

    const directoryMetricsBefore = metricCopy();
    const directoryCardBefore = ui.list.querySelector('.server-card');
    const directoryStart = performance.now();
    repaintDirectory(servers[0].server_id);
    const directoryModule = directoryCardBefore.querySelector('.directory-module');
    forceLayout(directoryModule);
    const directoryFirstWall = performance.now() - directoryStart;
    const firstModuleNodes = directoryModule.querySelectorAll('*').length;
    const firstDocumentNodes = document.getElementsByTagName('*').length;
    const firstRootNodes = directoryModule.querySelectorAll('.directory-node').length;
    const firstFileNodes = directoryModule.querySelectorAll('.directory-file').length;

    const expandStart = performance.now();
    openDirectoryNodes.add(`${servers[0].server_id}:${expandedRoot}`);
    repaintDirectory(servers[0].server_id);
    const expandedModule = directoryCardBefore.querySelector('.directory-module');
    forceLayout(expandedModule);
    const expandWall = performance.now() - expandStart;
    const expandedModuleNodes = expandedModule.querySelectorAll('*').length;
    const expandedDocumentNodes = document.getElementsByTagName('*').length;
    const expandedFileNodes = expandedModule.querySelectorAll('.directory-file').length;

    const collapseStart = performance.now();
    openDirectoryNodes.delete(`${servers[0].server_id}:${expandedRoot}`);
    repaintDirectory(servers[0].server_id);
    const collapsedModule = directoryCardBefore.querySelector('.directory-module');
    forceLayout(collapsedModule);
    const collapseWall = performance.now() - collapseStart;
    const collapsedModuleNodes = collapsedModule.querySelectorAll('*').length;
    const collapsedDocumentNodes = document.getElementsByTagName('*').length;
    const collapsedFileNodes = collapsedModule.querySelectorAll('.directory-file').length;
    const directoryMetricsAfter = metricCopy();
    const heapAfterDirectory = heap();

    const settingsStart = performance.now();
    openSettings({forceNormal: true});
    forceLayout(ui.dialog);
    const settingsWall = performance.now() - settingsStart;
    const editors = [...ui.editorList.querySelectorAll(':scope > .server-editor')];
    editors[0].open = true;
    forceLayout(editors[0]);
    const primaryGrid = editors[0].querySelector('.server-primary-fields');
    const displayNameBounds = editors[0].querySelector('[data-field="display_name"]').getBoundingClientRect();
    const backendBounds = editors[0].querySelector('[data-field="backend"]').getBoundingClientRect();
    const aliasBounds = editors[0].querySelector('[data-field="ssh_alias"]').getBoundingClientRect();
    const hostBounds = editors[0].querySelector('[data-field="host"]').getBoundingClientRect();
    const primaryHelpBounds = editors[0].querySelector('.primary-help').getBoundingClientRect();
    const primaryGridBounds = primaryGrid.getBoundingClientRect();
    const primaryFieldsAlign = Math.abs(displayNameBounds.top - backendBounds.top) < 0.5
      && Math.abs(displayNameBounds.height - backendBounds.height) < 0.5
      && Math.abs(aliasBounds.top - hostBounds.top) < 0.5
      && Math.abs(aliasBounds.height - hostBounds.height) < 0.5
      && Math.abs(primaryHelpBounds.left - primaryGridBounds.left) < 0.5;
    const primaryFieldGeometry = {
      display_name: {top: displayNameBounds.top, height: displayNameBounds.height},
      backend: {top: backendBounds.top, height: backendBounds.height},
      ssh_alias: {top: aliasBounds.top, height: aliasBounds.height},
      host: {top: hostBounds.top, height: hostBounds.height},
      help_left: primaryHelpBounds.left,
      grid_left: primaryGridBounds.left,
    };
    const editorBodiesOwnAllControls = editors.every(editor => {
      const body = editor.querySelector(':scope > .server-editor-body');
      return Boolean(
        body
        && body.contains(editor.querySelector('[data-field="display_name"]'))
        && body.contains(editor.querySelector('.server-editor-more'))
        && body.contains(editor.querySelector('.ssh-key-setup'))
      );
    });
    const primarySettingsGroupsStartCollapsed = !ui.profileSettings.open && !ui.importPanel.open;
    const editorIds = editors.map(editor => editor.querySelector('[data-field="id"]')?.value || '');
    const firstDisplayName = editors[0].querySelector('[data-field="display_name"]');
    firstDisplayName.value = 'Edited across pages';
    firstDisplayName.dispatchEvent(new Event('input', {bubbles: true}));
    const firstPassword = editors[0].querySelector('[data-field="password"]');
    firstPassword.value = 'benchmark-secret';
    firstPassword.dispatchEvent(new Event('input', {bubbles: true}));
    const detachedFirstEditor = editors[0];
    const firstPageHandlesSortable = editors.every(editor =>
      editor.querySelector('.server-drag-handle')?.getAttribute('aria-disabled') === 'false'
    );
    const firstDraftIdBeforeReorder = settingsServerDrafts[0].id;
    const secondDraftIdBeforeReorder = settingsServerDrafts[1].id;
    const dragTransfer = new DataTransfer();
    const dragTargetBounds = editors[1].getBoundingClientRect();
    editors[0].querySelector('.server-drag-handle').dispatchEvent(new DragEvent('dragstart', {
      bubbles: true,
      cancelable: true,
      dataTransfer: dragTransfer,
    }));
    editors[1].dispatchEvent(new DragEvent('dragover', {
      bubbles: true,
      cancelable: true,
      clientY: dragTargetBounds.bottom - 1,
      dataTransfer: dragTransfer,
    }));
    editors[1].dispatchEvent(new DragEvent('drop', {
      bubbles: true,
      cancelable: true,
      clientY: dragTargetBounds.bottom - 1,
      dataTransfer: dragTransfer,
    }));
    const directDragEventsReorder = settingsServerDrafts[0].id === secondDraftIdBeforeReorder
      && settingsServerDrafts[1].id === firstDraftIdBeforeReorder
      && ui.editorList.querySelector('.server-editor [data-field="id"]')?.value === secondDraftIdBeforeReorder;
    reorderServerDraft(1, 0);
    settingsServerPageOffset = 0;
    renderServerEditorPage();
    const helperMovedIndex = reorderServerDraft(0, 2);
    const directReorderHelperWorks = helperMovedIndex === 1
      && settingsServerDrafts[0].id === secondDraftIdBeforeReorder
      && settingsServerDrafts[1].id === firstDraftIdBeforeReorder;
    reorderServerDraft(helperMovedIndex, 0);
    const settingsDomNodes = ui.dialog.querySelectorAll('*').length;
    ui.editorNextPage.click();
    const secondPageEditors = [...ui.editorList.querySelectorAll(':scope > .server-editor')];
    const secondPageIds = secondPageEditors.map(editor => editor.querySelector('[data-field="id"]')?.value || '');
    const secondPageFirstBeforeDetachedSync = settingsServerDrafts[20].display_name;
    firstDisplayName.value = 'Detached editor stays with server zero';
    syncServerDraftFromEditor(detachedFirstEditor);
    settingsServerPageOffset = 100;
    renderServerEditorPage();
    const finalPageEditors = [...ui.editorList.querySelectorAll(':scope > .server-editor')];
    const finalPageHandlesSortable = finalPageEditors.every(editor =>
      editor.querySelector('.server-drag-handle')?.getAttribute('aria-disabled') === 'false'
    );
    ui.editorSearch.value = 'synthetic-119';
    ui.editorSearch.dispatchEvent(new Event('input', {bubbles: true}));
    const searchedHandlesDisabled = [...ui.editorList.querySelectorAll('.server-drag-handle')]
      .every(handle => handle.getAttribute('aria-disabled') === 'true');
    const searchedIds = [...ui.editorList.querySelectorAll(':scope > .server-editor')]
      .map(editor => editor.querySelector('[data-field="id"]')?.value || '');
    const collectedProfile = collectProfile();
    const collectedPasswords = collectPasswordUpdates();
    const detachedEditorSyncUsesStableIdentity = settingsServerDrafts[0].display_name === 'Detached editor stays with server zero'
      && settingsServerDrafts[20].display_name === secondPageFirstBeforeDetachedSync;
    ui.dialog.dispatchEvent(new Event('close'));
    const staleClosePreservesOpenSession = settingsServerDrafts.length === 120;
    const heapAfterSettings = heap();
    if (ui.dialog.open) {
      ui.dialog.close();
      ui.dialog.dispatchEvent(new Event('close'));
    }
    const closedEditorCount = ui.editorList.querySelectorAll(':scope > .server-editor').length;

    openSettings({forceNormal: true});
    window.VRAMRadarI18n.setLanguage('en');
    renderNotificationCenter(currentSnapshot);
    const englishCpuOverview = document.querySelector('#server-card-synthetic-003 .cpu-overview')?.textContent || '';
    const englishCpuProcess = document.querySelector('#server-card-synthetic-003 .process-table tbody tr')?.textContent || '';
    const directCpuInformationRendered = englishCpuOverview.includes('16 logical cores')
      && englishCpuOverview.includes('1 min')
      && englishCpuOverview.includes('5 min')
      && englishCpuOverview.includes('15 min')
      && englishCpuOverview.includes('0.42')
      && englishCpuOverview.includes('0.37')
      && englishCpuOverview.includes('0.31')
      && englishCpuOverview.includes('Running / waiting tasks')
      && englishCpuOverview.includes('average number of tasks running')
      && englishCpuProcess.includes('12.5%');
    const englishSchedulerRows = [...document.querySelectorAll('.scheduler-node-table tbody tr')]
      .map(row => row.innerText);
    const schedulerStateCellsFit = [...document.querySelectorAll('.scheduler-node-table td:last-child')]
      .every(cell => cell.scrollWidth <= cell.clientWidth + 1);
    const untranslatedChinese = [];
    const translationWalker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    let translationNode = translationWalker.nextNode();
    while (translationNode) {
      if (
        /[\u3400-\u9fff]/u.test(translationNode.nodeValue || '')
        && !translationNode.parentElement?.closest('template, script, style')
      ) untranslatedChinese.push((translationNode.nodeValue || '').trim());
      translationNode = translationWalker.nextNode();
    }
    document.querySelectorAll('[aria-label], [title], [placeholder]').forEach(element => {
      if (element.closest('template')) return;
      for (const attribute of ['aria-label', 'title', 'placeholder']) {
        const value = element.getAttribute(attribute) || '';
        if (/[\u3400-\u9fff]/u.test(value)) untranslatedChinese.push(`${attribute}: ${value}`);
      }
    });
    window.VRAMRadarI18n.setLanguage('zh-CN');
    ui.dialog.close();
    ui.dialog.dispatchEvent(new Event('close'));

    const assertions = {
      initial_visible_cards_paginated_to_50: visibleCardsAfterInitial === 50,
      initial_server_cards_created_once: metricDelta(initialMetricsAfter, initialMetricsBefore).serverCardCreates === 50,
      repeated_render_preserves_card_identity: firstCard === firstCardAfterRepeatedRender,
      notification_bell_is_always_visible: bellAlwaysVisible,
      notification_center_opens_renders_and_closes: notificationCenterOpens && notificationCenterCloses,
      notification_center_exposes_clear_action: notificationCenterOpens,
      update_download_progress_is_visible_and_determinate: updateDownloadProgressIsVisibleAndDeterminate,
      repeated_render_does_not_recreate_cards: metricDelta(repeatMetricsAfter, repeatMetricsBefore).serverCardCreates === 0,
      repeated_render_counts_all_iterations: metricDelta(repeatMetricsAfter, repeatMetricsBefore).fullRenders === 100,
      repeated_render_does_not_rebuild_navigator: metricDelta(repeatMetricsAfter, repeatMetricsBefore).navigatorBuilds === 0,
      task_watch_state_repaints_without_server_refresh: taskWatchStateRepaintsImmediately,
      watched_tasks_live_in_collapsible_sidebar: watchedPanelStartsCollapsed && watchedPanelExpands && watchedManagementIsOutsideSettings,
      watched_task_compact_marker_survives_both_sides: watchedMarkerVisibleRight && watchedMarkerVisibleLeft,
      navigator_right_collapsed_track_is_compact: rightCollapsedTrackWidth <= 50.5 && rightCollapsedTrackWidth >= rightRailBounds.width,
      navigator_left_collapsed_track_is_compact: leftCollapsedTrackWidth <= 50.5 && leftCollapsedTrackWidth >= rightRailBounds.width,
      navigator_left_stays_in_the_same_grid_row: Math.abs(leftRailBounds.top - rightRailBounds.top) < 0.5,
      navigator_expansion_does_not_reflow_main: Math.abs(rightExpandedMainBounds.width - rightCollapsedMainBounds.width) < 0.5
        && Math.abs(leftExpandedMainBounds.width - leftCollapsedMainBounds.width) < 0.5,
      directory_fixture_has_160_entries: directoryEntries.length === 160,
      directory_initially_skips_cached_children: firstRootNodes === 80 && firstFileNodes === 0,
      directory_expand_materializes_cached_children: expandedFileNodes === 80 && expandedModuleNodes > firstModuleNodes,
      directory_collapse_releases_cached_children: collapsedFileNodes === 0 && collapsedModuleNodes === firstModuleNodes,
      directory_updates_preserve_server_card: directoryCardBefore === ui.list.querySelector('.server-card'),
      directory_uses_three_local_repaints: metricDelta(directoryMetricsAfter, directoryMetricsBefore).directoryRepaints === 3,
      settings_renders_only_first_20_servers: editors.length === 20,
      settings_editor_body_owns_all_controls: editorBodiesOwnAllControls,
      settings_primary_groups_start_collapsed: primarySettingsGroupsStartCollapsed,
      settings_primary_connection_fields_align: primaryFieldsAlign,
      settings_first_page_ids_remain_unique: new Set(editorIds).size === 20,
      settings_first_page_handles_are_directly_sortable: firstPageHandlesSortable,
      settings_drag_event_chain_reorders_directly: directDragEventsReorder,
      settings_direct_reorder_helper_preserves_identity: directReorderHelperWorks,
      settings_second_page_is_distinct: secondPageIds.length === 20 && secondPageIds[0] !== editorIds[0],
      settings_final_page_handles_remain_sortable: finalPageHandlesSortable,
      settings_search_disables_ambiguous_reordering: searchedHandlesDisabled,
      settings_detached_editor_sync_uses_stable_identity: detachedEditorSyncUsesStableIdentity,
      settings_stale_close_event_preserves_open_session: staleClosePreservesOpenSession,
      settings_search_renders_only_match: searchedIds.length === 1 && searchedIds[0] === 'synthetic-119',
      settings_cross_page_edit_is_preserved: collectedProfile.servers.length === 120 && collectedProfile.servers[0].display_name === 'Detached editor stays with server zero',
      settings_cross_page_password_is_preserved_outside_profile: collectedPasswords['synthetic-000'] === 'benchmark-secret' && !JSON.stringify(collectedProfile).includes('benchmark-secret'),
      settings_close_releases_editor_dom: closedEditorCount === 0,
      direct_cpu_information_is_rendered: directCpuInformationRendered,
      english_interface_has_no_untranslated_chinese: untranslatedChinese.length === 0,
      english_scheduler_rows_cover_generic_gpu_models_and_states: englishSchedulerRows.some(row => row.includes('R2080 × 8 · 12 GiB/GPU') && row.includes('Allocated'))
        && englishSchedulerRows.some(row => row.includes('TITANX × 8 · 12 GiB/GPU') && row.includes('Partially allocated')),
      scheduler_state_cells_do_not_overflow: schedulerStateCellsFit,
    };
    return JSON.stringify({
      ok: Object.values(assertions).every(Boolean),
      synthetic_only: true,
      remote_connections: 0,
      document_hidden: document.hidden,
      initial_render: {
        server_count: 120,
        visible_server_cards: visibleCardsAfterInitial,
        wall_ms: round(initialWall),
        dom_nodes: initialDomNodes,
        metrics_delta: metricDelta(initialMetricsAfter, initialMetricsBefore),
      },
      repeated_render: {
        iterations: 100,
        total_wall_ms: round(repeatWall),
        average_wall_ms: round(repeatWall / 100),
        server_card_dom_identity_preserved: firstCard === firstCardAfterRepeatedRender,
        visible_server_cards: ui.list.querySelectorAll(':scope > .server-card').length,
        metrics_delta: metricDelta(repeatMetricsAfter, repeatMetricsBefore),
      },
      navigator_layout: {
        collapsed_rail_width_px: round(rightRailBounds.width),
        expanded_panel_width_px: round(expandedPanelBounds.width),
        right_collapsed_track_width_px: round(rightCollapsedTrackWidth),
        left_collapsed_track_width_px: round(leftCollapsedTrackWidth),
        right_expansion_main_layout_shift_px: round(rightExpandedMainBounds.width - rightCollapsedMainBounds.width),
        left_expansion_main_layout_shift_px: round(leftExpandedMainBounds.width - leftCollapsedMainBounds.width),
      },
      directory_module: {
        entry_count: directoryEntries.length,
        first_local_paint: {
          wall_ms: round(directoryFirstWall),
          module_dom_nodes: firstModuleNodes,
          document_dom_nodes: firstDocumentNodes,
          root_directory_nodes: firstRootNodes,
          cached_child_file_nodes: firstFileNodes,
        },
        expand_cached_subtree: {
          wall_ms: round(expandWall),
          module_dom_nodes: expandedModuleNodes,
          document_dom_nodes: expandedDocumentNodes,
          cached_child_file_nodes: expandedFileNodes,
        },
        collapse_cached_subtree: {
          wall_ms: round(collapseWall),
          module_dom_nodes: collapsedModuleNodes,
          document_dom_nodes: collapsedDocumentNodes,
          cached_child_file_nodes: collapsedFileNodes,
        },
        metrics_delta: metricDelta(directoryMetricsAfter, directoryMetricsBefore),
      },
      settings: {
        server_count: 120,
        wall_ms: round(settingsWall),
        editor_count: editors.length,
        unique_editor_ids: new Set(editorIds).size,
        dialog_dom_nodes: settingsDomNodes,
        primary_field_geometry: primaryFieldGeometry,
      },
      js_heap: {
        before: heapBefore,
        after_directory: heapAfterDirectory,
        after_settings: heapAfterSettings,
      },
      ui_render_metrics_final: metricCopy(),
      assertions,
      untranslated_chinese: [...new Set(untranslatedChinese)].slice(0, 100),
    });
  } catch (error) {
    return JSON.stringify({
      ok: false,
      synthetic_only: true,
      remote_connections: 0,
      error: String(error?.message || error),
      stack: String(error?.stack || ''),
    });
  }
})()
"""


def _wait_until_ready(window: Any, deadline: float) -> None:
    last_error: Exception | None = None
    readiness_script = """
        JSON.stringify({
          ready: document.readyState === 'complete'
            && typeof render === 'function'
            && typeof currentProfile !== 'undefined'
            && currentProfile !== null
            && typeof currentSnapshot !== 'undefined'
            && currentSnapshot !== null
            && Boolean(document.getElementById('settings-dialog')),
          state: document.readyState,
        })
    """
    while time.monotonic() < deadline:
        try:
            value = window.evaluate_js(readiness_script)
            payload = json.loads(value) if isinstance(value, str) else value
            if payload and payload.get("ready"):
                return
        except Exception as exc:  # pywebview raises while the page is loading
            last_error = exc
        time.sleep(0.05)
    if last_error is not None:
        raise TimeoutError(f"WebView did not become ready: {last_error}")
    raise TimeoutError("WebView did not become ready before the benchmark deadline")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    args = parser.parse_args()
    timeout_seconds = max(10.0, float(args.timeout_seconds))
    index_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "vram_radar"
        / "web"
        / "index.html"
    )
    if not index_path.is_file():
        print(json.dumps({"ok": False, "error": f"missing UI: {index_path}"}, indent=2))
        return 2

    try:
        import webview
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"pywebview unavailable: {exc}"}, indent=2))
        return 2

    logging.disable(logging.CRITICAL)
    api = FakeApi()
    result: dict[str, Any] = {}
    result_lock = threading.Lock()
    timed_out = threading.Event()
    started_at = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="vram-radar-webview-benchmark-") as temporary:
        window = webview.create_window(
            "VRAM Radar synthetic UI benchmark",
            url=index_path.as_uri(),
            js_api=api,
            width=1_180,
            height=780,
            min_size=(840, 600),
            hidden=True,
            focus=False,
            background_color="#071017",
        )
        if window is None:
            print(json.dumps({"ok": False, "error": "pywebview returned no window"}, indent=2))
            return 2

        def abort_on_timeout() -> None:
            timed_out.set()
            with result_lock:
                result.setdefault("error", f"benchmark exceeded {timeout_seconds:g} seconds")
                result["ok"] = False
            try:
                window.destroy()
            except Exception:
                pass

        watchdog = threading.Timer(timeout_seconds, abort_on_timeout)
        watchdog.daemon = True
        watchdog.start()

        def run_benchmark() -> None:
            try:
                deadline = time.monotonic() + max(1.0, timeout_seconds - 1.0)
                _wait_until_ready(window, deadline)
                startup_wall_ms = round((time.perf_counter() - started_at) * 1_000, 3)
                process_before = _windows_process_tree_metrics(os.getpid())
                raw = window.evaluate_js(BENCHMARK_JAVASCRIPT)
                payload = json.loads(raw) if isinstance(raw, str) else raw
                process_after = _windows_process_tree_metrics(os.getpid())
                if not isinstance(payload, dict):
                    raise TypeError(f"unexpected JavaScript result: {type(payload).__name__}")
                payload["startup_to_ready_wall_ms"] = startup_wall_ms
                payload["fake_api_calls"] = dict(api.calls)
                payload["timeout_seconds"] = timeout_seconds
                if process_before is not None and process_after is not None:
                    payload["windows_process_tree"] = {
                        "before": process_before,
                        "after": process_after,
                        "benchmark_cpu_seconds": round(
                            process_after["cpu_seconds"] - process_before["cpu_seconds"], 6
                        ),
                        "working_set_delta_mib": round(
                            process_after["working_set_mib"]
                            - process_before["working_set_mib"],
                            3,
                        ),
                    }
                else:
                    payload["windows_process_tree"] = None
                with result_lock:
                    result.update(payload)
            except Exception as exc:
                with result_lock:
                    result.update(
                        {
                            "ok": False,
                            "synthetic_only": True,
                            "remote_connections": 0,
                            "error": str(exc),
                        }
                    )
            finally:
                try:
                    window.destroy()
                except Exception:
                    pass

        try:
            webview.start(
                run_benchmark,
                private_mode=True,
                storage_path=temporary,
            )
        except Exception as exc:
            with result_lock:
                result.update(
                    {
                        "ok": False,
                        "synthetic_only": True,
                        "remote_connections": 0,
                        "error": f"pywebview failed: {exc}",
                    }
                )
        finally:
            watchdog.cancel()

    with result_lock:
        output = dict(result)
    if timed_out.is_set():
        output["timed_out"] = True
        output["ok"] = False
    else:
        output["timed_out"] = False
    output.setdefault("synthetic_only", True)
    output.setdefault("remote_connections", 0)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if output.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
