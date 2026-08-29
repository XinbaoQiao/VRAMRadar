from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from typing import Any

from .updater import INSTALL_MARKER


def _status(plan_path: Path, message: str) -> None:
    try:
        with (plan_path.parent / "update-status.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f} {message}\n")
    except OSError:
        pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_for_exit(pid: int, timeout_seconds: float = 30.0) -> bool:
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        wait_object_0 = 0
        wait_timeout = 258
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, max(1, int(timeout_seconds * 1000)))
            if result == wait_object_0:
                return True
            if result == wait_timeout:
                return False
            raise OSError("could not wait for the existing process")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _load_plan(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported update plan")
    return document


def run_update(plan_path: Path) -> int:
    plan_path = plan_path.resolve()
    _status(plan_path, "plan-load")
    plan = _load_plan(plan_path)
    install_root = Path(plan["install_root"]).resolve()
    executable = Path(plan["app_executable"]).resolve()
    installer = Path(plan["installer"]).resolve()
    activation_path = Path(plan["activation_path"]).resolve()
    expected_hash = str(plan["sha256"])
    version = str(plan["version"])
    pid = int(plan["pid"])
    restart_arguments = plan.get("restart_arguments")
    validation_mode = plan.get("validation_mode", False)
    if (
        executable.parent != install_root
        or executable.name.lower() != "vramradar.exe"
        or installer.parent != plan_path.parent
        or re.fullmatch(r"\d+\.\d+\.\d+", version) is None
        or installer.name != f"VRAMRadar-Setup-{version}.exe"
        or not (install_root / INSTALL_MARKER).is_file()
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
        or not isinstance(restart_arguments, list)
        or not isinstance(validation_mode, bool)
        or len(restart_arguments) not in {2, 3, 4, 5}
        or restart_arguments[0] != "--profile"
        or not isinstance(restart_arguments[1], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", restart_arguments[1]) is None
    ):
        raise ValueError("unsafe update plan")
    tail = restart_arguments[2:]
    if validation_mode and (len(tail) < 2 or tail[0] != "--home"):
        raise ValueError("unsafe validation update plan")
    if tail not in ([], ["--no-auto-import"]):
        if (
            len(tail) not in {2, 3}
            or tail[0] != "--home"
            or not Path(tail[1]).is_absolute()
            or any(character in tail[1] for character in ("\x00", "\r", "\n"))
            or (len(tail) == 3 and tail[2] != "--no-auto-import")
        ):
            raise ValueError("unsafe update restart arguments")
    if _hash(installer) != expected_hash:
        raise ValueError("installer hash changed after verification")

    _status(plan_path, "shutdown-request")
    # Reuse the main executable's authenticated activation client instead of
    # maintaining a second shutdown protocol in the updater. It selects the
    # same Profile/home and waits for the activation endpoint to disappear.
    subprocess.run(
        [str(executable), *restart_arguments, "--quit-existing"],
        check=False,
        timeout=20,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _status(plan_path, "shutdown-request-complete")
    if not _wait_for_exit(pid):
        raise RuntimeError("VRAM Radar did not close; the existing version was preserved")
    if _hash(installer) != expected_hash:
        raise ValueError("installer hash changed before execution")

    _status(plan_path, "existing-process-exited")
    backup = install_root.with_name(f"{install_root.name}.update-backup-{uuid.uuid4().hex}")
    install_root.replace(backup)
    _status(plan_path, "backup-created")
    try:
        command = [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/DIR={install_root}",
        ]
        if validation_mode:
            command.append("/VRAMRADARVALIDATION=1")
        completed = subprocess.run(command, check=False, timeout=180)
        _status(plan_path, f"installer-exit-{completed.returncode}")
        if completed.returncode != 0 or not executable.is_file() or not (install_root / INSTALL_MARKER).is_file():
            raise RuntimeError(f"installer failed with exit code {completed.returncode}")
        probe = subprocess.run(
            [str(executable), "--show-release"],
            check=False,
            timeout=20,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _status(plan_path, f"launch-probe-exit-{probe.returncode}")
        if probe.returncode != 0:
            raise RuntimeError("the updated application failed its launch probe")
    except Exception:
        if install_root.exists():
            shutil.rmtree(install_root, ignore_errors=True)
        backup.replace(install_root)
        subprocess.Popen([str(executable), *restart_arguments], close_fds=True)
        _status(plan_path, "rollback-restored")
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)
        subprocess.Popen([str(executable), *restart_arguments], close_fds=True)
        _status(plan_path, "update-complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        return 2
    try:
        return run_update(Path(values[0]))
    except Exception as exc:
        _status(Path(values[0]), f"failed-{type(exc).__name__}-{exc}")
        _status(Path(values[0]), traceback.format_exc().replace("\n", " | "))
        return 1
