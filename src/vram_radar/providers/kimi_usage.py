"""Kimi membership quota via the Kimi app's own stored login (opt-in only).

Runs only with ``providers.session_consent("kimi")`` (and only while Kimi is
displayed).  Mirrors grok_usage's safeguards:

* the token is read from ``bridge-store/token-store.json`` (Electron
  safeStorage v10: AES-GCM key from ``Local State`` unwrapped with DPAPI) and
  decrypted in memory only; it is never logged, cached or written anywhere;
* only the two read-only calls the Kimi app itself makes on start
  (``MembershipService/GetSubscription`` and ``GetSubscriptionStats``, seen
  in Kimi's own ``logs/main.log`` and its bundle) are sent, to www.kimi.com,
  with redirects refused so the header cannot be forwarded;
* no refresh flow: the refresh token is never touched (Kimi rotates it).  An
  expired access token is detected locally (JWT ``exp``) and reported as
  "sign in again / open Kimi once" without any network call;
* at most one round per 5 minutes, exponential backoff, 10 s timeout.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import grok_usage

LOG = logging.getLogger("vram_radar")

BACKEND = "https://www.kimi.com"
SERVICE = "/apiv2/kimi.gateway.membership.v2.MembershipService/"
METHODS = ("GetSubscription", "GetSubscriptionStats")
TIMEOUT_SECONDS = grok_usage.TIMEOUT_SECONDS
MAX_STORE_BYTES = 256 * 1024
MAX_BODY_BYTES = 256 * 1024
EXPIRY_SKEW = 60
TOKEN_STORE = Path("bridge-store") / "token-store.json"

_SUB_KEYS = {"level": "level", "isMember": "is_member", "is_member": "is_member",
             "omniRatio": "omni_ratio", "omni_ratio": "omni_ratio", "exhausted": "exhausted",
             "resetAt": "reset_at", "reset_at": "reset_at"}
_STATS_KEYS = {"overdrawn": "overdrawn", "sendBlocked": "send_blocked", "send_blocked": "send_blocked"}


def jwt_expiry(token: str) -> float | None:
    """``exp`` claim of a JWT (unverified; used only to skip doomed calls)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        exp = json.loads(base64.urlsafe_b64decode(part.encode("ascii"))).get("exp")
        return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None
    except Exception:
        return None


def read_access(root: Path) -> tuple[str | None, float | None]:
    """(access token, expiry) from Kimi's encrypted token store, in memory."""
    path = root / TOKEN_STORE
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_STORE_BYTES + 1)
        if len(data) > MAX_STORE_BYTES:
            return None, None
        document = json.loads(data.decode("utf-8", "replace"))
        stored = document.get("data") if isinstance(document, dict) else None
        if document.get("encryption") != "safeStorage.v1" or not isinstance(stored, str):
            return None, None
        key = grok_usage.read_os_crypt_key(root)
        plain = grok_usage._decrypt_v10(key, stored) if key else None
        del key
        tokens = json.loads(plain).get("tokens") if plain else None
        del plain
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        del tokens  # the refresh token is dropped unread
        if not isinstance(token, str) or token.count(".") != 2:
            return None, None
        return token, jwt_expiry(token)
    except (OSError, ValueError, AttributeError, TypeError):
        return None, None


def _pick(payload, keys, depth=0, found=None):
    found = {} if found is None else found
    if depth > 5:
        return found
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = keys.get(key)
            if name and name not in found and isinstance(value, (str, int, float, bool)):
                found[name] = value
            elif isinstance(value, (dict, list)):
                _pick(value, keys, depth + 1, found)
    elif isinstance(payload, list):
        for value in payload[:20]:
            _pick(value, keys, depth + 1, found)
    return found


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _as_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value) / (1000 if value > 1e11 else 1)
    if isinstance(value, str) and value:
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return _as_time(float(value))
            except ValueError:
                return None
    return None


def parse_usage(subscription, stats) -> dict | None:
    """Only the fields Kimi itself logs; anything unknown is ignored."""
    sub = _pick(subscription, _SUB_KEYS)
    st = _pick(stats, _STATS_KEYS)
    if not sub and not st:
        return None
    level = sub.get("level")
    ratio = sub.get("omni_ratio")
    try:
        ratio = float(ratio) if ratio is not None and not isinstance(ratio, bool) else None
    except (TypeError, ValueError):
        ratio = None
    return {"status": "ok", "level": str(level)[:20] if level is not None else None,
            "is_member": _as_bool(sub.get("is_member")), "exhausted": _as_bool(sub.get("exhausted")),
            "omni_ratio": ratio, "reset_at": _as_time(sub.get("reset_at")),
            "overdrawn": _as_bool(st.get("overdrawn")), "send_blocked": _as_bool(st.get("send_blocked"))}


def _post(method: str, token: str, backend: str = BACKEND):
    request = urllib.request.Request(
        backend.rstrip("/") + SERVICE + method, data=b"{}", method="POST",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json",
                 "connect-protocol-version": "1", "accept": "application/json",
                 "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kimi/3.2.4"})
    try:
        with grok_usage._OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return "error", None
        return "ok", json.loads(body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return ("unauthorized" if error.code in (401, 403) else "error"), None
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return "error", None


def fetch_usage(root: Path, backend: str = BACKEND, *, post=_post, clock=time.time) -> dict:
    """{"status": "ok"|"no_credential"|"unauthorized"|"error", ...}; never
    logs the token or a response body."""
    token, expiry = read_access(root)
    if not token:
        return {"status": "no_credential"}
    try:
        if expiry is not None and expiry <= clock() + EXPIRY_SKEW:
            return {"status": "unauthorized", "reason": "expired", "expired_at": expiry}
        replies = {}
        for method in METHODS:
            status, payload = post(method, token, backend)
            if status != "ok":
                return {"status": status, **({"reason": "rejected"} if status == "unauthorized" else {})}
            replies[method] = payload
        return parse_usage(replies.get("GetSubscription"), replies.get("GetSubscriptionStats")) or {"status": "error"}
    finally:
        del token


def signature(root: Path) -> tuple:
    try:
        stat = (root / TOKEN_STORE).stat()
        return (int(stat.st_mtime), stat.st_size)
    except OSError:
        return (0, 0)