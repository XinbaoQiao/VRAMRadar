from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import glob
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
from threading import RLock
import tomllib
from typing import Any

from .models import (
    ConfigError,
    Profile,
    ServerProfile,
    alias_choice_group_key,
    normalize_pending_alias_choice,
)
from .openssh_resolution import (
    format_openssh_connection_summary,
    resolve_openssh_destination,
    resolve_openssh_identity_files,
)


MAX_CATALOG_BYTES = 1_048_576
MAX_OPENSSH_FILES = 64
MAX_OPENSSH_TOTAL_BYTES = 4 * MAX_CATALOG_BYTES
MAX_OPENSSH_FINGERPRINT_CACHE_ENTRIES = 32
MAX_OPENSSH_INCLUDE_WATCHES = 256
MAX_OPENSSH_INCLUDE_WATCH_BYTES = 65_536
SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:password|passwd|secret|token|private[_-]?key|api[_-]?key|credential)(?:$|[_-])",
    re.IGNORECASE,
)
MEMORY_SUFFIX = re.compile(r"(\d+(?:\.\d+)?)\s*G(?:B)?(?:$|[^A-Z])", re.IGNORECASE)
BACKEND_MAP = {"ssh": "direct_ssh", "slurm": "slurm_ssh"}
OPENSSH_PATTERN_CHARACTERS = frozenset("*!?[]")
REMOTE_SSH_SETTINGS = (
    "Code/User/settings.json",
    "Code - Insiders/User/settings.json",
    "Cursor/User/settings.json",
    "Cursor Nightly/User/settings.json",
    "VSCodium/User/settings.json",
    "VSCodium - Insiders/User/settings.json",
    "Windsurf/User/settings.json",
    "Windsurf - Next/User/settings.json",
)
REMOTE_SSH_CONFIG = re.compile(
    r'"remote\.SSH\.configFile"\s*:\s*("(?:\\.|[^"\\])*")',
    re.IGNORECASE,
)
EDITOR_ENVIRONMENT = re.compile(r"\$\{env:([^}]+)\}", re.IGNORECASE)
EDITOR_USER_HOME = re.compile(r"\$\{userHome\}", re.IGNORECASE)
MAX_WINDOWS_REPARSE_FALLBACKS = 8


@dataclass(frozen=True, slots=True)
class _OpenSSHFileStamp:
    path: str
    mtime_ns: int
    ctime_ns: int
    size: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _OpenSSHIncludeWatch:
    pattern: str
    user_config_root: str
    candidate: str
    matches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _OpenSSHFingerprintCacheEntry:
    digest: str
    user_config_root: str
    files: tuple[_OpenSSHFileStamp, ...]
    include_watches: tuple[_OpenSSHIncludeWatch, ...]


class _OpenSSHDependencyProbe:
    """Collect only dependency metadata needed to validate a cached digest."""

    def __init__(self, user_config_root: Path) -> None:
        self.user_config_root = user_config_root
        self.files: dict[str, _OpenSSHFileStamp] = {}
        self.include_watches: dict[tuple[str, str], _OpenSSHIncludeWatch] = {}
        self.watch_bytes = 0
        self.cacheable = True

    def record_file(self, path: Path, metadata: os.stat_result) -> None:
        resolved = _canonical_local_path(path)
        key = os.path.normcase(str(resolved))
        self.files[key] = _OpenSSHFileStamp(
            path=str(resolved),
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
            size=metadata.st_size,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )

    def record_include(
        self,
        pattern: str,
        user_config_root: Path,
        candidate: Path,
        matches: tuple[Path, ...],
    ) -> None:
        # Preserve the lexical root for re-expanding relative patterns. A
        # symlink/junction-backed ~/.ssh may resolve to a different spelling;
        # mixing the resolved root with the original candidate would cause a
        # false cache miss on every validation.
        root_text = str(user_config_root)
        key = (pattern, os.path.normcase(root_text))
        match_keys = tuple(os.path.normcase(str(_canonical_local_path(path))) for path in matches)
        watch = _OpenSSHIncludeWatch(
            pattern=pattern,
            user_config_root=root_text,
            candidate=os.path.normcase(str(candidate)),
            matches=match_keys,
        )
        previous = self.include_watches.get(key)
        additional_bytes = len(pattern) + len(watch.candidate) + sum(len(value) for value in match_keys)
        if previous is not None:
            additional_bytes -= (
                len(previous.pattern)
                + len(previous.candidate)
                + sum(len(value) for value in previous.matches)
            )
        if (
            previous is None
            and len(self.include_watches) >= MAX_OPENSSH_INCLUDE_WATCHES
        ) or self.watch_bytes + additional_bytes > MAX_OPENSSH_INCLUDE_WATCH_BYTES:
            # Parsing remains unchanged. Only the optimization is disabled for
            # an unusually large dependency graph so cache memory stays bounded.
            self.cacheable = False
            return
        self.watch_bytes += additional_bytes
        self.include_watches[key] = watch

    def freeze(self, digest: str) -> _OpenSSHFingerprintCacheEntry | None:
        if not self.cacheable:
            return None
        return _OpenSSHFingerprintCacheEntry(
            digest=digest,
            user_config_root=os.path.normcase(str(_canonical_local_path(self.user_config_root))),
            files=tuple(self.files[key] for key in sorted(self.files)),
            include_watches=tuple(self.include_watches[key] for key in sorted(self.include_watches)),
        )


_OPENSSH_FINGERPRINT_CACHE: OrderedDict[str, _OpenSSHFingerprintCacheEntry] = OrderedDict()
_OPENSSH_FINGERPRINT_CACHE_LOCK = RLock()


def _expand_local_path(value: str | Path) -> str:
    raw = str(value).strip()
    local_home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home())
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
    raw = os.path.expandvars(os.path.expanduser(raw))
    raw = re.sub(
        r"%([A-Za-z_][A-Za-z0-9_]*)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        raw,
    )
    raw = re.sub(
        r"\$env:([A-Za-z_][A-Za-z0-9_]*)",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        raw,
        flags=re.IGNORECASE,
    )
    return raw


def _strip_windows_namespace_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\??\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    if value.startswith("\\??\\"):
        return value[4:]
    return value


def _canonical_local_path(path: Path, *, reparse_depth: int = 0) -> Path:
    """Resolve a local path, including a bounded WinError 448 junction fallback.

    Windows can refuse ``Path.resolve`` for a junction-backed ``~/.ssh`` when
    the packaged executable and the junction target have different trust
    labels. ``os.readlink`` reads the reparse record itself without traversing
    that boundary, so use its kernel-reported target and resolve the remaining
    suffix there. Other errors, ordinary files, and excessive reparse chains
    remain fail-closed.
    """

    try:
        return path.resolve()
    except OSError as exc:
        error_code = getattr(exc, "winerror", None) or exc.errno
        if sys.platform != "win32" or error_code != 448 or reparse_depth >= MAX_WINDOWS_REPARSE_FALLBACKS:
            raise

    absolute = Path(os.path.abspath(path))
    is_junction = getattr(os.path, "isjunction", lambda _value: False)
    for ancestor in (absolute, *absolute.parents):
        try:
            if not is_junction(ancestor) and not ancestor.is_symlink():
                continue
            target_text = _strip_windows_namespace_prefix(os.readlink(ancestor))
        except (OSError, ValueError):
            continue
        target = Path(target_text)
        if not target.is_absolute():
            target = ancestor.parent / target
        suffix = absolute.relative_to(ancestor)
        return _canonical_local_path(target / suffix, reparse_depth=reparse_depth + 1)
    raise OSError(448, "cannot traverse path because it contains an untrusted mount point", str(path))


def canonical_local_path(path: str | Path) -> Path:
    """Return the bounded junction-aware canonical spelling of a local path."""

    return _canonical_local_path(Path(path).expanduser())


def _resolved(value: str | Path) -> Path:
    return _canonical_local_path(Path(_expand_local_path(value)))


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("VRAM_RADAR_CONFIG_ROOT")
    if configured:
        roots.append(_resolved(configured))
    # Do not trust the launcher's current working directory for automatic
    # discovery. Packaged/source locations and an explicit environment override
    # are stable ownership boundaries; any other address must be user supplied.
    for value in (Path(sys.executable), Path(__file__)):
        path = value.resolve()
        roots.extend((path if path.is_dir() else path.parent, *(path if path.is_dir() else path.parent).parents))
    return roots


def _editor_settings_roots(home: Path) -> list[Path]:
    roots = [
        home / "Library" / "Application Support",
        home / ".config",
    ]
    for variable in ("APPDATA", "LOCALAPPDATA"):
        configured = os.environ.get(variable)
        if configured:
            roots.append(Path(configured).expanduser())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _expand_editor_config_path(configured: str, home: Path) -> Path | None:
    expanded = EDITOR_ENVIRONMENT.sub(
        lambda match: os.environ.get(match.group(1), match.group(0)),
        configured.strip(),
    )
    expanded = EDITOR_USER_HOME.sub(lambda _match: str(home), expanded)
    expanded = os.path.expandvars(expanded)
    if "${" in expanded:
        return None
    if expanded == "~":
        return home
    if expanded.startswith("~/") or expanded.startswith("~\\"):
        return home / expanded[2:]
    candidate = Path(expanded).expanduser()
    return candidate if candidate.is_absolute() else home / candidate


def _remote_ssh_config_candidates(home: Path) -> list[Path]:
    """Read only Remote-SSH config paths from common cross-platform editors."""

    candidates: list[Path] = []
    for settings_root in _editor_settings_roots(home):
        for relative in REMOTE_SSH_SETTINGS:
            settings = settings_root / relative
            try:
                if not settings.is_file() or settings.stat().st_size > MAX_CATALOG_BYTES:
                    continue
                text = settings.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError):
                continue
            match = REMOTE_SSH_CONFIG.search(_strip_jsonc_comments(text))
            if not match:
                continue
            try:
                configured = json.loads(match.group(1))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(configured, str) or not configured.strip():
                continue
            candidate = _expand_editor_config_path(configured, home)
            if candidate is not None:
                try:
                    candidates.append(candidate.resolve())
                except (OSError, RuntimeError):
                    continue
    return candidates


def _strip_jsonc_comments(text: str) -> str:
    """Remove JSONC comments while preserving strings and character offsets."""

    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            result.extend((" ", " "))
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if character == "/" and following == "*":
            result.extend((" ", " "))
            index += 2
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    result.extend((" ", " "))
                    index += 2
                    break
                result.append("\n" if text[index] == "\n" else "\r" if text[index] == "\r" else " ")
                index += 1
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _system_ssh_config_candidates() -> list[Path]:
    """Return conventional system/client-owned OpenSSH configs without execution."""

    roots: list[Path] = []
    if os.name == "nt":
        for variable in ("PROGRAMDATA", "ProgramData"):
            if configured := os.environ.get(variable):
                roots.append(Path(configured).expanduser() / "ssh")
        if configured := os.environ.get("ProgramFiles"):
            program_files = Path(configured).expanduser()
            roots.extend((program_files / "OpenSSH", program_files / "OpenSSH-Win64"))
    else:
        roots.extend((Path("/etc/ssh"), Path("/usr/local/etc/ssh"), Path("/opt/homebrew/etc/ssh")))
    if executable := shutil.which("ssh"):
        try:
            executable_root = Path(executable).resolve().parent
        except (OSError, RuntimeError):
            executable_root = None
        if executable_root is not None:
            roots.extend((executable_root, executable_root.parent / "etc" / "ssh"))

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved_root = root.resolve()
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(resolved_root))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(resolved_root / "ssh_config")
        candidates.extend(_existing_files(resolved_root / "ssh_config.d"))
    return candidates


def _existing_files(directory: Path) -> list[Path]:
    try:
        directory = _canonical_local_path(directory)
        return sorted(
            (_canonical_local_path(path) for path in directory.iterdir() if path.is_file()),
            key=lambda path: os.path.normcase(str(path)),
        )
    except OSError:
        return []


def server_config_candidates(*, include_openssh: bool = False) -> list[Path]:
    candidates: list[Path] = []
    environment = os.environ.get("VRAM_RADAR_SERVERS_CONFIG")
    if environment:
        candidates.append(_resolved(environment))
    for root in _candidate_roots():
        candidates.append(root / "config" / "servers.toml")
        # A portable Harness owns its catalog below the workspace-level
        # ``harness/config`` directory.  Source checkouts and packaged builds
        # commonly live elsewhere below that same workspace root.
        candidates.append(root / "harness" / "config" / "servers.toml")
    if include_openssh:
        home = Path.home()
        xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
        ssh_homes = [home]
        for variable in ("USERPROFILE", "HOME"):
            configured = os.environ.get(variable)
            if configured:
                ssh_homes.append(Path(configured).expanduser())
        candidates.extend(ssh_home / ".ssh" / "config" for ssh_home in ssh_homes)
        candidates.extend(
            (
                xdg_config / "ssh" / "config",
                home / ".colima" / "ssh_config",
                home / ".orbstack" / "ssh" / "config",
            )
        )
        candidates.extend(_remote_ssh_config_candidates(home))
        candidates.extend(_system_ssh_config_candidates())
        # Some tools write standalone fragments without adding an Include line.
        # Keep this bounded to the two conventional directories under ~/.ssh.
        for ssh_home in ssh_homes:
            candidates.extend(_existing_files(ssh_home / ".ssh" / "config.d"))
            candidates.extend(_existing_files(ssh_home / ".ssh" / "conf.d"))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        # Hosted macOS and Windows runners expose temporary directories through
        # aliases (``/var`` -> ``/private/var`` and 8.3 short names).  Return a
        # single canonical form so automatic discovery, existence checks, and
        # persisted selections all refer to the same file on every platform.
        try:
            resolved = _canonical_local_path(candidate.expanduser())
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def resolve_server_configs(*, include_openssh: bool = False) -> list[Path]:
    """Return every existing deterministic candidate in priority order."""

    return [
        path
        for path in server_config_candidates(include_openssh=include_openssh)
        if path.is_file()
    ]


def resolve_server_config(
    explicit: str | Path | None = None,
    *,
    include_openssh: bool = False,
) -> Path | None:
    if explicit:
        path = _resolved(explicit)
        if not path.is_file():
            raise ConfigError(f"服务器设置文件不存在：{path}")
        return path
    sources = resolve_server_configs(include_openssh=include_openssh)
    return sources[0] if sources else None


def _sensitive_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if SENSITIVE_KEY.search(str(key)):
                found.append(path)
            found.extend(_sensitive_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_sensitive_paths(child, f"{prefix}[{index}]"))
    return found


def _memory_from_name(name: str, fallback: Any) -> float | None:
    matches = MEMORY_SUFFIX.findall(name)
    if matches:
        parsed = float(matches[-1])
        return parsed if math.isfinite(parsed) and 0 < parsed <= 1000 else None
    if isinstance(fallback, bool) or not isinstance(fallback, (int, float)):
        return None
    parsed = float(fallback)
    return parsed if math.isfinite(parsed) and 0 < parsed <= 1000 else None


def _memory_map(server: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    partitions = server.get("slurm", {}).get("partitions", {})
    if not isinstance(partitions, dict):
        return result
    for partition in partitions.values():
        if not isinstance(partition, dict):
            continue
        fallback = partition.get("gpu_memory_gb")
        gpu_types = partition.get("gpu_types", [])
        if not isinstance(gpu_types, list):
            continue
        for gpu_type in gpu_types:
            if isinstance(gpu_type, str) and (capacity := _memory_from_name(gpu_type, fallback)) is not None:
                result[gpu_type] = capacity
    return result


def import_server_catalog(path: str | Path) -> tuple[tuple[ServerProfile, ...], list[str]]:
    source = _resolved(path)
    if not source.is_file():
        raise ConfigError(f"服务器设置文件不存在：{source}")
    if source.stat().st_size > MAX_CATALOG_BYTES:
        raise ConfigError("服务器设置文件超过 1 MiB，已拒绝导入")
    try:
        with source.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取服务器设置文件：{exc}") from exc
    if document.get("version") not in {2, 3} or not isinstance(document.get("servers"), dict):
        raise ConfigError("服务器设置文件必须是受支持的 servers.toml version 2 或 3")
    sensitive = _sensitive_paths(document)
    if sensitive:
        raise ConfigError("服务器设置文件包含不允许导入的凭据字段：" + ", ".join(sensitive))

    imported: list[ServerProfile] = []
    warnings: list[str] = []
    for server_id, raw in document["servers"].items():
        if not isinstance(raw, dict):
            warnings.append(f"{server_id}: 配置不是 TOML table，已跳过")
            continue
        backend = BACKEND_MAP.get(raw.get("backend"))
        alias = raw.get("ssh_alias")
        if backend is None:
            warnings.append(f"{server_id}: 不支持 backend={raw.get('backend')!r}，已跳过")
            continue
        if not isinstance(alias, str) or not alias.strip():
            warnings.append(f"{server_id}: 缺少 OpenSSH Alias，已跳过")
            continue
        payload: dict[str, Any] = {
            "id": server_id,
            "display_name": raw.get("display_name", server_id),
            "backend": backend,
            # Let the Profile schema reject strings such as ``"false"``;
            # truthiness conversion would silently enable a server the author
            # explicitly intended to keep disabled.
            "enabled": raw.get("enabled", False),
            "ssh_alias": alias.strip(),
        }
        if backend == "slurm_ssh":
            payload["gpu_memory_gib"] = _memory_map(raw)
        try:
            imported.append(ServerProfile.from_dict(payload))
        except ConfigError as exc:
            warnings.append(f"{server_id}: {exc}，已跳过")
    if not imported:
        raise ConfigError("服务器设置文件中没有可导入的服务器")
    return tuple(imported), warnings


def _server_id_from_alias(alias: str, used_ids: set[str]) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", alias).strip("._-")[:64]
    if not candidate or not candidate[0].isalnum():
        candidate = "ssh-host"
    base = candidate
    suffix = 2
    while candidate.casefold() in used_ids:
        marker = f"-{suffix}"
        candidate = f"{base[: 64 - len(marker)]}{marker}"
        suffix += 1
    used_ids.add(candidate.casefold())
    return candidate


def _openssh_user_config_root(source: Path) -> Path:
    """Resolve the root OpenSSH uses for relative Include directives."""

    source_key = _canonical_local_path(source)
    user_config_root = _canonical_local_path(Path.home() / ".ssh")
    for variable in ("USERPROFILE", "HOME"):
        if configured := os.environ.get(variable):
            root = _canonical_local_path(Path(configured).expanduser() / ".ssh")
            try:
                source_key.relative_to(root)
            except ValueError:
                continue
            return root
    for system_candidate in _system_ssh_config_candidates():
        candidate_key = _canonical_local_path(system_candidate)
        if source_key != candidate_key:
            continue
        return (
            candidate_key.parent.parent
            if candidate_key.parent.name.casefold() == "ssh_config.d"
            else candidate_key.parent
        )
    return user_config_root


def _resolve_openssh_include_pattern(pattern: str, user_config_root: Path) -> Path:
    expanded = _expand_local_path(pattern)
    # OpenSSH's %d token is the local user's home directory and is safe to
    # resolve during static import. Host-dependent tokens remain untouched and
    # therefore cannot accidentally import another host's conditional file.
    # Use a callable replacement so Windows backslashes in the home path are
    # treated as literal data instead of ``re.sub`` replacement escapes.
    expanded = re.sub(
        r"%d(?=$|[\\/])",
        lambda _match: str(user_config_root.parent),
        expanded,
    )
    candidate = Path(expanded)
    if not candidate.is_absolute():
        # OpenSSH resolves every relative Include in a user configuration
        # against ~/.ssh, including Includes found inside another Include.
        candidate = user_config_root / candidate
    return candidate


def _match_openssh_include(candidate: Path) -> tuple[Path, ...]:
    try:
        return tuple(
            _canonical_local_path(path)
            for value in sorted(glob.glob(str(candidate)))
            if (path := Path(value)).is_file()
        )
    except OSError:
        return ()


def _expand_openssh_include(
    pattern: str,
    user_config_root: Path,
    dependency_probe: _OpenSSHDependencyProbe | None = None,
) -> list[Path]:
    candidate = _resolve_openssh_include_pattern(pattern, user_config_root)
    matches = _match_openssh_include(candidate)
    if dependency_probe is not None:
        dependency_probe.record_include(pattern, user_config_root, candidate, matches)
    return list(matches)


def _openssh_fingerprint_cache_entry_valid(
    source: Path,
    entry: _OpenSSHFingerprintCacheEntry,
) -> bool:
    current_root = _openssh_user_config_root(source)
    if os.path.normcase(str(_canonical_local_path(current_root))) != entry.user_config_root:
        return False
    for expected in entry.files:
        try:
            observed = Path(expected.path).stat()
        except OSError:
            return False
        if not stat.S_ISREG(observed.st_mode):
            return False
        if (
            observed.st_mtime_ns != expected.mtime_ns
            or observed.st_ctime_ns != expected.ctime_ns
            or observed.st_size != expected.size
            or observed.st_dev != expected.device
            or observed.st_ino != expected.inode
        ):
            return False
    for watch in entry.include_watches:
        watch_root = Path(watch.user_config_root)
        candidate = _resolve_openssh_include_pattern(watch.pattern, watch_root)
        if os.path.normcase(str(candidate)) != watch.candidate:
            return False
        matches = tuple(
            os.path.normcase(str(path.resolve()))
            for path in _match_openssh_include(candidate)
        )
        if matches != watch.matches:
            return False
    return True


def _split_openssh_line(line: str) -> list[str]:
    # ``posix=False`` preserves Windows backslashes in Include paths. Strip
    # only a matching outer quote pair after tokenization.
    lexer = shlex.shlex(line, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    fields = [
        field[1:-1]
        if len(field) >= 2 and field[0] == field[-1] and field[0] in {'"', "'"}
        else field
        for field in list(lexer)
    ]
    if not fields:
        return fields
    if "=" in fields[0]:
        keyword, value = fields[0].split("=", 1)
        if keyword:
            fields = [keyword, *([value] if value else []), *fields[1:]]
    elif len(fields) >= 2 and fields[1] == "=":
        fields = [fields[0], *fields[2:]]
    elif len(fields) >= 2 and fields[1].startswith("="):
        fields = [fields[0], fields[1][1:], *fields[2:]]
    return fields


def _read_openssh_aliases(
    source: Path,
    dependency_probe: _OpenSSHDependencyProbe | None = None,
) -> tuple[list[tuple[str, ...]], int, list[str], str]:
    """Return concrete Host alias groups, Include count, warnings, and digest.

    Each group is the ordered concrete aliases from one ``Host`` line. Callers
    that only need a flat alias list can take the first entry of every group.
    """

    alias_groups: list[tuple[str, ...]] = []
    seen_aliases: set[str] = set()
    visited: set[str] = set()
    dependency_only: set[str] = set()
    visiting: set[str] = set()
    exit_state_cache: dict[tuple[str, bool], bool] = {}
    total_bytes = 0
    included_files = 0
    skipped_conditional_includes = 0
    dependency_hash = hashlib.sha256()
    user_config_root = (
        dependency_probe.user_config_root
        if dependency_probe is not None
        else _openssh_user_config_root(source)
    )

    def hash_conditional_dependencies(path: Path) -> None:
        """Hash conditional Include files without treating their Host entries as import candidates."""

        nonlocal total_bytes, included_files
        key = os.path.normcase(str(path.resolve()))
        if key in visited or key in dependency_only:
            return
        if key not in dependency_only and len(visited | dependency_only) >= MAX_OPENSSH_FILES:
            raise ConfigError(f"OpenSSH Include 文件超过 {MAX_OPENSSH_FILES} 个，已拒绝导入")
        try:
            metadata = path.stat()
            size = metadata.st_size
        except OSError:
            return
        if size > MAX_CATALOG_BYTES:
            raise ConfigError("单个 OpenSSH 配置文件超过 1 MiB，已拒绝导入")
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            return
        if dependency_probe is not None:
            dependency_probe.record_file(path, metadata)
        total_bytes += size
        if total_bytes > MAX_OPENSSH_TOTAL_BYTES:
            raise ConfigError("OpenSSH 配置及 Include 总计超过 4 MiB，已拒绝导入")
        dependency_only.add(key)
        included_files += 1
        dependency_hash.update(key.encode("utf-8", errors="surrogatepass"))
        dependency_hash.update(b"\0")
        dependency_hash.update("\n".join(lines).encode("utf-8", errors="replace"))
        dependency_hash.update(b"\0")
        # Fingerprinting is deliberately broader than static alias import:
        # actual OpenSSH can activate these nested files for a selected Host.
        for line_number, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = _split_openssh_line(stripped)
            except ValueError as exc:
                raise ConfigError(
                    f"OpenSSH 配置 {path} 第 {line_number} 行格式无效：{exc}"
                ) from exc
            if fields and fields[0].casefold() == "include":
                for pattern in fields[1:]:
                    for included in _expand_openssh_include(
                        pattern,
                        user_config_root,
                        dependency_probe,
                    ):
                        hash_conditional_dependencies(included)

    def visit(path: Path, *, root: bool = False, include_active: bool = True) -> bool:
        nonlocal total_bytes, included_files, skipped_conditional_includes
        key = os.path.normcase(str(path.resolve()))
        entry_state = include_active
        state_key = (key, entry_state)
        if state_key in exit_state_cache:
            return exit_state_cache[state_key]
        if key in visiting:
            return include_active
        if key not in visited and key not in dependency_only and len(visited | dependency_only) >= MAX_OPENSSH_FILES:
            raise ConfigError(f"OpenSSH Include 文件超过 {MAX_OPENSSH_FILES} 个，已拒绝导入")
        try:
            metadata = path.stat()
            size = metadata.st_size
        except OSError as exc:
            if root:
                raise ConfigError(f"无法读取 OpenSSH 配置文件：{exc}") from exc
            return include_active
        if size > MAX_CATALOG_BYTES:
            raise ConfigError("单个 OpenSSH 配置文件超过 1 MiB，已拒绝导入")
        already_hashed = key in dependency_only or key in visited
        if not already_hashed:
            total_bytes += size
            if total_bytes > MAX_OPENSSH_TOTAL_BYTES:
                raise ConfigError("OpenSSH 配置及 Include 总计超过 4 MiB，已拒绝导入")
        visited.add(key)
        if not root and not already_hashed:
            included_files += 1
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            if root:
                raise ConfigError(f"无法读取 OpenSSH 配置文件：{exc}") from exc
            return include_active
        if dependency_probe is not None:
            dependency_probe.record_file(path, metadata)
        if not already_hashed:
            dependency_hash.update(key.encode("utf-8", errors="surrogatepass"))
            dependency_hash.update(b"\0")
            dependency_hash.update("\n".join(lines).encode("utf-8", errors="replace"))
            dependency_hash.update(b"\0")
        visiting.add(key)
        for line_number, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = _split_openssh_line(stripped)
            except ValueError as exc:
                raise ConfigError(
                    f"OpenSSH 配置 {path} 第 {line_number} 行格式无效：{exc}"
                ) from exc
            if not fields:
                continue
            keyword = fields[0].casefold()
            if keyword == "include":
                if not include_active:
                    skipped_conditional_includes += 1
                    for pattern in fields[1:]:
                        for included in _expand_openssh_include(
                            pattern,
                            user_config_root,
                            dependency_probe,
                        ):
                            hash_conditional_dependencies(included)
                    continue
                for pattern in fields[1:]:
                    for included in _expand_openssh_include(
                        pattern,
                        user_config_root,
                        dependency_probe,
                    ):
                        # Include behaves as textual insertion. A Host/Match at
                        # the end of a fragment remains active for subsequent
                        # parent directives, exactly as OpenSSH evaluates it.
                        include_active = visit(included, include_active=include_active)
                continue
            if keyword == "match":
                include_active = len(fields) == 2 and fields[1].casefold() == "all"
                continue
            if keyword != "host":
                continue
            include_active = len(fields) == 2 and fields[1] == "*"
            group: list[str] = []
            for alias in fields[1:]:
                if not alias or any(character in alias for character in OPENSSH_PATTERN_CHARACTERS):
                    continue
                group.append(alias)
            if not group:
                continue
            # One Host line yields one import candidate (its first concrete
            # alias) plus sibling aliases for existing-profile binding. Aliases
            # already claimed by an earlier Host line stay in the group for
            # binding but do not create an extra import row.
            alias_groups.append(tuple(group))
            for alias in group:
                seen_aliases.add(alias.casefold())
        visiting.discard(key)
        exit_state_cache[state_key] = include_active
        return include_active

    visit(source, root=True)
    warnings: list[str] = []
    if skipped_conditional_includes:
        warnings.append(
            f"已跳过 {skipped_conditional_includes} 条条件 Host/Match 中的 Include；"
            "这类规则需由 OpenSSH 在实际连接时判断"
        )
    return alias_groups, included_files, warnings, dependency_hash.hexdigest()


def openssh_config_dependency_fingerprint(path: str | Path) -> str:
    """Hash a bounded static OpenSSH dependency graph without repeated reads.

    Cache entries contain only the digest, dependency paths, and stat/glob
    metadata. File text, aliases, credentials, and private-key material are
    never retained. Every hit revalidates the dependency metadata and Include
    match set, so edits and newly added or removed wildcard fragments invalidate
    the entry before its digest is reused.
    """

    try:
        source = _resolved(path)
        is_file = source.is_file()
    except (OSError, RuntimeError):
        # A cache fingerprint is an optimization, never a connection gate.
        # Windows can reject canonicalization through an otherwise readable
        # junction with ERROR_UNTRUSTED_MOUNT_POINT (448).  Treat that case as
        # an unreadable dependency instead of invalidating every server cache.
        return "unreadable"
    if not is_file:
        return "missing"
    cache_key = os.path.normcase(str(source))
    with _OPENSSH_FINGERPRINT_CACHE_LOCK:
        cached = _OPENSSH_FINGERPRINT_CACHE.get(cache_key)
        if cached is not None:
            try:
                valid = _openssh_fingerprint_cache_entry_valid(source, cached)
            except (OSError, RuntimeError):
                valid = False
            if valid:
                _OPENSSH_FINGERPRINT_CACHE.move_to_end(cache_key)
                return cached.digest
            _OPENSSH_FINGERPRINT_CACHE.pop(cache_key, None)
        try:
            probe = _OpenSSHDependencyProbe(_openssh_user_config_root(source))
            _aliases, _included, _warnings, digest = _read_openssh_aliases(
                source,
                probe,
            )
        except (ConfigError, OSError, RuntimeError):
            return "unreadable"
        entry = probe.freeze(digest)
        if entry is not None:
            _OPENSSH_FINGERPRINT_CACHE[cache_key] = entry
            _OPENSSH_FINGERPRINT_CACHE.move_to_end(cache_key)
            while len(_OPENSSH_FINGERPRINT_CACHE) > MAX_OPENSSH_FINGERPRINT_CACHE_ENTRIES:
                _OPENSSH_FINGERPRINT_CACHE.popitem(last=False)
        return digest


def import_openssh_config(path: str | Path) -> tuple[tuple[ServerProfile, ...], list[str]]:
    """Import concrete Host aliases, including bounded Include files."""

    imported, warnings, _alias_groups, _pending = _import_openssh_config_detailed(path)
    return imported, warnings


def _import_openssh_config_detailed(
    path: str | Path,
) -> tuple[
    tuple[ServerProfile, ...],
    list[str],
    tuple[tuple[str, ...], ...],
    list[dict[str, object]],
]:
    """Import OpenSSH Host aliases and return same-line groups plus pending choices."""

    source = _resolved(path)
    if not source.is_file():
        raise ConfigError(f"OpenSSH 配置文件不存在：{source}")
    alias_groups, included_files, parser_warnings, _dependency_digest = _read_openssh_aliases(
        source
    )
    if not alias_groups:
        raise ConfigError("OpenSSH 配置及其 Include 文件中没有可导入的具体 Host 别名")

    used_ids: set[str] = set()
    imported_rows: list[ServerProfile] = []
    identity_paths_found = 0
    merged_same_line = False
    skipped_same_destination = 0
    seen_destinations: dict[tuple[object, ...], str] = {}
    destination_aliases: dict[tuple[object, ...], list[str]] = {}
    imported_alias_keys: set[str] = set()
    pending_drafts: list[dict[str, object]] = []
    config_path = str(source)
    for group in alias_groups:
        if len(group) > 1:
            merged_same_line = True
            pending_drafts.append(
                {
                    "reason": "same_host_line",
                    "aliases": list(group),
                    "default_alias": group[0],
                    "config_path": config_path,
                }
            )
        alias = group[0]
        alias_key = alias.casefold()
        # Same Host line: only the first concrete alias becomes a server row.
        # Later aliases stay available via alias_groups for existing-profile
        # binding and are never imported as extra servers.
        if alias_key in imported_alias_keys:
            continue
        destination = resolve_openssh_destination(source, alias)
        fingerprint = destination.fingerprint()
        if fingerprint is not None:
            prior = seen_destinations.get(fingerprint)
            destination_aliases.setdefault(fingerprint, [])
            if prior is not None:
                skipped_same_destination += 1
                if alias not in destination_aliases[fingerprint]:
                    destination_aliases[fingerprint].append(alias)
                continue
            seen_destinations[fingerprint] = alias
            destination_aliases[fingerprint].append(alias)
        identity_resolution = resolve_openssh_identity_files(source, alias)
        identity_file = ""
        if identity_resolution.status == "exact" and len(identity_resolution.identity_files) == 1:
            identity_paths_found += 1
        imported_rows.append(
            ServerProfile.from_dict(
                {
                    "id": _server_id_from_alias(alias, used_ids),
                    "display_name": alias,
                    "backend": "direct_ssh",
                    "auto_detect_backend": True,
                    "auto_imported": True,
                    "enabled": True,
                    "ssh_alias": alias,
                    "ssh_config_file": str(source),
                    # Keep SSH Config authoritative for imported aliases. A
                    # discovered IdentityFile is confirmation metadata only;
                    # pinning it as -i would make a later config/key rotation
                    # fail before OpenSSH can apply the updated configuration.
                    "identity_file": identity_file,
                }
            )
        )
        imported_alias_keys.add(alias_key)
    for aliases in destination_aliases.values():
        if len(aliases) < 2:
            continue
        pending_drafts.append(
            {
                "reason": "same_destination",
                "aliases": list(aliases),
                "default_alias": aliases[0],
                "config_path": config_path,
            }
        )
    imported = tuple(imported_rows)
    warnings = ["OpenSSH 静态配置无法判断直连或 Slurm；将在保存验证时自动识别，失败时可手动选择"]
    if merged_same_line:
        warnings.append("同一条 Host 设置中的其他别名已合并为一台服务器")
    if skipped_same_destination:
        warnings.append(
            f"已跳过 {skipped_same_destination} 个与现有服务器连接设置相同的 SSH 别名"
        )
    if identity_paths_found:
        warnings.append(
            f"已确认 {identity_paths_found} 台服务器由 OpenSSH 配置管理私钥；不会固化路径"
        )
    warnings.extend(parser_warnings)
    if included_files:
        warnings.append(f"已安全解析 {included_files} 个 OpenSSH Include 文件")
    return imported, warnings, tuple(alias_groups), pending_drafts



def _source_is_openssh_config(source: Path) -> bool:
    if source.name.casefold() == "config" and source.parent.name.casefold() == ".ssh":
        return True
    if source.suffix.casefold() == ".toml":
        return False
    try:
        if source.stat().st_size > MAX_CATALOG_BYTES:
            return False
        leading_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    return bool(re.search(r"(?im)^\s*(?:host|include)\s*(?:=\s*)?\S+", leading_text))


def import_server_config(path: str | Path) -> tuple[tuple[ServerProfile, ...], list[str]]:
    source = _resolved(path)
    if source.name.casefold() == "config" and source.parent.name.casefold() == ".ssh":
        return import_openssh_config(source)
    if source.suffix.casefold() == ".toml":
        return import_server_catalog(source)
    try:
        if source.stat().st_size > MAX_CATALOG_BYTES:
            raise ConfigError("服务器设置文件超过 1 MiB，已拒绝导入")
        leading_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"无法读取服务器设置文件：{exc}") from exc
    if re.search(r"(?im)^\s*(?:host|include)\s*(?:=\s*)?\S+", leading_text):
        return import_openssh_config(source)
    raise ConfigError("无法识别配置格式；请选择 servers.toml v2/v3 或 OpenSSH ~/.ssh/config")


def profile_from_server_config(profile: Profile, path: str | Path) -> tuple[Profile, list[str]]:
    return profile_from_server_configs(profile, [path])


def profile_from_server_configs(
    profile: Profile,
    paths: list[str | Path] | tuple[str | Path, ...],
) -> tuple[Profile, list[str]]:
    sources = [_resolved(path) for path in paths]
    if not sources:
        raise ConfigError("没有可导入的服务器设置文件")

    imported_by_id: dict[str, ServerProfile] = {}
    imported_is_openssh: dict[str, bool] = {}
    alias_to_id: dict[str, str] = {}
    imported_order: list[str] = []
    warnings: list[str] = []
    successful_sources = 0
    # A deleted imported Host is an explicit local choice, not a transient
    # absence from the editor.  Keep that choice at the Profile layer so the
    # same rule is honored by save-time sync, startup sync, and manual import.
    # An active row always wins defensively; save_profile also removes active
    # aliases from the tombstone list so a deliberate manual re-add restores it.
    active_alias_keys = {
        server.ssh_alias.casefold() for server in profile.servers if server.ssh_alias
    }
    ignored_alias_keys = {
        alias.casefold() for alias in profile.ignored_ssh_aliases
    } - active_alias_keys
    ignored_imported_aliases: dict[str, str] = {}
    skipped_same_destination = 0
    accepted_destinations: dict[tuple[object, ...], str] = {}
    pending_drafts: list[dict[str, object]] = []
    for existing in profile.servers:
        if not existing.ssh_alias:
            continue
        config_path = existing.ssh_config_file or (str(sources[0]) if len(sources) == 1 else "")
        if not config_path:
            continue
        fingerprint = resolve_openssh_destination(config_path, existing.ssh_alias).fingerprint()
        if fingerprint is not None:
            accepted_destinations.setdefault(fingerprint, existing.ssh_alias)

    for source in sources:
        prefix = f"{source.name}: " if len(sources) > 1 else ""
        alias_groups: tuple[tuple[str, ...], ...] = ()
        try:
            source_pending_drafts: list[dict[str, object]] = []
            if _source_is_openssh_config(source):
                servers, source_warnings, alias_groups, source_pending_drafts = (
                    _import_openssh_config_detailed(source)
                )
                openssh_source = True
            else:
                servers, source_warnings = import_server_config(source)
                openssh_source = False
            pending_drafts.extend(source_pending_drafts)
        except ConfigError as exc:
            if len(sources) == 1:
                raise
            warnings.append(f"{prefix}{exc}，已跳过此来源")
            continue
        successful_sources += 1
        warnings.extend(f"{prefix}{warning}" for warning in source_warnings)
        group_keys_by_primary: dict[str, tuple[str, ...]] = {}
        for group in alias_groups:
            if not group:
                continue
            group_keys_by_primary[group[0].casefold()] = tuple(
                alias.casefold() for alias in group
            )

        for server in servers:
            alias_key = server.ssh_alias.casefold()
            group_keys = group_keys_by_primary.get(alias_key, (alias_key,))
            owning_existing = next(
                (
                    existing
                    for existing in profile.servers
                    if existing.ssh_alias and existing.ssh_alias.casefold() in group_keys
                ),
                None,
            )
            if owning_existing is not None:
                # Same Host line already represented by a local row (possibly via
                # a later alias). Keep that row and do not append the primary.
                for group_alias_key in group_keys:
                    alias_to_id.setdefault(group_alias_key, owning_existing.id)
                if owning_existing.id not in imported_by_id:
                    imported_by_id[owning_existing.id] = replace(
                        owning_existing,
                        ssh_config_file=server.ssh_config_file or owning_existing.ssh_config_file,
                    )
                    imported_is_openssh[owning_existing.id] = openssh_source
                    imported_order.append(owning_existing.id)
                elif server.ssh_config_file and not imported_by_id[owning_existing.id].ssh_config_file:
                    imported_by_id[owning_existing.id] = replace(
                        imported_by_id[owning_existing.id],
                        ssh_config_file=server.ssh_config_file,
                    )
                continue
            if alias_key in ignored_alias_keys:
                ignored_imported_aliases.setdefault(alias_key, server.ssh_alias)
                continue
            destination = resolve_openssh_destination(
                server.ssh_config_file or source,
                server.ssh_alias,
            ).fingerprint()
            if destination is not None and destination in accepted_destinations:
                skipped_same_destination += 1
                prior_alias = accepted_destinations[destination]
                pending_drafts.append(
                    {
                        "reason": "same_destination",
                        "aliases": [prior_alias, server.ssh_alias],
                        "default_alias": prior_alias,
                        "config_path": str(server.ssh_config_file or source),
                    }
                )
                continue
            duplicate_alias = alias_to_id.get(alias_key)
            if duplicate_alias:
                kept = duplicate_alias
                existing = imported_by_id[kept]
                if (
                    openssh_source
                    and not imported_is_openssh[kept]
                    and not existing.ssh_config_file
                    and existing.ssh_alias.casefold() == server.ssh_alias.casefold()
                ):
                    imported_by_id[kept] = replace(existing, ssh_config_file=server.ssh_config_file)
                    warnings.append(f"{prefix}{server.ssh_alias}: 已关联 OpenSSH 配置来源")
                    continue
                warnings.append(f"{prefix}{server.ssh_alias}: 与 {kept} 重复，保留优先来源")
                continue
            id_key = server.id.casefold()
            duplicate_id = next(
                (existing_id for existing_id in imported_order if existing_id.casefold() == id_key),
                None,
            )
            if duplicate_id:
                used_ids = {existing_id.casefold() for existing_id in imported_order}
                replacement_id = _server_id_from_alias(server.ssh_alias, used_ids)
                warnings.append(
                    f"{prefix}{server.ssh_alias}: ID 与 {duplicate_id} 冲突，已保存为 {replacement_id}"
                )
                server = replace(server, id=replacement_id)
            imported_by_id[server.id] = server
            imported_is_openssh[server.id] = openssh_source
            alias_to_id[alias_key] = server.id
            for group_alias_key in group_keys:
                alias_to_id.setdefault(group_alias_key, server.id)
            if destination is not None:
                accepted_destinations.setdefault(destination, server.ssh_alias)
            imported_order.append(server.id)

    if skipped_same_destination:
        warnings.append(
            f"已跳过 {skipped_same_destination} 个与现有服务器连接设置相同的 SSH 别名"
        )
    if ignored_imported_aliases:
        warnings.append(
            f"已跳过 {len(ignored_imported_aliases)} 台你主动移除过的服务器；"
            "SSH 配置中新增加的其他 Host 仍会自动导入"
        )
    if not successful_sources:
        raise ConfigError("发现的配置中没有可导入的服务器 Host 别名")
    if not imported_order:
        # A valid source containing only ignored aliases is a successful sync,
        # not a broken configuration.  Preserve local rows and synchronization
        # metadata without resurrecting anything the user removed.
        synchronized_path = str(sources[0]) if len(sources) == 1 else ""
        pending_alias_choices = _finalize_pending_alias_choices(
            profile,
            profile.servers,
            pending_drafts,
        )
        return replace(
            profile,
            server_config_path=synchronized_path,
            auto_sync_servers=len(sources) == 1,
            pending_alias_choices=pending_alias_choices,
        ), warnings

    # Profile IDs are case-insensitive. Normalize an imported spelling back to
    # the existing local spelling before any alias matching, otherwise a
    # manual ``GPU-A`` row plus an imported ``gpu-a`` row can look synchronized
    # in memory but fail the next Profile validation/save.
    for existing in profile.servers:
        imported_id = next(
            (
                candidate_id
                for candidate_id in imported_order
                if candidate_id.casefold() == existing.id.casefold()
            ),
            None,
        )
        if imported_id is None or imported_id == existing.id:
            continue
        imported = imported_by_id.pop(imported_id)
        imported_is_openssh[existing.id] = imported_is_openssh.pop(imported_id)
        imported_by_id[existing.id] = replace(imported, id=existing.id)
        imported_order[imported_order.index(imported_id)] = existing.id
        for alias_key, mapped_id in tuple(alias_to_id.items()):
            if mapped_id == imported_id:
                alias_to_id[alias_key] = existing.id
        warnings.append(f"{existing.display_name}: 已按大小写不敏感 ID 保留本地服务器 ID")

    # A user may rename an imported row while retaining its OpenSSH alias.
    # Bind that source back to the renamed row instead of appending a duplicate.
    for existing in profile.servers:
        imported_id = alias_to_id.get(existing.ssh_alias.casefold()) if existing.ssh_alias else None
        if not imported_id or imported_id == existing.id or existing.id in imported_by_id:
            continue
        imported = imported_by_id.pop(imported_id)
        imported_is_openssh[existing.id] = imported_is_openssh.pop(imported_id)
        imported_by_id[existing.id] = replace(imported, id=existing.id)
        imported_order[imported_order.index(imported_id)] = existing.id
        alias_to_id[existing.ssh_alias.casefold()] = existing.id
        warnings.append(f"{existing.display_name}: 已按 SSH Alias 保留本地服务器 ID")

    existing_by_id = {server.id: server for server in profile.servers}
    preserved_ids = [server.id for server in profile.servers if server.id in imported_by_id]
    preserved_id_set = set(preserved_ids)
    preserved: list[ServerProfile] = []
    for server_id in preserved_ids:
        imported = imported_by_id[server_id]
        existing = existing_by_id[server_id]
        if imported_is_openssh[server_id]:
            # OpenSSH does not describe VRAMRadar semantics. Keep the user's
            # reviewed direct/Slurm choice and local display settings on sync.
            # Sibling-alias binding seeds the imported row with the local alias
            # already, so adopting imported.ssh_alias here stays correct for
            # both ID matches and same-line Host groups.
            preserved.append(
                replace(
                    existing,
                    ssh_alias=imported.ssh_alias or existing.ssh_alias,
                    ssh_config_file=imported.ssh_config_file or existing.ssh_config_file,
                )
            )
        else:
            same_alias = existing.ssh_alias.casefold() == imported.ssh_alias.casefold()
            preserved.append(
                replace(
                    imported,
                    username=existing.username,
                    identity_file=existing.identity_file,
                    ssh_config_file=(
                        existing.ssh_config_file
                        if same_alias and existing.ssh_config_file
                        else imported.ssh_config_file
                    ),
                    auth_ref=existing.auth_ref,
                    connect_timeout_seconds=existing.connect_timeout_seconds,
                    show_other_user_commands=existing.show_other_user_commands,
                    default_work_directory=existing.default_work_directory,
                    prefer_identity_auth=existing.prefer_identity_auth,
                )
            )
    # Without persistent per-source provenance, deleting an unmatched local row
    # would be destructive and can also orphan its OS credential reference.
    # Preserve it and make the conservative sync behavior explicit.
    unmatched_existing = [server for server in profile.servers if server.id not in preserved_id_set]
    if unmatched_existing:
        warnings.append(f"已保留 {len(unmatched_existing)} 台未匹配的本地服务器；自动同步不会静默删除服务器")
    appended = [
        imported_by_id[server_id]
        for server_id in imported_order
        if server_id not in preserved_id_set
        and server_id not in {server.id for server in unmatched_existing}
    ]
    synchronized_path = str(sources[0]) if len(sources) == 1 else ""
    synchronized_servers = tuple([*preserved, *unmatched_existing, *appended])
    pending_alias_choices = _finalize_pending_alias_choices(
        profile,
        synchronized_servers,
        pending_drafts,
    )
    return replace(
        profile,
        servers=synchronized_servers,
        server_config_path=synchronized_path,
        auto_sync_servers=len(sources) == 1,
        pending_alias_choices=pending_alias_choices,
    ), warnings

def _choice_summaries(config_path: str, aliases: list[str]) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for alias in aliases:
        summaries[alias] = format_openssh_connection_summary(config_path, alias)
    return summaries


def _union_find_parent():
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    return parent, find, union


def _finalize_pending_alias_choices(
    profile: Profile,
    servers: tuple[ServerProfile, ...] | list[ServerProfile],
    drafts: list[dict[str, object]],
) -> tuple[dict, ...]:
    """Merge overlapping duplicate detections into one choice per machine."""

    from .models import MAX_PENDING_ALIAS_CHOICES, alias_choice_group_key

    resolved_keys = set(profile.resolved_alias_choice_keys)
    server_list = list(servers)
    servers_by_alias = {
        server.ssh_alias.casefold(): server
        for server in server_list
        if server.ssh_alias
    }
    servers_by_id = {server.id: server for server in server_list}
    alias_configs = {
        server.ssh_alias.casefold(): server.ssh_config_file
        for server in server_list if server.ssh_alias and server.ssh_config_file
    }
    for choice in profile.pending_alias_choices:
        for route in choice.get("routes") or []:
            if route.get("ssh_config_file"):
                for alias in route.get("aliases") or []:
                    alias_configs.setdefault(alias.casefold(), route["ssh_config_file"])

    # Host-line clusters: aliases that share one Host entry become one route.
    host_line_clusters: list[list[str]] = []
    link_groups: list[tuple[str, list[str], str]] = []  # reason, aliases, config_path
    for draft in drafts:
        reason = str(draft.get("reason") or "")
        aliases = [str(alias) for alias in (draft.get("aliases") or []) if alias]
        config_path = str(draft.get("config_path") or "")
        if config_path:
            for alias in aliases:
                alias_configs.setdefault(alias.casefold(), config_path)
        if reason == "same_host_line" and len(aliases) >= 2:
            host_line_clusters.append(list(aliases))
            link_groups.append((reason, list(aliases), config_path))
        elif reason == "same_destination" and len(aliases) >= 2:
            link_groups.append((reason, list(aliases), config_path))

    # Preserve runtime GPU-UUID choices that still point at a live server.
    for item in profile.pending_alias_choices:
        item_reasons = list(item.get("reasons") or [])
        if item.get("reason"):
            item_reasons = list(dict.fromkeys([*item_reasons, item["reason"]]))
        if "same_gpu_uuids" not in item_reasons:
            continue
        if item.get("kept_server_id") not in servers_by_id:
            continue
        aliases = [str(alias) for alias in item.get("aliases") or [] if alias]
        if len(aliases) < 2:
            continue
        config_path = ""
        kept = servers_by_id.get(str(item.get("kept_server_id")))
        if kept is not None and kept.ssh_config_file:
            config_path = kept.ssh_config_file
        link_groups.append(("same_gpu_uuids", aliases, config_path))
        for route in item.get("routes") or []:
            route_aliases = [str(alias) for alias in route.get("aliases") or [] if alias]
            if len(route_aliases) >= 2:
                host_line_clusters.append(route_aliases)

    if not link_groups:
        return ()

    _parent, find, union = _union_find_parent()
    for _reason, aliases, _config in link_groups:
        head = aliases[0].casefold()
        for alias in aliases[1:]:
            union(head, alias.casefold())
    # Also union aliases that share a kept server id from existing pending.
    for item in profile.pending_alias_choices:
        kept_id = item.get("kept_server_id")
        aliases = [str(alias) for alias in item.get("aliases") or [] if alias]
        if not aliases:
            continue
        head = aliases[0].casefold()
        for alias in aliases[1:]:
            union(head, alias.casefold())
        if kept_id and kept_id in servers_by_id:
            server_alias = servers_by_id[kept_id].ssh_alias
            if server_alias:
                union(head, server_alias.casefold())

    # Map each alias to its host-line cluster (primary = first on the Host line).
    alias_to_cluster: dict[str, tuple[str, ...]] = {}
    for cluster in host_line_clusters:
        normalized = tuple(cluster)
        for alias in cluster:
            alias_to_cluster[alias.casefold()] = normalized

    components: dict[str, list[str]] = {}
    for _reason, aliases, _config in link_groups:
        for alias in aliases:
            root = find(alias.casefold())
            bucket = components.setdefault(root, [])
            if alias.casefold() not in {item.casefold() for item in bucket}:
                bucket.append(alias)

    # Reasons and config paths per component.
    component_reasons: dict[str, list[str]] = {}
    component_config: dict[str, str] = {}
    for reason, aliases, config_path in link_groups:
        root = find(aliases[0].casefold())
        reasons = component_reasons.setdefault(root, [])
        if reason not in reasons:
            reasons.append(reason)
        if config_path and not component_config.get(root):
            component_config[root] = config_path

    finalized: list[dict] = []
    used_ids: set[str] = set()
    for root, aliases in components.items():
        if len(aliases) < 2:
            continue
        group_key = alias_choice_group_key("machine", aliases)
        # Honour either the merged machine key or any previously resolved
        # reason-specific key that covered the same alias set.
        if group_key in resolved_keys:
            continue
        alias_keys = {alias.casefold() for alias in aliases}
        if any(
            key.startswith("same_")
            and set(key.split(":", 1)[-1].split("|")) == alias_keys
            for key in resolved_keys
        ):
            continue

        # Build routes from host-line clusters; leftovers are solo routes.
        route_map: dict[str, list[str]] = {}
        for alias in aliases:
            cluster = alias_to_cluster.get(alias.casefold())
            if cluster is None:
                route_map.setdefault(alias.casefold(), [alias])
                continue
            primary = cluster[0]
            members = route_map.setdefault(primary.casefold(), [])
            for member in cluster:
                if member.casefold() not in {item.casefold() for item in members}:
                    if member.casefold() in alias_keys or member.casefold() == alias.casefold():
                        members.append(member)
            # Ensure the current alias is present even if cluster had extras
            # outside this component.
            if alias.casefold() not in {item.casefold() for item in members}:
                members.append(alias)
        # Trim route members to aliases that belong to this component.
        routes_aliases: list[list[str]] = []
        for members in route_map.values():
            trimmed = [alias for alias in members if alias.casefold() in alias_keys]
            if trimmed:
                routes_aliases.append(trimmed)
        if len(routes_aliases) < 2:
            # A single Host-line (or solo) cluster has nothing to choose between.
            continue

        kept_server = None
        default_alias = routes_aliases[0][0]
        for route_aliases in routes_aliases:
            for alias in route_aliases:
                match = servers_by_alias.get(alias.casefold())
                if match is not None:
                    kept_server = match
                    default_alias = match.ssh_alias
                    break
            if kept_server is not None:
                break
        if kept_server is None:
            continue
        # Prefer the route whose members include the kept server alias as primary.
        for route_aliases in routes_aliases:
            if kept_server.ssh_alias.casefold() in {alias.casefold() for alias in route_aliases}:
                default_alias = route_aliases[0]
                if kept_server.ssh_alias.casefold() == route_aliases[0].casefold():
                    default_alias = route_aliases[0]
                else:
                    # Keep the profile's alias as primary when it was a sibling.
                    default_alias = kept_server.ssh_alias
                    # Move kept alias to front of that route.
                    route_aliases[:] = [kept_server.ssh_alias] + [
                        alias
                        for alias in route_aliases
                        if alias.casefold() != kept_server.ssh_alias.casefold()
                    ]
                break

        config_path = (
            component_config.get(root)
            or kept_server.ssh_config_file
            or profile.server_config_path
            or ""
        )
        summaries = {
            alias: format_openssh_connection_summary(alias_configs.get(alias.casefold(), config_path), alias)
            for alias in aliases if alias_configs.get(alias.casefold(), config_path)
        }
        routes = []
        for route_aliases in routes_aliases:
            primary = route_aliases[0]
            routes.append(
                {
                    "primary_alias": primary,
                    "aliases": list(route_aliases),
                    "summary": summaries.get(primary, primary),
                    "ssh_config_file": alias_configs.get(primary.casefold(), config_path),
                }
            )
        # Stable order: default route first, then remaining by primary alias.
        routes.sort(
            key=lambda route: (
                0 if route["primary_alias"].casefold() == default_alias.casefold() else 1,
                route["primary_alias"].casefold(),
            )
        )

        prior = next(
            (
                item
                for item in profile.pending_alias_choices
                if item.get("group_key") == group_key
                or {str(alias).casefold() for alias in item.get("aliases") or []}
                == alias_keys
            ),
            None,
        )
        if prior:
            choice_id = prior["id"]
        else:
            digest = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:14]
            choice_id = _server_id_from_alias(f"ac-{digest}", used_ids)
        used_ids.add(choice_id.casefold())
        reasons = component_reasons.get(root) or ["same_destination"]
        finalized.append(
            normalize_pending_alias_choice(
                {
                    "id": choice_id,
                    "group_key": group_key,
                    "reason": reasons[0],
                    "reasons": reasons,
                    "kept_server_id": kept_server.id,
                    "display_name": kept_server.display_name,
                    "default_alias": default_alias,
                    "aliases": aliases,
                    "routes": routes,
                    "summaries": summaries,
                }
            )
        )
        if len(finalized) >= MAX_PENDING_ALIAS_CHOICES:
            break
    return tuple(finalized)

def coalesce_pending_alias_choices(
    profile: Profile,
    servers: tuple[ServerProfile, ...] | list[ServerProfile],
    choices: list[dict] | tuple[dict, ...],
) -> tuple[dict, ...]:
    """Merge overlapping pending choices that share an alias or kept server."""

    if not choices:
        return ()
    probe = replace(profile, pending_alias_choices=tuple(choices))
    return _finalize_pending_alias_choices(probe, servers, [])
