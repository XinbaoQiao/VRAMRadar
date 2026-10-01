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
    token, expiry, _ = read_tokens(root)
    return token, expiry


def read_tokens(root: Path) -> tuple[str | None, float | None, float | None]:
    """(access token, its expiry, refresh-token expiry).  The refresh token
    itself is only inspected for its ``exp`` claim and never returned or used
    (Kimi rotates it; we never run the refresh flow)."""
    path = root / TOKEN_STORE
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_STORE_BYTES + 1)
        if len(data) > MAX_STORE_BYTES:
            return None, None, None
        document = json.loads(data.decode("utf-8", "replace"))
        stored = document.get("data") if isinstance(document, dict) else None
        if document.get("encryption") != "safeStorage.v1" or not isinstance(stored, str):
            return None, None, None
        key = grok_usage.read_os_crypt_key(root)
        plain = grok_usage._decrypt_v10(key, stored) if key else None
        del key
        tokens = json.loads(plain).get("tokens") if plain else None
        del plain
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        refresh = tokens.get("refresh_token") if isinstance(tokens, dict) else None
        refresh_expiry = jwt_expiry(refresh) if isinstance(refresh, str) else None
        del tokens, refresh  # the refresh token itself is never kept
        if not isinstance(token, str) or token.count(".") != 2:
            return None, None, refresh_expiry
        return token, jwt_expiry(token), refresh_expiry
    except (OSError, ValueError, AttributeError, TypeError):
        return None, None, None


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


def _omni_balance(payload):
    """The FEATURE_OMNI credit balance from either response (or None)."""
    if not isinstance(payload, dict):
        return None
    candidates = []
    if isinstance(payload.get("subscriptionBalance"), dict):
        candidates.append(payload["subscriptionBalance"])
    if isinstance(payload.get("balances"), list):
        candidates.extend(b for b in payload["balances"][:20] if isinstance(b, dict))
    omni = [b for b in candidates if b.get("feature") in (None, "FEATURE_OMNI")]
    return (omni or candidates or [None])[0]


def _used_percent(ratio):
    """``amountUsedRatio`` is a 0..1 fraction; tolerate a 0..100 percentage."""
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:
        return None
    return min(100.0, value * 100 if value <= 1 else value)


def parse_usage(subscription, stats) -> dict | None:
    """Non-secret quota fields from GetSubscription / GetSubscriptionStats.

    Real shape (Kimi 3.2.4): ``subscription.goods.{title, membershipLevel}``,
    ``subscription.currentEndTime``, ``balances[]`` and
    ``subscriptionBalance`` with ``{feature, unit, amountUsedRatio,
    expireTime}``.  IDs are never read.  Older/log-style keys are a fallback.
    """
    balance = _omni_balance(stats) or _omni_balance(subscription)
    sub = subscription.get("subscription") if isinstance(subscription, dict) else None
    sub = sub if isinstance(sub, dict) else {}
    goods = sub.get("goods") if isinstance(sub.get("goods"), dict) else {}
    used = _used_percent(balance.get("amountUsedRatio")) if balance else None
    reset = None
    if balance:
        reset = _as_time(balance.get("expireTime")) or _as_time(
            (balance.get("upcomingExpiration") or {}).get("timestamp") if isinstance(balance.get("upcomingExpiration"), dict) else None)
    reset = reset or _as_time(sub.get("currentEndTime"))
    level = goods.get("membershipLevel")
    legacy_sub, legacy_stats = _pick(subscription, _SUB_KEYS), _pick(stats, _STATS_KEYS)
    if used is None and not sub and not legacy_sub and not legacy_stats:
        return None
    if level is None and legacy_sub.get("level") is not None:
        level = legacy_sub.get("level")
    member = (level not in ("LEVEL_FREE", "LEVEL_UNSPECIFIED") if isinstance(level, str) and level.startswith("LEVEL_")
              else _as_bool(legacy_sub.get("is_member")))
    exhausted = (used >= 100) if used is not None else _as_bool(legacy_sub.get("exhausted"))
    title = goods.get("title")
    return {"status": "ok", "used_percent": used, "reset_at": reset or _as_time(legacy_sub.get("reset_at")),
            "plan": str(title)[:24] if isinstance(title, str) else None,
            "level": str(level)[:24] if level is not None else None, "is_member": member,
            "exhausted": exhausted, "active": _as_bool(sub.get("active")),
            "overdrawn": _as_bool(legacy_stats.get("overdrawn")),
            "send_blocked": _as_bool(legacy_stats.get("send_blocked"))}


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
    token, expiry, refresh_expiry = read_tokens(root)
    if not token:
        return {"status": "no_credential"}
    try:
        if expiry is not None and expiry <= clock() + EXPIRY_SKEW:
            # Kimi's access token lives ~15 min and is renewed by Kimi itself
            # while it runs.  A still-valid login only needs Kimi opened.
            login_valid = refresh_expiry is not None and refresh_expiry > clock()
            return {"status": "unauthorized", "reason": "expired" if not login_valid else "access_expired",
                    "expired_at": expiry, "login_valid": login_valid}
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