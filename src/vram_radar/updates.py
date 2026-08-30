from __future__ import annotations

import json
import re
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


def _read_payload(request: Request, timeout_seconds: float, opener: Callable[..., Any]) -> Any:
    with opener(request, timeout=max(0.5, float(timeout_seconds))) as response:
        payload_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload_bytes) > _MAX_RESPONSE_BYTES:
        raise ValueError("release response is too large")
    return json.loads(payload_bytes.decode("utf-8"))


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
    opener: Callable[..., Any] = urlopen,
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
        payload = _read_payload(request, timeout_seconds, opener)
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
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "ok": False,
            "update_available": False,
            "current_version": installed_tag.removeprefix("v"),
            "error": "暂时无法检查 GitHub 最新版本",
        }
