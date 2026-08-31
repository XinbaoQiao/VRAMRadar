from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__
from .build_info import current_build_commit, current_release_tag


LATEST_RELEASE_API = "https://api.github.com/repositories/1347320362/releases/latest"
RELEASES_API = "https://api.github.com/repositories/1347320362/releases?per_page=20"
_RELEASE_PAGE_PATTERN = re.compile(
    r"(https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/VRAMRadar)"
    r"/releases/tag/(v?\d+\.\d+\.\d+(?:-macos-beta\.\d+)?)/?"
)
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-macos-beta\.(\d+))?$")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_ASSET_BYTES = 250 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _version_tuple(value: str) -> tuple[int, int, int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("unsupported release version")
    major, minor, patch, beta = match.groups()
    # A stable release sorts after every macOS beta of the same base version.
    return int(major), int(minor), int(patch), 1 if beta is None else 0, int(beta or 0)


def _read_payload(
    request: Request,
    timeout_seconds: float,
    opener: Callable[..., Any],
    *,
    ssl_context: ssl.SSLContext | None = None,
) -> Any:
    open_kwargs: dict[str, Any] = {"timeout": max(0.5, float(timeout_seconds))}
    if ssl_context is not None:
        open_kwargs["context"] = ssl_context
    with opener(request, **open_kwargs) as response:
        payload_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload_bytes) > _MAX_RESPONSE_BYTES:
        raise ValueError("release response is too large")
    return json.loads(payload_bytes.decode("utf-8"))


def _macos_system_trust_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError as exc:
        raise RuntimeError("macOS system trust support is unavailable") from exc
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _update_failure_details(exc: BaseException) -> tuple[str, str, int | None]:
    if isinstance(exc, HTTPError):
        if exc.code in {403, 429}:
            return "update_rate_limited", "GitHub 暂时限制了匿名检查，请稍后重试", exc.code
        return "update_http_failed", f"GitHub 返回 HTTP {exc.code}，请稍后重试", exc.code

    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (ssl.SSLCertVerificationError, ssl.SSLError)):
        return "update_tls_failed", "无法验证 GitHub 的 HTTPS 证书", None
    if isinstance(reason, socket.gaierror):
        return "update_dns_failed", "无法解析 GitHub 地址，请检查 DNS 或网络", None
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "update_timeout", "连接 GitHub 超时，请稍后重试", None
    if isinstance(exc, RuntimeError):
        return "update_tls_unavailable", "应用缺少 macOS 系统证书信任组件", None
    if isinstance(exc, (UnicodeError, json.JSONDecodeError)):
        return "update_response_invalid", "GitHub 返回了无法解析的响应", None
    if isinstance(exc, ValueError):
        return "update_metadata_invalid", "GitHub Release 元数据不完整或不受信任", None
    return "update_network_failed", "暂时无法连接 GitHub，请稍后重试", None


def _trusted_release(payload: Any) -> tuple[str, str, tuple[int, int, int, int, int]] | None:
    if not isinstance(payload, dict) or payload.get("draft"):
        return None
    tag_name = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag_name, str) or not isinstance(release_url, str):
        return None
    try:
        version = _version_tuple(tag_name)
    except ValueError:
        return None
    is_beta = version[3] == 0
    if bool(payload.get("prerelease")) != is_beta:
        return None
    release_page = _RELEASE_PAGE_PATTERN.fullmatch(release_url)
    if release_page is None or release_page.group(2) != tag_name:
        return None
    return tag_name, release_url, version


def _trusted_asset(payload: Any, tag_name: str, *, platform_name: str) -> dict[str, Any] | None:
    if platform_name == "win32":
        name = f"VRAMRadar-Setup-{tag_name.removeprefix('v')}.exe"
    elif platform_name == "darwin":
        name = f"VRAMRadar-{tag_name.removeprefix('v')}-macos.zip"
    else:
        return None
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        return None
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        return None
    asset = matches[0]
    release_url = payload.get("html_url")
    release_page = (
        _RELEASE_PAGE_PATTERN.fullmatch(release_url)
        if isinstance(release_url, str)
        else None
    )
    if release_page is None or release_page.group(2) != tag_name:
        return None
    expected_url = f"{release_page.group(1)}/releases/download/{tag_name}/{name}"
    digest = asset.get("digest")
    size = asset.get("size")
    digest_match = _SHA256_PATTERN.fullmatch(digest) if isinstance(digest, str) else None
    if (
        asset.get("browser_download_url") != expected_url
        or digest_match is None
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= _MAX_ASSET_BYTES
    ):
        return None
    return {
        "name": name,
        "url": expected_url,
        "sha256": digest_match.group(1).lower(),
        "size": size,
    }


def check_latest_release(
    current_version: str = __version__,
    *,
    current_tag: str | None = None,
    current_commit: str | None = None,
    timeout_seconds: float = 4.0,
    opener: Callable[..., Any] | None = None,
    platform_name: str = sys.platform,
) -> dict[str, Any]:
    """Check stable updates, plus newer macOS betas for a packaged beta build."""

    installed_tag = current_tag or current_release_tag()
    installed_commit = current_commit if current_commit is not None else current_build_commit()
    if not isinstance(installed_commit, str) or _COMMIT_PATTERN.fullmatch(installed_commit) is None:
        installed_commit = None
    installed = _version_tuple(installed_tag)
    beta_channel = installed[3] == 0
    request = Request(
        RELEASES_API if beta_channel else LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VRAMRadar/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        active_opener = opener or urlopen
        ssl_context = _macos_system_trust_context() if opener is None and platform_name == "darwin" else None
        payload = _read_payload(
            request,
            timeout_seconds,
            active_opener,
            ssl_context=ssl_context,
        )
        if beta_channel:
            if not isinstance(payload, list):
                raise ValueError("unexpected release response")
            releases = [release for item in payload if (release := _trusted_release(item)) is not None]
            if not releases:
                raise ValueError("release metadata is incomplete")
            tag_name, release_url, latest = max(releases, key=lambda item: item[2])
            published_at = next(
                (
                    item.get("published_at")
                    for item in payload
                    if isinstance(item, dict)
                    and item.get("tag_name") == tag_name
                    and isinstance(item.get("published_at"), str)
                ),
                None,
            )
        else:
            release = _trusted_release(payload)
            if release is None or release[2][3] == 0:
                raise ValueError("unexpected release response")
            tag_name, release_url, latest = release
            published_at = payload.get("published_at") if isinstance(payload.get("published_at"), str) else None
        release_payload = (
            next(
                (
                    item
                    for item in payload
                    if isinstance(item, dict) and item.get("tag_name") == tag_name
                ),
                {},
            )
            if beta_channel
            else payload
        )
        latest_commit_value = release_payload.get("target_commitish") if isinstance(release_payload, dict) else None
        latest_commit = (
            latest_commit_value.lower()
            if isinstance(latest_commit_value, str)
            and _COMMIT_PATTERN.fullmatch(latest_commit_value.lower())
            else None
        )
        replacement_available = bool(
            latest == installed
            and installed_commit is not None
            and latest_commit is not None
            and latest_commit != installed_commit
        )
        return {
            "ok": True,
            "update_available": latest > installed or replacement_available,
            "replacement_available": replacement_available,
            "current_version": installed_tag.removeprefix("v"),
            "latest_version": tag_name.removeprefix("v"),
            "current_build": installed_commit,
            "latest_build": latest_commit,
            "release_url": release_url,
            "published_at": published_at,
            "asset": _trusted_asset(
                release_payload,
                tag_name,
                platform_name=platform_name,
            ),
        }
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError, RuntimeError) as exc:
        error_code, error_message, http_status = _update_failure_details(exc)
        logging.getLogger("vram_radar").warning(
            "update check failed code=%s exception=%s",
            error_code,
            type(exc).__name__,
        )
        failure = {
            "ok": False,
            "update_available": False,
            "current_version": installed_tag.removeprefix("v"),
            "code": error_code,
            "error": error_message,
        }
        if http_status is not None:
            failure["http_status"] = http_status
        return failure
