from __future__ import annotations

import argparse
import hashlib
import json
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


def wait_until_removable(path: Path, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.2)
    raise RuntimeError(f"updated process did not release {path}")


def validate(installer: Path) -> None:
    installer = installer.resolve()
    version = installer.stem.removeprefix("VRAMRadar-Setup-")
    with tempfile.TemporaryDirectory(prefix="vram-radar-update-") as temporary:
        root = Path(temporary)
        install_root = root / "install"
        home = root / "home"
        profile = "packaged-update-test"
        subprocess.run(
            [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={install_root}", "/TASKS="],
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
            }
            plan_path = stage / "update-plan.json"
            plan_path.write_text(json.dumps(plan, separators=(",", ":")), encoding="utf-8")
            completed = subprocess.run([str(staged_updater), str(plan_path)], check=False, timeout=240)
            if completed.returncode != 0:
                raise RuntimeError(f"packaged updater exited with {completed.returncode}")
            wait_for(activation)
            subprocess.run(
                [str(executable), "--profile", profile, "--home", str(home), "--quit-existing"],
                check=True,
                timeout=20,
            )
            wait_until_removable(home / "logs" / "app.log")
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
