from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from typing import Any, Callable
from urllib.request import Request, urlopen


MAX_UPDATE_BYTES = 250 * 1024 * 1024
INSTALL_MARKER = ".vram-radar-installed"
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in {None, 443}:
        return ""
    return (parts.hostname or "").lower()


def download_verified_asset(
    asset: dict[str, Any],
    staging_root: Path,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout_seconds: float = 30.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    name = asset.get("name")
    url = asset.get("url")
    expected_hash = asset.get("sha256")
    expected_size = asset.get("size")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not isinstance(url, str)
        or _host(url) != "github.com"
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or not 1 <= expected_size <= MAX_UPDATE_BYTES
    ):
        raise ValueError("更新文件元数据不完整")

    stage = staging_root / uuid.uuid4().hex
    stage.mkdir(parents=True, exist_ok=False)
    partial = stage / f"{name}.part"
    destination = stage / name
    request = Request(url, headers={"User-Agent": "VRAMRadar-Updater/1", "Accept": "application/octet-stream"})
    digest = hashlib.sha256()
    total = 0
    try:
        if progress_callback is not None:
            progress_callback(0, expected_size)
        with opener(request, timeout=max(1.0, float(timeout_seconds))) as response, partial.open("xb") as handle:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            if _host(final_url) not in _ALLOWED_DOWNLOAD_HOSTS:
                raise ValueError("更新下载被重定向到非 GitHub 地址")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size or total > MAX_UPDATE_BYTES:
                    raise ValueError("更新文件大小与 Release 元数据不一致")
                digest.update(chunk)
                handle.write(chunk)
                if progress_callback is not None:
                    progress_callback(total, expected_size)
            handle.flush()
            os.fsync(handle.fileno())
        if total != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError("更新文件 SHA-256 或大小校验失败")
        partial.replace(destination)
        return destination
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def windows_update_capability() -> tuple[bool, str | None]:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False, "当前不是 Windows 安装版"
    install_root = Path(sys.executable).resolve().parent
    if Path(sys.executable).name.lower() != "vramradar.exe":
        return False, "当前程序文件名不受自动更新支持"
    if not (install_root / INSTALL_MARKER).is_file():
        return False, "当前不是由正式安装器安装的版本"
    if not (install_root / "VRAMRadarUpdater.exe").is_file():
        return False, "此版本缺少更新执行器"
    return True, None


def schedule_windows_update(
    installer: Path,
    *,
    sha256: str,
    version: str,
    activation_path: Path,
    restart_arguments: list[str],
) -> None:
    capable, reason = windows_update_capability()
    if not capable:
        raise RuntimeError(reason or "当前版本不支持一键更新")
    install_root = Path(sys.executable).resolve().parent
    stage = installer.resolve().parent
    helper_source = install_root / "VRAMRadarUpdater.exe"
    helper_copy = stage / "VRAMRadarUpdater.exe"
    shutil.copy2(helper_source, helper_copy)
    plan = {
        "schema_version": 1,
        "pid": os.getpid(),
        "app_executable": str(Path(sys.executable).resolve()),
        "install_root": str(install_root),
        "installer": str(installer.resolve()),
        "sha256": sha256,
        "version": version,
        "activation_path": str(activation_path.resolve()),
        "restart_arguments": list(restart_arguments),
    }
    plan_path = stage / "update-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [str(helper_copy), str(plan_path)],
        cwd=str(stage),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
