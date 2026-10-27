"""Host-only, hash-pinned authorization for the protected ACP read-only helper.

Never loaded from a server catalog or a portable Profile. The credential owner
still enforces its named-profile child authorization; these pins detect drift,
not a malicious user able to rewrite both registration and executable files.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

from .models import ServerProfile
from .ssh_transport import _long_path, regular_path

_active = ContextVar("radar_host_acp_transport", default=None)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _directory(path: Path) -> Path:
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("invalid working directory")
    if sys.platform.startswith("win"):
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("linked working directory")
    return _long_path(path.resolve(strict=True))


@dataclass(frozen=True)
class AcpTransport:
    python: str
    helper: str
    cwd: str
    credential_profile: str
    connection_fingerprint: str
    page_size: int = 100

    def command(self) -> list[str]:
        return [self.python, "-I", "-B", self.helper]

    def request(self, server: ServerProfile) -> bytes:
        from .service import connection_fingerprint
        if server.backend != "sensecore_acp" or connection_fingerprint(server) != self.connection_fingerprint:
            raise ValueError("connection changed")
        # Deliberately no command/argv/URL field. This protocol can only list a
        # single bounded page from the exact bound ACP connection.
        return json.dumps({
            "schema_version": 1, "action": "jobs-list",
            "credential_profile": self.credential_profile,
            "workspace": server.acp_workspace, "cluster": server.acp_cluster,
            "zone": server.acp_zone, "sco_profile": server.acp_profile,
            "sco_path": server.acp_sco_path, "page_size": self.page_size,
            "page_token": "1", "timeout_seconds": server.connect_timeout_seconds,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")


@contextmanager
def transport_scope(server: ServerProfile, transport: AcpTransport | None):
    token = _active.set((server, transport))
    try:
        yield
    finally:
        _active.reset(token)


def current_transport(server: ServerProfile) -> AcpTransport | None:
    bound = _active.get()
    return bound[1] if bound is not None and bound[0] == server else None


class HostAcpTransport:
    def __init__(self, path: Path, profile_id: str):
        self.path, self.profile_id = path, profile_id

    def __call__(self, server: ServerProfile) -> AcpTransport | None:
        from .connectors import ConnectorFailure
        from .service import connection_fingerprint
        if server.backend != "sensecore_acp":
            return None
        try:
            if not self.path.exists():
                if server.acp_protected_auth:
                    raise ValueError("required protected registration missing")
                return None
            registration = regular_path(self.path)
            if registration.stat().st_size > 65536:
                raise ValueError("oversized registration")
            data = json.loads(registration.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {"schema_version", "profiles"} or type(data["schema_version"]) is not int or data["schema_version"] != 1:
                raise ValueError("unsupported registration")
            binding = data["profiles"].get(self.profile_id, {}).get(server.id)
            if binding is None:
                if server.acp_protected_auth:
                    raise ValueError("required protected binding missing")
                return None
            if not isinstance(binding, dict) or set(binding) - {"page_size"} != {
                "connection_fingerprint", "python", "helper", "cwd", "credential_profile", "files_sha256"
            }:
                raise ValueError("unsupported binding")
            page_size = binding.get("page_size", 100)
            if type(page_size) is not int or page_size not in (10, 100):
                raise ValueError("unsupported page size")
            fingerprint = connection_fingerprint(server)
            if binding["connection_fingerprint"] != fingerprint:
                raise ValueError("connection changed")
            credential_profile = binding["credential_profile"]
            if not isinstance(credential_profile, str) or not _IDENTIFIER.fullmatch(credential_profile):
                raise ValueError("invalid credential profile")
            python, helper, cwd = (Path(binding[k]) for k in ("python", "helper", "cwd"))
            if helper.suffix.lower() != ".py" or python.suffix.lower() in {".py", ".cmd", ".bat", ".ps1"}:
                raise ValueError("invalid executable")
            pins = binding["files_sha256"]
            if not isinstance(pins, dict) or set(pins) != {str(python), str(helper)} or len(pins) != 2:
                raise ValueError("unbound executable")
            for name, digest in pins.items():
                if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                    raise ValueError("invalid pin")
                path = regular_path(Path(name))
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError("pinned file changed")
            return AcpTransport(str(regular_path(python)), str(regular_path(helper)),
                                str(_directory(cwd)), credential_profile, fingerprint, page_size)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ConnectorFailure("acp_transport_config_invalid",
                                   "云任务授权已变更，请修复本机注册",
                                   retryable=False, state="security_blocked") from None
