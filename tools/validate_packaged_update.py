from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {path}")


def wait_for_process_exit(pid: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        synchronize = 0x00100000
        wait_object_0 = 0
        wait_timeout = 258
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        try:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, remaining_ms)
            if result == wait_object_0:
                return
            if result != wait_timeout:
                raise OSError("could not wait for updated process")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)
    raise RuntimeError(f"updated process {pid} did not exit")


def validate(installer: Path) -> None:
    installer = installer.resolve()
    version = installer.stem.removeprefix("VRAMRadar-Setup-")
    with tempfile.TemporaryDirectory(prefix="vram-radar-update-") as temporary:
        root = Path(temporary)
        install_root = root / "install"
        home = root / "home"
        profile = "packaged-update-test"
        subprocess.run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                f"/DIR={install_root}",
                "/TASKS=",
                "/VRAMRADARVALIDATION=1",
            ],
            check=True,
            timeout=180,
        )
        executable = install_root / "VRAMRadar.exe"
        updater = install_root / "VRAMRadarUpdater.exe"
        marker = install_root / ".vram-radar-installed"
        if not executable.is_file() or not updater.is_file() or not marker.is_file():
            raise RuntimeError("installed bundle is missing the executable, updater, or marker")

        process = subprocess.Popen(
            [str(executable), "--profile", profile, "--home", str(home), "--no-auto-import"],
            close_fds=True,
        )
        activation = home / "runtime" / f"{profile}.activation.json"
        try:
            wait_for(activation)
            stage = root / "stage"
            stage.mkdir()
            staged_installer = stage / installer.name
            staged_updater = stage / updater.name
            shutil.copy2(installer, staged_installer)
            shutil.copy2(updater, staged_updater)
            plan = {
                "schema_version": 1,
                "pid": process.pid,
                "app_executable": str(executable),
                "install_root": str(install_root),
                "installer": str(staged_installer),
                "sha256": file_hash(staged_installer),
                "version": version,
                "activation_path": str(activation),
                "restart_arguments": [
                    "--profile", profile, "--home", str(home), "--no-auto-import",
                ],
                "validation_mode": True,
            }
            plan_path = stage / "update-plan.json"
            plan_path.write_text(json.dumps(plan, separators=(",", ":")), encoding="utf-8")
            completed = subprocess.run([str(staged_updater), str(plan_path)], check=False, timeout=240)
            if completed.returncode != 0:
                raise RuntimeError(f"packaged updater exited with {completed.returncode}")
            wait_for(activation)
            activation_document = json.loads(activation.read_text(encoding="utf-8"))
            updated_pid = int(activation_document["pid"])
            if updated_pid <= 0 or updated_pid == process.pid:
                raise RuntimeError("updated application did not publish a new process identity")
            subprocess.run(
                [str(executable), "--profile", profile, "--home", str(home), "--quit-existing"],
                check=True,
                timeout=20,
            )
            wait_for_process_exit(updated_pid)
            if not executable.is_file() or not marker.is_file():
                raise RuntimeError("updated installation is incomplete")
            if list(root.glob("install.update-backup-*")):
                raise RuntimeError("successful update left an installation backup behind")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=Path)
    args = parser.parse_args()
    validate(args.installer)
    print("packaged one-click update: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
