"""Opt-in host-owned SSH repair, separate from importable server Profiles.

The approved helper owns credentials, endpoint pinning, shared locking/backoff,
and the narrow remote mutation. Radar supplies no password or remote command.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .connectors import ConnectorFailure, _run_bounded_process
from .models import ServerProfile
from .service import connection_fingerprint


def recovery_pending() -> ConnectorFailure:
    return ConnectorFailure(
        "auth_recovery_pending", "SSH 自动恢复尚未验证成功，5 分钟后重试",
        retryable=True, state="auth_required", retry_after_seconds=300,
    )


class HostSshRecovery:
    """Run only a locally registered, hash-pinned Python helper on key rejection.

    No configuration means ordinary Radar authentication behavior. Registrations
    are an explicit local executable authorization, never an imported catalog
    field. Configuration is revalidated before every attempt, including cooldown.
    """

    def __init__(self, path: Path, profile_id: str, *, clock=time.monotonic) -> None:
        self.path = path
        self.profile_id = profile_id
        self.clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, tuple[float, bool]] = {}

    def __call__(self, server: ServerProfile) -> bool | None:
        with self._lock:
            try:
                if not self.path.exists():
                    return None
                if self.path.stat().st_size > 65536:
                    raise ValueError("oversized registration")
                config = json.loads(self.path.read_text(encoding="utf-8"))
                if config["schema_version"] != 1:
                    raise ValueError("unsupported registration")
                binding = config["profiles"].get(self.profile_id, {}).get(server.id)
                if binding is None:
                    return None
                if (server.backend != "direct_ssh"
                        or binding["connection_fingerprint"] != connection_fingerprint(server)):
                    raise ValueError("connection changed")
                python = Path(binding["python"])
                helper = Path(binding["helper"])
                pins = binding["files_sha256"]
                if str(python) not in pins or str(helper) not in pins or not pins:
                    raise ValueError("unbound executable")
                for name, digest in pins.items():
                    path = Path(name)
                    if not path.is_absolute() or not path.is_file():
                        raise ValueError("invalid pinned file")
                    with path.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != digest:
                        raise ValueError("pinned file changed")
                if helper.suffix.lower() != ".py":
                    raise ValueError("not a Python helper")
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                # Never echo configuration, helper output or exception details.
                raise ConnectorFailure(
                    "auth_recovery_config_invalid", "SSH 自动恢复配置已变化或无效，请核对本机授权",
                    retryable=False, state="security_blocked",
                ) from None

            now = self.clock()
            previous = self._attempts.get(server.id)
            if previous is not None and 0 <= now - previous[0] < 300:
                if previous[1] and now - previous[0] < 5:
                    return True  # Concurrent reads share one verified recovery.
                raise recovery_pending()
            self._attempts[server.id] = (now, False)
            allowed = {
                "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT",
                "USERPROFILE", "USERNAME", "USERDOMAIN", "HOMEDRIVE", "HOMEPATH",
                "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "TEMP", "TMP",
            }
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            try:
                result = _run_bounded_process(
                    [str(python), "-I", "-B", str(helper)],
                    stdin=subprocess.DEVNULL, timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
                    stdout_limit=4096, stderr_limit=4096,
                )
                receipt = json.loads(result.stdout)
                ok = (result.returncode == 0 and not result.stdout_truncated
                      and receipt.get("server") == server.id
                      and receipt.get("status") in {"ok", "key_recovered", "recent_key_verification"})
            except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
                ok = False
            self._attempts[server.id] = (self.clock(), ok)
            if not ok:
                raise recovery_pending()
            return True
