"""Explicit trusted-user executable authorization, never catalog-imported.

Like the existing SSH recovery registration, this local document is trusted
configuration. Hashes detect accidental drift; they are not an ACL/sandbox
against a user who can replace both configuration and executable code. The
credential owner independently enforces its own runtime/script authorization.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
import stat
import sys

from .models import ServerProfile

_active = ContextVar("radar_host_transport", default=None)


def _long_path(path: Path) -> Path:
    """Prefer the Windows long path so short 8.3 names do not break pin checks."""
    import os
    if os.name == "nt":
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(str(path), buf, len(buf))
        if n:
            return Path(buf.value)
    return path


def regular_path(path):
    if not path.is_absolute():
        raise ValueError("relative path")
    if sys.platform.startswith("win"):
        # Windows: reject any reparse/symlink in the path chain (junctions, etc.).
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("linked path")
        if not path.is_file():
            raise ValueError("not a regular file")
        return _long_path(path.resolve(strict=True))
    # POSIX: /tmp and /var commonly symlink; require a resolved regular file.
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("not a regular file")
    return resolved


@dataclass(frozen=True)
class Transport:
    python: str
    helper: str
    cwd: str
    alias: str

    def command(self, script: str, timeout: int) -> list[str]:
        return [self.python, "-I", "-B", self.helper, "ssh",
                "-o", f"ConnectTimeout={timeout}", "-o", "ConnectionAttempts=1",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=6",
                self.alias, "bash", "-lc", shlex.quote(script)]


@contextmanager
def transport_scope(server, transport):
    token = _active.set((server, transport))
    try:
        yield
    finally:
        _active.reset(token)


def current_transport(server):
    bound = _active.get()
    return bound[1] if bound is not None and bound[0] == server else None


class HostSshTransport:
    def __init__(self, path: Path, profile_id: str):
        self.path, self.profile_id = path, profile_id

    def __call__(self, server: ServerProfile):
        from .connectors import ConnectorFailure
        from .service import connection_fingerprint
        try:
            if not self.path.exists():
                return None
            registration = regular_path(self.path)
            if registration.stat().st_size > 65536:
                raise ValueError("oversized registration")
            data = json.loads(registration.read_text(encoding="utf-8"))
            if data["schema_version"] != 1:
                raise ValueError("unsupported registration")
            binding = data["profiles"].get(self.profile_id, {}).get(server.id)
            if binding is None:
                return None
            if (server.backend != "direct_ssh" or not server.ssh_alias
                    or binding["ssh_alias"] != server.ssh_alias
                    or binding["connection_fingerprint"] != connection_fingerprint(server)):
                raise ValueError("connection changed")
            python, helper, cwd = (Path(binding[k]) for k in ("python", "helper", "cwd"))
            pins = binding["files_sha256"]
            if str(python) not in pins or str(helper) not in pins or helper.suffix.lower() != ".py":
                raise ValueError("unbound executable")
            if not cwd.is_absolute() or not cwd.is_dir():
                raise ValueError("invalid working directory")
            for name, digest in pins.items():
                path = regular_path(Path(name))
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError("pinned file changed")
            return Transport(str(regular_path(python)), str(regular_path(helper)), str(_long_path(cwd.resolve(strict=True))), server.ssh_alias)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ConnectorFailure("host_transport_config_invalid",
                                   "本机密码连接配置已变化，请修复本机授权", retryable=False,
                                   state="security_blocked") from None
