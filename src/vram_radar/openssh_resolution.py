from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import glob
import os
from pathlib import Path
import re
import shlex
from typing import Literal


# Keep static configuration inspection bounded. These limits match the import
# path's public safety envelope, while the visit limit also prevents a small
# recursive Include graph from consuming unbounded CPU.
MAX_OPENSSH_FILE_BYTES = 1024 * 1024
MAX_OPENSSH_TOTAL_BYTES = 4 * 1024 * 1024
MAX_OPENSSH_FILE_VISITS = 64
MAX_OPENSSH_INCLUDE_MATCHES = 256
MAX_OPENSSH_LINES = 100_000
_ENDPOINT_FIELDS = ("hostname", "user", "port")
_DYNAMIC_VALUE_RE = re.compile(r"[%$`]|\x00")


@dataclass(frozen=True)
class OpenSSHEndpointResolution:
    """A non-executing, conservative resolution of one OpenSSH Host alias."""

    status: Literal["exact", "dynamic"]
    hostname: str = ""
    user: str = ""
    port: int | None = None
    reason: str = ""

    @property
    def exact(self) -> bool:
        return self.status == "exact"


@dataclass
class _ResolutionState:
    alias: str
    include_root: Path
    active: bool | None = True
    values: dict[str, str] = field(default_factory=dict)
    uncertain_fields: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    stack: set[str] = field(default_factory=set)
    file_visits: int = 0
    total_bytes: int = 0
    total_lines: int = 0
    matched_host: bool = False

    def mark_uncertain(self, fields: tuple[str, ...], reason: str) -> None:
        for name in fields:
            if name not in self.values:
                self.uncertain_fields.add(name)
        if reason not in self.reasons:
            self.reasons.append(reason)

    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(name for name in _ENDPOINT_FIELDS if name not in self.values)


def _split_openssh_line(line: str) -> list[str]:
    # posix=False retains Windows path separators. Only a matching outer quote
    # pair is stripped, which is sufficient for OpenSSH's whitespace grammar.
    lexer = shlex.shlex(line, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    fields = [
        value[1:-1]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
        else value
        for value in list(lexer)
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


def _host_patterns_match(alias: str, patterns: list[str]) -> bool | None:
    if not patterns:
        return None
    alias_key = alias.casefold()
    positive_match = False
    has_positive = False
    for raw_pattern in patterns:
        negated = raw_pattern.startswith("!")
        pattern = raw_pattern[1:] if negated else raw_pattern
        if not pattern:
            return None
        if not negated:
            has_positive = True
        try:
            matched = fnmatch.fnmatchcase(alias_key, pattern.casefold())
        except (TypeError, ValueError):
            return None
        if matched and negated:
            return False
        if matched:
            positive_match = True
    return positive_match if has_positive else False


def _known_system_config_roots() -> tuple[Path, ...]:
    candidates: list[Path] = [Path("/etc/ssh/ssh_config")]
    if program_data := os.environ.get("PROGRAMDATA"):
        candidates.append(Path(program_data) / "ssh" / "ssh_config")
    return tuple(candidates)


def _include_root(source: Path) -> Path:
    source_key = os.path.normcase(str(source.resolve(strict=False)))
    for system_config in _known_system_config_roots():
        system_key = os.path.normcase(str(system_config.resolve(strict=False)))
        if source_key == system_key:
            return system_config.parent
    return Path.home() / ".ssh"


def _expand_include_pattern(pattern: str, include_root: Path) -> tuple[Path | None, str]:
    home = include_root.parent
    expanded = pattern
    expanded = re.sub(r"%d(?=$|[\\/])", lambda _match: str(home), expanded)
    expanded = re.sub(
        r"\$(?:\{HOME\}|HOME\b)", lambda _match: str(home), expanded, flags=re.IGNORECASE
    )
    expanded = re.sub(
        r"\$env:(HOME|USERPROFILE)\b",
        lambda match: os.environ.get(match.group(1).upper()) or str(home),
        expanded,
        flags=re.IGNORECASE,
    )
    expanded = re.sub(
        r"%(HOME|USERPROFILE)%",
        lambda match: os.environ.get(match.group(1).upper()) or str(home),
        expanded,
        flags=re.IGNORECASE,
    )
    expanded = os.path.expandvars(os.path.expanduser(expanded))
    # Host-, destination-, and connection-dependent percent tokens cannot be
    # resolved without executing OpenSSH's configuration evaluator.
    if re.search(r"%(?![%])", expanded) or "${" in expanded or "$env:" in expanded.casefold():
        return None, "dynamic_include"
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = include_root / candidate
    return candidate, ""


def _include_matches(pattern: str, state: _ResolutionState) -> tuple[list[Path], str]:
    candidate, reason = _expand_include_pattern(pattern, state.include_root)
    if candidate is None:
        return [], reason
    try:
        matches = [Path(value) for value in sorted(glob.glob(str(candidate)))]
    except (OSError, RuntimeError, ValueError):
        return [], "include_unreadable"
    files = [path for path in matches if path.is_file()]
    if len(files) > MAX_OPENSSH_INCLUDE_MATCHES:
        return [], "include_limit"
    return files, ""


def _set_endpoint_value(state: _ResolutionState, keyword: str, values: list[str]) -> None:
    name = {"hostname": "hostname", "user": "user", "port": "port"}[keyword]
    if name in state.values or name in state.uncertain_fields:
        return
    if state.active is False:
        return
    if state.active is None:
        state.mark_uncertain((name,), "conditional_match")
        return
    if len(values) != 1 or not values[0] or _DYNAMIC_VALUE_RE.search(values[0]):
        state.mark_uncertain((name,), "dynamic_endpoint_value")
        return
    if name == "port":
        try:
            port = int(values[0], 10)
        except ValueError:
            state.mark_uncertain((name,), "invalid_port")
            return
        if not 1 <= port <= 65535:
            state.mark_uncertain((name,), "invalid_port")
            return
        state.values[name] = str(port)
        return
    state.values[name] = values[0]


def _visit_config(path: Path, state: _ResolutionState, *, root: bool = False) -> None:
    if state.file_visits >= MAX_OPENSSH_FILE_VISITS:
        state.mark_uncertain(state.unresolved_fields(), "include_limit")
        state.active = None
        return
    try:
        resolved = path.resolve(strict=True)
        key = os.path.normcase(str(resolved))
    except (OSError, RuntimeError):
        if root:
            state.mark_uncertain(state.unresolved_fields(), "config_unreadable")
        else:
            state.mark_uncertain(state.unresolved_fields(), "include_unreadable")
        state.active = None
        return
    if key in state.stack:
        state.mark_uncertain(state.unresolved_fields(), "include_cycle")
        state.active = None
        return
    try:
        size = resolved.stat().st_size
    except OSError:
        state.mark_uncertain(state.unresolved_fields(), "config_unreadable")
        state.active = None
        return
    if size > MAX_OPENSSH_FILE_BYTES or state.total_bytes + size > MAX_OPENSSH_TOTAL_BYTES:
        state.mark_uncertain(state.unresolved_fields(), "config_size_limit")
        state.active = None
        return
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        state.mark_uncertain(state.unresolved_fields(), "config_unreadable")
        state.active = None
        return
    if "\x00" in text:
        state.mark_uncertain(state.unresolved_fields(), "config_invalid")
        state.active = None
        return
    lines = text.splitlines()
    if state.total_lines + len(lines) > MAX_OPENSSH_LINES:
        state.mark_uncertain(state.unresolved_fields(), "config_line_limit")
        state.active = None
        return
    state.file_visits += 1
    state.total_bytes += size
    state.total_lines += len(lines)
    state.stack.add(key)
    try:
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = _split_openssh_line(stripped)
            except ValueError:
                state.mark_uncertain(state.unresolved_fields(), "config_invalid")
                state.active = None
                continue
            if not fields:
                continue
            keyword = fields[0].casefold()
            values = fields[1:]
            if keyword == "host":
                matched = _host_patterns_match(state.alias, values)
                state.active = matched
                if matched is True:
                    state.matched_host = True
                if matched is None:
                    state.mark_uncertain(state.unresolved_fields(), "dynamic_host_pattern")
                continue
            if keyword == "match":
                state.active = True if len(values) == 1 and values[0].casefold() == "all" else None
                continue
            if keyword == "include":
                if state.active is False:
                    continue
                if state.active is None:
                    # Whether the textual Include is evaluated, and therefore
                    # which state it leaves behind, depends on Match at runtime.
                    state.mark_uncertain(state.unresolved_fields(), "conditional_include")
                    continue
                if not values:
                    state.mark_uncertain(state.unresolved_fields(), "config_invalid")
                    state.active = None
                    continue
                for pattern in values:
                    included, reason = _include_matches(pattern, state)
                    if reason:
                        state.mark_uncertain(state.unresolved_fields(), reason)
                        state.active = None
                        break
                    for included_path in included:
                        _visit_config(included_path, state)
                continue
            if keyword in {"hostname", "user", "port"}:
                _set_endpoint_value(state, keyword, values)
                continue
            if keyword == "canonicalizehostname" and state.active is not False:
                if len(values) != 1 or values[0].casefold() not in {"no", "false"}:
                    if state.active is None:
                        state.mark_uncertain(("hostname",), "conditional_canonicalization")
                    elif "hostname" not in state.values:
                        state.mark_uncertain(("hostname",), "hostname_canonicalization")
    finally:
        state.stack.discard(key)


def resolve_openssh_endpoint(
    config_path: str | Path,
    alias: str,
) -> OpenSSHEndpointResolution:
    """Resolve HostName/User/Port without invoking ssh, a shell, or the network.

    OpenSSH's first-obtained-value rule is honored across bounded Include files.
    If a conditional or dynamic construct can affect any endpoint field, the
    whole result is marked ``dynamic`` so callers cannot accidentally publish a
    plausible-looking command with changed connection semantics.
    """

    source = Path(config_path).expanduser()
    state = _ResolutionState(alias=alias, include_root=_include_root(source))
    _visit_config(source, state, root=True)
    if not state.matched_host:
        return OpenSSHEndpointResolution(status="dynamic", reason="host_alias_not_found")
    if state.uncertain_fields:
        return OpenSSHEndpointResolution(
            status="dynamic",
            reason=state.reasons[0] if state.reasons else "dynamic_config",
        )

    # The system-wide ssh_config is evaluated after the selected user config
    # and can supply any still-unset field.  This resolver deliberately does
    # not read that platform-dependent layer, so it must not freeze guessed
    # defaults into -l/-p and thereby change the command's real semantics.
    hostname = state.values.get("hostname", "")
    user = state.values.get("user", "")
    raw_port = state.values.get("port", "")
    if not hostname or not user or not raw_port:
        return OpenSSHEndpointResolution(
            status="dynamic",
            reason="endpoint_fields_unspecified",
        )
    port = int(raw_port)
    return OpenSSHEndpointResolution(
        status="exact",
        hostname=hostname,
        user=user,
        port=port,
    )
