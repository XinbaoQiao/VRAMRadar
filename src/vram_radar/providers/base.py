"""Read-only, local discovery of AI desktop apps for the usage strip.

Safety contract shared by every provider module:

* Never decrypt, print, log or return credential material.  Credential files
  are only checked for *existence* (and non-emptiness); their content is not
  read unless the file is a known non-secret status file.
* Never write to, lock, or move another application's files.  Databases and
  logs are opened read-only; a locked file is copied to a private temporary
  file first (and that copy is deleted immediately).
* Never contact the network.  (Codex is the one exception and keeps using its
  own local ``codex app-server`` exactly as before; see usage_monitor.py.)
* Every read is bounded in size and every failure degrades to ``None``/
  "unknown" instead of raising into the UI thread.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

MAX_JSON_BYTES = 2_000_000
_STRUCTS: dict = {}


def _win_struct(name: str):
    """Win32 structures, defined once (a per-call ctypes class given to
    ``ctypes.POINTER`` stays cached by ctypes forever: a slow memory leak)."""
    cls = _STRUCTS.get(name)
    if cls is not None:
        return cls
    import ctypes
    from ctypes import wintypes
    if name == "PROCESSENTRY32W":
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
        cls = PROCESSENTRY32W
    elif name == "FixedInfo":
        class FixedInfo(ctypes.Structure):
            _fields_ = [("signature", wintypes.DWORD), ("struc", wintypes.DWORD),
                        ("ms", wintypes.DWORD), ("ls", wintypes.DWORD)]
        cls = FixedInfo
    else:
        raise KeyError(name)
    _STRUCTS[name] = cls
    return cls
MAX_TAIL_BYTES = 4_000_000


def text(value: Any, limit: int = 120) -> str:
    """Printable, bounded text for display (never used for secret values)."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value[:limit] if ord(c) >= 32)


def pair(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


# --------------------------------------------------------------------------
# Safe file helpers
# --------------------------------------------------------------------------

def safe_stat(path: Path):
    try:
        return path.stat()
    except (OSError, ValueError):
        return None


def exists(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _read_bytes(path: Path, *, limit: int, tail: bool) -> bytes | None:
    """Read at most ``limit`` bytes; fall back to a private copy if locked."""
    def read(source: Path) -> bytes:
        with open(source, "rb") as handle:
            if tail:
                try:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - limit))
                except OSError:
                    pass
            return handle.read(limit + (0 if tail else 1))
    try:
        return read(path)
    except PermissionError:
        # Some Electron apps hold exclusive handles.  Copying uses a separate
        # read handle; if that is also denied we simply report "unknown".
        pass
    except (OSError, ValueError):
        return None
    temporary = None
    try:
        handle, name = tempfile.mkstemp(prefix="vram-radar-probe-")
        os.close(handle)
        temporary = Path(name)
        shutil.copyfile(path, temporary)
        return read(temporary)
    except (OSError, ValueError):
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def read_json(path: Path, *, limit: int = MAX_JSON_BYTES) -> Any:
    """Parse a small JSON document; ``None`` on absence, size overflow or damage."""
    stat = safe_stat(path)
    if stat is None or stat.st_size > limit:
        return None
    data = _read_bytes(path, limit=limit, tail=False)
    if data is None or len(data) > limit:
        return None
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError):
        return None


def read_tail_lines(path: Path, *, limit: int = MAX_TAIL_BYTES) -> list[str]:
    data = _read_bytes(path, limit=limit, tail=True)
    if not data:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    # The first line of a tail window is usually partial.
    stat = safe_stat(path)
    if stat is not None and stat.st_size > limit and lines:
        lines = lines[1:]
    return lines


def newest_mtime(paths: Iterable[Path]) -> float | None:
    best = None
    for path in paths:
        stat = safe_stat(path)
        if stat is not None and (best is None or stat.st_mtime > best):
            best = stat.st_mtime
    return best


def nonempty_file(path: Path) -> bool:
    stat = safe_stat(path)
    return bool(stat is not None and stat.st_size > 0 and not os.path.isdir(path))


# --------------------------------------------------------------------------
# Environment (injectable for tests)
# --------------------------------------------------------------------------

@dataclass
class Process:
    pid: int
    name: str            # lower-case image name, e.g. "kimi.exe"
    path: str = ""       # full image path when it could be queried


class Environment:
    """Snapshot of the machine facts probes need.  Tests pass a fake."""

    def __init__(self) -> None:
        self.home = Path.home()
        self.appdata = Path(os.environ.get("APPDATA") or self.home / "AppData/Roaming")
        self.localappdata = Path(os.environ.get("LOCALAPPDATA") or self.home / "AppData/Local")
        self.programfiles = [Path(p) for p in dict.fromkeys(
            os.environ.get(name) for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")) if p]
        self.environ = dict(os.environ)
        self._processes: list[Process] | None = None
        self._uninstall: list[dict] | None = None
        self._packages: list[tuple[str, str]] | None = None
        self.extra_roots: list[Path] = []

    def processes(self) -> list[Process]:
        if self._processes is None:
            self._processes = list_processes()
        return self._processes

    def uninstall_entries(self) -> list[dict]:
        if self._uninstall is None:
            self._uninstall = list_uninstall_entries()
        return self._uninstall

    def packages(self) -> list[tuple[str, str]]:
        if self._packages is None:
            self._packages = list_msix_packages()
        return self._packages

    def expand(self, template: str) -> list[Path]:
        values = {"home": [self.home], "appdata": [self.appdata], "localappdata": [self.localappdata],
                  "programfiles": self.programfiles}
        match = re.match(r"^\{(\w+)\}(.*)$", template)
        if not match:
            return [Path(template)]
        roots = values.get(match.group(1), [])
        rest = match.group(2).lstrip("/\\")
        return [root / rest if rest else root for root in roots]


# --------------------------------------------------------------------------
# Windows facts (each function returns [] rather than raising)
# --------------------------------------------------------------------------

def list_processes() -> list[Process]:
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        PROCESSENTRY32W = _win_struct("PROCESSENTRY32W")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return []
        result = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok and len(result) < 20000:
                result.append(Process(int(entry.th32ProcessID), entry.szExeFile.lower()))
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return result
    except Exception:
        return []


def process_path(pid: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def list_uninstall_entries() -> list[dict]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY)]
    entries, seen = [], set()
    for hive, path, view in roots:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            for index in range(4096):
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, name) as key:
                        entry = {"key": name}
                        for field_name in ("DisplayName", "DisplayVersion", "DisplayIcon", "InstallLocation"):
                            try:
                                value = winreg.QueryValueEx(key, field_name)[0]
                                if isinstance(value, str):
                                    entry[field_name] = value[:1024]
                            except OSError:
                                pass
                except OSError:
                    continue
                marker = (hive, name, entry.get("DisplayName"))
                if entry.get("DisplayName") and marker not in seen:
                    seen.add(marker)
                    entries.append(entry)
    return entries


def list_msix_packages() -> list[tuple[str, str]]:
    """(PackageFullName, PackageRootFolder) for the current user, no admin needed."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
        path = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
                r"\AppModel\Repository\Packages")
        result = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as root:
            for index in range(8192):
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                folder = ""
                try:
                    with winreg.OpenKey(root, name) as key:
                        folder = str(winreg.QueryValueEx(key, "PackageRootFolder")[0])
                except OSError:
                    pass
                result.append((name, folder))
        return result
    except Exception:
        return []


def file_version(path: Path) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        version = ctypes.WinDLL("version")
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ""
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or not length.value:
            return ""
        FixedInfo = _win_struct("FixedInfo")
        info = ctypes.cast(pointer, ctypes.POINTER(FixedInfo)).contents
        if info.signature != 0xFEEF04BD:
            return ""
        parts = [info.ms >> 16, info.ms & 0xFFFF, info.ls >> 16, info.ls & 0xFFFF]
        while len(parts) > 3 and parts[-1] == 0:
            parts.pop()
        return ".".join(map(str, parts))
    except Exception:
        return ""


# --------------------------------------------------------------------------
# Generic install detection
# --------------------------------------------------------------------------

@dataclass
class Detection:
    installed: bool = False
    path: str = ""
    version: str = ""
    source: str = ""
    candidates: int = 0
    running: bool | None = None
    pids: list[int] = field(default_factory=list)


def _icon_path(value: str) -> str:
    value = (value or "").strip()
    if value.startswith('"'):
        value = value[1:].split('"', 1)[0]
    else:
        value = re.sub(r",\s*-?\d+$", "", value)
    return value.strip()


def _version_key(value: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", value or "")[:4])


def detect_install(env: Environment, *, uninstall: Iterable[str] = (), processes: Iterable[str] = (),
                   executables: Iterable[str] = (), folders: Iterable[str] = (),
                   packages: Iterable[str] = (), sibling_names: Iterable[str] = ()) -> Detection:
    """Merge every discovery source; prefer a running copy, then the newest one."""
    names = {name.lower() for name in processes}
    exe_names = list(executables)
    candidates: list[tuple[int, tuple, float, str, str, str]] = []  # rank, version, mtime, path, version, source

    def add(path: Path, version: str, source: str, rank: int) -> None:
        try:
            if not path.exists():
                return
            resolved = str(path)
            stat = path.stat()
        except (OSError, ValueError):
            return
        candidates.append((rank, _version_key(version), stat.st_mtime, resolved, version, source))

    def add_folder(folder: Path, version: str, source: str, rank: int) -> None:
        for exe in exe_names:
            if exists(folder / exe):
                add(folder / exe, version, source, rank)
                return
        # Squirrel installers keep the real executable under app-<version>.
        try:
            nested = sorted(folder.glob("app-*"), key=lambda p: _version_key(p.name), reverse=True)
        except OSError:
            nested = []
        for sub in nested[:4]:
            for exe in exe_names:
                if exists(sub / exe):
                    add(sub / exe, version or sub.name[4:], source, rank)
                    return

    detection = Detection()
    running = [process for process in env.processes() if process.name in names] if names else []
    detection.running = bool(running) if env.processes() else None
    detection.pids = [process.pid for process in running][:16]
    for process in running[:4]:
        path = process.path or process_path(process.pid)
        if path:
            add(Path(path), "", "process", 0)
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in uninstall]
    for entry in env.uninstall_entries():
        if not any(pattern.search(entry.get("DisplayName", "")) for pattern in patterns):
            continue
        version = entry.get("DisplayVersion", "")
        icon = _icon_path(entry.get("DisplayIcon", ""))
        if icon.lower().endswith(".exe") and Path(icon).name.lower() in {e.lower() for e in exe_names}:
            add(Path(icon), version, "registry", 1)
        elif icon.lower().endswith(".exe"):
            add_folder(Path(icon).parent, version, "registry", 1)
        location = (entry.get("InstallLocation") or "").strip().strip('"')
        if location:
            add_folder(Path(location), version, "registry", 1)
    package_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in packages]
    for full_name, root in env.packages():
        if any(pattern.search(full_name) for pattern in package_patterns):
            parts = full_name.split("_")
            version = parts[1] if len(parts) > 1 else ""
            if root:
                # WindowsApps content is often not listable; the registered
                # package itself is sufficient evidence of an installation.
                candidates.append((2, _version_key(version), 0.0, root, version, "msix"))
    for template in folders:
        for folder in env.expand(template):
            add_folder(folder, "", "known_path", 3)
    for root in env.extra_roots:
        for name in sibling_names:
            add_folder(root / name, "", "sibling_folder", 4)
    if sys.platform == "darwin":
        # macOS (detection only): an app bundle named after the Windows
        # executable/folder ("Kimi.exe" -> Kimi.app) in /Applications or
        # ~/Applications.  No process/registry/MSIX sources exist there.
        bundles = dict.fromkeys([Path(e).stem + ".app" for e in exe_names] + [n + ".app" for n in sibling_names])
        for root in (Path("/Applications"), env.home / "Applications"):
            for bundle in bundles:
                if exists(root / bundle):
                    add(root / bundle, "", "mac_app", 3)
    if not candidates:
        return detection
    unique = {}
    for item in candidates:
        key = os.path.normcase(item[3])
        if key not in unique or item[0] < unique[key][0]:
            unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: (item[0], tuple(-v for v in item[1]), -item[2]))
    best = ordered[0]
    version = best[4]
    if not version:
        for item in ordered:
            if item[4] and os.path.normcase(os.path.dirname(item[3])) == os.path.normcase(os.path.dirname(best[3])):
                version = item[4]
                break
    if not version and best[3].lower().endswith(".exe"):
        version = file_version(Path(best[3]))
    detection.installed = True
    detection.path = best[3]
    detection.version = text(version, 40)
    detection.source = best[5]
    detection.candidates = len(ordered)
    return detection


def pid_alive(env: Environment, pid: Any, names: Iterable[str]) -> bool | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    processes = env.processes()
    if not processes:
        return None
    wanted = {name.lower() for name in names}
    return any(process.pid == pid and process.name in wanted for process in processes)


# --------------------------------------------------------------------------
# Provider protocol
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    short: str
    probe: Callable[[Environment], dict]
    order: int = 100
    # Session-based quota reading (using the app's own saved login to send a
    # read-only quota query) is strictly opt-in per provider: the user must
    # consent first (see providers.session_consent). ``session_app`` is the
    # local app whose login is used, ``session_server`` whose server is asked.
    needs_session_consent: bool = False
    session_app: str = ""
    session_server: str = ""

    def label(self, english: bool = False) -> str:
        """Display name; English UI uses the app's Latin-script name."""
        return ENGLISH_NAMES.get(self.id, self.name) if english else self.name


# English display names for apps whose registry name is Chinese.
ENGLISH_NAMES = {"glm": "GLM", "qwen": "Qwen", "yuanbao": "Yuanbao"}


def base_state(spec_id: str, name: str, short: str, detection: Detection) -> dict:
    return {
        "id": spec_id, "name": name, "short": short,
        "installed": detection.installed, "install_path": detection.path,
        "version": detection.version, "install_source": detection.source,
        "install_candidates": detection.candidates,
        "running": detection.running, "signed_in": None, "last_used": None,
        "windows": [], "facts": [], "quota_available": False, "quota_reason": "",
        "headline": pair("—", "—"), "subline": pair("", ""),
        "state": "ready" if detection.installed else "not_installed", "code": "",
        "checked_at": time.time(),
    }


def format_tokens(value: float) -> str:
    value = max(0.0, float(value))
    # Switch unit when the smaller one would *display* 1000: 999 950 is
    # "1M", not "1000k" (and 999.6 is "1k", not "1000").
    units = ((1e9, "B"), (1e6, "M"), (1e3, "k"), (1.0, ""))
    for (limit, suffix), (lower, _) in zip(units, units[1:]):
        if value >= limit or round(value / lower, 1 if lower > 1 else 0) >= 1000:
            return f"{value / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.0f}"


def running_pair(running: bool | None) -> dict:
    if running is None:
        return pair("状态未知", "Unknown")
    return pair("运行中", "Running") if running else pair("未运行", "Not running")
