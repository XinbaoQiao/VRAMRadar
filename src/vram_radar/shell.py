from __future__ import annotations

import argparse
import base64
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Callable

from .connectors import (
    PASSWORD_FALLBACK_AUTH_CODES,
    ConnectorFailure,
    resolve_identity_path,
    resolve_ssh_config_path,
    run_remote,
    ssh_copy_details,
    ssh_login_argv,
)
from .models import (
    ConfigError,
    MAX_FAVORITE_SERVER_IDS,
    MAX_IGNORED_SSH_ALIASES,
    MAX_TASK_COMPLETION_WATCHES,
    Profile,
    normalize_task_completion_watch,
    normalize_saved_view,
    require_bounded_text,
    require_id,
    require_optional_ssh_token,
    require_optional_remote_directory,
)
from .build_info import current_build_commit, current_release_tag
from .server_catalog import (
    canonical_local_path,
    profile_from_server_config,
    profile_from_server_configs,
    import_server_config,
    resolve_server_config,
    resolve_server_configs,
)
from .secrets import SecretStore
from .service import DashboardService, favorite_resource_matches
from .ssh_keys import (
    INSTALL_AUTHORIZED_KEY_SCRIPT,
    VERIFY_SSH_KEY_SCRIPT,
    PreparedSshKey,
    SshKeySetupError,
    prepare_existing_key,
    prepare_generated_key,
    remove_generated_key,
)
from .storage import (
    ProfileStore,
    SnapshotCache,
    StoragePaths,
    WindowStateStore,
    atomic_write_text,
    storage_paths,
)
from .tray import (
    WindowsTrayController,
    configure_windows_native_chrome,
    native_window_is_normal,
    restore_window,
    show_macos_notification,
)
from .updates import check_latest_release
from .updater import download_verified_asset, schedule_windows_update, windows_update_capability
from .window_state import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, WindowGeometry, WindowStateController


def resource_path(relative: str) -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    base = Path(frozen_root) / "vram_radar" if frozen_root else Path(__file__).resolve().parent
    return base / relative


def _powershell_join(argv: list[str]) -> str:
    """Render one argv for PowerShell without exposing shell metacharacters."""

    if not argv:
        return ""
    # The executable is an application-owned constant (``ssh``). Every user-
    # influenced argument is single-quoted; PowerShell escapes an embedded
    # quote by doubling it and performs no variable or command expansion.
    quoted = ["'" + str(value).replace("'", "''") + "'" for value in argv[1:]]
    return " ".join([argv[0], *quoted])


def _openssh_config_value(value: object) -> str:
    """Quote one bounded Profile value for an OpenSSH configuration line."""

    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./~\\-]+", text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ssh_config_block(server: Any, details: Any) -> str:
    """Build a paste-ready Host block only from statically verified fields."""

    if not details.endpoint_complete:
        return ""
    resolution = details.resolution
    alias = server.ssh_alias or server.id
    lines = [
        f"Host {_openssh_config_value(alias)}",
        f"  HostName {_openssh_config_value(resolution.hostname)}",
        f"  User {_openssh_config_value(resolution.user)}",
        f"  Port {int(resolution.port)}",
    ]
    if server.identity_file:
        lines.append(f"  IdentityFile {_openssh_config_value(server.identity_file)}")
    lines.extend(
        (
            "  IdentitiesOnly yes",
            "  BatchMode yes",
            "  ClearAllForwardings yes",
        )
    )
    return "\n".join(lines)


def configure_logging(paths: StoragePaths) -> logging.Logger:
    paths.logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vram_radar")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(paths.logs / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _existing_file(path: str | Path) -> bool:
    try:
        candidate = Path(path).expanduser()
        if sys.platform == "win32":
            candidate = canonical_local_path(candidate)
        return candidate.is_file()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _authentication_endpoint_identity(server: Any) -> tuple[Any, ...]:
    """Return the non-secret endpoint identity to which a saved password applies."""

    alias = str(server.ssh_alias or "")
    effective_port = server.port if not alias or server.port_override else None
    config_path = resolve_ssh_config_path(server) if server.ssh_config_file else ""
    return (
        alias.casefold(),
        str(server.host or "").casefold(),
        str(server.username or ""),
        effective_port,
        bool(server.port_override),
        os.path.normcase(config_path),
    )


def _ssh_support_diagnostics(servers: tuple[Any, ...]) -> dict[str, Any]:
    """Return local SSH readiness facts without paths, aliases, fingerprints, or key data."""

    home = Path.home()
    default_key_candidates = tuple(
        home / ".ssh" / name
        for name in ("id_ed25519", "id_ecdsa", "id_rsa", "id_dsa")
    )
    agent_state = "tool_missing"
    loaded_key_count: int | None = None
    ssh_add = shutil.which("ssh-add")
    if ssh_add:
        try:
            completed = subprocess.run(
                [ssh_add, "-l"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                close_fds=os.name != "nt",
                check=False,
            )
            if completed.returncode == 0:
                agent_state = "ready"
                loaded_key_count = len([line for line in completed.stdout.splitlines() if line.strip()])
            elif completed.returncode == 1:
                agent_state = "empty"
                loaded_key_count = 0
            else:
                agent_state = "unavailable"
        except (OSError, subprocess.SubprocessError):
            agent_state = "unavailable"

    explicit_identity_servers = [server for server in servers if server.identity_file]
    explicit_config_servers = [server for server in servers if server.ssh_config_file]
    return {
        "client_available": shutil.which("ssh") is not None,
        "default_config_present": _existing_file(home / ".ssh" / "config"),
        "default_private_key_count": sum(_existing_file(path) for path in default_key_candidates),
        "ssh_auth_sock_present": bool(os.environ.get("SSH_AUTH_SOCK")),
        "agent_state": agent_state,
        "agent_loaded_key_count": loaded_key_count,
        "explicit_identity_server_count": len(explicit_identity_servers),
        "explicit_identity_file_present_count": sum(
            _existing_file(server.identity_file) for server in explicit_identity_servers
        ),
        "explicit_config_server_count": len(explicit_config_servers),
        "explicit_config_file_present_count": sum(
            _existing_file(server.ssh_config_file) for server in explicit_config_servers
        ),
    }


def _recent_connection_error_diagnostics(
    log_path: str | Path,
    selected_server_ids: set[str],
) -> dict[str, Any]:
    """Aggregate a bounded log tail by internal error code without returning identities or messages."""

    maximum_bytes = 262_144
    maximum_events = 500
    counts: dict[str, int] = {}
    scanned_bytes = 0
    try:
        with Path(log_path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            scanned_bytes = min(size, maximum_bytes)
            handle.seek(max(0, size - scanned_bytes))
            content = handle.read(scanned_bytes).decode("utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return {"log_present": False, "event_count": 0, "by_code": {}, "scanned_bytes": 0}

    pattern = re.compile(r"\bserver=([^\s]+)\s+code=([a-z0-9_]{1,64})\b")
    matched = 0
    for line in reversed(content.splitlines()):
        found = pattern.search(line)
        if not found or found.group(1) not in selected_server_ids:
            continue
        code = found.group(2)
        counts[code] = counts.get(code, 0) + 1
        matched += 1
        if matched >= maximum_events:
            break
    return {
        "log_present": True,
        "event_count": matched,
        "by_code": dict(sorted(counts.items())),
        "scanned_bytes": scanned_bytes,
    }


def _copy_text_to_system_clipboard(text: str) -> bool:
    """Copy text without placing it in argv, the environment, or a temporary file."""

    if not isinstance(text, str) or not text:
        return False
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["/usr/bin/pbcopy"],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                close_fds=True,
                check=False,
            )
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        data = ctypes.create_unicode_buffer(text)
        handle = kernel32.GlobalAlloc(0x0002, ctypes.sizeof(data))  # GMEM_MOVEABLE
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, ctypes.addressof(data), ctypes.sizeof(data))
        finally:
            kernel32.GlobalUnlock(handle)

        opened = False
        for _attempt in range(5):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.04)
        if not opened:
            kernel32.GlobalFree(handle)
            return False
        transferred = False
        try:
            if not user32.EmptyClipboard():
                return False
            transferred = bool(user32.SetClipboardData(13, handle))  # CF_UNICODETEXT
            return transferred
        finally:
            user32.CloseClipboard()
            if not transferred:
                kernel32.GlobalFree(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLockUnavailable(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "InstanceLock":
        if self.handle is not None:
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
        except OSError as exc:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            raise InstanceLockUnavailable("无法创建应用运行锁，请检查本地数据目录权限") from exc
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            raise InstanceAlreadyRunning("这个 Profile 已经有一个运行实例") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is not None:
            handle = self.handle
            self.handle = None
            handle.close()


class ActivationServer:
    def __init__(
        self,
        path: Path,
        on_activate: Callable[[], None],
        on_exit: Callable[[], None] | None = None,
        *,
        on_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.path = path
        self.on_activate = on_activate
        self.on_exit = on_exit
        self.on_probe = on_probe
        self.nonce = secrets.token_urlsafe(24)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(4)
        self.socket.settimeout(0.25)
        self.port = int(self.socket.getsockname()[1])
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="vram-radar-activation", daemon=True)

    def __enter__(self) -> "ActivationServer":
        document = json.dumps(
            {"schema_version": 1, "port": self.port, "nonce": self.nonce, "pid": os.getpid()},
            ensure_ascii=True,
        )
        atomic_write_text(self.path, document + "\n")
        self.thread.start()
        return self

    def _serve(self) -> None:
        while not self.stopped.is_set():
            try:
                connection, _address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                try:
                    message = connection.recv(512).decode("utf-8", errors="strict").strip()
                    supplied_nonce, separator, requested_action = message.partition(" ")
                    action = requested_action.upper() if separator else "SHOW"
                    if hmac.compare_digest(supplied_nonce, self.nonce) and action == "SHOW":
                        self.on_activate()
                        connection.sendall(b"OK\n")
                    elif hmac.compare_digest(supplied_nonce, self.nonce) and action == "EXIT" and self.on_exit:
                        self.on_exit()
                        connection.sendall(b"OK\n")
                    elif (
                        hmac.compare_digest(supplied_nonce, self.nonce)
                        and action == "PROBE"
                        and self.on_probe is not None
                    ):
                        try:
                            ready = bool(self.on_probe())
                        except Exception:
                            logging.getLogger("vram_radar").exception(
                                "authenticated frontend readiness probe failed"
                            )
                            ready = False
                        connection.sendall(b"READY\n" if ready else b"NOT_READY\n")
                    else:
                        connection.sendall(b"DENIED\n")
                except (OSError, UnicodeError):
                    continue

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stopped.set()
        self.socket.close()
        self.thread.join(timeout=1)
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _request_existing_instance_process(
    path: Path,
    *,
    action: str = "show",
    timeout_seconds: float = 3.0,
) -> int | None:
    normalized_action = action.strip().lower()
    if normalized_action not in {"show", "exit"}:
        raise ValueError("existing-instance action must be show or exit")
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("schema_version") != 1:
                raise ValueError("unsupported activation schema")
            port = int(document["port"])
            nonce = str(document["nonce"])
            pid = int(document["pid"])
            if not 1 <= port <= 65535 or len(nonce) < 16 or pid <= 0:
                raise ValueError("invalid activation endpoint")
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
                message = nonce if normalized_action == "show" else f"{nonce} EXIT"
                connection.sendall(message.encode("utf-8") + b"\n")
                connection.settimeout(1)
                if connection.recv(32).strip() == b"OK":
                    return pid
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            time.sleep(0.1)
    return None


def request_existing_instance(
    path: Path,
    *,
    action: str = "show",
    timeout_seconds: float = 3.0,
) -> bool:
    return _request_existing_instance_process(
        path,
        action=action,
        timeout_seconds=timeout_seconds,
    ) is not None


def _wait_for_process_exit(pid: int, timeout_seconds: float = 15.0) -> bool:
    """Wait for the exact authenticated desktop process to release its image.

    Installer-driven replacement must not infer shutdown from the activation
    endpoint disappearing: native WebView and tray teardown can continue after
    that file is removed, leaving the executable locked and a same-version
    reinstall only partially applied.
    """

    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        wait_object_0 = 0
        wait_timeout = 258
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER means the PID no longer exists. Access
            # denial or any other failure cannot prove shutdown, so Setup must
            # stop rather than replace a possibly locked executable.
            return ctypes.get_last_error() == error_invalid_parameter
        try:
            result = kernel32.WaitForSingleObject(
                handle,
                max(1, int(max(0.1, timeout_seconds) * 1000)),
            )
            if result == wait_object_0:
                return True
            if result == wait_timeout:
                return False
            return False
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        # A test/launcher may own the target as a direct child. On POSIX an
        # exited child remains addressable by kill(pid, 0) until it is reaped,
        # so observe that terminal state first. For the normal unrelated
        # desktop process, waitpid raises ChildProcessError and polling remains
        # read-only.
        try:
            exited_pid, _status = os.waitpid(pid, getattr(os, "WNOHANG", 1))
            if exited_pid == pid:
                return True
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def webview_start_options(debug: bool, icon_path: Path) -> dict[str, Any]:
    options: dict[str, Any] = {"debug": debug}
    # Windows and macOS obtain their application icon from the packaged
    # executable/.app bundle. pywebview's runtime icon option is only supported
    # by its GTK/Qt backends.
    if sys.platform not in {"win32", "darwin"}:
        options["icon"] = str(icon_path)
    return options


def window_smoke_worker(
    window: Any,
    result: dict[str, Any],
    timeout_seconds: float = 20.0,
    request_shutdown: Callable[[], None] | None = None,
) -> None:
    try:
        result["shown"] = bool(window.events.shown.wait(timeout_seconds))
        if not result["shown"]:
            result["error"] = "desktop window did not become visible before the smoke timeout"
    except Exception as exc:
        result["shown"] = False
        result["error"] = f"desktop window smoke failed: {exc}"
    finally:
        try:
            if request_shutdown is None:
                window.destroy()
            else:
                request_shutdown()
        except Exception as exc:
            result["shown"] = False
            result["error"] = f"desktop window could not close after the smoke: {exc}"


def window_frontend_is_ready(window: Any) -> bool:
    """Probe fixed DOM and bridge invariants without reading user content."""

    if window is None or not window.events.loaded.is_set():
        return False
    result = window.evaluate_js(
        """(() => {
          const shell = document.querySelector('.app-shell');
          const bounds = shell?.getBoundingClientRect();
          return Boolean(
            document.readyState === 'complete' &&
            document.body && document.body.childElementCount > 0 &&
            shell && bounds && bounds.width >= 320 && bounds.height > 0 &&
            window.pywebview && window.pywebview.api
          );
        })()"""
    )
    return result is True


def activation_worker(
    window: Any,
    requested: threading.Event,
    exit_requested: threading.Event,
    stopped: threading.Event,
    exit_application: Callable[[], None],
    restore_application: Callable[[], object] | None = None,
) -> None:
    while not stopped.is_set():
        if exit_requested.is_set():
            if window.events.shown.wait(15):
                try:
                    exit_application()
                except Exception:
                    logging.getLogger("vram_radar").exception("failed to exit the existing application")
            return
        if not requested.wait(0.25):
            continue
        # Shutdown wakes the same Event so the worker can join promptly. Do not
        # interpret that wake-up as a user activation after WebView has already
        # disposed its native form.
        if stopped.is_set():
            return
        requested.clear()
        if not window.events.shown.wait(15):
            continue
        # WebView teardown may start after the wake-up check but before the
        # native form is restored. Recheck at the final mutation boundary.
        if stopped.is_set():
            return
        try:
            if restore_application is None:
                restore_window(window)
            else:
                restore_application()
        except Exception:
            logging.getLogger("vram_radar").exception("failed to restore the existing window")


class WindowShutdownCoordinator:
    """Stop the activation worker before native WebView disposal begins.

    A close callback runs on the native UI thread. Joining the activation
    worker there can deadlock because ``restore_window`` may synchronously
    invoke that same UI thread. The first close is therefore cancelled while a
    small background coordinator wakes and joins the worker. Only after the
    join does it issue the final, permitted ``window.destroy()`` call.
    """

    def __init__(
        self,
        window: Any,
        activation_requested: threading.Event,
        activation_stopped: threading.Event,
        *,
        preferred_geometry: Callable[[], WindowGeometry] | None = None,
        before_destroy: Callable[[], None] | None = None,
    ) -> None:
        self.window = window
        self.activation_requested = activation_requested
        self.activation_stopped = activation_stopped
        self.preferred_geometry = preferred_geometry
        self.before_destroy = before_destroy
        self.shutdown_ready = threading.Event()
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._window_operation_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._tray_controller: WindowsTrayController | None = None
        self._thread: threading.Thread | None = None

    def bind_worker(self, worker: threading.Thread) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("window shutdown has already started")
            self._worker = worker

    def bind_tray_controller(self, tray_controller: WindowsTrayController) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("window shutdown has already started")
            self._tray_controller = tray_controller

    def request(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self.activation_stopped.set()
            self.activation_requested.set()
            self._thread = threading.Thread(
                target=self._finish,
                name="vram-radar-window-shutdown",
                daemon=True,
            )
            self._thread.start()

    def run_window_operation(self, operation: Callable[[], None]) -> bool:
        with self._window_operation_lock:
            if self.activation_stopped.is_set():
                return False
            operation()
            return True

    def restore(self, after_restore: Callable[[], None] | None = None) -> bool:
        def operation() -> None:
            geometry = self.preferred_geometry() if self.preferred_geometry is not None else None
            if geometry is None:
                restore_window(self.window)
            else:
                restore_window(self.window, geometry)
            if after_restore is not None:
                after_restore()

        return self.run_window_operation(operation)

    def hide(self) -> bool:
        return self.run_window_operation(self.window.hide)

    def _finish(self) -> None:
        try:
            worker = self._worker
            if worker is not None and worker is not threading.current_thread():
                worker.join()
            with self._window_operation_lock:
                tray_controller = self._tray_controller
                if tray_controller is not None:
                    tray_controller.prepare_exit()
                if self.before_destroy is not None:
                    try:
                        self.before_destroy()
                    except Exception:
                        logging.getLogger("vram_radar").exception(
                            "failed to flush desktop window state before shutdown"
                        )
                self.shutdown_ready.set()
            self.window.destroy()
        except Exception:
            logging.getLogger("vram_radar").exception("failed to shut down the desktop window")
        finally:
            self.finished.set()

    def on_closing(self) -> bool:
        if self.shutdown_ready.is_set():
            return True
        self.request()
        return False

    def wait(self, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return self.finished.is_set()


class AppApi:
    def __init__(
        self,
        profile: Profile,
        store: ProfileStore,
        paths: StoragePaths,
        service: DashboardService,
        secret_store: object | None = None,
        *,
        automatic_import_enabled: bool = True,
        restart_arguments: list[str] | None = None,
    ) -> None:
        self.profile = profile
        self.store = store
        self.paths = paths
        self.service = service
        self.secret_store = secret_store if secret_store is not None else (getattr(service, "secret_store", None) or SecretStore())
        self.latest_release_url: str | None = None
        self._latest_release: dict[str, Any] | None = None
        self._notification_callback: Callable[[str, str], bool] | None = None
        self._tray_controller: WindowsTrayController | None = None
        self._favorite_alert_state_lock = threading.Lock()
        self._favorite_alert_active_ids: set[str] = set()
        self._favorite_alert_monitor_running = False
        self._task_alert_state_lock = threading.Lock()
        self._task_alert_active: dict[str, dict[str, dict[str, str]]] = {}
        self._task_alert_events: list[dict[str, Any]] = []
        self._task_alert_sequence = 0
        self._task_alert_read_sequence = 0
        self._key_setup_lock = threading.Lock()
        self._host_key_trust_lock = threading.Lock()
        self._profile_mutation_lock = threading.RLock()
        self._profile_revision = 0
        self._automatic_import_enabled = bool(automatic_import_enabled)
        self._restart_arguments = list(restart_arguments or ["--profile", profile.id])

    def get_profile(self) -> dict[str, Any]:
        with self._profile_mutation_lock:
            return self._desktop_profile(self.profile)

    @staticmethod
    def _auth_ref(profile_id: str, server_id: str) -> str:
        return f"server:{profile_id}:{server_id}:login-password"

    @staticmethod
    def _validate_password(password: object) -> str:
        if not isinstance(password, str) or not password:
            raise ConfigError("服务器密码不能为空")
        if len(password.encode("utf-8")) > 16_384:
            raise ConfigError("服务器密码过长")
        if any(character in password for character in ("\x00", "\r", "\n")):
            raise ConfigError("服务器密码不能包含换行或 NUL 字符")
        return password

    def _desktop_profile(self, profile: Profile) -> dict[str, Any]:
        result = profile.to_dict()
        result["profile_revision"] = self._profile_revision
        for server in result.get("servers", []):
            server["has_password"] = bool(server.pop("auth_ref", ""))
        return result

    def _persist_local_preferences(
        self,
        updated_profile: Profile,
        *,
        replace_service: bool = False,
        expected_profile: Profile | None = None,
    ) -> dict[str, Any]:
        if not self._profile_mutation_lock.acquire(blocking=False):
            return {"ok": False, "error": "另一项设置正在保存，请稍后重试", "code": "profile_mutation_busy"}
        committed_profile: dict[str, Any] | None = None
        try:
            if expected_profile is not None and self.profile is not expected_profile:
                return {
                    "ok": False,
                    "error": "设置已被其他操作更新，请重新打开后再保存",
                    "code": "profile_changed",
                }
            old_profile = self.profile
            self.store.save(updated_profile)
            try:
                if replace_service:
                    self.service.replace_profile(
                        updated_profile,
                        SnapshotCache(self.paths, updated_profile.id),
                    )
            except Exception as exc:
                try:
                    self.store.save(old_profile)
                    if replace_service:
                        self.service.replace_profile(
                            old_profile,
                            SnapshotCache(self.paths, old_profile.id),
                        )
                except Exception as rollback_exc:
                    raise RuntimeError("profile_rollback_failed") from rollback_exc
                raise RuntimeError("profile_commit_failed") from exc
            self.profile = updated_profile
            self._profile_revision += 1
            self._reset_favorite_alerts_if_changed(old_profile, updated_profile)
            self._reset_task_alerts_if_servers_changed(old_profile, updated_profile)
            committed_profile = self._desktop_profile(updated_profile)
        except (OSError, ValueError, RuntimeError) as exc:
            if str(exc) == "profile_rollback_failed":
                return {
                    "ok": False,
                    "error": "设置提交失败且原配置恢复未完成；请停止操作并重启应用检查配置",
                    "code": "profile_rollback_failed",
                    "recovery_required": True,
                }
            return {"ok": False, "error": str(exc), "code": "profile_save_failed"}
        except Exception:
            logging.getLogger("vram_radar").exception("could not commit local profile preference")
            return {"ok": False, "error": "本地配置保存失败", "code": "profile_save_failed"}
        finally:
            self._profile_mutation_lock.release()
        return {"ok": True, "profile": committed_profile}

    def get_ssh_command(self, server_id: str) -> dict[str, Any]:
        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id"}
        with self._profile_mutation_lock:
            server = next((item for item in self.profile.servers if item.id == normalized_id), None)
        if server is None:
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
        details = ssh_copy_details(server)
        argv = list(details.argv)
        command = _powershell_join(argv) if sys.platform == "win32" else shlex.join(argv)
        config_block = _ssh_config_block(server, details)
        endpoint = {
            "hostname": details.resolution.hostname,
            "user": details.resolution.user,
            "port": details.resolution.port,
        }
        return {
            "ok": True,
            "server_id": normalized_id,
            "command": command,
            "shell": "powershell" if sys.platform == "win32" else "posix",
            "copy_text": config_block or command,
            "copy_format": "openssh-config" if config_block else "ssh-command",
            "endpoint_complete": details.endpoint_complete,
            "endpoint": endpoint,
            "resolution_status": details.resolution.status,
            "resolution_reason": details.resolution.reason,
            "warning": details.warning,
        }

    def open_terminal(self, server_id: str) -> dict[str, Any]:
        """Open one interactive SSH session without interpolating a shell command."""

        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id"}
        with self._profile_mutation_lock:
            server = next((item for item in self.profile.servers if item.id == normalized_id), None)
        if server is None:
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
        argv = ssh_login_argv(server)
        try:
            if sys.platform == "win32":
                powershell = shutil.which("powershell.exe")
                if not powershell:
                    return {
                        "ok": False,
                        "error": "找不到 Windows PowerShell，请先复制 SSH 命令",
                        "code": "terminal_missing",
                    }
                encoded_argv = base64.b64encode(
                    json.dumps(argv, ensure_ascii=True).encode("utf-8")
                ).decode("ascii")
                # Windows PowerShell 5 treats tokens after ``-Command`` as
                # more script text rather than positional argv. Embed only the
                # ASCII base64 envelope; no user-controlled command text reaches
                # the PowerShell grammar or Windows Terminal's subcommand parser.
                fixed_script = (
                    f"$encoded='{encoded_argv}';"
                    "$commandArgs=[Text.Encoding]::UTF8.GetString("
                    "[Convert]::FromBase64String($encoded))|ConvertFrom-Json;"
                    "if(-not $commandArgs -or $commandArgs.Count -lt 1){throw 'Missing command'};"
                    "& ([string]$commandArgs[0]) @($commandArgs|Select-Object -Skip 1)"
                )
                launch_argv = [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NoExit",
                    "-Command",
                    fixed_script,
                ]
                subprocess.Popen(
                    launch_argv,
                    close_fds=True,
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    ),
                )
            elif sys.platform == "darwin":
                command = shlex.join(argv)
                apple_command = command.replace("\\", "\\\\").replace('"', '\\"')
                script = (
                    'tell application "Terminal"\n'
                    "activate\n"
                    f'do script "{apple_command}"\n'
                    "end tell"
                )
                completed = subprocess.run(
                    ["/usr/bin/osascript", "-e", script],
                    close_fds=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    check=False,
                )
                if completed.returncode != 0:
                    raise OSError(f"osascript exited with {completed.returncode}")
            else:
                return {
                    "ok": False,
                    "error": "当前桌面平台暂不支持自动打开终端，请复制 SSH 命令",
                    "code": "terminal_unsupported",
                }
            return {"ok": True, "message": "已打开服务器终端"}
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            logging.getLogger("vram_radar").warning("could not open server terminal: %s", exc)
            return {
                "ok": False,
                "error": "无法打开服务器终端，请复制 SSH 命令后手动运行",
                "code": "open_terminal_failed",
            }

    def open_setup_terminal(self, platform_name: str) -> dict[str, Any]:
        """Open the platform's local command window without executing a command."""

        requested = platform_name.strip().lower() if isinstance(platform_name, str) else ""
        if requested not in {"windows", "macos"}:
            return {"ok": False, "error": "未知的命令窗口类型", "code": "invalid_platform"}
        if requested == "windows" and sys.platform != "win32":
            return {
                "ok": False,
                "error": "请在 Windows 电脑上使用“一键打开 PowerShell”",
                "code": "platform_mismatch",
            }
        if requested == "macos" and sys.platform != "darwin":
            return {
                "ok": False,
                "error": "请在 Mac 上使用“一键打开终端”",
                "code": "platform_mismatch",
            }
        try:
            if requested == "windows":
                executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
                if not executable:
                    return {
                        "ok": False,
                        "error": "未找到 PowerShell。请按 Windows 键，搜索并打开 PowerShell",
                        "code": "terminal_missing",
                    }
                subprocess.Popen(
                    [executable, "-NoLogo", "-NoProfile", "-NoExit"],
                    close_fds=True,
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    ),
                )
                return {"ok": True, "message": "PowerShell 已打开；复制命令、粘贴后按 Enter"}

            completed = subprocess.run(
                ["/usr/bin/open", "-a", "Terminal"],
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            if completed.returncode != 0:
                raise OSError(f"open exited with {completed.returncode}")
            return {"ok": True, "message": "终端已打开；复制命令、粘贴后按 Return"}
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            logging.getLogger("vram_radar").warning("could not open setup terminal: %s", exc)
            return {
                "ok": False,
                "error": "无法打开命令窗口，请按教程中的搜索步骤手动打开",
                "code": "open_terminal_failed",
            }

    def get_status(self, force: bool = False, server_id: str | None = None) -> dict[str, Any]:
        snapshot = self.service.request_refresh(force=bool(force), server_id=server_id or None)
        self._evaluate_favorite_alerts(snapshot)
        self._evaluate_task_completion_alerts(snapshot)
        return snapshot

    def get_snapshot(self) -> dict[str, Any]:
        """Return the current in-memory state for cheap UI completion polling."""

        snapshot = self.service.snapshot()
        self._evaluate_favorite_alerts(snapshot)
        self._evaluate_task_completion_alerts(snapshot)
        return snapshot

    def dismiss_notice(self, code: str) -> dict[str, Any]:
        """Dismiss one bounded runtime notice without changing saved configuration."""

        normalized = code.strip() if isinstance(code, str) else ""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", normalized):
            return {"ok": False, "error": "提示标识无效", "code": "invalid_notice_code"}
        self.service.clear_notice(normalized)
        return {"ok": True, "notices": self.service.snapshot().get("notices", [])}

    def request_background_refresh(self) -> dict[str, Any]:
        """Keep collection active while the WebView is hidden without sending its payload."""

        snapshot = self.service.request_refresh(force=False)
        self._evaluate_favorite_alerts(snapshot)
        self._evaluate_task_completion_alerts(snapshot)
        monitoring = snapshot.get("monitoring") or {}
        if monitoring.get("in_flight"):
            self._monitor_background_refresh_for_favorite_alerts()
        return {
            "ok": True,
            "revision": monitoring.get("revision"),
            "in_flight": bool(monitoring.get("in_flight")),
        }

    def bind_notification_callback(
        self,
        callback: Callable[[str, str], bool] | None,
    ) -> None:
        """Bind the platform notification surface without exposing it to Profile data."""

        self._notification_callback = callback
        if callback is not None:
            snapshot = self.service.snapshot()
            self._evaluate_favorite_alerts(snapshot)
            self._evaluate_task_completion_alerts(snapshot)
        else:
            with self._favorite_alert_state_lock:
                self._favorite_alert_active_ids.clear()

    @staticmethod
    def _task_alert_server_signature(profile: Profile) -> tuple[tuple[str, str, bool], ...]:
        return tuple((server.id, server.backend, server.enabled) for server in profile.servers)

    def _reset_task_alerts_if_servers_changed(
        self,
        old_profile: Profile,
        new_profile: Profile,
    ) -> None:
        if self._task_alert_server_signature(old_profile) == self._task_alert_server_signature(new_profile):
            return
        retained = {server.id for server in new_profile.servers if server.enabled}
        with self._task_alert_state_lock:
            self._task_alert_active = {
                server_id: tasks
                for server_id, tasks in self._task_alert_active.items()
                if server_id in retained
            }

    @staticmethod
    def _active_owned_tasks(server: dict[str, Any]) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        server_id = str(server.get("server_id") or "")
        tasks = server.get("tasks") or {}
        current_user = str(tasks.get("current_user") or "")
        for task in tasks.get("active") or []:
            if not isinstance(task, dict) or not current_user or str(task.get("user") or "") != current_user:
                continue
            task_id = str(task.get("job_id") or "").strip()
            if not task_id:
                continue
            task_key = f"slurm:{task_id}"
            result[task_key] = {
                "server_id": server_id,
                "task_key": task_key,
                "task_kind": "slurm",
                "task_id": task_id,
                "label": str(task.get("name") or task_id).strip() or task_id,
            }
        processes = server.get("processes") or {}
        process_user = str(processes.get("current_user") or "")
        for process in processes.get("active") or []:
            if not isinstance(process, dict):
                continue
            if process.get("owner_scope") != "mine" and (
                not process_user or str(process.get("user") or "") != process_user
            ):
                continue
            task_id = str(process.get("pid") or "").strip()
            if not task_id:
                continue
            started_at = str(process.get("started_at") or "").strip()
            task_key = f"process:{task_id}:{started_at}" if started_at else f"process:{task_id}"
            result[task_key] = {
                "server_id": server_id,
                "task_key": task_key,
                "task_kind": "process",
                "task_id": task_id,
                "label": str(process.get("name") or process.get("command_preview") or f"PID {task_id}").strip(),
            }
        return result

    def _attach_task_alert_state(self, snapshot: dict[str, Any]) -> None:
        with self._task_alert_state_lock:
            unread = sum(
                1
                for event in self._task_alert_events
                if int(event.get("sequence") or 0) > self._task_alert_read_sequence
            )
            snapshot["task_completion_alerts"] = {
                "unread_count": unread,
                "latest_sequence": self._task_alert_sequence,
                "events": [dict(event) for event in self._task_alert_events[-20:]],
            }

    def _evaluate_task_completion_alerts(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            return
        with self._profile_mutation_lock:
            profile = self.profile
        monitoring = snapshot.get("monitoring") or {}
        if monitoring.get("paused") is True:
            self._attach_task_alert_state(snapshot)
            return
        watched = {
            (watch["server_id"], watch["task_key"])
            for watch in profile.task_completion_watches
        }
        completed: list[dict[str, str]] = []
        with self._task_alert_state_lock:
            for server in snapshot.get("servers") or []:
                if not isinstance(server, dict) or (server.get("connection") or {}).get("state") != "online":
                    continue
                server_id = str(server.get("server_id") or "")
                current = self._active_owned_tasks(server)
                previous = self._task_alert_active.get(server_id)
                self._task_alert_active[server_id] = current
                if previous is None:
                    continue
                for task_key, task in previous.items():
                    if task_key in current:
                        continue
                    if profile.task_completion_alert_enabled or (server_id, task_key) in watched:
                        self._task_alert_sequence += 1
                        event: dict[str, Any] = {
                            **task,
                            "sequence": self._task_alert_sequence,
                            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                            "watched": (server_id, task_key) in watched,
                        }
                        self._task_alert_events.append(event)
                        self._task_alert_events = self._task_alert_events[-64:]
                        completed.append(task)
        self._attach_task_alert_state(snapshot)
        if not completed or self._notification_callback is None:
            return
        names = "、".join(task["label"] for task in completed[:3])
        remaining = len(completed) - 3
        if remaining > 0:
            names += f" 等 {len(completed)} 个任务"
        english = profile.ui_language == "en"
        title = "Task completed" if english else "任务已完成"
        message = (
            f"{names} finished." if english else f"{names} 已结束。"
        )
        try:
            if not self._notification_callback(title, message):
                logging.getLogger("vram_radar").warning("task completion notification was not shown")
        except Exception:
            logging.getLogger("vram_radar").exception("failed to show task completion notification")

    def mark_task_completion_alerts_read(self) -> dict[str, Any]:
        with self._task_alert_state_lock:
            self._task_alert_read_sequence = self._task_alert_sequence
        return {"ok": True, "unread_count": 0}

    def set_task_completion_watch(
        self,
        server_id: str,
        task_key: str,
        task_kind: str,
        task_id: str,
        label: str,
        watched: bool,
    ) -> dict[str, Any]:
        base_profile = self.profile
        try:
            watch = normalize_task_completion_watch(
                {
                    "server_id": server_id,
                    "task_key": task_key,
                    "task_kind": task_kind,
                    "task_id": task_id,
                    "label": label,
                }
            )
            if not isinstance(watched, bool):
                raise ConfigError("watched must be true or false")
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_task_watch"}
        retained = [
            dict(item)
            for item in base_profile.task_completion_watches
            if (item["server_id"], item["task_key"])
            != (watch["server_id"], watch["task_key"])
        ]
        if watched:
            retained.append(watch)
        if len(retained) > MAX_TASK_COMPLETION_WATCHES:
            return {
                "ok": False,
                "error": f"最多可单独关注 {MAX_TASK_COMPLETION_WATCHES} 个任务",
                "code": "task_watch_limit",
            }
        return self._persist_local_preferences(
            replace(base_profile, task_completion_watches=tuple(retained)),
            expected_profile=base_profile,
        )

    @staticmethod
    def _favorite_alert_policy(profile: Profile) -> tuple[bool, float, tuple[str, ...]]:
        return (
            profile.favorite_alert_enabled,
            profile.favorite_alert_min_memory_gib,
            profile.favorite_server_ids,
        )

    def _reset_favorite_alerts_if_changed(
        self,
        old_profile: Profile,
        new_profile: Profile,
    ) -> None:
        if self._favorite_alert_policy(old_profile) == self._favorite_alert_policy(new_profile):
            return
        with self._favorite_alert_state_lock:
            self._favorite_alert_active_ids.clear()

    @staticmethod
    def _favorite_alert_copy(
        matches: list[dict[str, Any]],
        *,
        language: str,
        minimum_memory_gib: float,
    ) -> tuple[str, str]:
        english = language == "en"
        rows: list[str] = []
        for match in matches[:3]:
            name = str(match.get("display_name") or match.get("server_id") or "GPU")
            idle_units = max(0, int(match.get("idle_units") or 0))
            available = max(0.0, float(match.get("available_memory_gib") or 0))
            available_text = f"{available:.2f}".rstrip("0").rstrip(".")
            if idle_units:
                rows.append(
                    f"{name}: {idle_units} idle GPU(s), up to {available_text} GiB free"
                    if english
                    else f"{name}：{idle_units} 张 GPU 空闲，单卡最多可用 {available_text} GiB"
                )
            else:
                threshold_text = f"{minimum_memory_gib:.2f}".rstrip("0").rstrip(".")
                rows.append(
                    f"{name}: a GPU reached {threshold_text} GiB free"
                    if english
                    else f"{name}：有 GPU 空闲显存达到 {threshold_text} GiB"
                )
        remaining = len(matches) - len(rows)
        if remaining > 0:
            rows.append(
                f"{remaining} more favorite server(s) also match"
                if english
                else f"另有 {remaining} 台收藏服务器也符合条件"
            )
        return (
            ("Favorite GPUs are available" if english else "收藏 GPU 已可用"),
            ("; ".join(rows) if english else "；".join(rows)),
        )

    def _evaluate_favorite_alerts(self, snapshot: dict[str, Any]) -> None:
        with self._profile_mutation_lock:
            profile = self.profile
        monitoring = snapshot.get("monitoring") or {}
        if not profile.favorite_alert_enabled or monitoring.get("paused") is True:
            with self._favorite_alert_state_lock:
                self._favorite_alert_active_ids.clear()
            return
        matches = favorite_resource_matches(
            snapshot,
            profile.favorite_server_ids,
            profile.favorite_alert_min_memory_gib,
        )
        current_ids = {str(match["server_id"]) for match in matches}
        callback = self._notification_callback
        with self._favorite_alert_state_lock:
            if callback is None:
                self._favorite_alert_active_ids.clear()
                return
            newly_available = [
                match
                for match in matches
                if str(match["server_id"]) not in self._favorite_alert_active_ids
            ]
            self._favorite_alert_active_ids = current_ids
        if not newly_available:
            return
        title, message = self._favorite_alert_copy(
            newly_available,
            language=profile.ui_language,
            minimum_memory_gib=profile.favorite_alert_min_memory_gib,
        )
        try:
            if not callback(title, message):
                logging.getLogger("vram_radar").warning("favorite GPU notification was not shown")
        except Exception:
            logging.getLogger("vram_radar").exception("failed to show favorite GPU notification")

    def _monitor_background_refresh_for_favorite_alerts(self) -> None:
        with self._favorite_alert_state_lock:
            if self._favorite_alert_monitor_running:
                return
            self._favorite_alert_monitor_running = True

        def monitor() -> None:
            try:
                deadline = time.monotonic() + 3_600
                while time.monotonic() < deadline:
                    snapshot = self.service.snapshot()
                    if not isinstance(snapshot, dict):
                        return
                    if not snapshot.get("monitoring", {}).get("in_flight"):
                        self._evaluate_favorite_alerts(snapshot)
                        self._evaluate_task_completion_alerts(snapshot)
                        return
                    time.sleep(0.25)
            finally:
                with self._favorite_alert_state_lock:
                    self._favorite_alert_monitor_running = False

        threading.Thread(
            target=monitor,
            name="vram-radar-favorite-alert",
            daemon=True,
        ).start()

    def bind_tray_controller(
        self,
        controller: WindowsTrayController | None,
    ) -> None:
        """Refresh native menu labels after a saved interface-language change."""

        self._tray_controller = controller

    def set_monitoring_paused(self, paused: bool) -> dict[str, Any]:
        if not isinstance(paused, bool):
            return {
                "ok": False,
                "error": "监控暂停状态必须是布尔值",
                "code": "invalid_monitoring_state",
            }
        snapshot = self.service.pause() if paused else self.service.resume()
        return {"ok": True, **snapshot}

    def recommend_resources(
        self,
        criteria: dict[str, Any] | int | None = None,
        min_memory_gib: float = 0,
        gpu_type: str = "",
        partition: str = "",
        same_node: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        try:
            if isinstance(criteria, dict):
                options = criteria
                gpu_count = options.get("gpu_count", 1)
                min_memory_gib = options.get("min_memory_gib", 0)
                gpu_type = options.get("gpu_type", "")
                partition = options.get("partition", "")
                same_node = options.get("same_node", True)
                limit = options.get("limit", 10)
            else:
                gpu_count = 1 if criteria is None else criteria
            return self.service.recommend_many(
                gpu_count=gpu_count,
                min_memory_gib=min_memory_gib,
                gpu_type=gpu_type,
                partition=partition,
                same_node=same_node,
                limit=limit,
            )
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "recommendation_only": True,
                "reason": str(exc),
                "candidates": [],
            }

    def get_cluster_nodes(
        self,
        server_id: str,
        cursor: int | dict[str, Any] = 0,
        limit: int = 100,
        query: str = "",
        gpu_type: str = "",
        partition: str = "",
        only_available: bool = False,
        only_issues: bool = False,
        revision: int | None = None,
    ) -> dict[str, Any]:
        try:
            if isinstance(cursor, dict):
                options = cursor
                cursor = options.get("cursor", options.get("offset", 0))
                limit = options.get("limit", 100)
                query = options.get("query", "")
                gpu_type = options.get("gpu_type", "")
                partition = options.get("partition", "")
                only_available = options.get("only_available", False)
                only_issues = options.get("only_issues", options.get("issues_only", False))
                revision = options.get("revision")
            return self.service.get_cluster_nodes(
                server_id,
                cursor=cursor,
                limit=limit,
                query=query,
                gpu_type=gpu_type,
                partition=partition,
                only_available=only_available,
                only_issues=only_issues,
                revision=revision,
            )
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "code": "invalid_cluster_query",
                "error": str(exc),
                "server_id": str(server_id),
                "nodes": [],
            }

    def show_notification(self, title: object, message: object) -> dict[str, Any]:
        try:
            normalized_title = require_bounded_text(
                title,
                "notification title",
                maximum_bytes=256,
            )
            normalized_message = require_bounded_text(
                message,
                "notification message",
                maximum_bytes=2_048,
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_notification"}
        callback = self._notification_callback
        if callback is None:
            return {
                "ok": False,
                "error": "当前系统没有可用的通知区域",
                "code": "notification_unavailable",
            }
        try:
            shown = bool(callback(normalized_title, normalized_message))
        except Exception:
            logging.getLogger("vram_radar").exception("failed to show a desktop notification")
            shown = False
        return {
            "ok": shown,
            **({} if shown else {"error": "无法显示系统通知", "code": "notification_failed"}),
        }

    def inspect_account_directory(
        self,
        server_id: str,
        root_path: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(server_id, str) or not server_id.strip():
            return {"ok": False, "error": "缺少服务器标识", "code": "server_not_found"}
        if not isinstance(force, bool):
            return {"ok": False, "error": "刷新目录参数必须是布尔值", "code": "invalid_directory"}
        try:
            selected_root = (
                None
                if root_path is None
                else require_optional_remote_directory(root_path, "展开目录")
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_directory"}
        if selected_root == "":
            return {"ok": False, "error": "展开目录不能为空", "code": "invalid_directory"}
        return self.service.inspect_account_directory(
            server_id.strip(),
            selected_root,
            force=force,
        )

    def set_default_directory(self, server_id: str, root_path: str) -> dict[str, Any]:
        if not isinstance(server_id, str) or not server_id.strip():
            return {"ok": False, "error": "缺少服务器标识", "code": "server_not_found"}
        server_id = server_id.strip()
        base_profile = self.profile
        server = next((item for item in base_profile.servers if item.id == server_id), None)
        if server is None:
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
        try:
            selected_root = require_optional_remote_directory(root_path, "默认展开目录")
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_directory"}

        account = None
        canonical_root = ""
        if selected_root:
            # Pinning is an explicit persistent action: require a fresh full
            # tree response instead of accepting a compact `unchanged`
            # freshness acknowledgment that intentionally omits `account`.
            inspected = self.service.inspect_account_directory(
                server_id,
                selected_root,
                force=True,
            )
            if not inspected.get("ok"):
                return inspected
            account = inspected.get("account")
            tree = account.get("directory_tree", {}) if isinstance(account, dict) else {}
            if not tree.get("supported") or not isinstance(tree.get("root"), str):
                return {
                    "ok": False,
                    "error": tree.get("warning") or "无法读取这个目录",
                    "code": "directory_unavailable",
                }
            canonical_root = require_optional_remote_directory(tree["root"], "默认展开目录")

        updated_servers = tuple(
            replace(item, default_work_directory=canonical_root) if item.id == server_id else item
            for item in base_profile.servers
        )
        updated_profile = replace(base_profile, servers=updated_servers)
        result = self._persist_local_preferences(
            updated_profile,
            replace_service=True,
            expected_profile=base_profile,
        )
        if result.get("ok"):
            result["account"] = account
        return result

    def set_navigator_side(self, side: str) -> dict[str, Any]:
        if not isinstance(side, str) or side.strip().lower() not in {"left", "right"}:
            return {
                "ok": False,
                "error": "服务器目录位置只能是左侧或右侧",
                "code": "invalid_navigator_side",
            }
        base_profile = self.profile
        updated_profile = replace(base_profile, navigator_side=side.strip().lower())
        return self._persist_local_preferences(updated_profile, expected_profile=base_profile)

    def set_close_behavior(self, behavior: str) -> dict[str, Any]:
        if not isinstance(behavior, str) or behavior.strip().lower() not in {"tray", "exit"}:
            return {
                "ok": False,
                "error": "关闭窗口时只能选择收起到通知区域或退出",
                "code": "invalid_close_behavior",
            }
        base_profile = self.profile
        return self._persist_local_preferences(
            replace(base_profile, close_behavior=behavior.strip().lower()),
            expected_profile=base_profile,
        )

    def set_server_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id"}
        if not isinstance(enabled, bool):
            return {
                "ok": False,
                "error": "服务器启用状态必须是布尔值",
                "code": "invalid_enabled_state",
            }
        base_profile = self.profile
        if not any(server.id == normalized_id for server in base_profile.servers):
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
        updated_servers = tuple(
            replace(server, enabled=enabled) if server.id == normalized_id else server
            for server in base_profile.servers
        )
        return self._persist_local_preferences(
            replace(base_profile, servers=updated_servers),
            replace_service=True,
            expected_profile=base_profile,
        )

    def set_favorite_server(self, server_id: str, favorite: bool) -> dict[str, Any]:
        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "favorite server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id"}
        if not isinstance(favorite, bool):
            return {
                "ok": False,
                "error": "收藏状态必须是布尔值",
                "code": "invalid_favorite_state",
            }
        base_profile = self.profile
        favorites = list(base_profile.favorite_server_ids)
        if favorite and normalized_id not in favorites:
            if len(favorites) >= MAX_FAVORITE_SERVER_IDS:
                return {
                    "ok": False,
                    "error": "收藏服务器数量已达到上限",
                    "code": "favorite_limit_reached",
                }
            favorites.append(normalized_id)
        elif not favorite:
            favorites = [candidate for candidate in favorites if candidate != normalized_id]
        return self._persist_local_preferences(
            replace(base_profile, favorite_server_ids=tuple(favorites)),
            expected_profile=base_profile,
        )

    @staticmethod
    def _saved_view_id(name: str) -> str:
        return f"view-{hashlib.sha256(name.casefold().encode('utf-8')).hexdigest()[:16]}"

    def save_saved_view(
        self,
        name: object,
        criteria: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_profile = self.profile
        try:
            if isinstance(name, dict) and criteria is None:
                candidate = copy.deepcopy(name)
                requested_name = candidate.get("name")
            else:
                if criteria is not None and not isinstance(criteria, dict):
                    raise ConfigError("saved view criteria must be a table")
                candidate = copy.deepcopy(criteria or {})
                requested_name = name
                candidate["name"] = name
            normalized_name = require_bounded_text(
                requested_name,
                "saved view name",
                maximum_bytes=128,
            )
            existing_by_name = next(
                (
                    view
                    for view in base_profile.saved_views
                    if str(view.get("name", "")).casefold() == normalized_name.casefold()
                ),
                None,
            )
            candidate["name"] = normalized_name
            candidate.setdefault(
                "id",
                existing_by_name["id"] if existing_by_name else self._saved_view_id(normalized_name),
            )
            saved_view = normalize_saved_view(candidate)
            views = [dict(view) for view in base_profile.saved_views]
            replacement_index = next(
                (index for index, view in enumerate(views) if view["id"] == saved_view["id"]),
                None,
            )
            if replacement_index is None:
                views.append(saved_view)
            else:
                views[replacement_index] = saved_view
            # Re-enter the Profile parser so list bounds and ID uniqueness stay
            # owned by the persistent profile schema.
            updated_profile = Profile.from_dict(
                {**base_profile.to_dict(), "saved_views": views},
                expected_id=base_profile.id,
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_saved_view"}
        result = self._persist_local_preferences(updated_profile, expected_profile=base_profile)
        if result.get("ok"):
            result["saved_view"] = saved_view
        return result

    def delete_saved_view(self, name_or_id: str) -> dict[str, Any]:
        base_profile = self.profile
        try:
            target = require_bounded_text(
                name_or_id,
                "saved view name or id",
                maximum_bytes=128,
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_saved_view"}
        retained = tuple(
            dict(view)
            for view in base_profile.saved_views
            if view["id"] != target and str(view["name"]).casefold() != target.casefold()
        )
        if len(retained) == len(base_profile.saved_views):
            return {"ok": False, "error": "找不到这个保存视图", "code": "saved_view_not_found"}
        return self._persist_local_preferences(
            replace(base_profile, saved_views=retained),
            expected_profile=base_profile,
        )

    def test_connection(self, server_id: str) -> dict[str, Any]:
        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id", "stages": []}
        server = next((item for item in self.profile.servers if item.id == normalized_id), None)
        if server is None:
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found", "stages": []}
        stages = [
            {
                "id": "configuration",
                "label": "本地配置",
                "state": "passed",
                "message": "已读取服务器配置",
            }
        ]
        if not server.enabled:
            stages.append(
                {
                    "id": "connection",
                    "label": "SSH 连接",
                    "state": "blocked",
                    "message": "这台服务器当前已暂停监控",
                }
            )
            return {
                "ok": False,
                "server_id": normalized_id,
                "code": "server_disabled",
                "error": "这台服务器已停用，请先恢复监控",
                "stages": stages,
            }
        try:
            # The exact monitoring collector is the validation boundary. Its
            # result is committed to the same runtime state shown on the main
            # dashboard, so a successful test cannot leave a stale error card.
            payload = self.service.probe_server(normalized_id)
        except ConnectorFailure as exc:
            collection_failure = exc.code in {
                "command_missing",
                "parse_failed",
                "config_invalid",
                "gpu_inventory_empty",
                "remote_permission_denied",
                "remote_command_failed",
                "response_too_large",
                "node_list_too_large",
            }
            if collection_failure and server.auto_detect_backend:
                detected_backend = (
                    "slurm_ssh" if server.backend == "direct_ssh" else "direct_ssh"
                )
                try:
                    payload = self.service.probe_server_backend(
                        normalized_id,
                        detected_backend,
                    )
                except ConnectorFailure:
                    payload = None
                if payload is not None:
                    updated_servers = tuple(
                        replace(item, backend=detected_backend, auto_detect_backend=False)
                        if item.id == normalized_id
                        else item
                        for item in self.profile.servers
                    )
                    persisted = self._persist_local_preferences(
                        replace(self.profile, servers=updated_servers),
                        replace_service=True,
                        expected_profile=self.profile,
                    )
                    if not persisted.get("ok"):
                        return {
                            "ok": False,
                            "server_id": normalized_id,
                            "code": persisted.get("code", "profile_save_failed"),
                            "error": persisted.get("error", "已识别连接类型，但保存失败"),
                            "stages": stages,
                        }
                    stages.extend(
                        [
                            {
                                "id": "connection",
                                "label": "SSH 连接",
                                "state": "passed",
                                "message": "SSH 连接和身份验证成功",
                            },
                            {
                                "id": "detection",
                                "label": "连接类型",
                                "state": "passed",
                                "message": (
                                    "已自动识别为 Slurm"
                                    if detected_backend == "slurm_ssh"
                                    else "已自动识别为 SSH 直连"
                                ),
                            },
                            {
                                "id": "collection",
                                "label": "资源读取",
                                "state": "passed",
                                "message": "GPU 或调度信息读取成功",
                            },
                        ]
                    )
                    return {
                        "ok": True,
                        "server_id": normalized_id,
                        "code": "connection_type_detected",
                        "message": "连接类型已自动识别并保存",
                        "detected_backend": detected_backend,
                        "profile": persisted.get("profile"),
                        "stages": stages,
                    }
            if collection_failure:
                stages.append(
                    {
                        "id": "connection",
                        "label": "SSH 连接",
                        "state": "passed",
                        "message": "SSH 连接和身份验证成功",
                    }
                )
            stages.append(
                {
                    "id": "collection" if collection_failure else "connection",
                    "label": "资源读取" if collection_failure else "SSH 连接",
                    "state": "failed",
                    "message": str(exc),
                }
            )
            return {
                "ok": False,
                "server_id": normalized_id,
                "code": exc.code,
                "error": str(exc),
                "retryable": exc.retryable,
                "stages": stages,
            }
        except Exception:
            logging.getLogger("vram_radar").exception(
                "unexpected connection-test failure for %s", normalized_id
            )
            stages.append(
                {
                    "id": "connection",
                    "label": "SSH 连接",
                    "state": "failed",
                    "message": "连接测试发生未知错误",
                }
            )
            return {
                "ok": False,
                "server_id": normalized_id,
                "code": "unknown",
                "error": "连接测试发生未知错误",
                "retryable": True,
                "stages": stages,
            }
        persisted_profile: dict[str, Any] | None = None
        detected_backend = server.backend
        if server.auto_detect_backend:
            # Imported OpenSSH aliases start with the direct collector because
            # it is the least assumptive probe. A login node can still expose
            # nvidia-smi while being governed by Slurm, so a successful direct
            # probe is not enough to classify it: prefer the Slurm collector
            # when that exact collector also succeeds.
            if server.backend == "direct_ssh":
                try:
                    slurm_payload = self.service.probe_server_backend(
                        normalized_id,
                        "slurm_ssh",
                    )
                except ConnectorFailure:
                    slurm_payload = None
                if isinstance(slurm_payload, dict):
                    detected_backend = "slurm_ssh"
                    payload = slurm_payload
            updated_servers = tuple(
                replace(
                    item,
                    backend=detected_backend,
                    auto_detect_backend=False,
                )
                if item.id == normalized_id
                else item
                for item in self.profile.servers
            )
            persisted = self._persist_local_preferences(
                replace(self.profile, servers=updated_servers),
                replace_service=detected_backend != server.backend,
                expected_profile=self.profile,
            )
            if not persisted.get("ok"):
                return {
                    "ok": False,
                    "server_id": normalized_id,
                    "code": persisted.get("code", "profile_save_failed"),
                    "error": persisted.get("error", "已验证连接类型，但保存失败"),
                    "stages": stages,
                }
            persisted_profile = persisted.get("profile")
            stages.append(
                {
                    "id": "detection",
                    "label": "连接类型",
                    "state": "passed",
                    "message": (
                        "已自动识别为 Slurm"
                        if detected_backend == "slurm_ssh"
                        else "已自动识别为 SSH 直连"
                    ),
                }
            )
        stages.extend(
            [
                {
                    "id": "connection",
                    "label": "SSH 连接",
                    "state": "passed",
                    "message": "SSH 连接和身份验证成功",
                },
                {
                    "id": "collection",
                    "label": "资源读取",
                    "state": "passed",
                    "message": "GPU 或调度信息读取成功",
                },
            ]
        )
        return {
            "ok": True,
            "server_id": normalized_id,
            "code": "connection_ok",
            "message": "连接测试成功",
            "profile": persisted_profile,
            "summary": {
                "backend": detected_backend,
                "total_gpus": max(0, int(payload.get("total_gpus") or 0)),
            },
            "stages": stages,
        }

    def trust_host_key(self, server_id: str) -> dict[str, Any]:
        """Record one previously unknown Host Key, then run the real collector.

        The web UI must obtain an explicit confirmation before calling this
        method. ``accept-new`` is deliberately scoped to this single attempt:
        it never accepts a changed key, and all ordinary background refreshes
        continue to use the user's normal OpenSSH trust policy.
        """

        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "error": str(exc), "code": "invalid_server_id"}
        with self._profile_mutation_lock:
            server = next((item for item in self.profile.servers if item.id == normalized_id), None)
        if server is None:
            return {"ok": False, "error": "找不到这台服务器", "code": "server_not_found"}
        if not server.enabled:
            return {
                "ok": False,
                "error": "这台服务器已停用，请先恢复监控",
                "code": "server_disabled",
            }
        if not self._host_key_trust_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "另一台服务器正在核验 Host Key，请稍后再试",
                "code": "host_key_trust_busy",
            }
        try:
            try:
                # The first bounded command lets OpenSSH write only an unknown
                # key to the configured known_hosts file. Authentication may
                # still fail after the key has been recorded; the monitored
                # probe below owns password fallback and the final result.
                run_remote(server, "true", accept_new_host_key=True)
            except ConnectorFailure as exc:
                if exc.code not in PASSWORD_FALLBACK_AUTH_CODES:
                    return {
                        "ok": False,
                        "server_id": normalized_id,
                        "code": exc.code,
                        "error": str(exc),
                        "retryable": exc.retryable,
                    }
            try:
                payload = self.service.probe_server(normalized_id)
            except ConnectorFailure as exc:
                return {
                    "ok": False,
                    "server_id": normalized_id,
                    "code": exc.code,
                    "error": str(exc),
                    "retryable": exc.retryable,
                    "host_key_recorded": exc.code != "host_key_untrusted",
                }
            return {
                "ok": True,
                "server_id": normalized_id,
                "code": "host_key_trusted",
                "message": "Host Key 已保存，服务器连接和资源读取成功",
                "summary": {
                    "backend": server.backend,
                    "total_gpus": max(0, int(payload.get("total_gpus") or 0)),
                },
            }
        finally:
            self._host_key_trust_lock.release()

    def _saved_server_password(self, server: Any) -> str:
        if not server.auth_ref:
            raise SshKeySetupError(
                "ssh_key_initial_auth_required",
                "当前 SSH Key 无法登录，且没有已保存的服务器密码；请先保存一次登录密码，或请管理员添加公钥",
            )
        getter = getattr(self.secret_store, "get", None)
        if getter is None:
            raise SshKeySetupError(
                "password_unavailable",
                "系统凭据存储不可用，无法完成首次公钥部署",
            )
        try:
            password = getter(server.auth_ref)
        except Exception as exc:
            raise SshKeySetupError(
                "password_unavailable",
                "无法从系统凭据存储读取服务器密码",
            ) from exc
        if not password:
            raise SshKeySetupError(
                "password_unavailable",
                "已保存的服务器密码不存在，请先在登录与高级设置中重新保存",
            )
        return password

    @staticmethod
    def _key_setup_stage(
        stage_id: str,
        label: str,
        state: str,
        message: str,
    ) -> dict[str, str]:
        return {"id": stage_id, "label": label, "state": state, "message": message}

    def configure_ssh_key(
        self,
        server_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._key_setup_lock.acquire(blocking=False):
            return {
                "ok": False,
                "code": "ssh_key_setup_busy",
                "error": "另一台服务器正在配置 SSH Key，请等待完成后再试",
                "stages": [],
            }
        if not self._profile_mutation_lock.acquire(blocking=False):
            self._key_setup_lock.release()
            return {
                "ok": False,
                "code": "profile_mutation_busy",
                "error": "另一项设置正在保存，请等待完成后再配置 SSH Key",
                "stages": [],
            }
        try:
            return self._configure_ssh_key(server_id, options)
        finally:
            self._profile_mutation_lock.release()
            self._key_setup_lock.release()

    def _configure_ssh_key(
        self,
        server_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Configure one server identity without ever returning key material."""

        stages: list[dict[str, str]] = []
        try:
            normalized_id = require_id(
                server_id.strip() if isinstance(server_id, str) else server_id,
                "server id",
            )
        except ConfigError as exc:
            return {"ok": False, "code": "invalid_server_id", "error": str(exc), "stages": stages}
        server = next((item for item in self.profile.servers if item.id == normalized_id), None)
        if server is None:
            return {
                "ok": False,
                "code": "server_not_found",
                "error": "找不到这台已保存的服务器",
                "stages": stages,
            }
        if options is None:
            options = {}
        if not isinstance(options, dict):
            return {
                "ok": False,
                "code": "invalid_key_setup",
                "error": "SSH Key 配置参数无效",
                "stages": stages,
            }
        mode = str(options.get("mode") or "existing").strip().lower()
        prepared: PreparedSshKey | None = None
        try:
            if mode == "existing":
                prepared = prepare_existing_key(
                    options.get("private_key_path", ""),
                    options.get("public_key_path", ""),
                )
                local_message = "已核对本地私钥和公钥；私钥不会离开这台电脑"
            elif mode == "generate":
                prepared = prepare_generated_key(
                    self.paths.config / "ssh_keys",
                    self.profile.id,
                    normalized_id,
                )
                local_message = (
                    "已生成并保护 VRAM Radar 专用 Ed25519 密钥"
                    if prepared.generated
                    else "已安全复用此前生成的 VRAM Radar 专用密钥"
                )
            else:
                raise SshKeySetupError("invalid_key_setup_mode", "请选择使用现有密钥或生成专用密钥")
            stages.append(self._key_setup_stage("local_key", "本地密钥", "passed", local_message))
        except SshKeySetupError as exc:
            stages.append(self._key_setup_stage("local_key", "本地密钥", "failed", str(exc)))
            return {"ok": False, "code": exc.code, "error": str(exc), "stages": stages}
        except Exception:
            stages.append(
                self._key_setup_stage("local_key", "本地密钥", "failed", "处理本地 SSH Key 时发生未知错误")
            )
            return {
                "ok": False,
                "code": "ssh_key_local_failed",
                "error": "处理本地 SSH Key 时发生未知错误",
                "stages": stages,
            }

        key_input = (prepared.public_line + "\n").encode("ascii")
        deploy_auth: str | None = None
        remote_added = False

        def cleanup_generated_key(*, safe_to_remove: bool) -> tuple[bool, bool]:
            """Return (local_key_retained, cleanup_succeeded).

            Retaining a generated key is intentional while the remote result is
            uncertain. When deletion is safe and attempted before any remote
            append, the helper result is authoritative: an unlink failure must
            never be reported as completed cleanup.
            """

            if not prepared.generated:
                return False, True
            if not safe_to_remove:
                return True, True
            removed = bool(remove_generated_key(prepared))
            return not removed, removed

        try:
            try:
                install_output = run_remote(
                    server,
                    INSTALL_AUTHORIZED_KEY_SCRIPT,
                    stdin_data=key_input,
                )
            except ConnectorFailure as exc:
                if exc.code not in PASSWORD_FALLBACK_AUTH_CODES:
                    raise
                deploy_auth = self._saved_server_password(server)
                install_output = run_remote(
                    server,
                    INSTALL_AUTHORIZED_KEY_SCRIPT,
                    **{"password": deploy_auth},
                    stdin_data=key_input,
                )
            marker = next(
                (line for line in install_output.splitlines() if line.startswith("VRAM_RADAR_KEY_SETUP|")),
                "",
            )
            parts = marker.split("|")
            if (
                len(parts) != 5
                or parts[1] not in {"installed", "already_present"}
                or parts[2] not in {"0", "1"}
                or parts[3] not in {"0", "1"}
                or not re.fullmatch(r"[0-7]{3,4}", parts[4])
            ):
                raise SshKeySetupError(
                    "ssh_key_remote_protocol",
                    "服务器没有返回可验证的公钥安装结果，未更新本地配置",
                )
            remote_added = parts[1] == "installed"
            stages.append(
                self._key_setup_stage(
                    "public_key",
                    "部署公钥",
                    "passed",
                    "服务器已存在同一公钥，未重复写入"
                    if not remote_added
                    else "公钥已仅追加写入 authorized_keys，未替换现有内容；私钥未传输",
                )
            )
        except SshKeySetupError as exc:
            safe_to_remove_local = exc.code in {
                "ssh_key_initial_auth_required",
                "password_unavailable",
            }
            local_retained, cleanup_succeeded = cleanup_generated_key(
                safe_to_remove=safe_to_remove_local
            )
            recovery_required = not safe_to_remove_local or not cleanup_succeeded
            message = str(exc) + (
                "；本地专用密钥清理失败，密钥已保留，请在设置中重试或人工删除"
                if not cleanup_succeeded
                else "；服务器结果不确定，本地专用密钥已保留，便于重试或人工恢复"
                if local_retained and not safe_to_remove_local
                else ""
            )
            stages.append(self._key_setup_stage("public_key", "部署公钥", "failed", message))
            return {
                "ok": False,
                "code": exc.code if cleanup_succeeded else "ssh_key_local_cleanup_failed",
                "error": message,
                "stages": stages,
                "local_key_retained": local_retained,
                "recovery_required": recovery_required,
            }
        except ConnectorFailure as exc:
            safe_no_remote_change = exc.code in {
                "auth_failed",
                "dns_failed",
                "ssh_refused",
                "host_key_changed",
                "host_key_untrusted",
                "ssh_missing",
                "ssh_key_invalid",
                "ssh_key_remote_home_unsafe",
                "ssh_key_remote_permissions",
            }
            local_retained, cleanup_succeeded = cleanup_generated_key(
                safe_to_remove=safe_no_remote_change
            )
            recovery_required = not safe_no_remote_change or not cleanup_succeeded
            message = str(exc)
            if exc.code == "auth_failed" and not server.auth_ref:
                message = "无法使用当前 SSH 配置登录；请先保存一次服务器密码，或请管理员添加公钥"
            if not cleanup_succeeded:
                message += "；本地专用密钥清理失败，密钥已保留，请人工检查"
            elif local_retained:
                message += "；服务器结果不确定，本地专用密钥已保留，便于重试或人工恢复"
            stages.append(self._key_setup_stage("public_key", "部署公钥", "failed", message))
            return {
                "ok": False,
                "code": exc.code if cleanup_succeeded else "ssh_key_local_cleanup_failed",
                "error": message,
                "stages": stages,
                "local_key_retained": local_retained,
                "recovery_required": recovery_required,
            }
        except Exception:
            message = "部署 SSH 公钥时发生未知错误；为避免丢失可能已部署密钥，本地专用密钥已保留"
            stages.append(self._key_setup_stage("public_key", "部署公钥", "failed", message))
            return {
                "ok": False,
                "code": "ssh_key_deploy_failed",
                "error": message,
                "stages": stages,
                "local_key_retained": bool(prepared.generated),
            }

        updated_server = replace(
            server,
            identity_file=str(prepared.private_path),
            prefer_identity_auth=True,
        )
        try:
            verify_output = run_remote(
                updated_server,
                VERIFY_SSH_KEY_SCRIPT,
                identities_only=True,
            )
            if "VRAM_RADAR_KEY_VERIFY|ok" not in verify_output.splitlines():
                raise SshKeySetupError("ssh_key_verify_protocol", "服务器没有返回可验证的 SSH Key 结果")
            stages.append(
                self._key_setup_stage(
                    "verification",
                    "免密验证",
                    "passed",
                    "已强制使用所选私钥完成一次独立 SSH 登录",
                )
            )
        except (ConnectorFailure, SshKeySetupError) as exc:
            local_retained, cleanup_succeeded = cleanup_generated_key(
                safe_to_remove=False
            )
            recovery_required = remote_added or not cleanup_succeeded
            message = (
                "公钥已写入，但所选私钥验证失败；如私钥带口令，请先加载 ssh-agent"
                if isinstance(exc, ConnectorFailure) and exc.code == "auth_failed"
                else str(exc)
            )
            if remote_added:
                message += (
                    "；为避免误删 authorized_keys 中其他程序的并发修改，"
                    "新增公钥和本地密钥已保留，请修复认证后重试或人工删除该公钥"
                )
            stages.append(self._key_setup_stage("verification", "免密验证", "failed", message))
            stages.append(
                self._key_setup_stage(
                    "recovery",
                    "安全恢复",
                    "failed" if recovery_required else "passed",
                    "未自动重写 authorized_keys；新增公钥和本地密钥已保留，需重试或人工处理"
                    if remote_added
                    else "服务器原本已有同一公钥，未改动远端内容；本地密钥已保留便于重试",
                )
            )
            return {
                "ok": False,
                "code": "ssh_key_recovery_required" if recovery_required else "ssh_key_verify_failed",
                "error": message,
                "stages": stages,
                "local_key_retained": local_retained,
                "recovery_required": recovery_required,
            }
        except Exception:
            local_retained, cleanup_succeeded = cleanup_generated_key(
                safe_to_remove=False
            )
            recovery_required = remote_added or not cleanup_succeeded
            message = "验证 SSH Key 时发生未知错误"
            if remote_added:
                message += "；为避免覆盖 authorized_keys 的并发修改，新增公钥和本地密钥已保留"
            stages.append(self._key_setup_stage("verification", "免密验证", "failed", message))
            stages.append(
                self._key_setup_stage(
                    "recovery",
                    "安全恢复",
                    "failed" if recovery_required else "passed",
                    "未自动重写 authorized_keys；请重试或人工检查新增公钥"
                    if remote_added
                    else "远端原有内容未改变；本地密钥已保留便于重试",
                )
            )
            return {
                "ok": False,
                "code": "ssh_key_recovery_required" if recovery_required else "ssh_key_verify_failed",
                "error": message,
                "stages": stages,
                "local_key_retained": local_retained,
                "recovery_required": recovery_required,
            }

        old_profile = self.profile
        updated_profile = replace(
            self.profile,
            servers=tuple(updated_server if item.id == normalized_id else item for item in self.profile.servers),
        )
        profile_saved = False
        try:
            self.store.save(updated_profile)
            profile_saved = True
            self.service.replace_profile(updated_profile, SnapshotCache(self.paths, updated_profile.id))
            self.profile = updated_profile
            self._profile_revision += 1
        except Exception:
            profile_rolled_back = True
            if profile_saved:
                try:
                    self.store.save(old_profile)
                    self.service.replace_profile(old_profile, SnapshotCache(self.paths, old_profile.id))
                except Exception:
                    profile_rolled_back = False
            local_retained, cleanup_succeeded = cleanup_generated_key(
                safe_to_remove=False
            )
            recovery_required = (
                not profile_rolled_back or remote_added or not cleanup_succeeded
            )
            stages.append(
                self._key_setup_stage(
                    "profile",
                    "保存配置",
                    "failed",
                    "本地配置保存失败；已撤销本次配置修改"
                    if profile_rolled_back
                    else "本地配置恢复未完成，当前状态需要人工确认",
                )
            )
            stages.append(
                self._key_setup_stage(
                    "recovery",
                    "安全恢复",
                    "passed" if not recovery_required else "failed",
                    "本地配置已恢复，服务器原有公钥内容未改变"
                    if not recovery_required
                    else (
                        "本地配置已恢复；为避免覆盖 authorized_keys 的并发修改，"
                        "已验证的新增公钥和本地密钥被保留，请重试保存或人工处理"
                    )
                    if profile_rolled_back and remote_added
                    else "本地配置恢复未完成；密钥已保留，请按诊断信息人工检查",
                )
            )
            return {
                "ok": False,
                "code": "profile_save_failed" if not recovery_required else "ssh_key_setup_recovery_required",
                "error": (
                    "无法保存 SSH Key 配置；本地配置已恢复，远端原有内容未改变"
                    if not recovery_required
                    else "SSH Key 配置未完成；为保护 authorized_keys，密钥已安全保留，请重试或复制诊断后人工检查"
                ),
                "stages": stages,
                "local_key_retained": local_retained,
                "recovery_required": recovery_required,
            }

        stages.append(
            self._key_setup_stage(
                "profile",
                "保存配置",
                "passed",
                "已切换为 SSH Key 优先；已保存的密码仅在密钥被拒绝时本地回退",
            )
        )
        return {
            "ok": True,
            "code": "ssh_key_configured",
            "message": "SSH 免密登录已配置并验证",
            "stages": stages,
            "profile": self._desktop_profile(updated_profile),
        }

    def save_profile(
        self,
        raw: dict[str, Any],
        password_updates: dict[str, object] | None = None,
        renamed_server_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self._profile_mutation_lock.acquire(blocking=False):
            return {"ok": False, "error": "另一项设置正在保存，请稍后重试", "code": "profile_mutation_busy"}
        try:
            requested_revision = raw.get("profile_revision") if isinstance(raw, dict) else None
            if (
                not isinstance(requested_revision, int)
                or isinstance(requested_revision, bool)
                or requested_revision != self._profile_revision
            ):
                return {
                    "ok": False,
                    "error": "服务器配置版本缺失或已经变化，请重新打开设置",
                    "code": "profile_changed",
                    "profile": self._desktop_profile(self.profile),
                }
            return self._save_profile_locked(raw, password_updates, renamed_server_ids)
        finally:
            self._profile_mutation_lock.release()

    def _save_profile_locked(
        self,
        raw: dict[str, Any],
        password_updates: dict[str, object] | None = None,
        renamed_server_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        changed: list[tuple[str, str | None]] = []
        sync_warnings: list[str] = []

        def rollback_secrets() -> bool:
            restored = True
            for ref, previous in reversed(changed):
                try:
                    if previous is None:
                        self.secret_store.delete(ref)
                    else:
                        self.secret_store.set(ref, previous)
                except Exception:
                    restored = False
            return restored

        def failure_after_secret_rollback(error: str, code: str) -> dict[str, Any]:
            if rollback_secrets():
                return {"ok": False, "error": error, "code": code}
            return {
                "ok": False,
                "error": (
                    "配置未保存，但系统凭据回滚失败，登录密码状态可能已变化。"
                    "请立即在服务器设置中重新输入或删除该密码。"
                ),
                "code": "credential_rollback_failed",
                "recovery_required": True,
            }

        try:
            candidate = copy.deepcopy(raw)
            if not isinstance(candidate, dict):
                raise ConfigError("profile must be a table")
            # Older/current UI collectors may submit only editable connection
            # fields. Local usability preferences have their own endpoints and
            # must not disappear when that profile form is saved.
            persisted_profile = self.profile.to_dict()
            for preference in (
                "close_behavior",
                "ui_language",
                "favorite_server_ids",
                "favorite_alert_enabled",
                "favorite_alert_min_memory_gib",
                "task_completion_alert_enabled",
                "task_completion_watches",
                "saved_views",
            ):
                if preference not in candidate:
                    candidate[preference] = copy.deepcopy(persisted_profile[preference])
            updates = password_updates or {}
            if not isinstance(updates, dict):
                raise ConfigError("password updates must be a table")
            renames = renamed_server_ids or {}
            if not isinstance(renames, dict) or not all(
                isinstance(new_id, str) and isinstance(old_id, str)
                for new_id, old_id in renames.items()
            ):
                raise ConfigError("server renames must map new IDs to old IDs")
            old_by_id = {server.id: server for server in self.profile.servers}
            server_rows = candidate.get("servers", [])
            if not isinstance(server_rows, list):
                raise ConfigError("profile servers must be an array")
            candidate_ids = [row.get("id") for row in server_rows if isinstance(row, dict)]
            if any(server_id not in candidate_ids for server_id in updates):
                raise ConfigError("密码更新引用了不存在的服务器")
            if any(new_id not in candidate_ids for new_id in renames):
                raise ConfigError("服务器重命名引用了不存在的新 ID")
            if len(set(renames.values())) != len(renames):
                raise ConfigError("同一服务器不能同时重命名为多个 ID")
            for new_id, old_id in renames.items():
                if new_id == old_id or old_id not in old_by_id:
                    raise ConfigError("服务器重命名引用了不存在的旧 ID")
                if old_id in candidate_ids:
                    raise ConfigError("服务器重命名后不能继续保留旧 ID")

            # Persist deletion intent before applying automatic synchronization.
            # Otherwise a Host removed from the editor is immediately appended
            # again from the still-valid SSH Config (and again after restart).
            requested_ignored = candidate.get("ignored_ssh_aliases", [])
            if not isinstance(requested_ignored, (list, tuple)):
                raise ConfigError("profile ignored_ssh_aliases must be an array")
            ignored_by_key: dict[str, str] = {}
            for raw_alias in [
                *persisted_profile.get("ignored_ssh_aliases", []),
                *requested_ignored,
            ]:
                alias = require_optional_ssh_token(raw_alias, "ignored SSH alias")
                if not alias:
                    raise ConfigError("ignored SSH alias must be a non-empty SSH token")
                ignored_by_key.setdefault(alias.casefold(), alias)

            # An alias explicitly present in the submitted list is a deliberate
            # restore/manual re-add and therefore clears its old tombstone.
            active_alias_keys: set[str] = set()
            for row in server_rows:
                if not isinstance(row, dict):
                    continue
                alias = require_optional_ssh_token(
                    row.get("ssh_alias", ""),
                    "server ssh_alias",
                )
                if alias:
                    active_alias_keys.add(alias.casefold())
            for alias_key in active_alias_keys:
                ignored_by_key.pop(alias_key, None)
            if len(ignored_by_key) > MAX_IGNORED_SSH_ALIASES:
                raise ConfigError(
                    "profile ignored_ssh_aliases cannot contain more than "
                    f"{MAX_IGNORED_SSH_ALIASES} entries"
                )
            candidate["ignored_ssh_aliases"] = list(ignored_by_key.values())

            if renames:
                candidate["favorite_server_ids"] = [
                    next(
                        (new_id for new_id, old_id in renames.items() if old_id == favorite_id),
                        favorite_id,
                    )
                    for favorite_id in candidate.get("favorite_server_ids", [])
                ]
                candidate["task_completion_watches"] = [
                    {
                        **watch,
                        "server_id": next(
                            (
                                new_id
                                for new_id, old_id in renames.items()
                                if old_id == watch.get("server_id")
                            ),
                            watch.get("server_id"),
                        ),
                    }
                    for watch in candidate.get("task_completion_watches", [])
                    if isinstance(watch, dict)
                ]
            for row in server_rows:
                if not isinstance(row, dict):
                    continue
                server_id = row.get("id")
                row.pop("auth_ref", None)
                old_server = old_by_id.get(renames.get(server_id, server_id))
                if server_id in updates:
                    new_value = updates[server_id]
                    if new_value is not None:
                        self._validate_password(new_value)
                        row["auth_ref"] = self._auth_ref(self.profile.id, server_id)
                elif old_server is not None and old_server.auth_ref:
                    row["auth_ref"] = old_server.auth_ref
            sync_source: Path | None = None
            if candidate.get("auto_sync_servers"):
                source = resolve_server_config(candidate.get("server_config_path"))
                if source is None:
                    raise ConfigError("启用自动同步前必须选择有效的服务器设置文件")
                sync_source = source
                candidate["server_config_path"] = str(source)
            profile = Profile.from_dict(candidate, expected_id=self.profile.id)
            if sync_source is not None:
                # Apply the same conservative synchronization now that startup
                # would apply later. A successful save must never expose one
                # server set now and a different set after restart.
                sync_sources = _server_sync_sources(sync_source)
                if len(sync_sources) > 1:
                    # The selected catalog is authoritative. Secondary
                    # OpenSSH discovery may be skipped with a warning, but an
                    # invalid primary must fail this save rather than appear
                    # synchronized because a different source happened to work.
                    import_server_config(sync_source)
                profile, sync_warnings = profile_from_server_configs(profile, sync_sources)
                if len(sync_sources) > 1:
                    profile = replace(
                        profile,
                        server_config_path=str(sync_source),
                        auto_sync_servers=True,
                    )

            # A saved password belongs to the endpoint the user reviewed. Do
            # not silently carry it to a different Host/User/Port/config after
            # a manual edit or catalog synchronization. Supplying a password
            # in this same save explicitly binds it to the new endpoint.
            endpoint_changed_ids: list[str] = []
            rebound_servers = []
            for new_server in profile.servers:
                old_server = old_by_id.get(renames.get(new_server.id, new_server.id))
                endpoint_changed = bool(
                    old_server is not None
                    and old_server.auth_ref
                    and new_server.id not in updates
                    and _authentication_endpoint_identity(old_server)
                    != _authentication_endpoint_identity(new_server)
                )
                if endpoint_changed:
                    endpoint_changed_ids.append(new_server.id)
                    rebound_servers.append(replace(new_server, auth_ref=""))
                else:
                    rebound_servers.append(new_server)
            if endpoint_changed_ids:
                profile = replace(profile, servers=tuple(rebound_servers))
                # The settings UI presents the first warning in its compact
                # success toast.  Put credential invalidation first so a less
                # important catalog warning cannot hide a required user action.
                sync_warnings.insert(
                    0,
                    "连接地址或账号已变化；为避免向新端点发送旧密码，已移除 "
                    f"{len(endpoint_changed_ids)} 台服务器的已保存密码，请重新确认后输入"
                )

            secret_operations: dict[str, str | None] = {}
            for server_id, new_value in updates.items():
                old_server = old_by_id.get(renames.get(server_id, server_id))
                old_ref = old_server.auth_ref if old_server else ""
                new_server = next(server for server in profile.servers if server.id == server_id)
                ref = new_server.auth_ref or old_ref
                if not ref:
                    continue
                secret_operations[ref] = (
                    None if new_value is None else self._validate_password(new_value)
                )

            retained_refs = {server.auth_ref for server in profile.servers if server.auth_ref}
            for old_server in self.profile.servers:
                if old_server.auth_ref and old_server.auth_ref not in retained_refs:
                    secret_operations[old_server.auth_ref] = None

            try:
                # Delete obsolete references first. If the OS credential store
                # refuses a deletion, no replacement secret has been created.
                ordered_operations = sorted(
                    secret_operations.items(),
                    key=lambda item: item[1] is not None,
                )
                for ref, new_value in ordered_operations:
                    previous = self.secret_store.get(ref)
                    changed.append((ref, previous))
                    if new_value is None:
                        self.secret_store.delete(ref)
                    else:
                        self.secret_store.set(ref, new_value)
            except Exception as exc:
                raise RuntimeError("credential_store_unavailable") from exc

            old_profile = self.profile
            self.store.save(profile)
            try:
                self.service.replace_profile(profile, SnapshotCache(self.paths, profile.id))
                clear_notice = getattr(self.service, "clear_notice", None)
                if callable(clear_notice):
                    clear_notice("server_catalog_sync_failed")
            except Exception as exc:
                try:
                    self.store.save(old_profile)
                    self.service.replace_profile(old_profile, SnapshotCache(self.paths, old_profile.id))
                except Exception as rollback_exc:
                    raise RuntimeError("profile_rollback_failed") from rollback_exc
                raise RuntimeError("profile_commit_failed") from exc
            self.profile = profile
            self._profile_revision += 1
            self._reset_favorite_alerts_if_changed(old_profile, profile)
            self._reset_task_alerts_if_servers_changed(old_profile, profile)
            tray_controller = self._tray_controller
            if tray_controller is not None:
                tray_controller.refresh_menu()
            return {
                "ok": True,
                "profile": self._desktop_profile(profile),
                "warnings": sync_warnings,
            }
        except (ConfigError, OSError, ValueError) as exc:
            return failure_after_secret_rollback(str(exc), "profile_invalid")
        except RuntimeError as exc:
            if str(exc) == "credential_store_unavailable":
                return failure_after_secret_rollback(
                    "无法访问系统凭据存储，请检查钥匙串或凭据管理器",
                    "credential_store_unavailable",
                )
            if str(exc) == "profile_rollback_failed":
                result = failure_after_secret_rollback(
                    "配置提交失败，且原配置恢复未完成；请停止操作并重启应用检查配置",
                    "profile_rollback_failed",
                )
                result["recovery_required"] = True
                return result
            return failure_after_secret_rollback(
                "服务器配置未能完整保存，已恢复原配置",
                "profile_save_failed",
            )
        except Exception:
            return failure_after_secret_rollback(
                "服务器配置保存失败",
                "profile_save_failed",
            )

    def get_redacted_diagnostics(self, server_id: str | None = None) -> dict[str, Any]:
        """Return bounded support evidence without remote or local identities."""

        if server_id is not None and (not isinstance(server_id, str) or not server_id.strip()):
            return {"ok": False, "error": "诊断范围无效"}
        with self._profile_mutation_lock:
            profile = self.profile
        selected_servers = profile.servers
        if server_id is not None:
            selected_servers = tuple(server for server in profile.servers if server.id == server_id)
            if not selected_servers:
                return {"ok": False, "error": "服务器不存在"}

        known_states = (
            "connecting",
            "online",
            "stale",
            "offline",
            "disabled",
            "auth_required",
            "security_blocked",
            "misconfigured",
        )
        state_counts = {state: 0 for state in known_states}
        state_counts["other"] = 0
        total_gpus = 0
        free_vram_gib = 0.0
        selected_rows: dict[str, dict[str, Any]] = {}
        try:
            snapshot = self.service.snapshot()
            rows = snapshot.get("servers", []) if isinstance(snapshot, dict) else []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if server_id is not None and (
                    not isinstance(row, dict) or row.get("server_id") != server_id
                ):
                    continue
                if not isinstance(row, dict):
                    state_counts["other"] += 1
                    continue
                row_server_id = row.get("server_id")
                if isinstance(row_server_id, str):
                    selected_rows[row_server_id] = row
                connection = row.get("connection", {})
                state = connection.get("state") if isinstance(connection, dict) else None
                state_counts[state if state in state_counts else "other"] += 1
                try:
                    total_gpus += max(0, int(row.get("total_gpus") or 0))
                except (TypeError, ValueError):
                    pass
                try:
                    free_vram_gib += max(0.0, float(row.get("free_vram_gib") or 0))
                except (TypeError, ValueError):
                    pass
        except Exception:
            logging.getLogger("vram_radar").exception("could not build redacted diagnostics")
            state_counts["other"] += 1

        backend_counts = {
            backend: sum(server.backend == backend for server in selected_servers)
            for backend in ("direct_ssh", "slurm_ssh")
        }
        server_diagnostics: list[dict[str, Any]] = []
        default_ssh_config = Path.home() / ".ssh" / "config"
        for index, server in enumerate(selected_servers, start=1):
            row = selected_rows.get(server.id, {})
            connection = row.get("connection", {}) if isinstance(row, dict) else {}
            if not isinstance(connection, dict):
                connection = {}
            error = connection.get("error")
            if not isinstance(error, dict):
                error = {}
            config_path = (
                resolve_ssh_config_path(server)
                if server.ssh_config_file
                else default_ssh_config
            )
            try:
                row_total_gpus = max(0, int(row.get("total_gpus") or 0))
            except (TypeError, ValueError):
                row_total_gpus = 0
            try:
                row_free_vram_gib = round(max(0.0, float(row.get("free_vram_gib") or 0)), 2)
            except (TypeError, ValueError):
                row_free_vram_gib = 0.0
            server_diagnostics.append(
                {
                    "label": f"server_{index}",
                    "backend": server.backend,
                    "enabled": server.enabled,
                    "endpoint_mode": "openssh_alias" if server.ssh_alias else "direct_host",
                    "username_configured": bool(server.username),
                    "identity_file_configured": bool(server.identity_file),
                    "identity_file_present": (
                        _existing_file(resolve_identity_path(server)) if server.identity_file else None
                    ),
                    "ssh_config_mode": "explicit" if server.ssh_config_file else "default",
                    "ssh_config_present": _existing_file(config_path),
                    "saved_password_configured": bool(server.auth_ref),
                    "host_key_policy": "openssh_known_hosts",
                    "connect_timeout_seconds": server.connect_timeout_seconds,
                    "connection_state": connection.get("state") or "not_observed",
                    "data_origin": connection.get("data_origin") or "none",
                    "error_code": error.get("code"),
                    "error_retryable": error.get("retryable"),
                    "last_attempt_at": connection.get("last_attempt_at"),
                    "last_success_at": connection.get("last_success_at"),
                    "total_gpus": row_total_gpus,
                    "free_vram_gib": row_free_vram_gib,
                }
            )
        diagnostics = {
            "schema_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "server" if server_id is not None else "profile",
            "release": current_release_tag(),
            "build_commit": current_build_commit(),
            "platform": "windows" if sys.platform == "win32" else (
                "macos" if sys.platform == "darwin" else "other"
            ),
            "architecture": platform.machine().lower() or "unknown",
            "packaged": bool(getattr(sys, "frozen", False)),
            "profile_schema_version": profile.schema_version,
            "server_count": len(selected_servers),
            "enabled_server_count": sum(server.enabled for server in selected_servers),
            "backend_counts": backend_counts,
            "connection_state_counts": state_counts,
            "total_gpus": total_gpus,
            "free_vram_gib": round(free_vram_gib, 2),
            "favorite_count": sum(server.id in profile.favorite_server_ids for server in selected_servers),
            "saved_view_count": len(profile.saved_views),
            "close_behavior": profile.close_behavior,
            "catalog": {
                "configured": bool(profile.server_config_path),
                "source_present": (
                    _existing_file(profile.server_config_path)
                    if profile.server_config_path
                    else None
                ),
                "auto_sync": profile.auto_sync_servers,
            },
            "local_ssh": _ssh_support_diagnostics(tuple(selected_servers)),
            "recent_connection_errors": _recent_connection_error_diagnostics(
                Path(self.paths.logs) / "app.log",
                {server.id for server in selected_servers},
            ) if isinstance(self.paths, StoragePaths) else {
                "log_present": False,
                "event_count": 0,
                "by_code": {},
                "scanned_bytes": 0,
            },
            "servers": server_diagnostics,
        }
        return {
            "ok": True,
            "diagnostics": diagnostics,
            "text": json.dumps(diagnostics, ensure_ascii=False, indent=2),
        }

    def copy_redacted_diagnostics(self, server_id: str | None = None) -> dict[str, Any]:
        result = self.get_redacted_diagnostics(server_id)
        if result.get("ok") is False:
            return result
        text = str(result.get("text") or "")
        return {
            **result,
            "copied": _copy_text_to_system_clipboard(text),
        }

    def get_diagnostics(self) -> str:
        # Preserve the old bridge method while removing its former raw-snapshot
        # disclosure. New clients should use get_redacted_diagnostics().
        return str(self.get_redacted_diagnostics()["text"])

    def open_logs_directory(self) -> dict[str, Any]:
        try:
            logs_path = Path(self.paths.logs)
            logs_path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                startfile = getattr(os, "startfile", None)
                if startfile is None:
                    raise OSError("Windows file manager is unavailable")
                startfile(str(logs_path))
            elif sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(logs_path)],
                    close_fds=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", str(logs_path)],
                    close_fds=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return {"ok": True, "message": "已打开日志文件夹"}
        except (OSError, TypeError, ValueError) as exc:
            logging.getLogger("vram_radar").warning("could not open logs directory: %s", exc)
            return {
                "ok": False,
                "error": "无法打开日志文件夹",
                "code": "open_logs_failed",
            }

    def check_for_updates(self) -> dict[str, Any]:
        result = check_latest_release()
        self.latest_release_url = (
            str(result["release_url"])
            if result.get("ok") and result.get("update_available") and result.get("release_url")
            else None
        )
        self._latest_release = dict(result) if result.get("ok") and result.get("update_available") else None
        asset = result.get("asset")
        if not isinstance(asset, dict):
            result["update_action"] = "browser"
        elif sys.platform == "win32":
            capable, reason = windows_update_capability()
            result["update_action"] = "one_click" if capable else "browser"
            if reason:
                result["update_action_reason"] = reason
        elif sys.platform == "darwin":
            result["update_action"] = "verified_download"
        else:
            result["update_action"] = "browser"
        return result

    def install_latest_update(self) -> dict[str, Any]:
        # Re-read authoritative GitHub metadata at the action boundary. The UI
        # cache is presentation state and must never authorize code execution.
        result = check_latest_release()
        if not result.get("ok") or not result.get("update_available"):
            return {"ok": False, "error": result.get("error") or "当前没有可安装的新版本"}
        asset = result.get("asset")
        release_url = result.get("release_url")
        if not isinstance(asset, dict):
            if isinstance(release_url, str):
                try:
                    webbrowser.open(release_url)
                except (OSError, webbrowser.Error):
                    pass
            return {"ok": False, "error": "Release 缺少可验证的安装资产，已打开下载页面"}
        try:
            installer = download_verified_asset(asset, Path(self.paths.cache) / "updates")
            if sys.platform == "win32":
                capable, reason = windows_update_capability()
                if not capable:
                    if isinstance(release_url, str):
                        webbrowser.open(release_url)
                    return {"ok": False, "error": reason or "当前版本不支持一键更新"}
                schedule_windows_update(
                    installer,
                    sha256=str(asset["sha256"]),
                    version=str(result["latest_version"]),
                    activation_path=Path(self.paths.runtime) / f"{self.profile.id}.activation.json",
                    restart_arguments=self._restart_arguments,
                )
                return {"ok": True, "scheduled": True, "message": "更新已校验，应用即将关闭并自动重启"}
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", "-R", str(installer)],
                    close_fds=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {
                    "ok": True,
                    "scheduled": False,
                    "message": "更新包已通过 SHA-256 校验，并已在 Finder 中显示",
                }
            return {"ok": False, "error": "当前平台暂不支持应用内安装"}
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            logging.getLogger("vram_radar").warning("safe update failed: %s", exc)
            return {"ok": False, "error": str(exc) or "更新失败，当前版本未被修改"}

    def open_latest_release(self) -> dict[str, Any]:
        if self.latest_release_url is None:
            return {"ok": False, "error": "尚未检测到可下载的新版本"}
        try:
            return {"ok": bool(webbrowser.open(self.latest_release_url))}
        except (OSError, webbrowser.Error):
            return {"ok": False, "error": "无法打开 GitHub Release 页面"}

    def discover_server_config(self) -> dict[str, Any]:
        if not self._automatic_import_enabled:
            return {
                "ok": False,
                "code": "automatic_import_disabled",
                "path": "",
                "paths": [],
                "message": (
                    "本次运行已通过 --no-auto-import 禁用本机服务器配置自动发现；"
                    "如需导入，请手动输入明确的 SSH Config 或 servers.toml 路径"
                ),
            }
        sources = resolve_server_configs(include_openssh=True)
        if not sources:
            return {
                "ok": False,
                "path": "",
                "paths": [],
                "message": "未发现 servers.toml 或 OpenSSH config；请按下方教程查找后手动输入路径",
            }
        return {
            "ok": True,
            "path": str(sources[0]),
            "paths": [str(source) for source in sources],
            "message": f"已发现 {len(sources)} 个服务器设置来源",
        }

    def import_server_config(self, path: str | list[str] = "") -> dict[str, Any]:
        if (
            not self._automatic_import_enabled
            and isinstance(path, str)
            and not path.strip()
        ):
            return {
                "ok": False,
                "error": (
                    "本次运行已通过 --no-auto-import 禁用默认配置查找；"
                    "请手动输入明确的 SSH Config 或 servers.toml 路径"
                ),
                "code": "automatic_import_disabled",
            }
        if not self._profile_mutation_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "另一项设置正在保存，请稍后再导入服务器",
                "code": "profile_mutation_busy",
            }
        try:
            return self._import_server_config_locked(path)
        finally:
            self._profile_mutation_lock.release()

    def _import_server_config_locked(self, path: str | list[str] = "") -> dict[str, Any]:
        try:
            if isinstance(path, list):
                if not path or not all(isinstance(value, str) and value.strip() for value in path):
                    raise ConfigError("没有可导入的服务器设置文件")
                sources = [resolve_server_config(value) for value in path]
                synchronized, warnings = profile_from_server_configs(
                    self.profile,
                    [source for source in sources if source is not None],
                )
                return {
                    "ok": True,
                    "path": "",
                    "paths": [str(source) for source in sources],
                    "auto_sync": False,
                    "persisted": False,
                    "validated": False,
                    "servers": self._desktop_profile(synchronized)["servers"],
                    "warnings": warnings,
                }
            source = resolve_server_config(path or None)
            if source is None:
                raise ConfigError("未发现默认 servers.toml，可手动输入文件地址")
            synchronized, warnings = profile_from_server_config(self.profile, source)
            return {
                "ok": True,
                "path": str(source),
                "paths": [str(source)],
                "auto_sync": True,
                "persisted": False,
                "validated": False,
                "servers": self._desktop_profile(synchronized)["servers"],
                "warnings": warnings,
            }
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

def _server_sync_sources(primary: Path | None) -> list[Path]:
    """Return the exact source set shared by save-time and startup synchronization."""

    if primary is None:
        return resolve_server_configs(include_openssh=True)
    if primary.suffix.casefold() != ".toml":
        return [primary]
    discovered = resolve_server_configs(include_openssh=True)
    return [
        primary,
        *[
            source
            for source in discovered
            if source != primary and source.suffix.casefold() != ".toml"
        ],
    ]


def _recover_matching_openssh_source(
    profile: Profile,
    sources: list[Path],
) -> tuple[Profile, list[str], Path] | None:
    """Recover a legacy/broken catalog only when one SSH source covers it all."""

    active_aliases = {
        server.ssh_alias.casefold()
        for server in profile.servers
        if server.ssh_alias
    }
    if not active_aliases:
        return None
    for source in sources:
        if source.suffix.casefold() == ".toml":
            continue
        try:
            imported, _warnings = import_server_config(source)
        except (ConfigError, OSError):
            continue
        imported_aliases = {
            server.ssh_alias.casefold()
            for server in imported
            if server.ssh_alias
        }
        if not active_aliases.issubset(imported_aliases):
            continue
        synchronized, warnings = profile_from_server_config(profile, source)
        return synchronized, warnings, source
    return None


def build_runtime(
    profile_id: str,
    home: Path | None,
    *,
    servers_config: Path | None = None,
    automatic_import_enabled: bool = True,
) -> tuple[StoragePaths, ProfileStore, Profile, DashboardService]:
    paths = storage_paths(home)
    logger = configure_logging(paths)
    store = ProfileStore(paths)
    profile = store.load(profile_id)
    explicit_source = servers_config is not None
    sources: list[Path] = []
    primary_source: Path | None = None
    startup_notices: list[dict[str, str]] = []

    try:
        if explicit_source:
            primary_source = resolve_server_config(servers_config)
            sources = _server_sync_sources(primary_source)
        elif automatic_import_enabled and profile.auto_sync_servers and profile.server_config_path:
            primary_source = resolve_server_config(profile.server_config_path)
            sources = _server_sync_sources(primary_source)
        if sources:
            if primary_source is not None and len(sources) > 1:
                import_server_config(primary_source)
            synchronized, warnings = profile_from_server_configs(profile, sources)
            if primary_source is not None and len(sources) > 1:
                # Keep catalog auto-sync while retaining the local OpenSSH
                # source attached during this first multi-source import.
                synchronized = replace(
                    synchronized,
                    server_config_path=str(primary_source),
                    auto_sync_servers=True,
                )
            if synchronized != profile:
                store.save(synchronized)
                profile = synchronized
            for warning in warnings:
                logger.warning("server catalog import: %s", warning)
    except (ConfigError, OSError) as exc:
        if explicit_source:
            raise
        logger.warning("server catalog auto-sync failed: %s", exc)
        recovery_sources = sources
        if not recovery_sources:
            try:
                recovery_sources = resolve_server_configs(include_openssh=True)
            except (ConfigError, OSError):
                recovery_sources = []
        recovered = _recover_matching_openssh_source(profile, recovery_sources)
        if recovered is not None:
            synchronized, warnings, recovered_source = recovered
            store.save(synchronized)
            profile = synchronized
            for warning in warnings:
                logger.warning("server catalog recovery: %s", warning)
            startup_notices.append(
                {
                    "code": "server_catalog_sync_recovered",
                    "severity": "warning",
                    "message": (
                        "原服务器同步文件已失效；已自动改用能够覆盖当前全部服务器的 "
                        f"OpenSSH 配置：{recovered_source}。"
                    ),
                }
            )
        else:
            startup_notices.append(
                {
                    "code": "server_catalog_sync_failed",
                    "severity": "error",
                    "message": (
                        "服务器自动同步失败，当前列表未按本地配置更新："
                        f"{exc}。请在设置中检查服务器配置文件路径和内容。"
                    ),
                }
            )
    secret_store = SecretStore()
    service = DashboardService(
        profile,
        SnapshotCache(paths, profile.id),
        logger=logger,
        secret_store=secret_store,
        startup_notices=startup_notices,
    )
    return paths, store, profile, service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显存雷达：本地优先的 SSH/Slurm GPU 显存桌面监控")
    parser.add_argument("--profile", default="default", help="local Profile ID")
    parser.add_argument("--home", type=Path, help="override per-user storage root (development and portable testing)")
    parser.add_argument("--servers-config", type=Path, help="import and auto-sync a servers.toml from this address")
    parser.add_argument(
        "--no-auto-import",
        action="store_true",
        help=(
            "disable local server-config discovery and stored startup auto-sync; "
            "an explicit --servers-config path remains available"
        ),
    )
    parser.add_argument("--once", action="store_true", help="print one live JSON snapshot without starting the GUI")
    parser.add_argument("--show-paths", action="store_true", help="print resolved per-user storage paths and exit")
    parser.add_argument("--show-release", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="enable the embedded webview developer tools")
    parser.add_argument("--gui-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--quit-existing", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.show_release:
        print(current_release_tag())
        return 0

    if args.quit_existing:
        try:
            profile = Profile.empty(args.profile)
        except ConfigError as exc:
            parser.error(str(exc))
        activation_path = storage_paths(args.home).runtime / f"{profile.id}.activation.json"
        existing_pid = _request_existing_instance_process(
            activation_path,
            action="exit",
            timeout_seconds=1,
        )
        if existing_pid is None:
            return 0
        return 0 if _wait_for_process_exit(existing_pid) else 1

    try:
        requested_profile = Profile.empty(args.profile)
    except ConfigError as exc:
        parser.error(str(exc))
    preliminary_paths = storage_paths(args.home)
    activation_path = preliminary_paths.runtime / f"{requested_profile.id}.activation.json"
    instance_lock = InstanceLock(preliminary_paths.runtime / f"{requested_profile.id}.lock")
    try:
        instance_lock.__enter__()
    except InstanceLockUnavailable as exc:
        parser.error(str(exc))
    except InstanceAlreadyRunning:
        if not request_existing_instance(activation_path):
            logging.getLogger("vram_radar").warning(
                "existing instance is still starting or could not be activated"
            )
        return 0

    try:
        paths, store, profile, service = build_runtime(
            args.profile,
            args.home,
            servers_config=args.servers_config,
            automatic_import_enabled=not args.no_auto_import,
        )
    except (ConfigError, OSError) as exc:
        instance_lock.__exit__(None, None, None)
        parser.error(str(exc))

    if args.show_paths:
        print(json.dumps({key: str(getattr(paths, key)) for key in ("config", "cache", "logs", "runtime")}, indent=2))
        instance_lock.__exit__(None, None, None)
        return 0
    if args.once:
        print(json.dumps(service.refresh(force=True), ensure_ascii=False, indent=2))
        instance_lock.__exit__(None, None, None)
        return 0

    try:
        import webview
    except ImportError as exc:
        instance_lock.__exit__(None, None, None)
        parser.error("pywebview is not installed; run the maintained uv environment or install the packaged App")
        raise AssertionError from exc

    api = AppApi(
        profile,
        store,
        paths,
        service,
        automatic_import_enabled=not args.no_auto_import,
        restart_arguments=[
            "--profile",
            profile.id,
            *(["--home", str(args.home.resolve())] if args.home is not None else []),
            *(["--no-auto-import"] if args.no_auto_import else []),
        ],
    )
    index_path = resource_path("web/index.html")
    icon_path = resource_path("assets/app-icon.png")
    activation_path = paths.runtime / f"{profile.id}.activation.json"
    try:
        with instance_lock:
            activation_requested = threading.Event()
            exit_requested = threading.Event()
            activation_stopped = threading.Event()
            window: Any = None
            with ActivationServer(
                activation_path,
                activation_requested.set,
                exit_requested.set,
                on_probe=lambda: window_frontend_is_ready(window),
            ):
                state_store = WindowStateStore(paths)
                window_state = WindowStateController(
                    state_store,
                    normal_state=lambda: native_window_is_normal(window),
                    # AppKit NSWindow state must only be queried from the UI
                    # event path.  The debounce callback runs on a
                    # ``threading.Timer`` worker, so macOS trusts the normal
                    # state already validated by ``on_resized`` plus explicit
                    # minimized/maximized suspension events.  Windows keeps
                    # the second probe to defeat WinForms event reordering.
                    recheck_normal_state_on_commit=sys.platform != "darwin",
                )
                initial_geometry = window_state.geometry
                window = webview.create_window(
                    "VRAM Radar",
                    url=index_path.as_uri(),
                    js_api=api,
                    width=initial_geometry.width,
                    height=initial_geometry.height,
                    min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
                    background_color="#071017",
                )
                window_state.attach(window)
                # ``before_show`` is a synchronous pywebview lifecycle event;
                # WinForms caption properties must be set on its native UI
                # thread rather than from the asynchronous ``shown`` event.
                window.events.before_show += lambda: configure_windows_native_chrome(window)
                shutdown = WindowShutdownCoordinator(
                    window,
                    activation_requested,
                    activation_stopped,
                    preferred_geometry=lambda: window_state.geometry,
                    before_destroy=window_state.close,
                )
                worker = threading.Thread(
                    target=activation_worker,
                    args=(
                        window,
                        activation_requested,
                        exit_requested,
                        activation_stopped,
                        shutdown.request,
                        shutdown.restore,
                    ),
                    name="vram-radar-window-activation",
                    daemon=True,
                )
                shutdown.bind_worker(worker)
                tray_controller: WindowsTrayController | None = None
                macos_notification_bound = False
                if sys.platform == "win32" and not args.gui_smoke:
                    candidate: WindowsTrayController | None = None

                    def notification_status() -> str:
                        snapshot = service.snapshot()
                        summary = snapshot.get("summary", {})
                        if service.is_paused():
                            return (
                                "Automatic monitoring paused"
                                if api.profile.ui_language == "en"
                                else "自动监控已暂停"
                            )
                        online = int(summary.get("online_servers") or 0)
                        total = int(summary.get("total_servers") or 0)
                        total_gpus = int(summary.get("total_gpus") or 0)
                        if api.profile.ui_language == "en":
                            return f"{online}/{total} online · {total_gpus} GPUs"
                        return f"{online}/{total} 台在线 · {total_gpus} 张 GPU"

                    def toggle_monitoring() -> None:
                        if service.is_paused():
                            service.resume()
                            service.request_refresh(force=True)
                        else:
                            service.pause()
                        if candidate is not None:
                            candidate.refresh_menu()

                    def show_settings() -> None:
                        shutdown.restore(
                            lambda: window.evaluate_js(
                                "document.getElementById('settings-button')?.click()"
                            )
                        )

                    candidate = WindowsTrayController(
                        window,
                        icon_path,
                        refresh_application=lambda: service.request_refresh(force=True),
                        open_settings=show_settings,
                        toggle_pause=toggle_monitoring,
                        is_paused=service.is_paused,
                        status_text=notification_status,
                        language=lambda: api.profile.ui_language,
                        close_behavior=lambda: (
                            "exit" if api.profile.close_behavior == "exit" else "hide"
                        ),
                        before_exit=shutdown.request,
                        restore_application=shutdown.restore,
                        hide_application=shutdown.hide,
                    )
                    try:
                        candidate.start()
                        tray_controller = candidate
                        shutdown.bind_tray_controller(candidate)
                        api.bind_tray_controller(candidate)
                        api.bind_notification_callback(candidate.notify)
                    except Exception:
                        logging.getLogger("vram_radar").exception("failed to start the Windows notification icon")
                elif sys.platform == "darwin" and not args.gui_smoke:
                    api.bind_notification_callback(show_macos_notification)
                    macos_notification_bound = True
                non_tray_closing_handler: Callable[[], bool] | None = None
                if tray_controller is None:
                    non_tray_closing_handler = shutdown.on_closing
                    window.events.closing += non_tray_closing_handler
                worker.start()
                smoke_result: dict[str, Any] = {}
                smoke_worker: threading.Thread | None = None
                if args.gui_smoke:
                    smoke_worker = threading.Thread(
                        target=window_smoke_worker,
                        args=(window, smoke_result, 20.0, shutdown.request),
                        name="vram-radar-gui-smoke",
                        daemon=True,
                    )
                    smoke_worker.start()
                start_options = webview_start_options(args.debug, icon_path)
                try:
                    webview.start(**start_options)
                finally:
                    shutdown.request()
                    if not shutdown.wait(timeout=15):
                        logging.getLogger("vram_radar").error(
                            "desktop shutdown did not finish within 15 seconds"
                        )
                    if tray_controller is not None:
                        api.bind_notification_callback(None)
                        api.bind_tray_controller(None)
                        tray_controller.stop()
                    else:
                        if macos_notification_bound:
                            api.bind_notification_callback(None)
                        if non_tray_closing_handler is not None:
                            try:
                                window.events.closing -= non_tray_closing_handler
                            except (ValueError, AttributeError):
                                pass
                    if smoke_worker is not None:
                        smoke_worker.join(timeout=1)
                if args.gui_smoke and not smoke_result.get("shown"):
                    logging.getLogger("vram_radar").error(
                        "%s", smoke_result.get("error") or "desktop window smoke failed"
                    )
                    return 1
    except InstanceAlreadyRunning:
        if not request_existing_instance(activation_path):
            logging.getLogger("vram_radar").warning("existing instance is still starting or could not be activated")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
