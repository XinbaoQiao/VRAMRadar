from __future__ import annotations

import csv
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import posixpath
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Any

from .askpass import PasswordBroker
from .models import ServerProfile
from .openssh_resolution import OpenSSHEndpointResolution, resolve_openssh_endpoint


SPLIT_MARKER = "__VRAM_RADAR_SPLIT__"
LIVE_TASK_MARKER = "__VRAM_RADAR_LIVE_TASKS__"
HISTORY_MARKER = "__VRAM_RADAR_TASK_HISTORY__"
HISTORY_SUPPORT_MARKER = "__VRAM_RADAR_HISTORY_SUPPORTED__="
CURRENT_USER_MARKER = "__VRAM_RADAR_CURRENT_USER__="
HOME_DIRECTORY_MARKER = "__VRAM_RADAR_HOME_HEX__="
HOST_MARKER = "__VRAM_RADAR_HOST__="
DIRECT_PROTOCOL_HEADER = "VRAM_RADAR_DIRECT|1"
DIRECT_PROTOCOL_END = "END|1"
DIRECT_METADATA_LIMIT = 128
# A single refresh must never be allowed to monopolize desktop memory. Eight
# MiB still leaves ample room for a 1,000-node / multi-thousand-GPU snapshot,
# while the global slot guard below caps the aggregate raw capture budget at
# roughly 64 MiB even when callers initiate queries outside DashboardService.
MAX_REMOTE_STDOUT_BYTES = 8 * 1024 * 1024
MAX_REMOTE_STDERR_BYTES = 64 * 1024
MAX_REMOTE_STDIN_BYTES = 16 * 1024
MAX_CONCURRENT_REMOTE_CAPTURES = 8
MAX_REMOTE_CAPTURE_BUDGET_BYTES = MAX_CONCURRENT_REMOTE_CAPTURES * (
    MAX_REMOTE_STDOUT_BYTES + MAX_REMOTE_STDERR_BYTES
)
REMOTE_READ_CHUNK_BYTES = 64 * 1024
SLURM_NODE_EXPANSION_LIMIT = 65_536
DIRECTORY_PROTOCOL_HEADER = "VRAM_RADAR_DIRECTORY|1"
DIRECTORY_PROTOCOL_END = "END|1"
DIRECTORY_VERSION_PROTOCOL_HEADER = "VRAM_RADAR_DIRECTORY_VERSION|1"
DIRECTORY_VERSION_PROTOCOL_END = "END|1"
DIRECTORY_ENTRY_LIMIT = 160
DIRECTORY_MAX_DEPTH = 1
MAX_CONCURRENT_DIRECTORY_QUERIES = 2
# Authentication failures for which a user-saved password is a safe, bounded
# fallback after the non-interactive key/agent path has been tried once. Keep
# this policy shared by ordinary collection and SSH Key bootstrap so the two
# paths cannot drift again.
PASSWORD_FALLBACK_AUTH_CODES = frozenset(
    {"auth_failed", "identity_passphrase_required", "ssh_agent_refused"}
)
GPU_GRES_RE = re.compile(r"(?:^|,)gpu(?::([^:,()]+))?:(\d+)")
JOB_GPU_RE = re.compile(r"(?:gres/)?gpu(?::[^:,()=]+)?[:=](\d+)")
UNAVAILABLE_STATES = (
    "down", "drain", "drng", "fail", "maint", "unknown", "unk", "inval", "reboot", "noresp"
)
SENSITIVE_NAME = r"[\w-]*(?:token|password|passwd|secret|api[-_]?key|access[-_]?key|private[-_]?key|credential|auth)[\w-]*"
SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"(?i)(\b{SENSITIVE_NAME}=)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
SENSITIVE_FLAG_EQUALS_RE = re.compile(
    rf"(?i)((?:^|\s)--?{SENSITIVE_NAME}=)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
SENSITIVE_FLAG_VALUE_RE = re.compile(
    rf"(?i)((?:^|\s)--?{SENSITIVE_NAME}\s+)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
URL_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?@")
AUTHORIZATION_RE = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)(?:\"[^\"]*\"|'[^']*'|\S+)")
SENSITIVE_HEADER_RE = re.compile(
    rf"(?i)((?:x-)?{SENSITIVE_NAME}\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
BEARER_RE = re.compile(r"(?i)(\bbearer\s+)(?:\"[^\"]*\"|'[^']*'|\S+)")
SENSITIVE_PAIR_RE = re.compile(
    rf"(?i)((?:^|\s)(?:--env|-e|--set)\s+{SENSITIVE_NAME}\s+)(?:\"[^\"]*\"|'[^']*'|\S+)"
)


class ConnectorFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool, state: str = "offline") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.state = state


class _BoundedProcessResult:
    __slots__ = ("returncode", "stdout", "stderr", "stdout_truncated")

    def __init__(
        self,
        returncode: int,
        stdout: bytes | bytearray,
        stderr: bytes | bytearray,
        stdout_truncated: bool,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_truncated = stdout_truncated


_REMOTE_CAPTURE_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REMOTE_CAPTURES)
_DIRECTORY_QUERY_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_DIRECTORY_QUERIES)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Best-effort termination for ssh and any ProxyCommand descendants."""

    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def classify_process_error(
    detail: str,
    *,
    returncode: int = 255,
    password_auth: bool = False,
) -> ConnectorFailure:
    lower = detail.lower()
    if "vram_radar_key_conflict" in lower:
        return ConnectorFailure(
            "ssh_key_remote_conflict",
            "authorized_keys 在配置期间被其他程序修改；为避免覆盖，已停止本次操作，请重试",
            retryable=True,
            state="misconfigured",
        )
    key_setup_errors = {
        "vram_radar_key_invalid": (
            "ssh_key_invalid",
            "选择的 SSH 公钥格式无效",
        ),
        "vram_radar_key_unsafe_home": (
            "ssh_key_remote_home_unsafe",
            "服务器主目录不安全，已停止修改 authorized_keys",
        ),
        "vram_radar_key_unsafe_ssh_dir": (
            "ssh_key_remote_permissions",
            "服务器的 .ssh 目录所有者或权限不安全，或是符号链接",
        ),
        "vram_radar_key_unsafe_authorized_keys": (
            "ssh_key_remote_permissions",
            "服务器的 authorized_keys 所有者或权限不安全、不是普通文件，或是符号链接",
        ),
        "vram_radar_key_write_failed": (
            "ssh_key_remote_write_failed",
            "服务器拒绝写入 SSH 公钥，请检查主目录权限或联系管理员",
        ),
    }
    for marker, (code, message) in key_setup_errors.items():
        if marker in lower:
            return ConnectorFailure(code, message, retryable=False, state="misconfigured")
    if "can't open user config file" in lower or "cannot open user config file" in lower:
        return ConnectorFailure(
            "ssh_config_missing",
            "OpenSSH 配置文件不存在或不可读取",
            retryable=False,
            state="misconfigured",
        )
    if (
        "bad configuration option" in lower
        or "bad owner or permissions on" in lower and "config" in lower
        or "terminating, " in lower and "bad configuration" in lower
        or "no argument after keyword" in lower
    ):
        return ConnectorFailure(
            "ssh_config_invalid",
            "OpenSSH 配置格式或权限无效，请先在终端检查该配置",
            retryable=False,
            state="misconfigured",
        )
    if "identity file" in lower and ("not accessible" in lower or "no such file" in lower):
        return ConnectorFailure(
            "identity_file_missing",
            "SSH 私钥文件不存在或不可读取",
            retryable=False,
            state="misconfigured",
        )
    if (
        "unprotected private key file" in lower
        or "bad permissions" in lower and ("identity" in lower or "private key" in lower)
        or "permissions " in lower and "private key" in lower and "too open" in lower
    ):
        return ConnectorFailure(
            "identity_file_permissions",
            "SSH 私钥权限过宽，请限制为仅当前用户可读",
            retryable=False,
            state="misconfigured",
        )
    if "load key" in lower and ("invalid format" in lower or "error in libcrypto" in lower):
        return ConnectorFailure(
            "identity_file_invalid",
            "SSH 私钥格式无效或无法由当前 OpenSSH 读取",
            retryable=False,
            state="misconfigured",
        )
    if "load key" in lower and (
        "incorrect passphrase" in lower
        or "passphrase required" in lower
        or "encrypted private key" in lower
    ):
        return ConnectorFailure(
            "identity_passphrase_required",
            "SSH 私钥需要口令，请先在系统 ssh-agent/钥匙串中解锁该密钥",
            retryable=False,
            state="auth_required",
        )
    if "load key" in lower and ("permission denied" in lower or "operation not permitted" in lower):
        return ConnectorFailure(
            "identity_file_permissions",
            "当前用户无权读取 SSH 私钥，请检查文件所有者和权限",
            retryable=False,
            state="misconfigured",
        )
    if "sign_and_send_pubkey" in lower and "agent refused operation" in lower:
        return ConnectorFailure(
            "ssh_agent_refused",
            "ssh-agent 拒绝使用该密钥，请重新解锁或重新加入密钥",
            retryable=False,
            state="auth_required",
        )
    if "too many authentication failures" in lower:
        return ConnectorFailure(
            "auth_failed",
            "SSH 尝试了过多身份；请指定正确私钥或清理 ssh-agent 中无关密钥",
            retryable=False,
            state="auth_required",
        )
    if "could not resolve hostname" in lower or "name or service not known" in lower:
        return ConnectorFailure("dns_failed", "无法解析服务器地址", retryable=True)
    if "hostname contains invalid characters" in lower or "invalid hostname" in lower:
        return ConnectorFailure(
            "hostname_invalid", "服务器地址格式无效", retryable=False, state="misconfigured"
        )
    if "connection timed out" in lower or "operation timed out" in lower:
        return ConnectorFailure("ssh_timeout", "服务器连接超时", retryable=True)
    if "connection refused" in lower:
        return ConnectorFailure("ssh_refused", "服务器拒绝 SSH 连接", retryable=True)
    if "no route to host" in lower or "network is unreachable" in lower:
        return ConnectorFailure("network_unreachable", "当前网络无法到达服务器", retryable=True)
    if "connection reset" in lower or "connection aborted" in lower:
        return ConnectorFailure("ssh_connection_reset", "SSH 连接被中途重置", retryable=True)
    if "remote host identification has changed" in lower:
        return ConnectorFailure(
            "host_key_changed", "服务器 Host Key 已变化，请人工核对指纹", retryable=False, state="security_blocked"
        )
    if "host key verification failed" in lower:
        return ConnectorFailure(
            "host_key_untrusted",
            "服务器 Host Key 无法自动保存或验证，请检查 known_hosts 权限与 SSH 配置",
            retryable=False,
            state="security_blocked",
        )
    if "invalid -j argument" in lower or "jumphost loop via" in lower:
        return ConnectorFailure(
            "proxy_config_invalid",
            "ProxyJump 配置无效，请检查跳板别名和循环引用",
            retryable=False,
            state="misconfigured",
        )
    if (
        "stdio forwarding failed" in lower
        or "proxycommand" in lower and "exec" in lower
        or "connection closed by unknown port 65535" in lower
    ):
        return ConnectorFailure(
            "proxy_failed", "SSH 跳板或代理转发失败，请先单独验证跳板连接", retryable=True
        )
    auth_denied = bool(
        re.search(
            r"^(?:[^\n]*@[^\n]*:\s*)?permission denied"
            r"(?:,\s*please try again|\s+\([^\n()]+\))?\.?\s*$",
            lower,
            flags=re.MULTILINE,
        )
        or re.search(r"^authentication failed\.?\s*$", lower, flags=re.MULTILINE)
    )
    if returncode == 255 and auth_denied:
        method = "保存的服务器密码" if password_auth else "SSH Key、ssh-agent 或用户名"
        return ConnectorFailure(
            "auth_failed",
            f"SSH 身份验证失败，请检查{method}",
            retryable=False,
            state="auth_required",
        )
    if "command not found" in lower or "not recognized as an internal" in lower:
        if returncode == 255:
            return ConnectorFailure(
                "proxy_command_missing",
                "本机 SSH ProxyCommand 缺少所需命令，请检查跳板或代理配置",
                retryable=False,
                state="misconfigured",
            )
        return ConnectorFailure(
            "command_missing",
            "SSH 已连接并通过认证，但当前服务器类型缺少所需命令；集群登录节点请检查是否应选择 Slurm",
            retryable=False,
            state="misconfigured",
        )
    if returncode != 255 and "permission denied" in lower:
        return ConnectorFailure(
            "remote_permission_denied",
            "SSH 已连接，但当前账号无权执行资源检测命令",
            retryable=False,
            state="misconfigured",
        )
    if returncode != 255:
        return ConnectorFailure(
            "remote_command_failed",
            "SSH 已连接，但服务器环境检测命令执行失败",
            retryable=False,
            state="misconfigured",
        )
    return ConnectorFailure("ssh_failed", "SSH 查询失败", retryable=True)


def _expand_profile_path(value: str, *, default_directory: Path) -> str:
    raw = value.strip()
    local_home = (
        os.environ.get("HOME")
        or os.environ.get("USERPROFILE")
        or str(Path.home())
    )
    raw = re.sub(r"\$(?:\{HOME\}|HOME\b)", lambda _match: local_home, raw, flags=re.IGNORECASE)
    raw = re.sub(
        r"\$env:(HOME|USERPROFILE)\b",
        lambda match: os.environ.get(match.group(1).upper()) or local_home,
        raw,
        flags=re.IGNORECASE,
    )
    raw = re.sub(
        r"%(HOME|USERPROFILE)%",
        lambda match: os.environ.get(match.group(1).upper()) or local_home,
        raw,
        flags=re.IGNORECASE,
    )
    expanded = os.path.expandvars(os.path.expanduser(raw))
    expanded = re.sub(
        r"%([A-Za-z_][A-Za-z0-9_]*)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        expanded,
    )
    expanded = re.sub(
        r"\$env:([A-Za-z_][A-Za-z0-9_]*)",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        expanded,
        flags=re.IGNORECASE,
    )
    # Preserve an absolute path written for another supported platform. If a
    # Profile is moved from macOS to Windows (or the reverse), preflight should
    # report that exact path as missing instead of silently rebasing it under
    # the current user's .ssh directory.
    candidate = Path(expanded)
    if candidate.is_absolute():
        try:
            return str(candidate.resolve(strict=False))
        except (OSError, RuntimeError):
            return str(candidate)
    if expanded.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", expanded):
        return expanded
    if not candidate.is_absolute():
        candidate = default_directory / candidate
    try:
        return str(candidate.resolve(strict=False))
    except (OSError, RuntimeError):
        return str(candidate)


def resolve_ssh_config_path(server: ServerProfile) -> str:
    return _expand_profile_path(server.ssh_config_file, default_directory=Path.home() / ".ssh")


def _effective_ssh_config_path(server: ServerProfile) -> str:
    """Return the explicit or default OpenSSH config used for alias connections.

    OpenSSH normally discovers ``~/.ssh/config`` itself.  That implicit lookup
    is not reliable for desktop children launched from every Windows shell and
    trust context, especially when ``.ssh`` is a junction.  Supplying ``-F``
    keeps monitoring, connection tests, and copied commands on the same config
    file without changing any of OpenSSH's Host/Include/ProxyJump semantics.
    """

    if server.ssh_config_file:
        return resolve_ssh_config_path(server)
    if not server.ssh_alias:
        return ""
    default_config = Path.home() / ".ssh" / "config"
    try:
        return str(default_config) if default_config.is_file() else ""
    except OSError:
        return ""


def resolve_identity_path(server: ServerProfile) -> str:
    return _expand_profile_path(server.identity_file, default_directory=Path.home() / ".ssh")


def ssh_login_argv(
    server: ServerProfile,
    *,
    identities_only: bool | None = None,
) -> list[str]:
    """Build the interactive command that reaches the same endpoint as monitoring."""

    target = server.ssh_alias or (
        f"{server.username}@{server.host}" if server.username else server.host
    )
    options: list[str] = []
    config_path = _effective_ssh_config_path(server)
    if config_path:
        options.extend(["-F", config_path])
    if server.ssh_alias and server.host:
        options.extend(["-o", f"HostName={server.host}"])
    if server.ssh_alias and server.username:
        options.extend(["-l", server.username])
    if (not server.ssh_alias or server.port_override) and server.port:
        options.extend(["-p", str(server.port)])
    if server.identity_file:
        options.extend(["-i", resolve_identity_path(server)])
    restrict_identities = (
        server.prefer_identity_auth if identities_only is None else identities_only
    )
    if restrict_identities and server.identity_file:
        options.extend(["-o", "IdentitiesOnly=yes"])
    return ["ssh", *options, "--", target]


@dataclass(frozen=True)
class SSHCopyDetails:
    argv: tuple[str, ...]
    endpoint_complete: bool
    resolution: OpenSSHEndpointResolution
    warning: str = ""


def ssh_copy_details(server: ServerProfile) -> SSHCopyDetails:
    """Build a readable login command without executing OpenSSH configuration.

    Imported aliases keep ``-F`` and the alias target, so ProxyJump,
    ProxyCommand, Match, identity, and other OpenSSH semantics remain owned by
    the original configuration. HostName/User/Port are only made explicit when
    the bounded static resolver can prove all three values.
    """

    login = ssh_login_argv(server)
    if not server.ssh_alias:
        complete = bool(server.host and server.username and server.port)
        resolution = OpenSSHEndpointResolution(
            status="exact" if complete else "dynamic",
            hostname=server.host if complete else "",
            user=server.username if complete else "",
            port=server.port if complete else None,
            reason="" if complete else "local_default_unavailable",
        )
        return SSHCopyDetails(
            argv=tuple(login),
            endpoint_complete=complete,
            resolution=resolution,
            warning=(
                "用户名使用本机 OpenSSH 默认值，命令已保留原始连接语义。"
                if not complete
                else ""
            ),
        )

    if server.host and server.username and server.port_override:
        resolution = OpenSSHEndpointResolution(
            status="exact",
            hostname=server.host,
            user=server.username,
            port=server.port,
        )
        return SSHCopyDetails(
            argv=tuple(login),
            endpoint_complete=True,
            resolution=resolution,
        )

    config_path = (
        resolve_ssh_config_path(server)
        if server.ssh_config_file
        else str((Path.home() / ".ssh" / "config").resolve(strict=False))
    )
    resolution = resolve_openssh_endpoint(config_path, server.ssh_alias)
    if not resolution.exact:
        return SSHCopyDetails(
            argv=tuple(login),
            endpoint_complete=False,
            resolution=resolution,
            warning=(
                "SSH 配置包含条件、动态规则或未匹配到该 Host；"
                "命令已保留原始别名，连接时仍由 OpenSSH 完整解析。"
            ),
        )

    hostname = server.host or resolution.hostname
    username = server.username or resolution.user
    port = server.port if server.port_override else resolution.port
    if not hostname or not username or port is None:
        incomplete = OpenSSHEndpointResolution(status="dynamic", reason="endpoint_incomplete")
        return SSHCopyDetails(
            argv=tuple(login),
            endpoint_complete=False,
            resolution=incomplete,
            warning="SSH 端点信息不完整，命令已保留原始连接语义。",
        )

    target_index = login.index("--")
    options = list(login[1:target_index])
    if not server.host:
        options.extend(["-o", f"HostName={hostname}"])
    if not server.username:
        options.extend(["-l", username])
    if not server.port_override:
        options.extend(["-p", str(port)])
    argv = ("ssh", *options, "--", server.ssh_alias)
    return SSHCopyDetails(
        argv=argv,
        endpoint_complete=True,
        resolution=OpenSSHEndpointResolution(
            status="exact",
            hostname=hostname,
            user=username,
            port=port,
        ),
    )


def ssh_copy_argv(server: ServerProfile) -> list[str]:
    """Compatibility wrapper for callers that only need the command argv."""

    return list(ssh_copy_details(server).argv)


def ssh_argv(
    server: ServerProfile,
    remote_script: str,
    *,
    password_auth: bool = False,
    identities_only: bool = False,
    accept_new_host_key: bool = False,
) -> list[str]:
    login = ssh_login_argv(
        server,
        identities_only=False if password_auth else identities_only or server.prefer_identity_auth,
    )
    target_index = login.index("--")
    options = login[1:target_index]
    target = login[target_index + 1]
    options.extend(["-o", "ClearAllForwardings=yes"])
    # Keep first use independent of the executable or installer directory.
    # OpenSSH records a previously unknown key in the user's configured
    # known_hosts file, while accept-new still refuses a changed key. The
    # argument remains for API compatibility with older callers.
    options.extend(["-o", "StrictHostKeyChecking=accept-new"])
    if password_auth:
        options.extend(
            [
                "-o", "BatchMode=no",
                "-o", "NumberOfPasswordPrompts=1",
                "-o", "PasswordAuthentication=yes",
                "-o", "KbdInteractiveAuthentication=yes",
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "PubkeyAuthentication=no",
            ]
        )
    else:
        options.extend(["-o", "BatchMode=yes"])
    options.extend(["-o", f"ConnectTimeout={server.connect_timeout_seconds}"])
    if password_auth and server.identity_file:
        identity = resolve_identity_path(server)
        options = [
            option
            for index, option in enumerate(options)
            if option != "-i" and not (index > 0 and options[index - 1] == "-i" and option == identity)
        ]
    return ["ssh", *options, "--", target, "bash", "-lc", shlex.quote(remote_script)]


def _askpass_executable() -> str:
    names = ["VRAMRadarAskPass.exe", "vram-radar-askpass.exe"] if os.name == "nt" else ["VRAMRadarAskPass", "vram-radar-askpass"]
    executable_dir = os.path.dirname(sys.executable)
    for name in names:
        candidate = os.path.join(executable_dir, name)
        if os.path.isfile(candidate):
            return candidate
    discovered = shutil.which("vram-radar-askpass")
    if discovered:
        return discovered
    raise ConnectorFailure(
        "password_helper_missing",
        "当前安装缺少安全密码助手，请重新安装 VRAM Radar",
        retryable=False,
        state="misconfigured",
    )


def _run_bounded_process(
    argv: list[str],
    *,
    stdin: int,
    timeout: float,
    creationflags: int,
    env: dict[str, str] | None,
    stdout_limit: int,
    stderr_limit: int,
    input_data: bytes | None = None,
) -> _BoundedProcessResult:
    """Run a child while keeping both captured streams within fixed memory bounds."""

    process = subprocess.Popen(
        argv,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(
            creationflags | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else creationflags
        ),
        env=env,
        bufsize=0,
        start_new_session=os.name != "nt",
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract guard
        process.kill()
        raise RuntimeError("failed to capture SSH process streams")

    stdout = bytearray()
    stderr = bytearray()
    stdout_truncated = threading.Event()
    termination_started = threading.Event()
    termination_lock = threading.Lock()

    def terminate_tree_once() -> None:
        with termination_lock:
            if termination_started.is_set():
                return
            termination_started.set()
            _terminate_process_tree(process)

    def drain(stream: Any, target: bytearray, limit: int, *, abort_on_overflow: bool) -> None:
        seen = 0
        try:
            while True:
                chunk = stream.read(REMOTE_READ_CHUNK_BYTES)
                if not chunk:
                    break
                seen += len(chunk)
                remaining = max(0, limit + 1 - len(target))
                if remaining:
                    target.extend(chunk[:remaining])
                if abort_on_overflow and seen > limit and not stdout_truncated.is_set():
                    stdout_truncated.set()
                    terminate_tree_once()
        except (OSError, ValueError):
            # Cleanup closes the parent pipe ends to unblock a ProxyCommand
            # descendant that outlives ssh. Captured bytes remain valid.
            pass

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout, stdout_limit),
        kwargs={"abort_on_overflow": True},
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr, stderr_limit),
        kwargs={"abort_on_overflow": False},
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread: threading.Thread | None = None
    if input_data is not None:
        if process.stdin is None:  # pragma: no cover - Popen contract guard
            process.kill()
            raise RuntimeError("failed to open SSH process stdin")

        def feed_stdin() -> None:
            try:
                process.stdin.write(input_data)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                # The remote side can reject a connection before consuming
                # stdin. Its bounded stderr remains the failure channel.
                pass
            finally:
                process.stdin.close()

        stdin_thread = threading.Thread(target=feed_stdin, name="vram-radar-ssh-stdin", daemon=True)
        stdin_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_tree_once()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        if stdin_thread is not None:
            stdin_thread.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        raise
    if stdin_thread is not None:
        stdin_thread.join(timeout=2)
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        process.stdout.close()
        process.stderr.close()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
    else:
        process.stdout.close()
        process.stderr.close()
    # Keep the bytearrays instead of cloning them into bytes. The caller decodes
    # them while it still owns a global capture slot, avoiding a second raw
    # near-limit copy for every concurrent SSH response.
    return _BoundedProcessResult(returncode, stdout, stderr, stdout_truncated.is_set())


def run_remote(
    server: ServerProfile,
    remote_script: str,
    *,
    password: str | None = None,
    stdin_data: bytes | None = None,
    identities_only: bool = False,
    accept_new_host_key: bool = False,
) -> str:
    if stdin_data is not None and len(stdin_data) > MAX_REMOTE_STDIN_BYTES:
        raise ConnectorFailure(
            "request_too_large",
            "发送到服务器的数据超过安全上限",
            retryable=False,
            state="misconfigured",
        )
    if server.ssh_config_file:
        config_path = Path(resolve_ssh_config_path(server))
        if not config_path.is_file():
            raise ConnectorFailure(
                "ssh_config_missing",
                "OpenSSH 配置文件不存在或不可读取",
                retryable=False,
                state="misconfigured",
            )
    if server.identity_file and password is None:
        identity_path = Path(resolve_identity_path(server))
        if not identity_path.is_file():
            raise ConnectorFailure(
                "identity_file_missing",
                "SSH 私钥文件不存在或不可读取",
                retryable=False,
                state="misconfigured",
            )
    argv = ssh_argv(
        server,
        remote_script,
        password_auth=password is not None,
        identities_only=identities_only,
        accept_new_host_key=accept_new_host_key,
    )
    environment = None
    # Bound aggregate capture memory across refresh, connection-test and folder
    # requests. The slot remains owned through decoding so raw and text copies
    # cannot multiply across an unbounded number of concurrent callers.
    with _REMOTE_CAPTURE_SLOTS:
        try:
            askpass = _askpass_executable() if password is not None else None
            broker = PasswordBroker(password) if password is not None else None
            if broker is not None:
                environment = os.environ.copy()
                environment.update(broker.environment)
                environment.update(
                    {
                        "SSH_ASKPASS": askpass or "",
                        "SSH_ASKPASS_REQUIRE": "force",
                        "DISPLAY": environment.get("DISPLAY") or "vram-radar:0",
                    }
                )
            context = broker if broker is not None else nullcontext()
            with context:
                result = _run_bounded_process(
                    argv,
                    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                    timeout=server.connect_timeout_seconds + 15,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    env=environment,
                    stdout_limit=MAX_REMOTE_STDOUT_BYTES,
                    stderr_limit=MAX_REMOTE_STDERR_BYTES,
                    input_data=stdin_data,
                )
        except FileNotFoundError as exc:
            raise ConnectorFailure("ssh_missing", "本机未找到 OpenSSH 客户端", retryable=False, state="misconfigured") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConnectorFailure("ssh_timeout", "服务器查询超时", retryable=True) from exc
        if result.stdout_truncated:
            raise ConnectorFailure(
                "response_too_large",
                "服务器返回的数据超过安全上限，已中止本次刷新",
                retryable=False,
                state="misconfigured",
            )
        if result.returncode != 0:
            # The direct-GPU protocol carries hex-encoded process argv on stdout.
            # It must never become an exception message or reach the application log.
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise classify_process_error(
                detail,
                returncode=result.returncode,
                password_auth=password is not None,
            )
        return result.stdout.decode("utf-8", errors="replace")


def parse_nvidia_smi_rows(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row or not any(field.strip() for field in row):
            continue
        if len(row) not in {7, 8}:
            raise ConnectorFailure("parse_failed", "nvidia-smi 返回了无法识别的数据", retryable=False, state="misconfigured")
        values = [field.strip() for field in row]
        if len(values) == 8:
            index, gpu_uuid, name, total, used, free, utilization, temperature = values
        else:
            index, name, total, used, free, utilization, temperature = values
            gpu_uuid = ""
        try:
            total_mib = float(total)
            used_mib = float(used)
            free_mib = float(free)
            utilization_percent = None if utilization == "N/A" else float(utilization)
            temperature_c = None if temperature == "N/A" else float(temperature)
        except ValueError as exc:
            raise ConnectorFailure(
                "parse_failed", "nvidia-smi 返回了无法识别的数值", retryable=False, state="misconfigured"
            ) from exc
        result.append(
            {
                "gpu_index": index,
                "gpu_uuid": gpu_uuid or None,
                "gpu_type": name,
                "memory_total_gib": round(total_mib / 1024, 2),
                "memory_used_gib": round(used_mib / 1024, 2),
                "memory_free_gib": round(free_mib / 1024, 2),
                "utilization_percent": utilization_percent,
                "temperature_c": temperature_c,
            }
        )
    if not result:
        raise ConnectorFailure("parse_failed", "nvidia-smi 没有返回 GPU", retryable=False, state="misconfigured")
    return result


def _decode_hex(value: str, label: str, *, limit: int = 4_000_000) -> str:
    if len(value) > limit * 2 or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
        raise ConnectorFailure("parse_failed", f"服务器返回了无效的 {label}", retryable=True)
    try:
        return bytes.fromhex(value).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise ConnectorFailure("parse_failed", f"服务器返回了无效的 {label}", retryable=True) from exc


def _parse_direct_protocol(output: str) -> tuple[dict[str, str], dict[str, str]]:
    lines = output.splitlines()
    if len(lines) < 2 or lines[0] != DIRECT_PROTOCOL_HEADER or lines[-1] != DIRECT_PROTOCOL_END:
        raise ConnectorFailure("parse_failed", "服务器返回了不完整的 GPU 快照", retryable=True)
    fields: dict[str, str] = {}
    metadata: dict[str, str] = {}
    allowed = {
        "HOST_HEX",
        "CURRENT_UID",
        "CURRENT_USER_HEX",
        "HOME_HEX",
        "GPU_HEX",
        "PROCESS_A_SUPPORTED",
        "PROCESS_A_HEX",
        "PROCESS_B_SUPPORTED",
        "PROCESS_B_HEX",
        "METADATA_LIMIT",
        "CPU_COUNT",
        "CPU_LOAD_HEX",
    }
    for raw in lines[1:-1]:
        if raw.startswith("META|"):
            parts = raw.split("|", 3)
            if len(parts) != 4 or not parts[1].isdigit() or parts[2] not in {"OK", "ERR"}:
                raise ConnectorFailure("parse_failed", "服务器返回了无效的进程元数据", retryable=True)
            if parts[1] in metadata:
                raise ConnectorFailure("parse_failed", "服务器返回了重复的进程元数据", retryable=True)
            metadata[parts[1]] = _decode_hex(parts[3], "进程元数据") if parts[2] == "OK" else ""
            continue
        key, separator, value = raw.partition("=")
        if not separator or key not in allowed or key in fields:
            raise ConnectorFailure("parse_failed", "服务器返回了无法识别的 GPU 快照字段", retryable=True)
        fields[key] = value
    # HOME_HEX was added without changing the direct-GPU protocol version so
    # older cached fixtures remain readable. Fresh probes always include it.
    required = allowed - {"HOME_HEX", "CPU_COUNT", "CPU_LOAD_HEX"}
    if not required.issubset(fields):
        raise ConnectorFailure("parse_failed", "服务器返回了不完整的 GPU 快照字段", retryable=True)
    if fields["CURRENT_UID"] and not fields["CURRENT_UID"].isdigit():
        raise ConnectorFailure("parse_failed", "服务器返回了无效的当前用户标识", retryable=True)
    if fields["PROCESS_A_SUPPORTED"] not in {"0", "1"} or fields["PROCESS_B_SUPPORTED"] not in {"0", "1"}:
        raise ConnectorFailure("parse_failed", "服务器返回了无效的进程可见性状态", retryable=True)
    if not fields["METADATA_LIMIT"].isdigit() or int(fields["METADATA_LIMIT"]) != DIRECT_METADATA_LIMIT:
        raise ConnectorFailure("parse_failed", "服务器返回了不兼容的进程采集上限", retryable=True)
    if len(metadata) > DIRECT_METADATA_LIMIT:
        raise ConnectorFailure("parse_failed", "服务器返回了过多的进程元数据", retryable=True)
    return fields, metadata


def _parse_cpu_snapshot(fields: dict[str, str], *, sampled_at: datetime) -> dict[str, Any] | None:
    """Parse optional host CPU facts without making GPU collection depend on them."""

    if "CPU_COUNT" not in fields and "CPU_LOAD_HEX" not in fields:
        return None
    logical_cores: int | None = None
    raw_count = fields.get("CPU_COUNT", "").strip()
    if raw_count.isdigit():
        candidate = int(raw_count)
        if 1 <= candidate <= 65_536:
            logical_cores = candidate

    load_average: list[float] = []
    if "CPU_LOAD_HEX" in fields:
        try:
            raw_load = _decode_hex(fields["CPU_LOAD_HEX"], "CPU 负载", limit=256).split()
        except ConnectorFailure:
            raw_load = []
        if len(raw_load) >= 3:
            parsed: list[float] = []
            for value in raw_load[:3]:
                if not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", value):
                    parsed = []
                    break
                number = float(value)
                if not 0 <= number <= 1_000_000:
                    parsed = []
                    break
                parsed.append(number)
            load_average = parsed
    return {
        "supported": logical_cores is not None or len(load_average) == 3,
        "logical_cores": logical_cores,
        "load_average": load_average,
        "sampled_at": sampled_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _account_summary(current_user: str, home_directory: str) -> dict[str, str]:
    return {
        "username": current_user.strip(),
        "home_directory": home_directory.strip(),
    }


def parse_directory_protocol(output: str) -> dict[str, Any]:
    lines = output.splitlines()
    if len(lines) < 2 or lines[0] != DIRECTORY_PROTOCOL_HEADER or lines[-1] != DIRECTORY_PROTOCOL_END:
        raise ConnectorFailure("parse_failed", "服务器返回了不完整的文件夹结构", retryable=True)

    allowed = {
        "USER_HEX",
        "HOME_HEX",
        "ROOT_HEX",
        "ROOT_SOURCE",
        "WARNING_HEX",
        "SUPPORTED",
        "LIMIT",
        "MAX_DEPTH",
        "TRUNCATED",
        "ROOT_VERSION_HEX",
    }
    fields: dict[str, str] = {}
    raw_entries: list[tuple[str, str, str, str]] = []
    for raw in lines[1:-1]:
        if raw.startswith("ENTRY|"):
            parts = raw.split("|", 4)
            if len(parts) != 5:
                raise ConnectorFailure("parse_failed", "服务器返回了无效的文件夹条目", retryable=True)
            raw_entries.append((parts[1], parts[2], parts[3], parts[4]))
            continue
        key, separator, value = raw.partition("=")
        if not separator or key not in allowed or key in fields:
            raise ConnectorFailure("parse_failed", "服务器返回了无法识别的文件夹字段", retryable=True)
        fields[key] = value

    if not (allowed - {"ROOT_VERSION_HEX"}).issubset(fields):
        raise ConnectorFailure("parse_failed", "服务器返回了不完整的文件夹字段", retryable=True)
    if fields["SUPPORTED"] not in {"0", "1"} or fields["TRUNCATED"] not in {"0", "1"}:
        raise ConnectorFailure("parse_failed", "服务器返回了无效的文件夹状态", retryable=True)
    if not fields["LIMIT"].isdigit() or int(fields["LIMIT"]) != DIRECTORY_ENTRY_LIMIT:
        raise ConnectorFailure("parse_failed", "服务器返回了不兼容的文件夹条目上限", retryable=True)
    if not fields["MAX_DEPTH"].isdigit() or int(fields["MAX_DEPTH"]) != DIRECTORY_MAX_DEPTH:
        raise ConnectorFailure("parse_failed", "服务器返回了不兼容的文件夹深度", retryable=True)
    if len(raw_entries) > DIRECTORY_ENTRY_LIMIT:
        raise ConnectorFailure("parse_failed", "服务器返回了过多的文件夹条目", retryable=True)

    supported = fields["SUPPORTED"] == "1"
    username = _decode_hex(fields["USER_HEX"], "当前用户名", limit=65_536).strip()
    home_directory = _decode_hex(fields["HOME_HEX"], "账号主目录", limit=65_536).strip()
    root_directory = _decode_hex(fields["ROOT_HEX"], "展开目录", limit=65_536).strip()
    warning = _decode_hex(fields["WARNING_HEX"], "文件夹提示", limit=65_536).strip()
    root_source = fields["ROOT_SOURCE"]
    if root_source not in {"auto", "home", "pinned", "requested"}:
        raise ConnectorFailure("parse_failed", "服务器返回了无效的目录来源", retryable=True)
    normalized_home = posixpath.normpath(home_directory)
    normalized_root = posixpath.normpath(root_directory)
    root_within_home = normalized_root == normalized_home or (
        normalized_home == "/" or normalized_root.startswith(normalized_home.rstrip("/") + "/")
    )
    missing_unreadable_home = not supported and not home_directory and root_directory == "/"
    if (
        not missing_unreadable_home
        and (
            not posixpath.isabs(home_directory)
            or not posixpath.isabs(root_directory)
            or normalized_home != home_directory
            or normalized_root != root_directory
            or not root_within_home
            or any(ord(character) < 32 for character in home_directory + root_directory)
        )
    ):
        raise ConnectorFailure("parse_failed", "服务器返回了不安全的展开目录", retryable=True)
    entries: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    allowed_kinds = {"directory", "file", "symlink", "other"}
    for path_hex, kind, size_text, modified_text in raw_entries:
        relative_path = _decode_hex(path_hex, "文件夹条目名称", limit=65_536)
        parts = relative_path.split("/")
        if (
            not relative_path
            or relative_path.startswith("/")
            or any(ord(character) < 32 for character in relative_path)
            or any(part in {"", ".", ".."} for part in parts)
            or len(parts) > DIRECTORY_MAX_DEPTH
            or relative_path in seen_paths
            or kind not in allowed_kinds
            or (size_text and (not size_text.isdigit() or int(size_text) > 2**63 - 1))
            or (modified_text and not re.fullmatch(r"-?\d+", modified_text))
        ):
            raise ConnectorFailure("parse_failed", "服务器返回了不安全的文件夹条目", retryable=True)
        seen_paths.add(relative_path)
        modified_at = None
        if modified_text:
            try:
                modified_at = datetime.fromtimestamp(int(modified_text), timezone.utc).isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z")
            except (OSError, OverflowError, ValueError) as exc:
                raise ConnectorFailure("parse_failed", "服务器返回了无效的修改时间", retryable=True) from exc
        entries.append(
            {
                "path": relative_path,
                "parent_path": "/".join(parts[:-1]),
                "absolute_path": posixpath.join(root_directory, relative_path),
                "parent_absolute_path": (
                    root_directory
                    if len(parts) == 1
                    else posixpath.join(root_directory, *parts[:-1])
                ),
                "name": parts[-1],
                "depth": len(parts),
                "kind": kind,
                "size_bytes": int(size_text) if size_text else None,
                "modified_at": modified_at,
                "has_more": kind == "directory" and len(parts) == DIRECTORY_MAX_DEPTH,
            }
        )

    if fields["TRUNCATED"] == "1":
        for entry in entries:
            if entry["kind"] == "directory":
                entry["has_more"] = True

    if not supported and entries:
        raise ConnectorFailure("parse_failed", "服务器返回了矛盾的文件夹状态", retryable=True)
    result: dict[str, Any] = {
        "username": username,
        "home_directory": home_directory,
        "directory_tree": {
            "supported": supported,
            "root": root_directory,
            "root_source": root_source,
            "max_depth": DIRECTORY_MAX_DEPTH,
            "entry_limit": DIRECTORY_ENTRY_LIMIT,
            "truncated": fields["TRUNCATED"] == "1",
            "entries": entries,
        },
    }
    if fields.get("ROOT_VERSION_HEX"):
        result["directory_tree"]["version_token"] = _decode_hex(
            fields["ROOT_VERSION_HEX"], "目录版本", limit=4096
        )
    if not supported:
        result["directory_tree"]["warning"] = warning or "当前目录不可读取"
    return result


def query_account_directory(
    server: ServerProfile,
    *,
    password: str | None = None,
    identities_only: bool = False,
    root_path: str | None = None,
    root_source: str = "auto",
) -> dict[str, Any]:
    """Read a bounded work-directory tree without opening file contents.

    An omitted root is inferred from common code folders and project markers.
    Each request returns one level with at most 160 entries, reports symlinks
    as leaves, and never opens file contents. Child folders are separate lazy
    requests owned by the service cache.
    """

    if root_source not in {"auto", "pinned", "requested"}:
        raise ValueError("root_source must be auto, pinned, or requested")
    requested_root = root_path or ""
    requested_source = root_source if requested_root else "auto"

    script = f"""\
set -u
hex_encode() {{ od -An -v -tx1 | tr -d ' \\n'; }}
printf '{DIRECTORY_PROTOCOL_HEADER}\\n'
account_user=$(id -un 2>/dev/null || true)
home_dir=${{HOME:-}}
if [ -z "$home_dir" ] && command -v getent >/dev/null 2>&1; then
    home_dir=$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{{print $6; exit}}')
fi
if [ -n "$home_dir" ] && canonical_home=$(cd -- "$home_dir" 2>/dev/null && pwd -P); then
    home_dir=$canonical_home
fi
printf 'USER_HEX='; printf '%s' "$account_user" | hex_encode; printf '\\n'
printf 'HOME_HEX='; printf '%s' "$home_dir" | hex_encode; printf '\\n'
printf 'LIMIT={DIRECTORY_ENTRY_LIMIT}\\n'
printf 'MAX_DEPTH={DIRECTORY_MAX_DEPTH}\\n'
if [ -z "$home_dir" ] || [ ! -d "$home_dir" ] || [ ! -r "$home_dir" ]; then
    printf 'ROOT_HEX='; printf '%s' "${{home_dir:-/}}" | hex_encode; printf '\\n'
    printf 'ROOT_SOURCE=home\\nWARNING_HEX='; printf '%s' '当前账号主目录不可读取' | hex_encode; printf '\\n'
    printf 'SUPPORTED=0\\nTRUNCATED=0\\n{DIRECTORY_PROTOCOL_END}\\n'
    exit 0
fi

requested_root={shlex.quote(requested_root)}
requested_source={shlex.quote(requested_source)}
root_dir=$home_dir
root_source=home
warning=''
directory_version() {{
    local value
    if command -v python3 >/dev/null 2>&1 && value=$(python3 - "$1" 2>/dev/null <<'VRAM_RADAR_VERSION_PY'
import os
import sys
metadata = os.stat(sys.argv[1], follow_symlinks=False)
print("%s:%s:%s:%s:%s" % (
    metadata.st_dev, metadata.st_ino, metadata.st_size,
    metadata.st_mtime_ns, metadata.st_ctime_ns,
))
VRAM_RADAR_VERSION_PY
    ); then
        printf '%s' "$value"
    elif value=$(stat -c '%d:%i:%s:%Y:%Z' -- "$1" 2>/dev/null); then
        printf '%s' "$value"
    elif value=$(stat -f '%d:%i:%z:%m:%c' "$1" 2>/dev/null); then
        printf '%s' "$value"
    fi
}}
within_home() {{
    if [ "$home_dir" = / ]; then
        [[ "$1" = /* ]]
        return
    fi
    case "$1" in
        "$home_dir"|"$home_dir"/*) return 0 ;;
        *) return 1 ;;
    esac
}}
entry_mtime() {{
    local value
    value=$(stat -c '%Y' -- "$1" 2>/dev/null) || value=$(stat -f '%m' "$1" 2>/dev/null) || value=0
    [[ "$value" =~ ^-?[0-9]+$ ]] || value=0
    printf '%s' "$value"
}}
has_project_marker() {{
    local candidate=$1 marker
    for marker in .git pyproject.toml package.json Cargo.toml go.mod CMakeLists.txt requirements.txt environment.yml setup.py Makefile; do
        [ -e "$candidate/$marker" ] && return 0
    done
    return 1
}}
best_rank=0
best_mtime=-9223372036854775808
consider_root() {{
    local candidate=$1 rank=$2 canonical modified
    [ -d "$candidate" ] && [ -r "$candidate" ] || return 0
    canonical=$(cd -- "$candidate" 2>/dev/null && pwd -P) || return 0
    within_home "$canonical" || return 0
    modified=$(entry_mtime "$canonical")
    if (( rank > best_rank || (rank == best_rank && modified > best_mtime) )); then
        root_dir=$canonical
        best_rank=$rank
        best_mtime=$modified
    fi
}}

if [ -n "$requested_root" ]; then
    if canonical_requested=$(cd -- "$requested_root" 2>/dev/null && pwd -P) \
        && within_home "$canonical_requested" && [ -r "$canonical_requested" ]; then
        root_dir=$canonical_requested
        root_source=$requested_source
    else
        warning='该目录不存在、不可读或不在账号主目录内'
    fi
else
    shopt -s nullglob dotglob
    candidate_budget=160
    common_roots=(
        "$home_dir/code" "$home_dir/Code" "$home_dir/projects" "$home_dir/Projects"
        "$home_dir/repos" "$home_dir/Repos" "$home_dir/workspace" "$home_dir/Workspace"
        "$home_dir/workspaces" "$home_dir/Workspaces" "$home_dir/research" "$home_dir/Research"
        "$home_dir/src" "$home_dir/dev" "$home_dir/Development" "$home_dir/git" "$home_dir/GitHub"
        "$home_dir/Documents/GitHub" "$home_dir/Documents/Projects" "$home_dir/Documents/Code"
    )
    for candidate in "${{common_roots[@]}}"; do
        [ -d "$candidate" ] || continue
        consider_root "$candidate" 10
        has_project_marker "$candidate" && consider_root "$candidate" 30
        if (( candidate_budget > 0 )); then
            while IFS= read -r -d '' child; do
                (( candidate_budget -= 1 ))
                [ -d "$child" ] && has_project_marker "$child" && consider_root "$child" 30
                (( candidate_budget > 0 )) || break
            done < <(find "$candidate" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
        fi
    done
    if (( candidate_budget > 0 )); then
        while IFS= read -r -d '' candidate; do
            (( candidate_budget -= 1 ))
            [ -d "$candidate" ] && has_project_marker "$candidate" && consider_root "$candidate" 30
            (( candidate_budget > 0 )) || break
        done < <(find "$home_dir" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
    fi
    (( best_rank > 0 )) && root_source=auto
fi

printf 'ROOT_HEX='; printf '%s' "$root_dir" | hex_encode; printf '\\n'
printf 'ROOT_SOURCE=%s\\n' "$root_source"
printf 'ROOT_VERSION_HEX='; directory_version "$root_dir" | hex_encode; printf '\\n'
printf 'WARNING_HEX='; printf '%s' "$warning" | hex_encode; printf '\\n'
if [ -n "$warning" ]; then
    printf 'SUPPORTED=0\\nTRUNCATED=0\\n{DIRECTORY_PROTOCOL_END}\\n'
    exit 0
fi
printf 'SUPPORTED=1\\n'
if command -v python3 >/dev/null 2>&1 && python3 - "$root_dir" <<'VRAM_RADAR_PY'
import itertools
import os
import sys

root = sys.argv[1]
limit = {DIRECTORY_ENTRY_LIMIT}
try:
    with os.scandir(root) as scanner:
        sampled = list(itertools.islice(scanner, limit + 1))
except OSError:
    raise SystemExit(1)

truncated = len(sampled) > limit
entries = sorted(sampled[:limit], key=lambda item: os.fsencode(item.name))
lines = []
for entry in entries:
    try:
        if entry.is_symlink():
            kind = "symlink"
        elif entry.is_dir(follow_symlinks=False):
            kind = "directory"
        elif entry.is_file(follow_symlinks=False):
            kind = "file"
        else:
            kind = "other"
        metadata = entry.stat(follow_symlinks=False)
        size = str(metadata.st_size)
        modified = str(int(metadata.st_mtime))
    except OSError:
        kind = "other"
        size = ""
        modified = ""
    encoded = os.fsencode(entry.name).hex()
    lines.append(f"ENTRY|{{encoded}}|{{kind}}|{{size}}|{{modified}}")
sys.stdout.write("\\n".join(lines))
if lines:
    sys.stdout.write("\\n")
sys.stdout.write(f"TRUNCATED={{int(truncated)}}\\n")
VRAM_RADAR_PY
then
    :
else
    entry_count=0
    tree_truncated=0
    while IFS= read -r -d '' entry; do
            entry_count=$((entry_count + 1))
            if (( entry_count > {DIRECTORY_ENTRY_LIMIT} )); then
                tree_truncated=1
                break
            fi
            if [ "$root_dir" = / ]; then
                relative_path=${{entry#/}}
            else
                relative_path=${{entry#"$root_dir"/}}
            fi
            if [ -L "$entry" ]; then
                entry_kind=symlink
            elif [ -d "$entry" ]; then
                entry_kind=directory
            elif [ -f "$entry" ]; then
                entry_kind=file
            else
                entry_kind=other
            fi
            metadata=''
            if metadata=$(stat -c '%s|%Y' -- "$entry" 2>/dev/null); then
                :
            elif metadata=$(stat -f '%z|%m' "$entry" 2>/dev/null); then
                :
            else
                metadata='|'
            fi
            entry_size=${{metadata%%|*}}
            entry_modified=${{metadata#*|}}
            printf 'ENTRY|'; printf '%s' "$relative_path" | hex_encode
            printf '|%s|%s|%s\\n' "$entry_kind" "$entry_size" "$entry_modified"
    done < <(find "$root_dir" -mindepth 1 -maxdepth {DIRECTORY_MAX_DEPTH} -print0 2>/dev/null)
    printf 'TRUNCATED=%s\\n' "$tree_truncated"
fi
printf '{DIRECTORY_PROTOCOL_END}\\n'
"""
    # Directory work has its own smaller admission budget. It may use at most
    # two of the global eight capture slots, leaving monitoring capacity free.
    with _DIRECTORY_QUERY_SLOTS:
        return parse_directory_protocol(
            run_remote(server, script, password=password, identities_only=identities_only)
        )


def parse_directory_version_protocol(output: str) -> dict[str, Any]:
    """Parse the bounded response used to validate one cached directory root."""

    lines = output.splitlines()
    if (
        len(lines) != 5
        or lines[0] != DIRECTORY_VERSION_PROTOCOL_HEADER
        or lines[-1] != DIRECTORY_VERSION_PROTOCOL_END
    ):
        raise ConnectorFailure("parse_failed", "服务器返回了不完整的目录版本", retryable=True)
    fields: dict[str, str] = {}
    for raw in lines[1:-1]:
        key, separator, value = raw.partition("=")
        if not separator or key not in {"ROOT_HEX", "VERSION_HEX", "SUPPORTED"} or key in fields:
            raise ConnectorFailure("parse_failed", "服务器返回了无效的目录版本", retryable=True)
        fields[key] = value
    if set(fields) != {"ROOT_HEX", "VERSION_HEX", "SUPPORTED"} or fields["SUPPORTED"] not in {"0", "1"}:
        raise ConnectorFailure("parse_failed", "服务器返回了无效的目录版本状态", retryable=True)
    root = _decode_hex(fields["ROOT_HEX"], "展开目录", limit=65_536).strip()
    token = _decode_hex(fields["VERSION_HEX"], "目录版本", limit=4096)
    if (
        not root
        or not posixpath.isabs(root)
        or posixpath.normpath(root) != root
        or any(ord(character) < 32 for character in root)
        or (fields["SUPPORTED"] == "1" and not token)
        or (fields["SUPPORTED"] == "0" and token)
    ):
        raise ConnectorFailure("parse_failed", "服务器返回了不安全的目录版本", retryable=True)
    return {"root": root, "version_token": token, "supported": fields["SUPPORTED"] == "1"}


def query_account_directory_version(
    server: ServerProfile,
    *,
    password: str | None = None,
    identities_only: bool = False,
    root_path: str,
) -> dict[str, Any]:
    """Read only one directory's identity/change token, never its children.

    GNU and BSD ``stat`` both update the directory mtime/ctime when direct
    children are added, removed, or renamed. The service also performs a
    bounded periodic full refresh to cover in-place child size/mtime changes,
    which do not necessarily update the parent directory metadata.
    """

    if not root_path or not posixpath.isabs(root_path) or posixpath.normpath(root_path) != root_path:
        raise ValueError("root_path must be an absolute normalized POSIX path")
    script = f"""\
set -u
hex_encode() {{ od -An -v -tx1 | tr -d ' \\n'; }}
printf '{DIRECTORY_VERSION_PROTOCOL_HEADER}\\n'
home_dir=${{HOME:-}}
if [ -z "$home_dir" ] && command -v getent >/dev/null 2>&1; then
    home_dir=$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{{print $6; exit}}')
fi
if [ -n "$home_dir" ] && canonical_home=$(cd -- "$home_dir" 2>/dev/null && pwd -P); then
    home_dir=$canonical_home
fi
requested_root={shlex.quote(root_path)}
canonical_root=''
if [ -n "$home_dir" ] && canonical_root=$(cd -- "$requested_root" 2>/dev/null && pwd -P); then
    if [ "$home_dir" = / ]; then
        case "$canonical_root" in /*) ;; *) canonical_root='' ;; esac
    else
        case "$canonical_root" in
            "$home_dir"|"$home_dir"/*) ;;
            *) canonical_root='' ;;
        esac
    fi
fi
printf 'ROOT_HEX='; printf '%s' "${{canonical_root:-$requested_root}}" | hex_encode; printf '\\n'
version=''
if [ -n "$canonical_root" ]; then
    if command -v python3 >/dev/null 2>&1 && version=$(python3 - "$canonical_root" 2>/dev/null <<'VRAM_RADAR_VERSION_PY'
import os
import sys
metadata = os.stat(sys.argv[1], follow_symlinks=False)
print("%s:%s:%s:%s:%s" % (
    metadata.st_dev, metadata.st_ino, metadata.st_size,
    metadata.st_mtime_ns, metadata.st_ctime_ns,
))
VRAM_RADAR_VERSION_PY
    ); then
        :
    elif version=$(stat -c '%d:%i:%s:%Y:%Z' -- "$canonical_root" 2>/dev/null); then
        :
    elif version=$(stat -f '%d:%i:%z:%m:%c' "$canonical_root" 2>/dev/null); then
        :
    else
        version=''
    fi
fi
printf 'VERSION_HEX='; printf '%s' "$version" | hex_encode; printf '\\n'
if [ -n "$version" ]; then printf 'SUPPORTED=1\\n'; else printf 'SUPPORTED=0\\n'; fi
printf '{DIRECTORY_VERSION_PROTOCOL_END}\\n'
"""
    with _DIRECTORY_QUERY_SLOTS:
        return parse_directory_version_protocol(
            run_remote(server, script, password=password, identities_only=identities_only)
        )


def redact_command_preview(command: str, *, limit: int = 600) -> tuple[str, bool]:
    preview = command.replace("\x00", " ")
    preview = URL_USERINFO_RE.sub(r"\1[已隐藏]@", preview)
    preview = AUTHORIZATION_RE.sub(r"\1[已隐藏]", preview)
    preview = SENSITIVE_HEADER_RE.sub(r"\1[已隐藏]", preview)
    preview = BEARER_RE.sub(r"\1[已隐藏]", preview)
    preview = SENSITIVE_PAIR_RE.sub(r"\1[已隐藏]", preview)
    preview = SENSITIVE_ASSIGNMENT_RE.sub(r"\1[已隐藏]", preview)
    preview = SENSITIVE_FLAG_EQUALS_RE.sub(r"\1[已隐藏]", preview)
    preview = SENSITIVE_FLAG_VALUE_RE.sub(r"\1[已隐藏]", preview)
    preview = re.sub(r"\s+", " ", preview).strip()
    truncated = len(preview) > limit
    if truncated:
        preview = preview[: limit - 1].rstrip() + "…"
    return preview, truncated


def infer_process_name(process_name: str, command: str) -> str:
    fallback = posixpath.basename(str(process_name or "").strip()) or "未命名进程"
    try:
        tokens = shlex.split(command[:8192], posix=True)
    except ValueError:
        tokens = command[:8192].split()
    label_flags = {"--run-name", "--run_name", "--experiment", "--experiment-name", "--exp-name", "--job-name"}
    label = ""
    for index, token in enumerate(tokens):
        if token in label_flags and index + 1 < len(tokens):
            label = tokens[index + 1]
            break
        for flag in label_flags:
            if token.startswith(f"{flag}="):
                label = token.split("=", 1)[1]
                break
        if label:
            break
    executable = ""
    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens):
            executable = tokens[index + 1]
            break
        if token.endswith(".py"):
            executable = posixpath.basename(token)
            break
    if not executable and tokens:
        executable = posixpath.basename(tokens[0])
    safe_label, _ = redact_command_preview(label, limit=120)
    safe_executable, _ = redact_command_preview(executable or fallback, limit=120)
    if safe_label and safe_executable and safe_label != safe_executable:
        return f"{safe_label} · {safe_executable}"
    return safe_label or safe_executable or fallback


def _parse_process_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row or not any(field.strip() for field in row):
            continue
        if len(row) != 4:
            raise ValueError("invalid process row")
        gpu_uuid, pid, process_name, memory = (field.strip() for field in row)
        if not pid.isdigit() or not gpu_uuid:
            raise ValueError("invalid process identity")
        try:
            memory_mib = None if memory in {"N/A", "[N/A]", "[Not Supported]"} else float(memory)
        except ValueError as exc:
            raise ValueError("invalid process memory") from exc
        rows.append(
            {
                "gpu_uuid": gpu_uuid,
                "pid": pid,
                "process_name": process_name,
                "memory_used_gib": None if memory_mib is None else round(memory_mib / 1024, 2),
            }
        )
    return rows


def _parse_process_metadata(pid: str, text: str) -> dict[str, Any] | None:
    parts = text.strip().split(None, 4)
    if len(parts) < 4 or parts[0] != pid or not parts[1].isdigit() or not parts[3].isdigit():
        return None
    cpu_percent: float | None = None
    command = parts[4] if len(parts) == 5 else ""
    command_parts = command.split(None, 1)
    if command_parts and re.fullmatch(r"\d+(?:\.\d+)?", command_parts[0]):
        candidate = float(command_parts[0])
        if 0 <= candidate <= 1_000_000:
            cpu_percent = round(candidate, 2)
            command = command_parts[1] if len(command_parts) == 2 else ""
    return {
        "uid": parts[1],
        "user": parts[2],
        "elapsed_seconds": int(parts[3]),
        "cpu_percent": cpu_percent,
        "command": command,
    }


def _build_direct_processes(
    process_a_text: str,
    process_b_text: str,
    metadata_text: dict[str, str],
    gpus: list[dict[str, Any]],
    current_uid: str,
    current_user: str,
    *,
    sampled_at: datetime,
    show_other_user_commands: bool = False,
) -> dict[str, Any]:
    first = _parse_process_rows(process_a_text)
    second = _parse_process_rows(process_b_text)
    first_keys = {(item["gpu_uuid"], item["pid"]) for item in first}
    second_keys = {(item["gpu_uuid"], item["pid"]) for item in second}
    stable = [item for item in second if (item["gpu_uuid"], item["pid"]) in first_keys]
    gpu_indices = {str(item.get("gpu_uuid") or ""): item["gpu_index"] for item in gpus if item.get("gpu_uuid")}
    by_pid: dict[str, dict[str, Any]] = {}
    for allocation in stable:
        pid = allocation["pid"]
        process = by_pid.setdefault(
            pid,
            {
                "pid": pid,
                "process_name": allocation["process_name"],
                "allocations": [],
            },
        )
        process["allocations"].append(
            {
                "gpu_index": gpu_indices.get(allocation["gpu_uuid"]),
                "memory_used_gib": allocation["memory_used_gib"],
            }
        )
    active: list[dict[str, Any]] = []
    for pid, process in by_pid.items():
        metadata = _parse_process_metadata(pid, metadata_text.get(pid, ""))
        uid = metadata["uid"] if metadata else ""
        if uid and current_uid:
            owner_scope = "mine" if uid == current_uid else "other"
        else:
            owner_scope = "unknown"
        raw_command = metadata["command"] if metadata else ""
        redacted_command, command_truncated = redact_command_preview(raw_command)
        safe_process_name, _ = redact_command_preview(process["process_name"], limit=160)
        elapsed_seconds = metadata["elapsed_seconds"] if metadata else None
        started_at = None
        if elapsed_seconds is not None:
            started_at = (sampled_at - timedelta(seconds=elapsed_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
        command_allowed = owner_scope == "mine" or show_other_user_commands
        if owner_scope == "mine" and redacted_command:
            command_preview = redacted_command
            command_visibility = "self"
        elif show_other_user_commands and redacted_command:
            command_preview = redacted_command
            command_visibility = "redacted_other" if owner_scope == "other" else "redacted_visible"
        elif raw_command:
            command_preview = None
            command_truncated = False
            command_visibility = "hidden_for_privacy"
        else:
            command_preview = None
            command_truncated = False
            command_visibility = "unavailable"
        allocations = sorted(
            process["allocations"],
            key=lambda item: (item["gpu_index"] is None, int(item["gpu_index"]) if str(item["gpu_index"] or "").isdigit() else 9999),
        )
        memory_values = [item["memory_used_gib"] for item in allocations if item["memory_used_gib"] is not None]
        active.append(
            {
                "pid": pid,
                "uid": uid or None,
                "user": metadata["user"] if metadata else None,
                "owner_scope": owner_scope,
                "name": infer_process_name(safe_process_name, raw_command if command_allowed else ""),
                "process_name": safe_process_name,
                "command_preview": command_preview or None,
                "command_truncated": command_truncated,
                "command_visibility": command_visibility,
                "elapsed_seconds": elapsed_seconds,
                "cpu_percent": metadata["cpu_percent"] if metadata else None,
                "started_at": started_at,
                "metadata_visibility": "full" if metadata else "none",
                "allocations": allocations,
                "memory_used_gib": round(sum(memory_values), 2) if memory_values else None,
            }
        )
    active.sort(
        key=lambda item: (
            {"mine": 0, "other": 1, "unknown": 2}[item["owner_scope"]],
            str(item.get("user") or ""),
            min((int(allocation["gpu_index"]) for allocation in item["allocations"] if str(allocation["gpu_index"] or "").isdigit()), default=9999),
            int(item["pid"]),
        )
    )
    visible_metadata = sum(item["metadata_visibility"] != "none" for item in active)
    return {
        "supported": True,
        "source": "nvidia-smi + ps",
        "sampled_at": sampled_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "current_user": current_user,
        "metadata_visibility": "full" if visible_metadata == len(active) else ("partial" if visible_metadata else "none"),
        "metadata_limited": len({item["pid"] for item in first}) > DIRECT_METADATA_LIMIT,
        "dropped_transient_count": len(first_keys - second_keys),
        "deferred_new_count": len(second_keys - first_keys),
        "active": active,
    }


def query_direct_ssh(
    server: ServerProfile,
    *,
    password: str | None = None,
    identities_only: bool = False,
) -> dict[str, Any]:
    script = f"""\
set -eu
hex_encode() {{ od -An -v -tx1 | tr -d ' \\n'; }}
printf '{DIRECT_PROTOCOL_HEADER}\\n'
printf 'HOST_HEX='; hostname | tr -d '\\r\\n' | hex_encode; printf '\\n'
printf 'CURRENT_UID=%s\\n' "$(id -u)"
printf 'CURRENT_USER_HEX='; id -un | tr -d '\\r\\n' | hex_encode; printf '\\n'
home_dir=${{HOME:-}}
if [ -z "$home_dir" ] && command -v getent >/dev/null 2>&1; then
    home_dir=$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{{print $6; exit}}') || true
fi
printf 'HOME_HEX='; printf '%s' "$home_dir" | hex_encode; printf '\\n'
cpu_count=''
if command -v getconf >/dev/null 2>&1; then
    cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
fi
if [ -z "$cpu_count" ] && command -v nproc >/dev/null 2>&1; then
    cpu_count=$(nproc 2>/dev/null || true)
fi
case "$cpu_count" in
    ''|*[!0-9]*) cpu_count='';;
esac
printf 'CPU_COUNT=%s\\n' "$cpu_count"
cpu_load=''
if [ -r /proc/loadavg ]; then
    cpu_load=$(awk '{{print $1" "$2" "$3}}' /proc/loadavg 2>/dev/null || true)
elif command -v sysctl >/dev/null 2>&1; then
    cpu_load=$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{{}}' || true)
fi
printf 'CPU_LOAD_HEX='; printf '%s' "$cpu_load" | hex_encode; printf '\\n'
gpu_rows=$(nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu --format=csv,noheader,nounits)
printf 'GPU_HEX='; printf '%s' "$gpu_rows" | hex_encode; printf '\\n'
process_a=''
if process_a=$(nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader,nounits 2>/dev/null); then
    printf 'PROCESS_A_SUPPORTED=1\\n'
else
    printf 'PROCESS_A_SUPPORTED=0\\n'
fi
printf 'PROCESS_A_HEX='; printf '%s' "$process_a" | hex_encode; printf '\\n'
printf 'METADATA_LIMIT={DIRECT_METADATA_LIMIT}\\n'
all_pids=$(printf '%s\\n' "$process_a" | awk -F',' '{{ pid=$2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", pid); if (pid ~ /^[0-9]+$/) print pid }}' | sort -un)
current_uid=$(id -u)
pids=$(
    {{
        for pid in $all_pids; do printf 'W|%s\\n' "$pid"; done
        ps -eo pid= -o uid= 2>/dev/null | awk '{{ print "P|" $1 "|" $2 }}'
    }} | awk -F'|' -v current_uid="$current_uid" '
        $1 == "W" {{ wanted[$2] = 1; next }}
        $1 == "P" && ($2 in wanted) {{ owner[$2] = $3; next }}
        END {{
            for (pid in wanted) {{
                priority = owner[pid] == current_uid ? 0 : (owner[pid] != "" ? 1 : 2)
                print priority, pid
            }}
        }}
    ' | sort -k1,1n -k2,2n | sed -n '1,{DIRECT_METADATA_LIMIT}p' | awk '{{ print $2 }}'
)
for pid in $pids; do
    if meta=$(ps -ww -p "$pid" -o pid= -o uid= -o user:64= -o etimes= -o pcpu= -o args= 2>/dev/null); then
        printf 'META|%s|OK|' "$pid"; printf '%s' "$meta" | hex_encode; printf '\\n'
    else
        printf 'META|%s|ERR|\\n' "$pid"
    fi
done
process_b=''
if process_b=$(nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader,nounits 2>/dev/null); then
    printf 'PROCESS_B_SUPPORTED=1\\n'
else
    printf 'PROCESS_B_SUPPORTED=0\\n'
fi
printf 'PROCESS_B_HEX='; printf '%s' "$process_b" | hex_encode; printf '\\n'
printf '{DIRECT_PROTOCOL_END}\\n'
"""
    output = run_remote(server, script, password=password, identities_only=identities_only)
    fields, metadata = _parse_direct_protocol(output)
    host = _decode_hex(fields["HOST_HEX"], "服务器名称")
    current_user = _decode_hex(fields["CURRENT_USER_HEX"], "当前用户名")
    home_directory = _decode_hex(fields.get("HOME_HEX", ""), "账号主目录", limit=65_536)
    gpu_text = _decode_hex(fields["GPU_HEX"], "GPU 数据")
    gpus = parse_nvidia_smi_rows(gpu_text)
    sampled_at = datetime.now(timezone.utc)
    cpu = _parse_cpu_snapshot(fields, sampled_at=sampled_at)
    processes: dict[str, Any]
    if fields["PROCESS_A_SUPPORTED"] == "1" and fields["PROCESS_B_SUPPORTED"] == "1":
        try:
            processes = _build_direct_processes(
                _decode_hex(fields["PROCESS_A_HEX"], "GPU 进程数据"),
                _decode_hex(fields["PROCESS_B_HEX"], "GPU 进程数据"),
                metadata,
                gpus,
                fields["CURRENT_UID"],
                current_user,
                sampled_at=sampled_at,
                show_other_user_commands=server.show_other_user_commands,
            )
        except ValueError:
            processes = {
                "supported": False,
                "source": "nvidia-smi + ps",
                "current_user": current_user,
                "active": [],
                "warning": "GPU 进程数据格式暂时无法识别；显存数据仍会正常刷新。",
            }
    else:
        processes = {
            "supported": False,
            "source": "nvidia-smi + ps",
            "current_user": current_user,
            "active": [],
            "warning": "当前驱动未提供 GPU 进程快照；显存数据仍会正常刷新。",
        }
    result = {
        "server_id": server.id,
        "display_name": server.display_name,
        "backend": server.backend,
        "view_kind": "live-memory",
        "host": host.strip(),
        "total_gpus": len(gpus),
        "total_vram_gib": round(sum(item["memory_total_gib"] for item in gpus), 2),
        "free_vram_gib": round(sum(item["memory_free_gib"] for item in gpus), 2),
        "gpus": gpus,
        "processes": processes,
        "account": _account_summary(current_user, home_directory),
    }
    if cpu is not None:
        result["cpu"] = cpu
    return result


def parse_node_rows(text: str) -> list[dict[str, Any]]:
    nodes_by_name: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = raw.split("|", 3)
        if len(fields) != 4:
            raise ConnectorFailure("parse_failed", "sinfo 返回了无法识别的数据", retryable=True)
        node, partition, state, gres = (field.strip() for field in fields)
        entries = [
            (match.group(1) or "GPU", int(match.group(2)))
            for match in GPU_GRES_RE.finditer(gres)
        ]
        if entries:
            normalized_partition = partition.rstrip("*")
            existing = nodes_by_name.get(node)
            if existing is None:
                nodes_by_name[node] = {
                    "node": node,
                    "partitions": [normalized_partition],
                    "states": [state],
                    "gpu_counts": {name: count for name, count in entries},
                }
                continue
            if normalized_partition not in existing["partitions"]:
                existing["partitions"].append(normalized_partition)
            if state not in existing["states"]:
                existing["states"].append(state)
            for name, count in entries:
                # sinfo emits the same physical device capacity once for every
                # partition. Summing those rows would invent GPUs.
                existing["gpu_counts"][name] = max(existing["gpu_counts"].get(name, 0), count)
    nodes: list[dict[str, Any]] = []
    for item in nodes_by_name.values():
        entries = sorted(item["gpu_counts"].items(), key=lambda entry: entry[0].casefold())
        nodes.append(
            {
                "node": item["node"],
                "partition": ", ".join(item["partitions"]),
                "partitions": list(item["partitions"]),
                "state": ", ".join(item["states"]),
                "gpu_entries": entries,
                "total_gpus": sum(count for _, count in entries),
            }
        )
    return nodes


def parse_job_rows(text: str) -> dict[str, int]:
    used: dict[str, int] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = raw.split("|")
        if len(fields) != 2:
            raise ConnectorFailure("parse_failed", "Slurm 返回了无法识别的节点分配数据", retryable=True)
        node, gres = fields
        count = sum(int(match.group(1)) for match in JOB_GPU_RE.finditer(gres))
        if count:
            used[node.strip()] = used.get(node.strip(), 0) + count
    return used


def _gpu_count(value: str) -> int:
    return sum(int(match.group(1)) for match in JOB_GPU_RE.finditer(value))


def _normalized_job_state(value: str) -> str:
    return (value.strip().split()[0].split("+")[0] if value.strip() else "UNKNOWN").upper()


def _expand_slurm_nodelist(value: str, *, limit: int = SLURM_NODE_EXPANSION_LIMIT) -> set[str]:
    """Expand the common numeric bracket form used by Slurm NodeList values."""

    def split_top_level(text: str) -> list[str]:
        parts: list[str] = []
        start = 0
        depth = 0
        for index, character in enumerate(text):
            if character == "[":
                depth += 1
            elif character == "]":
                depth = max(0, depth - 1)
            elif character == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        parts.append(text[start:])
        return [part for part in parts if part]

    expanded: set[str] = set()

    def add_node(node: str) -> None:
        if node in expanded:
            return
        if len(expanded) >= limit:
            raise ConnectorFailure(
                "node_list_too_large",
                "Slurm 节点列表超过安全解析上限",
                retryable=False,
                state="misconfigured",
            )
        expanded.add(node)

    def append_option(options: list[str], option: str) -> None:
        if len(options) >= limit:
            raise ConnectorFailure(
                "node_list_too_large",
                "Slurm 节点列表超过安全解析上限",
                retryable=False,
                state="misconfigured",
            )
        options.append(option)

    def expand_part(part: str) -> None:
        match = re.search(r"\[([^\]]+)\]", part)
        if not match:
            if "[" in part or "]" in part:
                raise ConnectorFailure("parse_failed", "Slurm 返回了无效的压缩节点列表", retryable=True)
            add_node(part)
            return
        options: list[str] = []
        for token in match.group(1).split(","):
            range_match = re.fullmatch(r"(\d+)-(\d+)(?::(\d+))?", token)
            if not range_match:
                append_option(options, token)
                continue
            start_text, end_text, step_text = range_match.groups()
            start_number, end_number = int(start_text), int(end_text)
            stride = int(step_text or "1")
            if stride < 1:
                raise ConnectorFailure("parse_failed", "Slurm 返回了无效的压缩节点步长", retryable=True)
            step = stride if end_number >= start_number else -stride
            stop = end_number + 1 if step > 0 else end_number - 1
            width = max(len(start_text), len(end_text))
            for number in range(start_number, stop, step):
                append_option(options, f"{number:0{width}d}")
        prefix, suffix = part[: match.start()], part[match.end() :]
        for option in options:
            expand_part(f"{prefix}{option}{suffix}")

    for component in split_top_level(value.strip()):
        expand_part(component)
    return expanded


def job_ids_by_node_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map parsed live tasks to nodes without one remote ``scontrol`` call per job."""

    result: dict[str, list[str]] = {}
    seen_by_node: dict[str, set[str]] = {}
    for task in tasks:
        job_id = str(task.get("job_id") or "").strip()
        node_list = str(task.get("nodes") or "").strip()
        if not job_id or not node_list:
            continue
        for node in sorted(_expand_slurm_nodelist(node_list)):
            seen = seen_by_node.setdefault(node, set())
            if job_id not in seen:
                result.setdefault(node, []).append(job_id)
                seen.add(job_id)
    return result


def parse_live_task_rows(text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = raw.split("|", 10)
        if len(fields) != 11:
            raise ConnectorFailure("parse_failed", "squeue 返回了无法识别的任务详情", retryable=True)
        job_id, state, user, elapsed, time_limit, submitted_at, nodes, reason, node_count_text, gres, job_name = (
            field.strip() for field in fields
        )
        try:
            node_count = max(1, int(node_count_text))
        except ValueError as exc:
            raise ConnectorFailure("parse_failed", "squeue 返回了无效的任务节点数", retryable=True) from exc
        gpu_count = _gpu_count(gres) * node_count
        if gpu_count < 1:
            continue
        node_list = "" if nodes in {"", "(null)", "N/A"} else nodes
        tasks.append(
            {
                "job_id": job_id,
                "name": "" if job_name in {"", "(null)", "N/A"} else job_name,
                "state": _normalized_job_state(state),
                "user": user,
                "elapsed": elapsed,
                "time_limit": time_limit,
                "submitted_at": submitted_at,
                "nodes": node_list,
                "reason": reason if not node_list else "",
                "gpu_count": gpu_count,
            }
        )
    return tasks


def parse_history_task_rows(text: str, gpu_nodes: set[str] | None = None) -> tuple[bool, list[dict[str, Any]]]:
    gpu_nodes = gpu_nodes or set()
    supported = False
    tasks: list[dict[str, Any]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith(HISTORY_SUPPORT_MARKER):
            supported = raw.removeprefix(HISTORY_SUPPORT_MARKER).strip() == "1"
            continue
        fields = raw.split("|", 9)
        if len(fields) != 10:
            continue
        job_id, state, user, elapsed, ended_at, nodes, requested, allocated, exit_code, job_name = (
            field.strip() for field in fields
        )
        gpu_count = _gpu_count(f"{requested},{allocated}")
        node_list = "" if nodes in {"", "Unknown", "None assigned"} else nodes
        if (gpu_count < 1 and not (_expand_slurm_nodelist(node_list) & gpu_nodes)) or "." in job_id:
            continue
        tasks.append(
            {
                "job_id": job_id,
                "name": "" if job_name in {"", "(null)", "N/A"} else job_name,
                "state": _normalized_job_state(state),
                "user": user,
                "elapsed": elapsed,
                "ended_at": ended_at,
                "nodes": node_list,
                "gpu_count": gpu_count or None,
                "exit_code": exit_code,
            }
        )
    tasks.sort(key=lambda item: (item["ended_at"], item["job_id"]), reverse=True)
    return supported, tasks[:40]


def allocated_gpus_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Derive per-node GPU allocations when Slurm omits GRES from AllocTRES."""

    result: dict[str, int] = {}
    for task in tasks:
        nodes = _expand_slurm_nodelist(str(task.get("nodes") or ""))
        gpu_count = int(task.get("gpu_count") or 0)
        if not nodes or gpu_count < 1:
            continue
        per_node = max(1, (gpu_count + len(nodes) - 1) // len(nodes))
        for node in nodes:
            result[node] = result.get(node, 0) + per_node
    return result


def build_node_view(
    nodes: list[dict[str, Any]],
    used_by_node: dict[str, int],
    memory_map: dict[str, float],
    *,
    job_ids_by_node: dict[str, list[str]] | None = None,
    live_tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    job_ids_by_node = job_ids_by_node or {}
    task_by_id = {task["job_id"]: task for task in live_tasks or []}
    result: list[dict[str, Any]] = []
    for node in nodes:
        total = node["total_gpus"]
        allocated = min(used_by_node.get(node["node"], 0), total)
        available = not any(token in node["state"].lower() for token in UNAVAILABLE_STATES)
        free = max(total - allocated, 0) if available else 0
        entries = node["gpu_entries"]
        gpu_type = ", ".join(name if count == 1 else f"{name} x{count}" for name, count in entries)
        capacities = [float(memory_map[name]) for name, _ in entries if name in memory_map]
        uniform = capacities[0] if len(capacities) == len(entries) and len(set(capacities)) == 1 else None
        total_vram = sum(float(memory_map[name]) * count for name, count in entries) if all(name in memory_map for name, _ in entries) else None
        result.append(
            {
                "node": node["node"],
                "partition": node["partition"],
                "state": node["state"],
                "gpu_type": gpu_type,
                "memory_per_gpu_gib": uniform,
                "total_gpus": total,
                "allocated_gpus": allocated,
                "free_gpus": free,
                "total_vram_gib": total_vram,
                "free_vram_gib": free * uniform if uniform is not None else None,
                "tasks": [task_by_id[job_id] for job_id in job_ids_by_node.get(node["node"], []) if job_id in task_by_id],
            }
        )
    return sorted(result, key=lambda item: (item["free_gpus"] == 0, -(item["memory_per_gpu_gib"] or 0), -item["free_gpus"], item["node"]))


def query_slurm_ssh(
    server: ServerProfile,
    *,
    password: str | None = None,
    identities_only: bool = False,
) -> dict[str, Any]:
    queue_scope = "-a" if server.show_other_user_commands else '-u "$current_user"'
    script = f"""\
set -eu
set -o pipefail
current_user=$(id -un)
printf '{CURRENT_USER_MARKER}%s\\n' "$current_user"
home_dir=${{HOME:-}}
if [ -z "$home_dir" ] && command -v getent >/dev/null 2>&1; then
    home_dir=$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{{print $6; exit}}') || true
fi
printf '{HOME_DIRECTORY_MARKER}'; printf '%s' "$home_dir" | od -An -v -tx1 | tr -d ' \\n'; printf '\\n'
sinfo -N -h -o '%N|%P|%t|%G'
printf '{SPLIT_MARKER}\\n'
scontrol show nodes -o | awk '
{{
    node = ""
    alloc = ""
    for (i = 1; i <= NF; i++) {{
        if ($i ~ /^NodeName=/) {{
            node = $i
            sub(/^NodeName=/, "", node)
        }} else if ($i ~ /^AllocTRES=/) {{
            alloc = $i
            sub(/^AllocTRES=/, "", alloc)
        }}
    }}
    if (node != "")
        printf "%s|%s\\n", node, alloc
}}'
printf '{LIVE_TASK_MARKER}\\n'
squeue {queue_scope} -t PENDING,RUNNING,COMPLETING,CONFIGURING,SUSPENDED -h -o '%i|%T|%u|%M|%l|%V|%N|%R|%D|%b|%j' | sed -n '1,2000p'
printf '{HISTORY_MARKER}\\n'
if command -v sacct >/dev/null 2>&1; then
    if sacct {queue_scope} -X -S now-24hours -n -P --format=JobIDRaw,State,User,Elapsed,End,NodeList,ReqTRES,AllocTRES,ExitCode,JobName%256 2>/dev/null \
        | awk -F'|' '$2 ~ /COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY/' \
        | sort -t'|' -k5,5r | sed -n '1,80p'; then
        printf '{HISTORY_SUPPORT_MARKER}1\\n'
    else
        printf '{HISTORY_SUPPORT_MARKER}0\\n'
    fi
else
    printf '{HISTORY_SUPPORT_MARKER}0\\n'
fi
"""
    output = run_remote(server, script, password=password, identities_only=identities_only)
    node_text, allocation_marker, rest = output.partition(SPLIT_MARKER)
    allocation_text, live_marker, rest = rest.partition(LIVE_TASK_MARKER)
    live_text, history_marker, history_text = rest.partition(HISTORY_MARKER)
    if not allocation_marker or not live_marker or not history_marker:
        raise ConnectorFailure("parse_failed", "Slurm 返回了不完整的调度快照", retryable=True)
    current_user = ""
    home_directory = ""
    node_lines: list[str] = []
    for raw in node_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith(CURRENT_USER_MARKER):
            current_user = stripped.removeprefix(CURRENT_USER_MARKER).strip()
        elif stripped.startswith(HOME_DIRECTORY_MARKER):
            home_directory = _decode_hex(
                stripped.removeprefix(HOME_DIRECTORY_MARKER).strip(), "账号主目录", limit=65_536
            )
        elif stripped:
            node_lines.append(raw)
    parsed_nodes = parse_node_rows("\n".join(node_lines))
    if not parsed_nodes:
        raise ConnectorFailure(
            "gpu_inventory_empty",
            "SSH 和 Slurm 已连接，但没有识别到任何 GPU GRES；请检查 sinfo 输出和 GRES 配置",
            retryable=False,
            state="misconfigured",
        )
    live_tasks = parse_live_task_rows(live_text)
    history_supported, recent_tasks = parse_history_task_rows(
        history_text, {node["node"] for node in parsed_nodes}
    )
    slurm_allocations = parse_job_rows(allocation_text)
    visible_task_allocations = allocated_gpus_from_tasks(live_tasks)
    used_by_node = {
        node["node"]: max(
            slurm_allocations.get(node["node"], 0),
            visible_task_allocations.get(node["node"], 0),
        )
        for node in parsed_nodes
    }
    nodes = build_node_view(
        parsed_nodes,
        used_by_node,
        server.gpu_memory_gib,
        job_ids_by_node=job_ids_by_node_from_tasks(live_tasks),
        live_tasks=live_tasks,
    )
    counts: dict[str, int] = {}
    for task in [*live_tasks, *recent_tasks]:
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    node_totals = [item["total_vram_gib"] for item in nodes]
    total_vram_gib = None if any(value is None for value in node_totals) else round(sum(node_totals), 2)
    node_free_totals = [item["free_vram_gib"] for item in nodes]
    free_vram_gib = (
        None
        if any(value is None for value in node_free_totals)
        else round(sum(float(value) for value in node_free_totals), 2)
    )
    return {
        "server_id": server.id,
        "display_name": server.display_name,
        "backend": server.backend,
        "view_kind": "scheduler",
        "total_gpus": sum(item["total_gpus"] for item in nodes),
        "free_gpus": sum(item["free_gpus"] for item in nodes),
        "total_vram_gib": total_vram_gib,
        "free_vram_gib": free_vram_gib,
        "nodes": nodes,
        "tasks": {
            "current_user": current_user,
            "active": live_tasks,
            "recent": recent_tasks,
            "counts": counts,
            "history_supported": history_supported,
            "history_window_hours": 24,
        },
        "account": _account_summary(current_user, home_directory),
    }


def query_server(
    server: ServerProfile,
    *,
    password: str | None = None,
    identities_only: bool = False,
) -> dict[str, Any]:
    if server.backend == "direct_ssh":
        return query_direct_ssh(server, password=password, identities_only=identities_only)
    if server.backend == "slurm_ssh":
        return query_slurm_ssh(server, password=password, identities_only=identities_only)
    raise ConnectorFailure("config_invalid", f"不支持的后端：{server.backend}", retryable=False, state="misconfigured")
