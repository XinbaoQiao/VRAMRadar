from __future__ import annotations

import json
from pathlib import Path
import re

from . import __version__


_RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-macos-beta\.\d+)?$")
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _metadata() -> dict[str, object]:
    metadata = Path(__file__).with_name("_build_info.json")
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def current_release_tag() -> str:
    """Return immutable build metadata, or the source/stable version fallback."""

    release_tag = _metadata().get("release_tag")
    if isinstance(release_tag, str) and _RELEASE_TAG.fullmatch(release_tag):
        return release_tag
    return f"v{__version__}"


def current_build_commit() -> str | None:
    """Return the exact packaged source commit when the build recorded one."""

    source_commit = _metadata().get("source_commit")
    if isinstance(source_commit, str) and _SOURCE_COMMIT.fullmatch(source_commit):
        return source_commit
    return None
