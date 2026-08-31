from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
import tomllib
import uuid

from platformdirs import PlatformDirs
import tomli_w

from .models import ConfigError, Profile, require_id
from .window_state import WindowGeometry


@dataclass(frozen=True)
class StoragePaths:
    config: Path
    cache: Path
    logs: Path
    runtime: Path


def storage_paths(home: Path | None = None) -> StoragePaths:
    explicit = home or (Path(os.environ["VRAM_RADAR_HOME"]) if os.environ.get("VRAM_RADAR_HOME") else None)
    if explicit is not None:
        root = explicit.expanduser().resolve()
        return StoragePaths(root / "config", root / "cache", root / "logs", root / "runtime")
    dirs = PlatformDirs(appname="VRAMRadar", appauthor="VRAMRadar", roaming=False, ensure_exists=False)
    return StoragePaths(
        Path(dirs.user_config_dir),
        Path(dirs.user_cache_dir),
        Path(dirs.user_log_dir),
        Path(dirs.user_runtime_dir),
    )


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed ``.tmp`` name lets two independent profile/cache writes trample
    # each other before the final replace.  Keep the temporary beside the
    # destination (so replace remains atomic) while giving every writer its own
    # file.  The AppApi mutation lock serializes profile semantics; this also
    # makes lower-level cache writes and crash recovery safe.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class ProfileStore:
    def __init__(self, paths: StoragePaths) -> None:
        self.paths = paths

    def profile_path(self, profile_id: str) -> Path:
        require_id(profile_id, "profile id")
        return self.paths.config / "profiles" / f"{profile_id}.toml"

    def load(self, profile_id: str) -> Profile:
        path = self.profile_path(profile_id)
        if not path.exists():
            return Profile.empty(profile_id)
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid profile TOML: {exc}") from exc
        return Profile.from_dict(raw, expected_id=profile_id)

    def save(self, profile: Profile) -> Path:
        path = self.profile_path(profile.id)
        atomic_write_text(path, tomli_w.dumps(profile.to_dict()))
        return path

    def list_profiles(self) -> list[str]:
        root = self.paths.config / "profiles"
        if not root.exists():
            return []
        return sorted(path.stem for path in root.glob("*.toml") if path.is_file())


class NotificationStateStore:
    """Machine-local durable state for the unified notification center.

    The Profile owns notification policy.  This separate bounded JSON file owns
    ephemeral observations, unread state, and the last active-task baseline so
    a completion that happened while the app was closed can be reported on the
    next live refresh.
    """

    MAX_BYTES = 1_048_576

    def __init__(self, paths: StoragePaths, profile_id: str) -> None:
        require_id(profile_id, "profile id")
        self.path = paths.config / "notifications" / f"{profile_id}.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return {}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return {}
        return raw

    def save(self, state: dict[str, Any]) -> Path:
        document = {"schema_version": 1, **state}
        atomic_write_text(
            self.path,
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
        return self.path


class WindowStateStore:
    """Small machine-local store for the last useful normal window size."""

    def __init__(self, paths: StoragePaths) -> None:
        self.path = paths.config / "window-state.json"

    def load(self) -> WindowGeometry:
        if not self.path.is_file():
            return WindowGeometry()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return WindowGeometry()
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return WindowGeometry()
        geometry = WindowGeometry.validated(raw.get("width"), raw.get("height"))
        return geometry or WindowGeometry()

    def save(self, geometry: WindowGeometry) -> Path:
        validated = WindowGeometry.validated(geometry.width, geometry.height)
        if validated is None:
            raise ValueError("window geometry is outside the supported bounds")
        document = {
            "schema_version": 1,
            "width": validated.width,
            "height": validated.height,
        }
        atomic_write_text(
            self.path,
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
        return self.path


class SnapshotCache:
    def __init__(self, paths: StoragePaths, profile_id: str) -> None:
        require_id(profile_id, "profile id")
        self.root = paths.cache / profile_id

    def path_for(self, server_id: str) -> Path:
        require_id(server_id, "server id")
        return self.root / f"{server_id}.json"

    def load(self, server_id: str, *, connection_fingerprint: str) -> dict[str, Any] | None:
        path = self.path_for(server_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 2
            or raw.get("server_id") != server_id
            or raw.get("connection_fingerprint") != connection_fingerprint
            or not isinstance(raw.get("last_success_at"), str)
            or not isinstance(raw.get("payload"), dict)
        ):
            return None
        return raw

    def save(
        self,
        server_id: str,
        last_success_at: str,
        payload: dict[str, Any],
        *,
        connection_fingerprint: str,
    ) -> Path:
        document = {
            "schema_version": 2,
            "server_id": server_id,
            "connection_fingerprint": connection_fingerprint,
            "last_success_at": last_success_at,
            "payload": payload,
        }
        path = self.path_for(server_id)
        atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return path
