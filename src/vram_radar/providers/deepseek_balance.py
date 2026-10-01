"""Read-only DeepSeek wallet balance, mirroring DeepSeek Harness itself.

How the app gets it (verified in ``@deepseek-ai/dsh-deepseek-account-platform``
of DeepSeek Harness 0.2.0-rc.2): after the user signs in, the app stores a
platform *grant* in ``$DSH_HOME/.credentials.yaml`` under the record
``deepseek-account-platform/default`` (``kind: grant``, ``payload.token``,
``payload.issuer``) and shows the account wallet by issuing::

    GET {issuer}/api/v0/users/get_user_summary     x-dsh-auth-token: <token>

whose envelope is ``{code: 0, data: {biz_code: 0, biz_data: {normal_wallets,
bonus_wallets}}}`` with ``{currency: CNY|USD, balance: "<decimal>"}`` items.

For a plain API-key record the documented public read-only endpoint
``GET https://api.deepseek.com/user/balance`` (``Authorization: Bearer``) is
used instead.

Safety contract:
* only GET; never POST/PUT/DELETE, never a sign-in, refresh or logout call;
* the token is read into a local variable, sent only to the issuer's own
  https origin on the allow-list, and never stored, returned, logged or put
  in an exception message;
* redirects are refused (a redirect could forward the header elsewhere);
* response bodies are bounded; parse failures become ``None``, never ``0``;
* callers poll at most every ``MIN_INTERVAL`` seconds with exponential
  backoff after failures (see ``BalanceCache``).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

ALLOWED_ORIGINS = ("https://platform.deepseek.com",)
API_BALANCE_URL = "https://api.deepseek.com/user/balance"
SUMMARY_PATH = "/api/v0/users/get_user_summary"
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_BODY_BYTES = 64 * 1024
TIMEOUT_SECONDS = 10
MIN_INTERVAL = 300          # the app itself refreshes on demand; we stay far below that
MAX_BACKOFF = 3600
_DECIMAL = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$", re.I)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never forward the auth header
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _records(text: str) -> dict[str, dict]:
    """Minimal parser for the app's credentials document: only the
    ``records:`` mapping two levels deep (kind + payload scalars)."""
    records: dict[str, dict] = {}
    current: dict | None = None
    section = None
    in_payload = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        key, _, value = line.partition(":")
        key, value = key.strip().strip("'\""), value.strip().strip("'\"")
        if indent == 0:
            section = key
            current = None
            continue
        if section != "records":
            continue
        if indent == 2:
            current = records.setdefault(key, {"payload": {}})
            in_payload = False
        elif current is not None and indent == 4:
            in_payload = key == "payload" and not value
            if key != "payload":
                current[key] = value
        elif current is not None and indent >= 6 and in_payload:
            current["payload"][key] = value
    return records


def read_grant(home: Path) -> tuple[str, str, str] | None:
    """(kind, token_or_key, origin) for the app's stored account, or None.

    The returned secret must stay in the caller's local scope."""
    path = home / ".credentials.yaml"
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_CREDENTIAL_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_CREDENTIAL_BYTES:
        return None
    records = _records(data.decode("utf-8", "replace"))
    grant = records.get("deepseek-account-platform/default")
    if grant and grant.get("kind") == "grant":
        token = grant["payload"].get("token", "")
        issuer = grant["payload"].get("issuer", "").rstrip("/")
        if token and not token.startswith("dsh_mock_") and issuer in ALLOWED_ORIGINS:
            return "grant", token, issuer
    for name, record in records.items():
        if record.get("kind") == "api-key" and name.startswith("llm-deepseek"):
            key = record.get("key") or record["payload"].get("key", "")
            if key:
                return "api-key", key, "https://api.deepseek.com"
    return None


def _get(url: str, headers: dict) -> tuple[int | None, object]:
    request = urllib.request.Request(url, headers={**headers, "Accept": "application/json",
                                                   "User-Agent": "VRAM-Radar"}, method="GET")
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                return response.status, None
            return response.status, json.loads(body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None, None


def _amount(value) -> Decimal | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not _DECIMAL.match(text):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and abs(amount) < Decimal("1e12") else None


def _wallets(items, kind: str) -> list[dict]:
    wallets = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("currency") in {"CNY", "USD"}:
            amount = _amount(item.get("balance"))
            if amount is not None:
                wallets.append({"currency": item["currency"], "amount": amount, "kind": kind})
    return wallets


def parse_summary(payload) -> dict | None:
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("biz_code") != 0 or not isinstance(data.get("biz_data"), dict):
        return None
    biz = data["biz_data"]
    wallets = _wallets(biz.get("normal_wallets"), "topped_up") + _wallets(biz.get("bonus_wallets"), "granted")
    return {"wallets": wallets, "available": None}


def parse_api_balance(payload) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("balance_infos"), list):
        return None
    wallets = []
    for info in payload["balance_infos"]:
        if not isinstance(info, dict) or info.get("currency") not in {"CNY", "USD"}:
            continue
        for field, kind in (("topped_up_balance", "topped_up"), ("granted_balance", "granted")):
            amount = _amount(info.get(field))
            if amount is not None:
                wallets.append({"currency": info["currency"], "amount": amount, "kind": kind})
    available = payload.get("is_available")
    return {"wallets": wallets, "available": available if isinstance(available, bool) else None}


def totals(wallets: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for wallet in wallets:
        result[wallet["currency"]] = result.get(wallet["currency"], Decimal(0)) + wallet["amount"]
    return result


def fetch_balance(home: Path) -> dict:
    """One read-only query.  Returns a sanitized result without secrets."""
    credential = read_grant(home)
    if credential is None:
        return {"status": "no_credential"}
    kind, secret, origin = credential
    if kind == "grant":
        status, payload = _get(origin + SUMMARY_PATH, {"x-dsh-auth-token": secret})
        parsed = parse_summary(payload) if status == 200 else None
        source = "platform"
    else:
        status, payload = _get(API_BALANCE_URL, {"Authorization": "Bearer " + secret})
        parsed = parse_api_balance(payload) if status == 200 else None
        source = "api"
    del secret
    if status in (401, 403) or (isinstance(payload, dict) and payload.get("code") == 40003):
        return {"status": "unauthorized", "source": source}
    if status == 429:
        return {"status": "rate_limited", "source": source}
    if parsed is None:
        return {"status": "network" if status is None else "unavailable", "http": status, "source": source}
    return {"status": "ok", "source": source, **parsed}


class BalanceCache:
    """Conservative poller: at most once per ``MIN_INTERVAL`` seconds, with
    exponential backoff after failures; serves the last good value meanwhile."""

    def __init__(self, fetch=fetch_balance, *, interval: float = MIN_INTERVAL, clock=time.time):
        self.fetch, self.interval, self.clock = fetch, interval, clock
        self.lock = threading.Lock()
        self.last_good: dict | None = None
        self.last_good_at: float | None = None
        self.last: dict | None = None
        self.next_at = 0.0
        self.failures = 0
        self.signature = None

    def get(self, home: Path, signature) -> dict:
        now = self.clock()
        with self.lock:
            if signature != self.signature:   # credential file changed: sign-in/out
                self.signature, self.next_at, self.failures = signature, 0.0, 0
                self.last_good = self.last_good_at = None
            if now < self.next_at and self.last is not None:
                return self._view(now)
        result = self.fetch(home)
        with self.lock:
            self.last = result
            if result.get("status") == "ok":
                self.last_good, self.last_good_at, self.failures = result, now, 0
                self.next_at = now + self.interval
            elif result.get("status") in {"no_credential", "unauthorized"}:
                self.last_good = self.last_good_at = None
                self.failures = 0
                self.next_at = now + self.interval
            else:
                self.failures += 1
                self.next_at = now + min(MAX_BACKOFF, self.interval * (2 ** min(self.failures, 4)))
            return self._view(now)

    def _view(self, now: float) -> dict:
        view = dict(self.last or {})
        if view.get("status") != "ok" and self.last_good is not None:
            view = {**self.last_good, "status": "ok", "stale_error": view.get("status")}
        if self.last_good_at is not None:
            view["fetched_at"] = self.last_good_at
        return view
