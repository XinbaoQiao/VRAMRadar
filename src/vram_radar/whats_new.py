"""User-facing "what\'s new" notes for feature releases.

Bug-fix releases stay silent: the popup appears only when a GitHub Release
body marks notes with ``show_whats_new: true`` (see docs).  Dismissals are
remembered per version so the card never repeats forever.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import require_id
from .storage import StoragePaths, atomic_write_text

# HTML comment in the GitHub Release body (ignored by Markdown renderers).
_MARKER = re.compile(
    r"<!--\s*vram-radar:whats-new\s*(?P<body>\{.*?\})\s*-->",
    re.IGNORECASE | re.DOTALL,
)
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-macos-beta\.\d+)?$")

MAX_ITEMS = 6
MAX_ITEM_CHARS = 120
MAX_STORE_BYTES = 65_536


def _clean_lines(value: Any, *, english: bool) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).strip()
        if not text or len(text) > MAX_ITEM_CHARS:
            continue
        if english and _CJK.search(text):
            continue
        out.append(text)
        if len(out) >= MAX_ITEMS:
            break
    return out


def parse_whats_new(body: str | None, *, version: str) -> dict[str, Any] | None:
    """Return normalized notes when the release opts in; otherwise None.

    Required JSON inside ``<!-- vram-radar:whats-new ... -->``::

        {"show_whats_new": true, "en": ["..."], "zh": ["..."]}
    """
    if not isinstance(body, str) or not body.strip():
        return None
    ver = str(version or "").removeprefix("v").strip()
    if not _VERSION.fullmatch(ver):
        return None
    match = _MARKER.search(body)
    if match is None:
        return None
    try:
        payload = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("show_whats_new") is not True:
        return None
    english = _clean_lines(payload.get("en"), english=True)
    chinese = _clean_lines(payload.get("zh"), english=False)
    if not english or not chinese:
        return None
    return {
        "show": True,
        "version": ver,
        "en": english,
        "zh": chinese,
    }


def lines_for_language(notes: dict[str, Any], language: str) -> list[str]:
    if language == "en":
        return list(notes.get("en") or [])
    return list(notes.get("zh") or [])


def dialog_spec(notes: dict[str, Any], language: str) -> dict[str, Any]:
    """Spec for ``ui_dialogs.show_dialog`` (Windows native card)."""
    english = language == "en"
    lines = lines_for_language(notes, language)
    version = str(notes.get("version") or "")
    bullets = [f"• {line}" for line in lines]
    return {
        "headline": "What's new" if english else "新功能",
        "lines": bullets,
        "note": (f"VRAM Radar {version}" if version else ""),
        "buttons": [("ok", "Got it" if english else "知道了", True)],
        "cancel": "ok",
    }


class WhatsNewStore:
    """Dismissed versions + one pending payload after a feature update install."""

    def __init__(self, paths: StoragePaths, profile_id: str = "default") -> None:
        require_id(profile_id, "profile id")
        self.path = paths.config / "whats-new.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": 1, "dismissed": {}, "pending": None}
        try:
            if self.path.stat().st_size > MAX_STORE_BYTES:
                return {"schema_version": 1, "dismissed": {}, "pending": None}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"schema_version": 1, "dismissed": {}, "pending": None}
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return {"schema_version": 1, "dismissed": {}, "pending": None}
        dismissed = raw.get("dismissed")
        if not isinstance(dismissed, dict):
            dismissed = {}
        pending = raw.get("pending")
        if pending is not None and not isinstance(pending, dict):
            pending = None
        return {"schema_version": 1, "dismissed": dismissed, "pending": pending}

    def _save(self, state: dict[str, Any]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        )

    def is_dismissed(self, version: str) -> bool:
        ver = str(version or "").removeprefix("v").strip()
        state = self._load()
        return bool(state.get("dismissed", {}).get(ver))

    def dismiss(self, version: str) -> None:
        ver = str(version or "").removeprefix("v").strip()
        if not ver:
            return
        state = self._load()
        dismissed = dict(state.get("dismissed") or {})
        dismissed[ver] = True
        # Bound growth: keep the newest ~40 keys.
        if len(dismissed) > 40:
            keys = list(dismissed.keys())[-40:]
            dismissed = {key: True for key in keys}
        pending = state.get("pending")
        if isinstance(pending, dict) and str(pending.get("version") or "") == ver:
            pending = None
        self._save({"schema_version": 1, "dismissed": dismissed, "pending": pending})

    def set_pending(self, notes: dict[str, Any] | None) -> None:
        if not notes or not notes.get("show"):
            return
        ver = str(notes.get("version") or "").removeprefix("v").strip()
        if not ver or self.is_dismissed(ver):
            return
        state = self._load()
        state["pending"] = {
            "show": True,
            "version": ver,
            "en": list(notes.get("en") or []),
            "zh": list(notes.get("zh") or []),
        }
        self._save(state)

    def take_pending_for(self, current_version: str) -> dict[str, Any] | None:
        """Return pending notes when they match the running version and are new."""
        ver = str(current_version or "").removeprefix("v").strip()
        state = self._load()
        pending = state.get("pending")
        if not isinstance(pending, dict):
            return None
        if str(pending.get("version") or "") != ver:
            return None
        if self.is_dismissed(ver):
            state["pending"] = None
            self._save(state)
            return None
        if not pending.get("en") or not pending.get("zh"):
            return None
        return {
            "show": True,
            "version": ver,
            "en": list(pending.get("en") or []),
            "zh": list(pending.get("zh") or []),
        }

    def should_offer(self, notes: dict[str, Any] | None) -> bool:
        if not notes or not notes.get("show"):
            return False
        ver = str(notes.get("version") or "").removeprefix("v").strip()
        return bool(ver) and not self.is_dismissed(ver)
