from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SUPPORTED_BACKENDS = frozenset({"direct_ssh", "slurm_ssh"})
SUPPORTED_CLOSE_BEHAVIORS = frozenset({"tray", "exit"})
SUPPORTED_UI_LANGUAGES = frozenset({"zh-CN", "en"})
SUPPORTED_SAVED_VIEW_FILTERS = frozenset({"all", "available", "tasks", "issues"})
MAX_FAVORITE_SERVER_IDS = 512
MAX_IGNORED_SSH_ALIASES = 4096
MAX_SAVED_VIEWS = 32
MAX_SAVED_VIEW_QUERY_BYTES = 256
MAX_SAVED_VIEW_TEXT_BYTES = 128
DEFAULT_MEMORY_MAP = {
    "A100-40G": 40.0,
    "3g.20gb": 20.0,
    "RTX-3090": 24.0,
    "R2080": 12.0,
    "TITANX": 12.0,
}


class ConfigError(ValueError):
    """Raised when a local profile is invalid."""


def require_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ConfigError(f"{label} must match {SAFE_ID.pattern}")
    return value


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be true or false")
    return value


def require_optional_ssh_token(value: Any, label: str, *, maximum_bytes: int = 1024) -> str:
    """Validate one local OpenSSH endpoint/user token without narrowing valid host syntax."""

    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    token = value.strip()
    if (
        not token
        or token.startswith("-")
        or len(token.encode("utf-8")) > maximum_bytes
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in token)
    ):
        raise ConfigError(f"{label} must be a bounded SSH token without whitespace or control characters")
    return token


def require_optional_local_path(value: Any, label: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    path = value.strip()
    if (
        not path
        or len(path.encode("utf-8")) > 4096
        or any(character in path for character in ("\x00", "\r", "\n"))
    ):
        raise ConfigError(f"{label} is too long or contains control characters")
    return path


def require_optional_remote_directory(value: Any, label: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    directory = value.strip()
    if (
        not directory.startswith("/")
        or len(directory.encode("utf-8")) > 4096
        or any(character in directory for character in ("\x00", "\r", "\n"))
    ):
        raise ConfigError(f"{label} must be an absolute remote directory without control characters")
    return directory


def require_bounded_text(
    value: Any,
    label: str,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ConfigError(f"{label} must be a non-empty string")
    if len(text.encode("utf-8")) > maximum_bytes or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        raise ConfigError(f"{label} is too long or contains control characters")
    return text


def normalize_saved_view(raw: Any) -> dict[str, Any]:
    """Validate one small, non-secret fleet/multi-GPU view."""

    if not isinstance(raw, dict):
        raise ConfigError("each saved view must be a table")
    allowed_keys = {
        "id",
        "name",
        "query",
        "filter",
        "gpu_count",
        "min_memory_gib",
        "gpu_type",
        "partition",
        "same_node",
    }
    unknown_keys = set(raw) - allowed_keys
    if unknown_keys:
        raise ConfigError(f"saved view contains unsupported fields: {', '.join(sorted(unknown_keys))}")

    view_id = require_id(raw.get("id"), "saved view id")
    name = require_bounded_text(
        raw.get("name"),
        f"saved view {view_id} name",
        maximum_bytes=MAX_SAVED_VIEW_TEXT_BYTES,
    )
    query = require_bounded_text(
        raw.get("query", ""),
        f"saved view {view_id} query",
        maximum_bytes=MAX_SAVED_VIEW_QUERY_BYTES,
        allow_empty=True,
    )
    selected_filter = raw.get("filter", "all")
    if not isinstance(selected_filter, str) or selected_filter.strip().lower() not in SUPPORTED_SAVED_VIEW_FILTERS:
        raise ConfigError(
            f"saved view {view_id} filter must be one of {sorted(SUPPORTED_SAVED_VIEW_FILTERS)}"
        )

    gpu_count = raw.get("gpu_count", 1)
    if isinstance(gpu_count, bool) or not isinstance(gpu_count, int) or not 1 <= gpu_count <= 10_000:
        raise ConfigError(f"saved view {view_id} gpu_count must be between 1 and 10000")
    minimum_memory = raw.get("min_memory_gib", 0)
    if (
        isinstance(minimum_memory, bool)
        or not isinstance(minimum_memory, (int, float))
        or not 0 <= float(minimum_memory) <= 1000
    ):
        raise ConfigError(f"saved view {view_id} min_memory_gib must be between 0 and 1000")
    gpu_type = require_bounded_text(
        raw.get("gpu_type", ""),
        f"saved view {view_id} gpu_type",
        maximum_bytes=MAX_SAVED_VIEW_TEXT_BYTES,
        allow_empty=True,
    )
    partition = require_bounded_text(
        raw.get("partition", ""),
        f"saved view {view_id} partition",
        maximum_bytes=MAX_SAVED_VIEW_TEXT_BYTES,
        allow_empty=True,
    )
    same_node = raw.get("same_node", False)
    if not isinstance(same_node, bool):
        raise ConfigError(f"saved view {view_id} same_node must be true or false")
    return {
        "id": view_id,
        "name": name,
        "query": query,
        "filter": selected_filter.strip().lower(),
        "gpu_count": gpu_count,
        "min_memory_gib": float(minimum_memory),
        "gpu_type": gpu_type,
        "partition": partition,
        "same_node": same_node,
    }


@dataclass(frozen=True)
class ServerProfile:
    id: str
    display_name: str
    backend: str
    enabled: bool = True
    ssh_alias: str = ""
    host: str = ""
    port: int = 22
    port_override: bool = False
    username: str = ""
    identity_file: str = ""
    ssh_config_file: str = ""
    auth_ref: str = ""
    default_work_directory: str = ""
    connect_timeout_seconds: int = 10
    show_other_user_commands: bool = False
    prefer_identity_auth: bool = False
    gpu_memory_gib: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MEMORY_MAP))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServerProfile":
        if not isinstance(raw, dict):
            raise ConfigError("each server must be a table")
        server_id = require_id(raw.get("id"), "server id")
        backend = raw.get("backend")
        if backend not in SUPPORTED_BACKENDS:
            raise ConfigError(f"server {server_id} backend must be one of {sorted(SUPPORTED_BACKENDS)}")
        alias = require_optional_ssh_token(raw.get("ssh_alias", ""), f"server {server_id} ssh_alias")
        host = require_optional_ssh_token(raw.get("host", ""), f"server {server_id} host")
        if not alias and not host:
            raise ConfigError(f"server {server_id} requires ssh_alias or host")
        port = raw.get("port", 22)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigError(f"server {server_id} port must be between 1 and 65535")
        raw_port_override = raw.get("port_override")
        if raw_port_override is None:
            # Profiles written before the explicit override bit could only have
            # expressed an alias-port override by changing the legacy 22 value.
            port_override = bool(alias and port != 22)
        else:
            port_override = require_bool(raw_port_override, f"server {server_id} port_override")
        timeout = raw.get("connect_timeout_seconds", 10)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 3 <= timeout <= 60:
            raise ConfigError(f"server {server_id} connect_timeout_seconds must be between 3 and 60")
        memory = dict(DEFAULT_MEMORY_MAP)
        custom_memory = raw.get("gpu_memory_gib", {})
        if not isinstance(custom_memory, dict):
            raise ConfigError(f"server {server_id} gpu_memory_gib must be a table")
        for raw_name, value in custom_memory.items():
            if not isinstance(raw_name, str):
                raise ConfigError(f"server {server_id} has an invalid gpu_memory_gib entry")
            name = require_bounded_text(
                raw_name,
                f"server {server_id} gpu_memory_gib name",
                maximum_bytes=128,
            )
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 < float(value) <= 1000
            ):
                raise ConfigError(f"server {server_id} has an invalid gpu_memory_gib entry")
            memory[name] = float(value)
        return cls(
            id=server_id,
            display_name=require_bounded_text(
                raw.get("display_name", server_id),
                f"server {server_id} display_name",
                maximum_bytes=256,
            ),
            backend=backend,
            enabled=require_bool(raw.get("enabled", True), f"server {server_id} enabled"),
            ssh_alias=alias,
            host=host,
            port=port,
            port_override=port_override,
            username=require_optional_ssh_token(
                raw.get("username", ""), f"server {server_id} username", maximum_bytes=256
            ),
            identity_file=require_optional_local_path(
                raw.get("identity_file", ""), f"server {server_id} identity_file"
            ),
            ssh_config_file=require_optional_local_path(
                raw.get("ssh_config_file", ""), f"server {server_id} ssh_config_file"
            ),
            auth_ref=require_optional_local_path(raw.get("auth_ref", ""), f"server {server_id} auth_ref"),
            default_work_directory=require_optional_remote_directory(
                raw.get("default_work_directory", ""),
                f"server {server_id} default_work_directory",
            ),
            connect_timeout_seconds=timeout,
            show_other_user_commands=require_bool(
                raw.get("show_other_user_commands", False),
                f"server {server_id} show_other_user_commands",
            ),
            prefer_identity_auth=require_bool(
                raw.get("prefer_identity_auth", False),
                f"server {server_id} prefer_identity_auth",
            ),
            gpu_memory_gib=memory,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "display_name": self.display_name,
            "backend": self.backend,
            "enabled": self.enabled,
            "port": self.port,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "show_other_user_commands": self.show_other_user_commands,
        }
        if self.prefer_identity_auth:
            result["prefer_identity_auth"] = True
        if self.port_override:
            result["port_override"] = True
        for key in (
            "ssh_alias",
            "host",
            "username",
            "identity_file",
            "ssh_config_file",
            "auth_ref",
            "default_work_directory",
        ):
            value = getattr(self, key)
            if value:
                result[key] = value
        if self.backend == "slurm_ssh":
            result["gpu_memory_gib"] = self.gpu_memory_gib
        return result


@dataclass(frozen=True)
class Profile:
    id: str
    display_name: str
    refresh_seconds: int = 15
    servers: tuple[ServerProfile, ...] = ()
    server_config_path: str = ""
    auto_sync_servers: bool = False
    ignored_ssh_aliases: tuple[str, ...] = ()
    navigator_side: str = "right"
    close_behavior: str = "tray"
    ui_language: str = "zh-CN"
    favorite_server_ids: tuple[str, ...] = ()
    saved_views: tuple[dict[str, Any], ...] = ()
    schema_version: int = 1

    @classmethod
    def empty(cls, profile_id: str) -> "Profile":
        require_id(profile_id, "profile id")
        return cls(id=profile_id, display_name="我的 GPU")

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, expected_id: str | None = None) -> "Profile":
        if not isinstance(raw, dict):
            raise ConfigError("profile must be a TOML table")
        if raw.get("schema_version") != 1:
            raise ConfigError("profile schema_version must be 1")
        profile_id = require_id(raw.get("id"), "profile id")
        if expected_id is not None and profile_id != expected_id:
            raise ConfigError(f"profile id {profile_id!r} does not match filename {expected_id!r}")
        refresh = raw.get("refresh_seconds", 15)
        if isinstance(refresh, bool) or not isinstance(refresh, int) or not 5 <= refresh <= 300:
            raise ConfigError("refresh_seconds must be between 5 and 300")
        servers = tuple(ServerProfile.from_dict(item) for item in raw.get("servers", []))
        ids = [server.id for server in servers]
        if len(ids) != len({server_id.casefold() for server_id in ids}):
            raise ConfigError("server ids must be unique within a profile, ignoring case")
        server_config_path = require_optional_local_path(
            raw.get("server_config_path", ""), "profile server_config_path"
        )
        auto_sync_servers = raw.get("auto_sync_servers", False)
        if not isinstance(auto_sync_servers, bool):
            raise ConfigError("profile auto_sync_servers must be true or false")
        if auto_sync_servers and not server_config_path:
            raise ConfigError("profile auto_sync_servers requires server_config_path")
        ignored_ssh_alias_rows = raw.get("ignored_ssh_aliases", [])
        if not isinstance(ignored_ssh_alias_rows, (list, tuple)):
            raise ConfigError("profile ignored_ssh_aliases must be an array")
        if len(ignored_ssh_alias_rows) > MAX_IGNORED_SSH_ALIASES:
            raise ConfigError(
                "profile ignored_ssh_aliases cannot contain more than "
                f"{MAX_IGNORED_SSH_ALIASES} entries"
            )
        ignored_ssh_aliases: list[str] = []
        for raw_alias in ignored_ssh_alias_rows:
            alias = require_optional_ssh_token(raw_alias, "ignored SSH alias")
            if not alias:
                raise ConfigError("ignored SSH alias must be a non-empty SSH token")
            ignored_ssh_aliases.append(alias)
        if len(ignored_ssh_aliases) != len({alias.casefold() for alias in ignored_ssh_aliases}):
            raise ConfigError("profile ignored_ssh_aliases must be unique, ignoring case")
        navigator_side = raw.get("navigator_side", "right")
        if not isinstance(navigator_side, str) or navigator_side.strip().lower() not in {"left", "right"}:
            raise ConfigError("profile navigator_side must be left or right")
        close_behavior = raw.get("close_behavior", "tray")
        if (
            not isinstance(close_behavior, str)
            or close_behavior.strip().lower() not in SUPPORTED_CLOSE_BEHAVIORS
        ):
            raise ConfigError("profile close_behavior must be tray or exit")
        ui_language = raw.get("ui_language", "zh-CN")
        if not isinstance(ui_language, str) or ui_language.strip() not in SUPPORTED_UI_LANGUAGES:
            raise ConfigError("profile ui_language must be zh-CN or en")
        favorite_server_ids = raw.get("favorite_server_ids", [])
        if not isinstance(favorite_server_ids, (list, tuple)):
            raise ConfigError("profile favorite_server_ids must be an array")
        if len(favorite_server_ids) > MAX_FAVORITE_SERVER_IDS:
            raise ConfigError(
                f"profile favorite_server_ids cannot contain more than {MAX_FAVORITE_SERVER_IDS} entries"
            )
        favorites = tuple(
            require_id(server_id, "favorite server id") for server_id in favorite_server_ids
        )
        if len(favorites) != len(set(favorites)):
            raise ConfigError("profile favorite_server_ids must be unique")
        saved_view_rows = raw.get("saved_views", [])
        if not isinstance(saved_view_rows, (list, tuple)):
            raise ConfigError("profile saved_views must be an array")
        if len(saved_view_rows) > MAX_SAVED_VIEWS:
            raise ConfigError(f"profile saved_views cannot contain more than {MAX_SAVED_VIEWS} entries")
        saved_views = tuple(normalize_saved_view(item) for item in saved_view_rows)
        view_ids = [view["id"] for view in saved_views]
        if len(view_ids) != len(set(view_ids)):
            raise ConfigError("saved view ids must be unique within a profile")
        return cls(
            id=profile_id,
            display_name=require_bounded_text(
                raw.get("display_name", profile_id),
                "profile display_name",
                maximum_bytes=256,
            ),
            refresh_seconds=refresh,
            servers=servers,
            server_config_path=server_config_path,
            auto_sync_servers=auto_sync_servers,
            ignored_ssh_aliases=tuple(ignored_ssh_aliases),
            navigator_side=navigator_side.strip().lower(),
            close_behavior=close_behavior.strip().lower(),
            ui_language=ui_language.strip(),
            favorite_server_ids=favorites,
            saved_views=saved_views,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "display_name": self.display_name,
            "refresh_seconds": self.refresh_seconds,
            "server_config_path": self.server_config_path,
            "auto_sync_servers": self.auto_sync_servers,
            "ignored_ssh_aliases": list(self.ignored_ssh_aliases),
            "navigator_side": self.navigator_side,
            "close_behavior": self.close_behavior,
            "ui_language": self.ui_language,
            "favorite_server_ids": list(self.favorite_server_ids),
            "saved_views": [dict(view) for view in self.saved_views],
            "servers": [server.to_dict() for server in self.servers],
        }
