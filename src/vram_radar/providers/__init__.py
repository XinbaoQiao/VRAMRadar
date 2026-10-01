"""Provider registry and background monitor for the multi-model usage strip.

Adding a provider = one module exposing ``probe(env) -> dict`` + one entry in
``PROVIDERS``.  Probes run only on the monitor thread, never on the UI thread.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
import threading
import time
from typing import Callable

from . import deepseek, generic, grok, kimi, openai_cli as codex
from .base import Environment, ProviderSpec, pair

PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(codex.ID, codex.NAME, codex.SHORT, codex.probe, 0),
    ProviderSpec(deepseek.ID, deepseek.NAME, deepseek.SHORT, deepseek.probe, 10),
    ProviderSpec(grok.ID, grok.NAME, grok.SHORT, grok.probe, 20),
    ProviderSpec(kimi.ID, kimi.NAME, kimi.SHORT, kimi.probe, 30),
    ProviderSpec("claude", "Claude", "Cl", generic.claude, 40),
    ProviderSpec("glm", "GLM 智谱清言", "GLM", generic.glm, 50),
    ProviderSpec("qwen", "Qwen 通义", "Qw", generic.qwen, 60),
    ProviderSpec("yuanbao", "腾讯元宝", "YB", generic.yuanbao, 70),
)
PROVIDER_IDS = tuple(spec.id for spec in PROVIDERS)
DEFAULT_PROVIDERS = ("codex",)
PROBE_SECONDS = 60
FORCE_FLOOR_SECONDS = 5
LOG = logging.getLogger("vram_radar")


def normalize_selection(values) -> tuple[str, ...]:
    """Known ids in registry order; unknown/duplicate ids are dropped."""
    if not isinstance(values, (list, tuple)):
        return DEFAULT_PROVIDERS
    wanted = {value for value in values if isinstance(value, str)}
    selected = tuple(provider_id for provider_id in PROVIDER_IDS if provider_id in wanted)
    return selected or DEFAULT_PROVIDERS


def probe_all(env: Environment | None = None, *, codex_executable: str = "",
              specs: tuple[ProviderSpec, ...] = PROVIDERS) -> dict[str, dict]:
    env = env or Environment()
    results: dict[str, dict] = {}
    # Two passes: folders holding one detected app are also searched for the
    # others, so custom install roots (e.g. D:\Apps\<App>) are found too.
    for attempt in range(2):
        for spec in specs:
            if attempt and results.get(spec.id, {}).get("installed"):
                continue
            started = time.monotonic()
            try:
                state = (spec.probe(env, executable=codex_executable) if spec.id == "codex"
                         else spec.probe(env))
            except Exception as exc:  # a broken probe must not hide the others
                LOG.warning("usage provider %s probe failed (%s)", spec.id, type(exc).__name__)
                state = {"id": spec.id, "name": spec.name, "short": spec.short, "installed": False,
                         "state": "error", "code": "probe_failed", "windows": [], "facts": [],
                         "headline": pair("—", "—"), "subline": pair("检测失败", "Probe failed"),
                         "checked_at": time.time()}
            state["probe_ms"] = round((time.monotonic() - started) * 1000)
            results[spec.id] = state
        if attempt == 0:
            roots = []
            for state in results.values():
                path = state.get("install_path") or ""
                if state.get("installed") and path and state.get("install_source") != "msix":
                    folder = Path(path).parent.parent  # <root>\<App>\<exe>
                    if folder not in roots and len(folder.parts) > 1:
                        roots.append(folder)
            new_roots = [root for root in roots if root not in env.extra_roots]
            if not new_roots:
                break
            env.extra_roots.extend(new_roots)
    return results


class ProviderMonitor:
    """Single background worker; the UI thread only copies ``snapshot()``."""

    def __init__(self, *, probe: Callable[..., dict] = probe_all, interval: float = PROBE_SECONDS):
        self.probe, self.interval = probe, interval
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.enabled = False
        self.closed = False
        self.codex_executable = ""
        self.next_read = 0.0
        self.last_force = 0.0
        self.worker: threading.Thread | None = None
        self.states: dict[str, dict] = {}
        self.loading = False

    def configure(self, enabled: bool, codex_executable: str = "") -> None:
        with self.lock:
            if self.closed:
                return
            changed = (self.enabled, self.codex_executable) != (enabled, codex_executable)
            self.enabled, self.codex_executable = enabled, codex_executable
            if not changed:
                return
            self.next_read = 0
            if enabled and self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True, name="usage-provider-monitor")
                self.worker.start()
            self.wake.set()

    def refresh(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now - self.last_force >= FORCE_FLOOR_SECONDS:
                self.last_force = now
                self.next_read = 0
                self.wake.set()

    def snapshot(self) -> dict[str, dict]:
        with self.lock:
            return copy.deepcopy(self.states)

    def _run(self) -> None:
        while True:
            with self.lock:
                if self.closed:
                    return
                wait = max(0.0, self.next_read - time.monotonic()) if self.enabled else 60
                ready = self.enabled and wait == 0
                executable = self.codex_executable
                if ready:
                    self.loading = True
                self.wake.clear()
            if not ready:
                self.wake.wait(min(wait, 60))
                continue
            try:
                states = self.probe(codex_executable=executable)
            except Exception as exc:
                LOG.warning("usage provider scan failed (%s)", type(exc).__name__)
                states = None
            with self.lock:
                if isinstance(states, dict):
                    self.states = states
                self.loading = False
                self.next_read = time.monotonic() + self.interval

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.wake.set()
        if self.worker:
            self.worker.join(timeout=5)
