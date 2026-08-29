from __future__ import annotations

import json
from pathlib import Path
import re

from . import __version__


_RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-macos-beta\.\d+)?$")


def current_release_tag() -> str:
    """Return immutable build metadata, or the source/stable version fallback."""

    metadata = Path(__file__).with_name("_build_info.json")
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        release_tag = payload.get("release_tag") if isinstance(payload, dict) else None
        if isinstance(release_tag, str) and _RELEASE_TAG.fullmatch(release_tag):
            return release_tag
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return f"v{__version__}"
