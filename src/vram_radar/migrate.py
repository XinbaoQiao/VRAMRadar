"""Offline profile migration packages (QR + file).

Design (vramradar-migrate/v1):
- Default export is a *compact* profile: servers + non-default preferences only.
  Machine-local noise (navigator geometry, SSH discovery paths, session consent)
  is omitted so the QR stays sparse and readable.
- Login passwords and private-key bytes are never placed in a clear static QR
  or file. Optional passphrase seals them into an encrypted ``secrets`` block
  (PBKDF2-HMAC-SHA256 + HMAC-SHA256 keystream + HMAC integrity).
- Compact payloads encode as ``VR1:`` tokens for QR; larger ones (especially
  with sealed private keys) fall back to ``.vramradar-migrate`` files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import zlib
from pathlib import Path
from typing import Any

from .models import ConfigError, Profile, ServerProfile

FORMAT = "vramradar-migrate/v1"
TOKEN_PREFIX = "VR1:"
# Prefer sparse QR: ~v15-L at this budget. Oversized packages use the file.
QR_MAX_TOKEN_CHARS = 1200
PBKDF2_ITERS = 200_000
SEAL_VERSION = 1
MAX_PRIVATE_KEY_EXPORT_BYTES = 16 * 1024

_MACHINE_LOCAL_PROFILE_KEYS = frozenset({
    "server_config_path",
    "navigator_width",
    "navigator_height",
    "ignored_ssh_aliases",
    "pending_alias_choices",
    "resolved_alias_choice_keys",
    "usage_session_consent",
    "codex_executable",
})

_SERVER_CORE_KEYS = frozenset({
    "id",
    "display_name",
    "backend",
    "host",
    "username",
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


def _compact_server_dict(server: dict[str, Any]) -> dict[str, Any]:
    baseline = ServerProfile(
        id="x",
        display_name="x",
        backend=str(server.get("backend") or "direct_ssh"),
        host="h",
        username="u",
    ).to_dict()
    row: dict[str, Any] = {}
    for key, value in server.items():
        if key in _SERVER_CORE_KEYS:
            row[key] = value
            continue
        if key == "password" or key == "has_password":
            continue
        if key in baseline and baseline[key] == value:
            continue
        if value in ("", None, [], (), False):
            continue
        row[key] = value
    for need in ("id", "display_name", "backend"):
        if need not in row and need in server:
            row[need] = server[need]
    return row


def _compact_profile_dict(raw: dict[str, Any], *, strip_auth_refs: bool) -> tuple[dict[str, Any], int]:
    profile_id = str(raw.get("id") or "default")
    defaults = Profile.empty(profile_id).to_dict()
    compact: dict[str, Any] = {
        "schema_version": 1,
        "id": profile_id,
    }
    password_refs = 0
    for key, value in raw.items():
        if key in ("schema_version", "id", "servers"):
            continue
        if key in _MACHINE_LOCAL_PROFILE_KEYS:
            continue
        if key in defaults and defaults[key] == value:
            continue
        if value in ("", None, [], (), False) and key not in defaults:
            continue
        compact[key] = value

    servers: list[dict[str, Any]] = []
    for server in raw.get("servers", []) or []:
        if not isinstance(server, dict):
            continue
        row = dict(server)
        row.pop("password", None)
        row.pop("has_password", None)
        if row.get("auth_ref"):
            password_refs += 1
            if strip_auth_refs:
                row.pop("auth_ref", None)
        servers.append(_compact_server_dict(row))
    compact["servers"] = servers
    return compact, password_refs


def _export_profile_dict(profile: Profile, *, strip_auth_refs: bool) -> tuple[dict[str, Any], int]:
    return _compact_profile_dict(profile.to_dict(), strip_auth_refs=strip_auth_refs)


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


def collect_private_keys(profile: Profile) -> dict[str, str]:
    """Return ``sshkey:<server_id>`` → base64 private-key PEM for small local keys."""
    out: dict[str, str] = {}
    for server in profile.servers:
        path_text = str(server.identity_file or "").strip()
        if not path_text:
            continue
        try:
            path = Path(path_text).expanduser()
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size <= 0 or size > MAX_PRIVATE_KEY_EXPORT_BYTES:
                continue
            raw = path.read_bytes()
        except Exception:
            continue
        if b"PRIVATE KEY" not in raw:
            continue
        out[f"sshkey:{server.id}"] = base64.b64encode(raw).decode("ascii")
    return out


def split_sealed_payload(secrets: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    passwords = {k: v for k, v in secrets.items() if not k.startswith("sshkey:")}
    keys = {k: v for k, v in secrets.items() if k.startswith("sshkey:")}
    return passwords, keys


def build_bundle(
    profile: Profile,
    *,
    secret_store: Any | None = None,
    passphrase: str | None = None,
    include_secrets: bool = False,
    include_private_keys: bool = False,
    created_at: float | None = None,
) -> dict[str, Any]:
    include = bool(include_secrets)
    include_keys = bool(include_private_keys)
    if include_keys and not include:
        raise ConfigError("导出私钥前请先勾选含登录密码")
    if (include or include_keys) and not passphrase:
        raise ConfigError("导出密码时必须设置迁移口令")
    sealed = None
    exported, password_refs = _export_profile_dict(profile, strip_auth_refs=not include)
    secret_payload: dict[str, str] = {}
    if include:
        secret_payload.update(collect_secrets(profile, secret_store))
    if include_keys:
        keys = collect_private_keys(profile)
        if not keys:
            raise ConfigError("当前配置没有可读的 SSH 私钥可导出")
        secret_payload.update(keys)
    if include and not secret_payload:
        raise ConfigError("当前配置没有已保存的登录密码或私钥可导出")
    if secret_payload:
        sealed = seal_secrets(passphrase or "", secret_payload)
        password_refs = len([k for k in secret_payload if not k.startswith("sshkey:")]) or password_refs
    notes = [
        "默认不含登录密码；勾选后用口令加密写入。",
        "私钥默认只保留路径；勾选含私钥后才会加密打包。",
        "额度监控的本机登录态不会迁移，到新电脑后需重新授权。",
    ]
    return {
        "format": FORMAT,
        "created_at": int(created_at if created_at is not None else time.time()),
        "includes_secrets": bool(sealed),
        "includes_private_keys": bool(include_keys and sealed),
        "password_count": len([k for k in (secret_payload or {}) if not k.startswith("sshkey:")]) if sealed else 0,
        "password_slots": password_refs,
        "private_key_count": len([k for k in (secret_payload or {}) if k.startswith("sshkey:")]) if sealed else 0,
        "notes": notes,
        "profile": exported,
        "secrets": sealed,
    }


def bundle_to_token(bundle: dict[str, Any]) -> str:
    slim = {
        key: value
        for key, value in bundle.items()
        if key != "notes" and value is not None
    }
    if not slim.get("includes_secrets"):
        slim.pop("secrets", None)
    if not slim.get("includes_private_keys"):
        slim.pop("includes_private_keys", None)
        slim.pop("private_key_count", None)
    raw = json.dumps(slim, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
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
        "includes_private_keys": bool(bundle.get("includes_private_keys")),
        "password_count": int(bundle.get("password_count") or 0),
        "password_slots": int(bundle.get("password_slots") or 0),
        "private_key_count": int(bundle.get("private_key_count") or 0),
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

    passwords, _keys = split_sealed_payload(secrets)
    new_secrets: dict[str, str] = {}
    new_servers = []
    for server in profile.servers:
        candidates = [
            server.auth_ref,
            f"server:{target_profile_id}:{server.id}:login-password",
            f"server:{profile.id}:{server.id}:login-password",
            server.id,
        ]
        password = next((passwords[key] for key in candidates if key and key in passwords), None)
        if password:
            new_ref = f"server:{target_profile_id}:{server.id}:login-password"
            new_secrets[new_ref] = password
            new_servers.append(replace(server, auth_ref=new_ref))
        else:
            new_servers.append(replace(server, auth_ref=""))
    return replace(profile, servers=tuple(new_servers)), new_secrets


def restore_private_keys(
    profile: Profile,
    secrets: dict[str, str],
    *,
    keys_root: Path,
) -> tuple[Profile, list[str]]:
    """Write sealed private keys under keys_root and retarget identity_file."""
    from dataclasses import replace

    _passwords, key_map = split_sealed_payload(secrets)
    if not key_map:
        return profile, []
    keys_root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    new_servers = []
    for server in profile.servers:
        blob_b64 = key_map.get(f"sshkey:{server.id}")
        if not blob_b64:
            new_servers.append(server)
            continue
        try:
            raw = base64.b64decode(blob_b64.encode("ascii"))
        except Exception:
            warnings.append(f"服务器 {server.display_name or server.id} 的私钥无法解码")
            new_servers.append(server)
            continue
        if b"PRIVATE KEY" not in raw or len(raw) > MAX_PRIVATE_KEY_EXPORT_BYTES:
            warnings.append(f"服务器 {server.display_name or server.id} 的私钥无效")
            new_servers.append(server)
            continue
        dest = keys_root / f"migrate-{server.id}-ed25519"
        try:
            dest.write_bytes(raw)
            try:
                os.chmod(dest, 0o600)
            except Exception:
                pass
        except Exception:
            warnings.append(f"服务器 {server.display_name or server.id} 的私钥未能写入本机")
            new_servers.append(server)
            continue
        new_servers.append(replace(server, identity_file=str(dest)))
        warnings.append(f"已恢复私钥到 {dest}")
    return replace(profile, servers=tuple(new_servers)), warnings


def secrets_for_import(bundle: dict[str, Any], passphrase: str | None) -> dict[str, str]:
    if not bundle.get("includes_secrets"):
        return {}
    return open_secrets(passphrase or "", bundle["secrets"])
