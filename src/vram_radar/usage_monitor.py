"""Opt-in Codex quota reads through its local app-server (no credential access).

Protocol flow informed by Amygdala42/CodexUsage; see docs/third-party-notices.md.
Only sanitized quota fields leave this module. No prompts or turns are sent.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import platform
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from .connectors import _terminate_process_tree

REFRESH_SECONDS = 300
SETUP_RETRY_SECONDS = 15
MAX_LINE_BYTES = 1_048_576


class UsageError(Exception):
    """A fixed public error code, never raw Codex output."""


def find_codex(override: str = "") -> Path:
    """Find a native executable, including GUI installations with a small PATH."""
    def usable(path: Path) -> bool:
        try:
            return path.is_file() and (path.suffix.lower() == ".exe" if sys.platform == "win32"
                                       else os.access(path, os.X_OK))
        except OSError:
            return False

    def newest(root: Path, pattern: str) -> list[Path]:
        # Installers may remove an old version during discovery. One inaccessible
        # location must not prevent checking other supported installations.
        found = []
        try:
            for path in root.glob(pattern):
                try:
                    found.append((path.stat().st_mtime, str(path), path))
                except OSError:
                    continue
        except OSError:
            pass
        return [item[2] for item in sorted(found, reverse=True)]

    if override:
        path = Path(override).expanduser()
        if not path.is_absolute() or not usable(path):
            raise UsageError("invalid_executable")
        return path.resolve()
    candidates: list[Path] = []
    found = shutil.which("codex.exe" if sys.platform == "win32" else "codex")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.extend(newest(Path(local) / "OpenAI" / "Codex" / "bin", "*/codex.exe"))
        if found:
            candidates.append(Path(found))
        # npm's .cmd shim is not invoked through a shell. Use its native payload.
        roaming = os.environ.get("APPDATA")
        npm_roots = []
        if roaming:
            npm_roots.append(Path(roaming) / "npm")
        # Custom npm prefixes are discoverable through their PATH shim without
        # running .cmd through a shell or asking users to find the native binary.
        shim = shutil.which("codex.cmd")
        if shim:
            npm_roots.append(Path(shim).parent)
        for root in dict.fromkeys(npm_roots):
            candidates.extend(newest(root / "node_modules/@openai", "codex*/**/codex.exe"))
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Codex.app/Contents/Resources/codex"),
            Path.home() / "Applications/Codex.app/Contents/Resources/codex",
            Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
            Path.home() / ".local/bin/codex",
        ])
        if found:
            candidates.append(Path(found))
    elif found:
        candidates.append(Path(found))
    # VS Code's official Codex extension ships a native CLI too. Do not require
    # a separate npm install when that is the user's existing Codex installation.
    if sys.platform in {"win32", "darwin"}:
        arm = platform.machine().lower() in {"arm64", "aarch64"}
        target = ("windows" if sys.platform == "win32" else "macos") + ("-aarch64" if arm else "-x86_64")
        binary = "codex.exe" if sys.platform == "win32" else "codex"
        for folder in (".vscode", ".vscode-insiders"):
            candidates.extend(newest(Path.home() / folder / "extensions", f"openai.chatgpt-*/bin/{target}/{binary}"))
    for candidate in candidates:
        if usable(candidate):
            return candidate.resolve()
    raise UsageError("not_installed")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _text(value: Any, fallback: str = "") -> str:
    return "".join(c for c in value[:100] if ord(c) >= 32) if isinstance(value, str) else fallback


def validate_account(account_result: dict) -> dict:
    account = account_result.get("account")
    if not isinstance(account, dict):
        raise UsageError("login_required")
    if account.get("type") not in {"chatgpt", "chatgptAuthTokens"}:
        raise UsageError("unsupported_account")
    return account


def parse_usage(account_result: dict, limits: dict) -> dict:
    account = validate_account(account_result)
    buckets = limits.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = limits.get("rateLimits")
        buckets = {"codex": legacy} if isinstance(legacy, dict) else {}
    windows = []
    for key, bucket in sorted(buckets.items(), key=lambda item: (item[0] != "codex", item[0]))[:32]:
        if not isinstance(bucket, dict):
            continue
        for slot in ("primary", "secondary"):
            value = bucket.get(slot)
            if not isinstance(value, dict):
                continue
            used = _number(value.get("usedPercent"))
            minutes = _number(value.get("windowDurationMins"))
            reset = _number(value.get("resetsAt"))
            windows.append({
                "id": _text(key) + ":" + slot,
                "name": _text(bucket.get("limitName"), _text(key)),
                "remaining_percent": None if used is None else max(0, min(100, 100 - used)),
                "window_minutes": minutes if minutes is not None and 0 < minutes <= 5256000 else None,
                "resets_at": reset if reset is not None and 0 < reset <= 253402300799 else None,
            })
    return {"plan": _text(account.get("planType")), "windows": windows,
            "fetched_at": time.time()}


def _stop_process(process: subprocess.Popen) -> None:
    # Never touch an existing Codex instance. POSIX wrappers share our new session.
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM for an already-exited process group.
            # Reap/check our child; do not mask real denial for a live child.
            if process.poll() is None:
                raise
    elif process.poll() is None:
        _terminate_process_tree(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    for stream in (process.stdin, process.stdout):
        if stream:
            stream.close()


def read_usage(runtime: Path, executable: str, cancel: threading.Event,
               *, timeout: float = 30, command: list[str] | None = None) -> dict:
    """One bounded, read-only RPC exchange. ``command`` is a fixture injection."""
    if cancel.is_set():
        raise UsageError("cancelled")
    argv = command or [str(find_codex(executable)), "app-server", "-c", "analytics.enabled=false",
                       "-c", "feedback.enabled=false", "-c",
                       "log_dir=" + json.dumps(str(runtime / "logs"))]
    runtime.mkdir(parents=True, exist_ok=True)
    # Codex owns its own login store. Do not copy API keys/tokens into the child.
    permitted = {"PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT",
                 "WINDIR", "COMSPEC", "PATHEXT", "CODEX_HOME", "LANG", "LC_ALL",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {key: value for key, value in os.environ.items() if key.upper() in permitted}
    if sys.platform == "darwin":
        # Finder does not inherit the interactive shell's Homebrew PATH; npm
        # installations need env(1) to find node alongside their CLI shim.
        env["PATH"] = env.get("PATH", "/usr/bin:/bin") + ":/opt/homebrew/bin:/usr/local/bin"
    env.update({"RUST_LOG": "off", "TMP": str(runtime), "TEMP": str(runtime), "TMPDIR": str(runtime)})
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(argv, cwd=runtime, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options)
    except OSError:
        raise UsageError("start_failed") from None
    inbox: queue.Queue = queue.Queue(maxsize=64)
    done = threading.Event()

    def receive() -> None:
        try:
            for _ in range(512):
                line = process.stdout.readline(MAX_LINE_BYTES + 1)
                if len(line) > MAX_LINE_BYTES:
                    raise UsageError("invalid_response")
                while not done.is_set():
                    try:
                        inbox.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if not line or done.is_set():
                    return
            raise UsageError("invalid_response")
        except (OSError, ValueError, UsageError):
            while not done.is_set():
                try:
                    inbox.put(None, timeout=0.1)
                    break
                except queue.Full:
                    pass

    reader = threading.Thread(target=receive, daemon=True, name="codex-usage-reader")
    reader.start()
    deadline = time.monotonic() + timeout

    def send(message: dict) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def request(identifier: int, method: str, params: dict | None = None) -> dict:
        send({"id": identifier, "method": method, "params": params})
        while True:
            if cancel.is_set():
                raise UsageError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UsageError("timeout")
            try:
                line = inbox.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if not line:
                raise UsageError("disconnected")
            try:
                message = json.loads(line.decode("utf-8-sig"))
            except (ValueError, UnicodeError, RecursionError):
                raise UsageError("invalid_response") from None
            if not isinstance(message, dict) or "method" in message or message.get("id") != identifier:
                continue
            if message.get("error") is not None:
                raise UsageError("service_error")
            if not isinstance(message.get("result"), dict):
                raise UsageError("invalid_response")
            return message["result"]

    try:
        request(1, "initialize", {"clientInfo": {"name": "vram_radar", "title": "VRAM Radar", "version": "1"}})
        send({"method": "initialized", "params": {}})
        account = request(2, "account/read", {"refreshToken": False})
        validate_account(account)
        return parse_usage(account, request(3, "account/rateLimits/read"))
    except (OSError, ValueError):
        raise UsageError("disconnected") from None
    finally:
        done.set()
        _stop_process(process)
        reader.join(timeout=2)


class CodexUsageMonitor:
    """One independent worker; opt-out/change invalidates pending results."""

    def __init__(self, runtime: Path | None, *, fetch=read_usage, interval: float = REFRESH_SECONDS):
        self.runtime, self.fetch, self.interval = runtime, fetch, interval
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.cancel = threading.Event()
        self.enabled = False
        self.executable = ""
        self.closed = False
        self.generation = 0
        self.next_read = 0.0
        self.worker: threading.Thread | None = None
        self.state: dict = {"state": "disabled", "windows": []}

    def configure(self, enabled: bool, executable: str) -> None:
        with self.lock:
            if self.closed or (self.enabled, self.executable) == (enabled, executable):
                return
            self.enabled, self.executable = enabled, executable
            self.generation += 1
            self.cancel.set()
            self.state = {"state": "loading" if enabled else "disabled", "windows": []}
            self.next_read = 0
            if enabled and self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True, name="codex-usage-monitor")
                self.worker.start()
            self.wake.set()

    def snapshot(self, *, force: bool = False) -> dict:
        with self.lock:
            # Coalesce clicks; enforce a small retry floor for repeated failures.
            if force and self.enabled and self.state["state"] != "loading" and time.monotonic() >= getattr(self, "retry_after", 0):
                self.next_read = 0
                self.state = {**self.state, "state": "loading", "code": ""}
                self.wake.set()
            result = copy.deepcopy(self.state)
            result["enabled"] = self.enabled
            result["stale"] = bool(result.get("fetched_at") and time.time() - result["fetched_at"] > self.interval + 30)
            return result

    def _run(self) -> None:
        while True:
            with self.lock:
                if self.closed:
                    return
                wait = max(0, self.next_read - time.monotonic()) if self.enabled else 60
                ready = self.enabled and wait == 0
                if ready:
                    generation, executable = self.generation, self.executable
                    self.cancel = cancel = threading.Event()
                    self.state = {**self.state, "state": "loading", "code": ""}
                self.wake.clear()
            if not ready:
                self.wake.wait(min(wait, 60))
                continue
            try:
                result = {**self.fetch(self.runtime / "codex-usage", executable, cancel), "state": "ready", "code": ""}
            except UsageError as exc:
                result = {"state": "error", "code": str(exc), "windows": []}
            except Exception:
                result = {"state": "error", "code": "unavailable", "windows": []}
            with self.lock:
                if generation == self.generation and not self.closed:
                    self.state = result
                    self.retry_after = time.monotonic() + 10
                    # Installation/sign-in can finish while Radar stays open.
                    # Detect that promptly without requiring a restart or refresh.
                    recovering = result.get("code") in {"not_installed", "invalid_executable", "start_failed",
                                                        "login_required", "unsupported_account"}
                    delay = min(self.interval, SETUP_RETRY_SECONDS) if recovering else self.interval
                    self.next_read = time.monotonic() + delay

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.cancel.set()
            self.wake.set()
        if self.worker:
            self.worker.join(timeout=5)
