"""Offline profile migration packages (QR + file).

Design (vramradar-migrate/v1):
- Default export carries servers and preferences only. Login passwords and
  private-key bytes are never placed in a clear static QR or file.
- Optional passphrase seals passwords from the OS credential store into an
  encrypted ``secrets`` block (PBKDF2-HMAC-SHA256 + HMAC-SHA256 keystream +
  HMAC integrity). SSH private keys stay as path strings; the key files are
  not embedded.
- Compact payloads can be encoded as ``VR1:`` tokens for QR; larger ones use
  the ``.vramradar-migrate`` file. The same package is the foundation for a
  future phone import.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import zlib
from typing import Any

from .models import ConfigError, Profile

FORMAT = "vramradar-migrate/v1"
TOKEN_PREFIX = "VR1:"
# Byte-mode QR capacity ~2953 at v40-L; keep headroom for camera + EC-M.
QR_MAX_TOKEN_CHARS = 1800
PBKDF2_ITERS = 200_000
SEAL_VERSION = 1

_EXCLUDED_PROFILE_KEYS = frozenset({
    # Machine-local discovery path; safe but noisy on a new computer.
    # Kept intentionally — user may still want it. Nothing secret here.
})


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def seal_secrets(passphrase: str, secrets: dict[str, str]) -> dict[str, Any]:
    if not isinstance(passphrase, str) or len(passphrase) < 8:
        raise ConfigError("迁移口令至少 8 个字符")
    if not secrets:
        raise ConfigError("没有可加密的登录密码")
    for ref, password in secrets.items():
        if not isinstance(ref, str) or not ref.strip():
            raise ConfigError("凭据引用无效")
        if not isinstance(password, str) or not password:
            raise ConfigError("登录密码不能为空")
    salt = os.urandom(16)
    nonce = os.urandom(16)
    material = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, PBKDF2_ITERS, dklen=64
    )
    enc_key, mac_key = material[:32], material[32:]
    plaintext = json.dumps(secrets, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, _keystream(enc_key, nonce, len(plaintext))))
    mac = hmac.new(mac_key, b"vramradar-migrate-secrets-v1" + salt + nonce + ciphertext, hashlib.sha256).digest()
    return {
        "v": SEAL_VERSION,
        "kdf": "pbkdf2-sha256",
        "iters": PBKDF2_ITERS,
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "ct": _b64e(ciphertext),
        "mac": _b64e(mac),
    }


def open_secrets(passphrase: str, sealed: dict[str, Any]) -> dict[str, str]:
    if not isinstance(passphrase, str) or not passphrase:
        raise ConfigError("请输入迁移口令")
    if not isinstance(sealed, dict):
        raise ConfigError("加密凭据块无效")
    if sealed.get("v") != SEAL_VERSION or sealed.get("kdf") != "pbkdf2-sha256":
        raise ConfigError("不支持的凭据加密格式")
    try:
        iters = int(sealed["iters"])
        salt = _b64d(str(sealed["salt"]))
        nonce = _b64d(str(sealed["nonce"]))
        ciphertext = _b64d(str(sealed["ct"]))
        mac = _b64d(str(sealed["mac"]))
    except Exception as exc:
        raise ConfigError("加密凭据块损坏") from exc
    if iters < 100_000 or iters > 5_000_000:
        raise ConfigError("凭据加密参数无效")
    material = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iters, dklen=64)
    enc_key, mac_key = material[:32], material[32:]
    expected = hmac.new(mac_key, b"vramradar-migrate-secrets-v1" + salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise ConfigError("迁移口令不正确，或文件已损坏")
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))
    try:
        raw = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ConfigError("解密后的凭据无效") from exc
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in raw.items()):
        raise ConfigError("解密后的凭据无效")
    return raw


def _export_profile_dict(profile: Profile, *, strip_auth_refs: bool) -> tuple[dict[str, Any], int]:
    raw = profile.to_dict()
    servers: list[dict[str, Any]] = []
    password_refs = 0
    for server in raw.get("servers", []):
        if not isinstance(server, dict):
            continue
        row = dict(server)
        if row.get("auth_ref"):
            password_refs += 1
            if strip_auth_refs:
                row.pop("auth_ref", None)
        # Never export live password fields if a caller stuffed one in.
        row.pop("password", None)
        row.pop("has_password", None)
        servers.append(row)
    raw["servers"] = servers
    return raw, password_refs


def collect_secrets(profile: Profile, secret_store: Any) -> dict[str, str]:
    secrets: dict[str, str] = {}
    getter = getattr(secret_store, "get", None)
    if getter is None:
        return secrets
    for server in profile.servers:
        if not server.auth_ref:
            continue
        try:
            value = getter(server.auth_ref)
        except Exception:
            value = None
        if isinstance(value, str) and value:
            secrets[server.auth_ref] = value
    return secrets


def build_bundle(
    profile: Profile,
    *,
    secret_store: Any | None = None,
    passphrase: str | None = None,
    include_secrets: bool = False,
    created_at: float | None = None,
) -> dict[str, Any]:
    include = bool(include_secrets)
    if include and not passphrase:
        raise ConfigError("导出密码时必须设置迁移口令")
    sealed = None
    password_refs = 0
    exported, password_refs = _export_profile_dict(profile, strip_auth_refs=not include)
    if include:
        secrets = collect_secrets(profile, secret_store)
        if not secrets:
            raise ConfigError("当前配置没有已保存的登录密码可导出")
        sealed = seal_secrets(passphrase or "", secrets)
        password_refs = len(secrets)
    notes = [
        "默认不含登录密码；密码仅在勾选并用口令加密后导出。",
        "不含 SSH 私钥内容；仅保留本机路径，需在新电脑自行放置密钥。",
        "额度监控的本机登录态不会迁移，到新电脑后需重新授权。",
    ]
    return {
        "format": FORMAT,
        "created_at": int(created_at if created_at is not None else time.time()),
        "includes_secrets": include,
        "password_count": password_refs if include else 0,
        "password_slots": password_refs,
        "notes": notes,
        "profile": exported,
        "secrets": sealed,
    }


def bundle_to_token(bundle: dict[str, Any]) -> str:
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return TOKEN_PREFIX + _b64e(zlib.compress(raw, level=9))


def token_fits_qr(token: str) -> bool:
    return len(token) <= QR_MAX_TOKEN_CHARS


def encode_bundle_text(bundle: dict[str, Any]) -> str:
    return json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"


def parse_migrate_text(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ConfigError("迁移内容为空")
    cleaned = text.strip()
    if cleaned.startswith("\ufeff"):
        cleaned = cleaned.lstrip("\ufeff")
    if cleaned.startswith(TOKEN_PREFIX):
        try:
            raw = zlib.decompress(_b64d(cleaned[len(TOKEN_PREFIX):]))
            bundle = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ConfigError("迁移码无效或已损坏") from exc
    else:
        try:
            bundle = json.loads(cleaned)
        except Exception as exc:
            raise ConfigError("迁移文件不是有效的 JSON") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ConfigError("不是 VRAM Radar 迁移包")
    profile = bundle.get("profile")
    if not isinstance(profile, dict):
        raise ConfigError("迁移包缺少配置")
    if bundle.get("includes_secrets") and not isinstance(bundle.get("secrets"), dict):
        raise ConfigError("迁移包声明含密码但缺少加密块")
    return bundle


def preview_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    profile = bundle.get("profile") if isinstance(bundle, dict) else None
    servers = profile.get("servers") if isinstance(profile, dict) else []
    server_names: list[str] = []
    if isinstance(servers, list):
        for row in servers[:12]:
            if isinstance(row, dict):
                server_names.append(str(row.get("display_name") or row.get("id") or "").strip() or "(unnamed)")
    return {
        "format": bundle.get("format"),
        "created_at": bundle.get("created_at"),
        "includes_secrets": bool(bundle.get("includes_secrets")),
        "password_count": int(bundle.get("password_count") or 0),
        "password_slots": int(bundle.get("password_slots") or 0),
        "server_count": len(servers) if isinstance(servers, list) else 0,
        "server_names": server_names,
        "display_name": str((profile or {}).get("display_name") or ""),
        "ui_language": str((profile or {}).get("ui_language") or ""),
        "notes": list(bundle.get("notes") or []),
    }


def materialize_profile(bundle: dict[str, Any], *, target_profile_id: str) -> Profile:
    raw = dict(bundle["profile"])
    raw["id"] = target_profile_id
    raw["schema_version"] = 1
    # Drop auth_refs when secrets were not included / will be rebound later.
    if not bundle.get("includes_secrets"):
        for server in raw.get("servers", []) or []:
            if isinstance(server, dict):
                server.pop("auth_ref", None)
    return Profile.from_dict(raw, expected_id=target_profile_id)



def rebind_secrets_to_profile(
    profile: Profile,
    secrets: dict[str, str],
    *,
    target_profile_id: str,
) -> tuple[Profile, dict[str, str]]:
    """Rewrite auth_ref keys to ``server:<target>:<server_id>:login-password``."""
    from dataclasses import replace

    new_secrets: dict[str, str] = {}
    new_servers = []
    for server in profile.servers:
        candidates = [
            server.auth_ref,
            f"server:{target_profile_id}:{server.id}:login-password",
            f"server:{profile.id}:{server.id}:login-password",
            server.id,
        ]
        password = next((secrets[key] for key in candidates if key and key in secrets), None)
        if password:
            new_ref = f"server:{target_profile_id}:{server.id}:login-password"
            new_secrets[new_ref] = password
            new_servers.append(replace(server, auth_ref=new_ref))
        else:
            new_servers.append(replace(server, auth_ref=""))
    return replace(profile, servers=tuple(new_servers)), new_secrets



def secrets_for_import(bundle: dict[str, Any], passphrase: str | None) -> dict[str, str]:
    if not bundle.get("includes_secrets"):
        return {}
    return open_secrets(passphrase or "", bundle["secrets"])
