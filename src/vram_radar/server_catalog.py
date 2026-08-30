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

from .models import ConfigError, Profile, ServerProfile


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
    if document.get("version") != 2 or not isinstance(document.get("servers"), dict):
        raise ConfigError("服务器设置文件必须是受支持的 servers.toml version 2")
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
) -> tuple[list[str], int, list[str], str]:
    aliases: list[str] = []
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
            for alias in fields[1:]:
                alias_key = alias.casefold()
                if not alias or any(character in alias for character in OPENSSH_PATTERN_CHARACTERS):
                    continue
                if alias_key not in seen_aliases:
                    seen_aliases.add(alias_key)
                    aliases.append(alias)
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
    return aliases, included_files, warnings, dependency_hash.hexdigest()


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

    source = _resolved(path)
    if not source.is_file():
        raise ConfigError(f"OpenSSH 配置文件不存在：{source}")
    aliases, included_files, parser_warnings, _dependency_digest = _read_openssh_aliases(source)
    if not aliases:
        raise ConfigError("OpenSSH 配置及其 Include 文件中没有可导入的具体 Host 别名")

    used_ids: set[str] = set()
    imported = tuple(
        ServerProfile.from_dict(
            {
                "id": _server_id_from_alias(alias, used_ids),
                "display_name": alias,
                "backend": "direct_ssh",
                "enabled": True,
                "ssh_alias": alias,
                "ssh_config_file": str(source),
            }
        )
        for alias in aliases
    )
    warnings = ["OpenSSH 配置无法判断直连或 Slurm；已按直连 SSH 导入，请确认每台服务器的类型"]
    warnings.extend(parser_warnings)
    if included_files:
        warnings.append(f"已安全解析 {included_files} 个 OpenSSH Include 文件")
    return imported, warnings


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
    raise ConfigError("无法识别配置格式；请选择 servers.toml v2 或 OpenSSH ~/.ssh/config")


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
    for source in sources:
        prefix = f"{source.name}: " if len(sources) > 1 else ""
        try:
            servers, source_warnings = import_server_config(source)
        except ConfigError as exc:
            if len(sources) == 1:
                raise
            warnings.append(f"{prefix}{exc}，已跳过此来源")
            continue
        successful_sources += 1
        warnings.extend(f"{prefix}{warning}" for warning in source_warnings)
        openssh_source = source.suffix.casefold() != ".toml"
        for server in servers:
            alias_key = server.ssh_alias.casefold()
            if alias_key in ignored_alias_keys:
                ignored_imported_aliases.setdefault(alias_key, server.ssh_alias)
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
            imported_order.append(server.id)

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
        return replace(
            profile,
            server_config_path=synchronized_path,
            auto_sync_servers=len(sources) == 1,
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
            preserved.append(
                replace(
                    existing,
                    ssh_alias=imported.ssh_alias,
                    ssh_config_file=imported.ssh_config_file,
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
    return replace(
        profile,
        servers=tuple([*preserved, *unmatched_existing, *appended]),
        server_config_path=synchronized_path,
        auto_sync_servers=len(sources) == 1,
    ), warnings
