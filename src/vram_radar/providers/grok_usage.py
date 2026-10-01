"""Grok Bot session-based weekly-usage reader (Windows only, opt-in).

The user explicitly authorized Radar to reuse Grok Bot's own locally stored
sign-in to call the *same* read-only endpoint the app itself calls for the
usage shown in its avatar/account menu.  This is gated on
``providers.session_consent("grok")`` and never runs otherwise.

Endpoint (from the shipped bundle, Grok Bot 0.65.0):
    POST {backend}/aiserver.v1.DashboardService/GetSandUsageStatus
    backend defaults to https://api2.cursor.sh ; Connect-RPC, here spoken as
    its JSON variant (Content-Type application/json, connect-protocol-version
    1, body ``{}``).  Response carries ``usagePercent`` (0-100) and
    ``nextResetTimestampUtc`` (an RFC3339 / {seconds,nanos} Timestamp).

Auth: ``authorization: Bearer <access token>``.  The token lives in
``%APPDATA%/Grok Bot/sand-secrets.json`` under ``cursor-accounts`` (plaintext
JSON: {active, accounts}); the active account's ``cursor-access-token`` value
is a Chromium OSCrypt ``v10`` blob (AES-256-GCM).  The AES key is the
``os_crypt.encrypted_key`` in ``Local State`` (base64, ``DPAPI`` prefix,
unwrapped with CryptUnprotectData as the current Windows user).  All crypto is
done in memory with the OS (DPAPI + CNG/BCrypt), adding no third-party
dependency.  The token and the raw response are never logged.

We only ever READ: we use the stored access token as-is and never touch the
refresh token or any refresh/login flow (which could rotate tokens or log the
app out).  An expired/invalid token surfaces as ``unauthorized`` and the
caller falls back to the on-screen menu reading.
"""
from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

LOG = logging.getLogger("vram_radar")

DEFAULT_BACKEND = "https://api2.cursor.sh"
RPC_PATH = "/aiserver.v1.DashboardService/GetSandUsageStatus"
TIMEOUT_SECONDS = 10
MIN_INTERVAL = 300          # the app refreshes on demand; we stay at/above 5 min
MAX_BACKOFF = 3600
MAX_SECRETS_BYTES = 1 * 1024 * 1024
MAX_LOCAL_STATE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 256 * 1024
V10_PREFIX = b"v10"
SCOPED_PREFIX = "scoped:v1:"
PLAINTEXT_PREFIX = "plaintext:v1:"

# Client headers the app sends (values only; no secrets).  Harmless to mirror
# and keeps the request indistinguishable from the app's own.
CLIENT_VERSION = "0.65.0"
CLIENT_HEADERS = {
    "x-cursor-client-type": "sand",
    "x-cursor-client-source": "sand-desktop",
    "x-cursor-client-version": CLIENT_VERSION,
    "x-cursor-client-os": "win32",
    "x-sand-box-namespace": "prod",
    "x-ghost-mode": "true",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never forward the auth header
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


# --------------------------------------------------------------------------
# Windows crypto (DPAPI + AES-256-GCM via CNG), in-memory only.
# --------------------------------------------------------------------------
def _dpapi_unprotect(blob: bytes) -> bytes | None:
    """CryptUnprotectData as the current user.  Returns None off Windows or on
    failure; never raises."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                           ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                                  ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes | None:
    """AES-256-GCM via Windows CNG (BCrypt).  Returns None on any failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    bcrypt = ctypes.windll.bcrypt

    def check(status):
        return status == 0

    class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG), ("dwInfoVersion", wintypes.ULONG),
            ("pbNonce", ctypes.c_void_p), ("cbNonce", wintypes.ULONG),
            ("pbAuthData", ctypes.c_void_p), ("cbAuthData", wintypes.ULONG),
            ("pbTag", ctypes.c_void_p), ("cbTag", wintypes.ULONG),
            ("pbMacContext", ctypes.c_void_p), ("cbMacContext", wintypes.ULONG),
            ("cbAAD", wintypes.ULONG), ("cbData", ctypes.c_ulonglong),
            ("dwFlags", wintypes.ULONG),
        ]

    alg = ctypes.c_void_p()
    if not check(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg),
                 ctypes.c_wchar_p("AES"), None, 0)):
        return None
    hkey = ctypes.c_void_p()
    try:
        chain = ctypes.create_unicode_buffer("ChainingModeGCM")
        if not check(bcrypt.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"),
                     ctypes.cast(chain, ctypes.c_void_p),
                     (len(chain.value) + 1) * ctypes.sizeof(ctypes.c_wchar), 0)):
            return None
        keybuf = ctypes.create_string_buffer(key, len(key))
        if not check(bcrypt.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0,
                     ctypes.cast(keybuf, ctypes.c_void_p), len(key), 0)):
            return None
        info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
        ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
        info.cbSize = ctypes.sizeof(info)
        info.dwInfoVersion = 1
        nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
        tag_buf = ctypes.create_string_buffer(tag, len(tag))
        info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
        info.cbNonce = len(nonce)
        info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
        info.cbTag = len(tag)
        ct_buf = ctypes.create_string_buffer(ciphertext, len(ciphertext))
        out = ctypes.create_string_buffer(len(ciphertext))
        done = wintypes.ULONG(0)
        status = bcrypt.BCryptDecrypt(hkey, ctypes.cast(ct_buf, ctypes.c_void_p), len(ciphertext),
                                      ctypes.byref(info), None, 0,
                                      ctypes.cast(out, ctypes.c_void_p), len(ciphertext),
                                      ctypes.byref(done), 0)
        if not check(status):
            return None
        return out.raw[:done.value]
    finally:
        if hkey:
            bcrypt.BCryptDestroyKey(hkey)
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def read_os_crypt_key(userdata: Path) -> bytes | None:
    """DPAPI-unwrapped AES key from ``Local State`` (or None)."""
    path = userdata / "Local State"
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_LOCAL_STATE_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_LOCAL_STATE_BYTES:
        return None
    try:
        document = json.loads(data.decode("utf-8", "replace"))
        encoded = document["os_crypt"]["encrypted_key"]
    except (ValueError, KeyError, TypeError):
        return None
    try:
        raw = base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None
    if not raw.startswith(b"DPAPI"):
        return None
    return _dpapi_unprotect(raw[len("DPAPI"):])


def _decrypt_v10(key: bytes, stored: str) -> str | None:
    """Decrypt one OSCrypt ``v10`` value; tolerates the ``scoped:``/``plaintext:``
    envelopes the app also understands.  The clear value stays local."""
    if not isinstance(stored, str) or not stored:
        return None
    if stored.startswith(PLAINTEXT_PREFIX):
        try:
            return base64.b64decode(stored[len(PLAINTEXT_PREFIX):]).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return None
    if stored.startswith(SCOPED_PREFIX):
        rest = stored[len(SCOPED_PREFIX):]
        _, sep, cipher = rest.partition(":")
        if not sep:
            return None
        stored = cipher
    if key is None:
        return None
    try:
        blob = base64.b64decode(stored)
    except (ValueError, TypeError):
        return None
    if not blob.startswith(V10_PREFIX) or len(blob) < 3 + 12 + 16:
        return None
    nonce = blob[3:15]
    tag = blob[-16:]
    ciphertext = blob[15:-16]
    plain = _aes_gcm_decrypt(key, nonce, ciphertext, tag)
    if plain is None:
        return None
    return plain.decode("utf-8", "replace")


def read_access_token(userdata: Path) -> str | None:
    """Active account's Grok Bot access token, decrypted in memory, or None.

    Reads only the access token; the refresh token is never touched."""
    path = userdata / "sand-secrets.json"
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_SECRETS_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_SECRETS_BYTES:
        return None
    try:
        secrets = json.loads(data.decode("utf-8", "replace"))
        accounts_raw = secrets["cursor-accounts"]
    except (ValueError, KeyError, TypeError):
        return None
    # ``cursor-accounts`` is itself a plaintext/scoped envelope around JSON.
    key = read_os_crypt_key(userdata)
    document = accounts_raw
    if isinstance(accounts_raw, str):
        if accounts_raw.lstrip().startswith("{"):
            document = accounts_raw
        else:
            document = _decrypt_v10(key, accounts_raw)
        try:
            document = json.loads(document) if document else None
        except ValueError:
            document = None
    if not isinstance(document, dict):
        return None
    active = document.get("active")
    accounts = document.get("accounts")
    if not isinstance(accounts, dict):
        return None
    account = accounts.get(active) if isinstance(active, str) else None
    if not isinstance(account, dict):
        return None
    stored = account.get("cursor-access-token")
    return _decrypt_v10(key, stored) if isinstance(stored, str) else None


# --------------------------------------------------------------------------
# Response parsing.
# --------------------------------------------------------------------------
def _reset_ms(value) -> float | None:
    """A protobuf Timestamp as Connect-JSON (RFC3339 string) or {seconds,nanos}."""
    if isinstance(value, str) and value:
        text = value.strip().replace("Z", "+00:00")
        try:
            import datetime
            return datetime.datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    if isinstance(value, dict):
        seconds = value.get("seconds")
        try:
            return float(seconds) if seconds is not None else None
        except (TypeError, ValueError):
            return None
    return None


def parse_usage(payload) -> dict | None:
    """Pull ``used_percent`` (0-100) and ``reset_at`` (epoch s) out of a
    GetSandUsageStatus response.  Accepts camelCase or snake_case."""
    if not isinstance(payload, dict):
        return None
    used = payload.get("usagePercent", payload.get("usage_percent"))
    try:
        used = float(used) if used is not None else None
    except (TypeError, ValueError):
        used = None
    if used is None or not 0 <= used <= 100:
        return None
    reset = _reset_ms(payload.get("nextResetTimestampUtc", payload.get("next_reset_timestamp_utc")))
    return {"status": "ok", "used_percent": used, "reset_at": reset}


# --------------------------------------------------------------------------
# HTTP (read-only, no refresh).
# --------------------------------------------------------------------------
def _post_usage(backend: str, token: str) -> dict:
    url = backend.rstrip("/") + RPC_PATH
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "connect-protocol-version": "1",
        "accept": "application/json",
        "user-agent": (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       f"(KHTML, like Gecko) Sand/{CLIENT_VERSION} Safari/537.36"),
        **CLIENT_HEADERS,
    }
    request = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                return {"status": "error"}
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                return {"status": "error"}
            parsed = parse_usage(payload)
            return parsed or {"status": "error"}
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return {"status": "unauthorized"}
        return {"status": "error"}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return {"status": "error"}


def fetch_usage(userdata: Path, backend: str = DEFAULT_BACKEND) -> dict:
    """Read token (in memory) and query usage.  Never logs token or body.

    Returns {"status": "ok"|"no_credential"|"unauthorized"|"error", ...}.
    """
    token = (read_access_token(userdata))
    if not token:
        return {"status": "no_credential"}
    try:
        return _post_usage(backend, token)
    finally:
        del token


def _signature(userdata: Path) -> tuple:
    """Cheap change-detector for the stored credential (never its value)."""
    try:
        stat = (userdata / "sand-secrets.json").stat()
        return (int(stat.st_mtime), stat.st_size)
    except OSError:
        return (0, 0)


class UsageCache:
    """At most one query per ``MIN_INTERVAL`` seconds, exponential backoff on
    error, serving the last good reading meanwhile."""

    def __init__(self, fetch=fetch_usage, *, interval: float = MIN_INTERVAL, clock=time.time,
                 signature=None):
        self.fetch, self.interval, self.clock = fetch, interval, clock
        self.signature_of = signature  # None: grok's sand-secrets.json (looked up per call)
        self.lock = threading.Lock()
        self.last: dict | None = None
        self.last_good: dict | None = None
        self.last_good_at: float | None = None
        self.next_at = 0.0
        self.failures = 0
        self.signature = None

    def get(self, userdata: Path, backend: str = DEFAULT_BACKEND) -> dict:
        now = self.clock()
        signature = (self.signature_of or _signature)(userdata)
        with self.lock:
            if signature != self.signature:   # sign-in/out or token rotation
                self.signature, self.next_at, self.failures = signature, 0.0, 0
                self.last_good = self.last_good_at = None
            if now < self.next_at and self.last is not None:
                return self._view(now)
        result = self.fetch(userdata, backend)
        with self.lock:
            self.last = result
            status = result.get("status")
            if status == "ok":
                self.last_good, self.last_good_at, self.failures = result, now, 0
                self.next_at = now + self.interval
            elif status in {"no_credential", "unauthorized"}:
                self.last_good = self.last_good_at = None
                self.failures = 0
                self.next_at = now + self.interval
            else:
                self.failures += 1
                self.next_at = now + min(MAX_BACKOFF, self.interval * (2 ** min(self.failures, 4)))
            return self._view(now)

    def reset(self) -> None:
        """Next ``get`` queries at once (bypasses interval and backoff)."""
        with self.lock:
            self.next_at, self.failures = 0.0, 0

    def _view(self, now: float) -> dict:
        view = dict(self.last or {"status": "error"})
        if view.get("status") not in {"ok", "no_credential", "unauthorized"} and self.last_good is not None:
            view = {**self.last_good, "status": "ok", "stale_error": self.last.get("status")}
        if self.last_good_at is not None and view.get("status") == "ok":
            view["fetched_at"] = self.last_good_at
        return view
