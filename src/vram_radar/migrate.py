"""Offline profile migration packages (QR + file + optional LAN handoff).

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
import socket
import threading
import time
import uuid
import zlib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import ConfigError, Profile, ServerProfile

FORMAT = "vramradar-migrate/v1"
TOKEN_PREFIX = "VR1:"
# Prefer sparse QR: ~v15-L at this budget. Oversized packages use the file.
QR_MAX_TOKEN_CHARS = 1200
PBKDF2_ITERS = 200_000
SEAL_VERSION = 1
MAX_PRIVATE_KEY_EXPORT_BYTES = 16 * 1024
DEFAULT_SEAL_TTL_SECONDS = 30 * 60
MIN_SEAL_TTL_SECONDS = 5 * 60
MAX_SEAL_TTL_SECONDS = 24 * 60 * 60
DEFAULT_LAN_TTL_SECONDS = 10 * 60
MIN_LAN_TTL_SECONDS = 60
MAX_LAN_TTL_SECONDS = 30 * 60

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
        raise ConfigError("保护密码至少 8 位")
    if not secrets:
        raise ConfigError("没有可导出的登录密码")
    for ref, password in secrets.items():
        if not isinstance(ref, str) or not ref.strip():
            raise ConfigError("密码信息无效")
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
        raise ConfigError("请输入保护密码")
    if not isinstance(sealed, dict):
        raise ConfigError("加密内容无效")
    if sealed.get("v") != SEAL_VERSION or sealed.get("kdf") != "pbkdf2-sha256":
        raise ConfigError("不支持的加密格式")
    try:
        iters = int(sealed["iters"])
        salt = _b64d(str(sealed["salt"]))
        nonce = _b64d(str(sealed["nonce"]))
        ciphertext = _b64d(str(sealed["ct"]))
        mac = _b64d(str(sealed["mac"]))
    except Exception as exc:
        raise ConfigError("加密内容已损坏") from exc
    if iters < 100_000 or iters > 5_000_000:
        raise ConfigError("加密参数无效")
    material = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iters, dklen=64)
    enc_key, mac_key = material[:32], material[32:]
    expected = hmac.new(mac_key, b"vramradar-migrate-secrets-v1" + salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise ConfigError("保护密码不正确，或文件已损坏")
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))
    try:
        raw = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ConfigError("解密后的密码无效") from exc
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in raw.items()):
        raise ConfigError("解密后的密码无效")
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
        if key == "password" or key == "has_pw":
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


def _compact_profile_dict(
    raw: dict[str, Any],
    *,
    strip_auth_refs: bool,
    server_ids: set[str] | None = None,
    include_preferences: bool = True,
) -> tuple[dict[str, Any], int]:
    profile_id = str(raw.get("id") or "default")
    defaults = Profile.empty(profile_id).to_dict()
    compact: dict[str, Any] = {
        "schema_version": 1,
        "id": profile_id,
    }
    password_refs = 0
    if include_preferences:
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
    else:
        name = str(raw.get("display_name") or "").strip()
        if name and name != defaults.get("display_name"):
            compact["display_name"] = name

    servers: list[dict[str, Any]] = []
    for server in _filter_servers(list(raw.get("servers", []) or []), server_ids=server_ids):
        row = dict(server)
        row.pop("password", None)
        row.pop("has_pw", None)
        if row.get("auth_ref"):
            password_refs += 1
            if strip_auth_refs:
                row.pop("auth_ref", None)
        servers.append(_compact_server_dict(row))
    compact["servers"] = servers
    return compact, password_refs


def _export_profile_dict(
    profile: Profile,
    *,
    strip_auth_refs: bool,
    server_ids: set[str] | None = None,
    include_preferences: bool = True,
) -> tuple[dict[str, Any], int]:
    return _compact_profile_dict(
        profile.to_dict(),
        strip_auth_refs=strip_auth_refs,
        server_ids=server_ids,
        include_preferences=include_preferences,
    )


def collect_secrets(
    profile: Profile,
    secret_store: Any,
    *,
    server_ids: set[str] | None = None,
) -> dict[str, str]:
    secrets: dict[str, str] = {}
    getter = getattr(secret_store, "get", None)
    if getter is None:
        return secrets
    for server in profile.servers:
        if server_ids is not None and server.id not in server_ids:
            continue
        if not server.auth_ref:
            continue
        try:
            value = getter(server.auth_ref)
        except Exception:
            value = None
        if isinstance(value, str) and value:
            secrets[server.auth_ref] = value
    return secrets


def collect_private_keys(
    profile: Profile,
    *,
    server_ids: set[str] | None = None,
) -> dict[str, str]:
    """Return ``sshkey:<server_id>`` → base64 private-key PEM for small local keys."""
    out: dict[str, str] = {}
    for server in profile.servers:
        if server_ids is not None and server.id not in server_ids:
            continue
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
    server_ids: list[str] | tuple[str, ...] | None = None,
    include_preferences: bool = True,
    ttl_seconds: int | None = None,
    package_id: str | None = None,
) -> dict[str, Any]:
    include = bool(include_secrets)
    include_keys = bool(include_private_keys)
    include_prefs = bool(include_preferences)
    selected_ids = _normalize_server_ids(server_ids)
    if selected_ids is not None and not selected_ids:
        raise ConfigError("请至少选择一台服务器")
    if include_keys and not include:
        raise ConfigError("导出私钥前请先勾选「含登录密码」")
    if (include or include_keys) and not passphrase:
        raise ConfigError("导出密码时请设置保护密码")
    sealed = None
    exported, password_refs = _export_profile_dict(
        profile,
        strip_auth_refs=not include,
        server_ids=selected_ids,
        include_preferences=include_prefs,
    )
    if selected_ids is not None and not exported["servers"]:
        raise ConfigError("所选服务器不存在")
    secret_payload: dict[str, str] = {}
    if include:
        secret_payload.update(collect_secrets(profile, secret_store, server_ids=selected_ids))
    if include_keys:
        keys = collect_private_keys(profile, server_ids=selected_ids)
        if not keys:
            raise ConfigError("当前配置没有可读取的 SSH 私钥可导出")
        secret_payload.update(keys)
    if include and not secret_payload:
        raise ConfigError("没有已保存的登录密码或私钥可导出")
    created = int(created_at if created_at is not None else time.time())
    expires_at = None
    pkg_id = None
    if secret_payload:
        sealed = seal_secrets(passphrase or "", secret_payload)
        password_refs = len([k for k in secret_payload if not k.startswith("sshkey:")]) or password_refs
        ttl = _clamp_ttl(
            ttl_seconds,
            default=DEFAULT_SEAL_TTL_SECONDS,
            minimum=MIN_SEAL_TTL_SECONDS,
            maximum=MAX_SEAL_TTL_SECONDS,
        )
        expires_at = created + ttl
        pkg_id = str(package_id or uuid.uuid4())
    notes = [
        "默认不含登录密码；可选保护密码加密后写入。",
        "私钥默认只保留路径；含私钥时体积变大。",
        "额度监测的本地登录态不会迁移；到新电脑后需重新授权。",
    ]
    if expires_at is not None:
        notes.append("含密码的迁移码有时限，请尽快在受信任设备上导入，勿长期留存。")
    if not include_prefs:
        notes.append("本次仅导出服务器，未偏好设置。")
    return {
        "format": FORMAT,
        "created_at": created,
        "expires_at": expires_at,
        "package_id": pkg_id,
        "includes_secrets": bool(sealed),
        "includes_private_keys": bool(include_keys and sealed),
        "includes_preferences": include_prefs,
        "selected_server_ids": sorted(selected_ids) if selected_ids is not None else None,
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
        slim.pop("expires_at", None)
        slim.pop("package_id", None)
    if not slim.get("includes_private_keys"):
        slim.pop("includes_private_keys", None)
        slim.pop("private_key_count", None)
    if slim.get("includes_preferences", True):
        slim.pop("includes_preferences", None)
    if slim.get("selected_server_ids") is None:
        slim.pop("selected_server_ids", None)
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
            raise ConfigError("迁移文件格式无效") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ConfigError("不是 VRAM Radar 的迁移文件")
    profile = bundle.get("profile")
    if not isinstance(profile, dict):
        raise ConfigError("迁移文件缺少配置")
    if bundle.get("includes_secrets") and not isinstance(bundle.get("secrets"), dict):
        raise ConfigError("迁移文件声称含密码，但缺少加密内容")
    return bundle



def preview_bundle(bundle: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    profile = bundle.get("profile") if isinstance(bundle, dict) else None
    servers = profile.get("servers") if isinstance(profile, dict) else []
    server_names: list[str] = []
    server_rows: list[dict[str, Any]] = []
    if isinstance(servers, list):
        for row in servers:
            if not isinstance(row, dict):
                continue
            name = str(row.get("display_name") or row.get("id") or "").strip() or "(unnamed)"
            sid = str(row.get("id") or "").strip()
            server_rows.append({
                "id": sid,
                "display_name": name,
                "host": str(row.get("host") or ""),
                "has_auth_ref": bool(row.get("auth_ref")),
                "has_identity_file": bool(str(row.get("identity_file") or "").strip()),
            })
            if len(server_names) < 12:
                server_names.append(name)
    expires_at = bundle.get("expires_at")
    current = float(now if now is not None else time.time())
    expired = False
    expires_in_seconds = None
    if expires_at is not None:
        try:
            expiry = float(expires_at)
            expires_in_seconds = int(expiry - current)
            expired = expires_in_seconds < 0
        except Exception:
            expires_in_seconds = None
    return {
        "format": bundle.get("format"),
        "created_at": bundle.get("created_at"),
        "expires_at": expires_at,
        "expires_in_seconds": expires_in_seconds,
        "expired": expired,
        "package_id": bundle.get("package_id"),
        "includes_secrets": bool(bundle.get("includes_secrets")),
        "includes_private_keys": bool(bundle.get("includes_private_keys")),
        "includes_preferences": bool(bundle.get("includes_preferences", True)),
        "selected_server_ids": list(bundle.get("selected_server_ids") or []) or None,
        "password_count": int(bundle.get("password_count") or 0),
        "password_slots": int(bundle.get("password_slots") or 0),
        "private_key_count": int(bundle.get("private_key_count") or 0),
        "server_count": len(servers) if isinstance(servers, list) else 0,
        "server_names": server_names,
        "servers": server_rows,
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


def _filter_servers(
    servers: list[Any],
    *,
    server_ids: set[str] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for server in servers or []:
        if not isinstance(server, dict):
            continue
        sid = str(server.get("id") or "").strip()
        if server_ids is not None and sid not in server_ids:
            continue
        out.append(server)
    return out


def _normalize_server_ids(server_ids: list[str] | tuple[str, ...] | None) -> set[str] | None:
    if server_ids is None:
        return None
    cleaned = {str(item).strip() for item in server_ids if str(item).strip()}
    return cleaned


def _clamp_ttl(ttl_seconds: int | None, *, default: int, minimum: int, maximum: int) -> int:
    if ttl_seconds is None:
        return default
    try:
        value = int(ttl_seconds)
    except Exception as exc:
        raise ConfigError("有效期无效") from exc
    if value < minimum or value > maximum:
        raise ConfigError("有效期超出允许范围")
    return value


def assert_bundle_usable(
    bundle: dict[str, Any],
    *,
    now: float | None = None,
    consumed_ids: set[str] | frozenset[str] | None = None,
) -> None:
    """Reject expired or consumed sealed packages."""
    if not bundle.get("includes_secrets"):
        return
    current = float(now if now is not None else time.time())
    expires_at = bundle.get("expires_at")
    if expires_at is not None:
        try:
            expiry = float(expires_at)
        except Exception as exc:
            raise ConfigError("迁移文件有效期无效") from exc
        if current > expiry:
            raise ConfigError("含密码的迁移码已过期，请在原电脑重新生成")
    package_id = str(bundle.get("package_id") or "").strip()
    if package_id and consumed_ids and package_id in consumed_ids:
        raise ConfigError("此迁移码已在本机用过，请重新生成")


def merge_profiles(
    current: Profile,
    incoming: Profile,
    *,
    apply_preferences: bool = True,
) -> Profile:
    """Merge servers by id (incoming wins). Optionally apply incoming preferences."""
    by_id = {server.id: server for server in current.servers}
    for server in incoming.servers:
        by_id[server.id] = server
    merged_servers = tuple(by_id.values())
    if apply_preferences:
        base = replace(incoming, id=current.id, servers=merged_servers)
        return replace(
            base,
            server_config_path=current.server_config_path,
            navigator_width=current.navigator_width,
            navigator_height=current.navigator_height,
            ignored_ssh_aliases=current.ignored_ssh_aliases,
            pending_alias_choices=current.pending_alias_choices,
            resolved_alias_choice_keys=current.resolved_alias_choice_keys,
            usage_session_consent=current.usage_session_consent,
            codex_executable=current.codex_executable,
        )
    return replace(current, servers=merged_servers)


def post_import_check(profile: Profile, secret_store: Any) -> dict[str, Any]:
    """Mark servers missing passwords or private-key files after import."""
    getter = getattr(secret_store, "get", None)
    items: list[dict[str, Any]] = []
    missing_pw = 0
    missing_key = 0
    for server in profile.servers:
        has_pw = False
        if server.auth_ref and getter is not None:
            try:
                value = getter(server.auth_ref)
            except Exception:
                value = None
            has_pw = isinstance(value, str) and bool(value)
        identity = str(server.identity_file or "").strip()
        key_ok = False
        key_missing = False
        if identity:
            try:
                key_ok = Path(identity).expanduser().is_file()
            except Exception:
                key_ok = False
            key_missing = not key_ok
        needs_pw = bool(server.auth_ref) and not has_pw
        if needs_pw:
            missing_pw += 1
        if key_missing:
            missing_key += 1
        items.append({
            "id": server.id,
            "display_name": server.display_name or server.id,
            "host": server.host,
            "has_pw": has_pw,
            "auth_ref": server.auth_ref or "",
            "identity_file": identity,
            "identity_file_ok": key_ok if identity else None,
            "missing_pw": needs_pw,
            "missing_key": key_missing,
            "ok": not needs_pw and not key_missing,
        })
    return {
        "server_count": len(items),
        "missing_pw_count": missing_pw,
        "missing_key_count": missing_key,
        "ok_count": sum(1 for row in items if row["ok"]),
        "servers": items,
    }


def list_export_targets(profile: Profile) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for server in profile.servers:
        rows.append({
            "id": server.id,
            "display_name": server.display_name or server.id,
            "host": server.host,
            "username": server.username,
            "has_auth_ref": bool(server.auth_ref),
            "has_identity_file": bool(str(server.identity_file or "").strip()),
        })
    return rows


def guess_lan_ipv4() -> str:
    """Best-effort LAN IPv4 for same-network handoff URLs."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


class LanMigrateTransfer:
    """One-shot / short-lived HTTP handoff on the local network."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._token = ""
        self._payload = ""
        self._expires_at = 0.0
        self._downloads_left = 0
        self._host = ""
        self._port = 0
        self._started_at = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._httpd is not None and self._downloads_left > 0 and time.time() <= self._expires_at
            return {
                "active": active,
                "url": self.url() if active else "",
                "token": self._token if active else "",
                "host": self._host if active else "",
                "port": self._port if active else 0,
                "expires_at": int(self._expires_at) if active else None,
                "expires_in_seconds": max(0, int(self._expires_at - time.time())) if active else 0,
                "downloads_left": self._downloads_left if active else 0,
            }

    def url(self) -> str:
        if not self._host or not self._port or not self._token:
            return ""
        return f"http://{self._host}:{self._port}/m/{self._token}"

    def stop(self) -> None:
        with self._lock:
            httpd = self._httpd
            self._httpd = None
            self._token = ""
            self._payload = ""
            self._downloads_left = 0
            self._expires_at = 0.0
            self._host = ""
            self._port = 0
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass

    def start(
        self,
        payload_text: str,
        *,
        ttl_seconds: int | None = None,
        max_downloads: int = 1,
    ) -> dict[str, Any]:
        if not isinstance(payload_text, str) or not payload_text.strip():
            raise ConfigError("没有可传送的迁移内容")
        ttl = _clamp_ttl(
            ttl_seconds,
            default=DEFAULT_LAN_TTL_SECONDS,
            minimum=MIN_LAN_TTL_SECONDS,
            maximum=MAX_LAN_TTL_SECONDS,
        )
        downloads = max(1, min(int(max_downloads or 1), 3))
        self.stop()
        token = _b64e(os.urandom(18))
        host = guess_lan_ipv4()
        transfer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or ""
                with transfer._lock:
                    alive = (
                        transfer._httpd is not None
                        and transfer._downloads_left > 0
                        and time.time() <= transfer._expires_at
                        and path == f"/m/{transfer._token}"
                    )
                    body = transfer._payload if alive else ""
                    if alive:
                        transfer._downloads_left -= 1
                        remaining = transfer._downloads_left
                    else:
                        remaining = 0
                if not alive or not body:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"not found or expired\n")
                    if remaining <= 0:
                        threading.Thread(target=transfer.stop, daemon=True).start()
                    return
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-VRAMRadar-Migrate", "1")
                self.end_headers()
                self.wfile.write(data)
                if remaining <= 0:
                    threading.Thread(target=transfer.stop, daemon=True).start()

        httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        port = int(httpd.server_address[1])
        with self._lock:
            self._httpd = httpd
            self._token = token
            self._payload = payload_text
            self._expires_at = time.time() + ttl
            self._downloads_left = downloads
            self._host = host
            self._port = port
            self._started_at = time.time()
        thread = threading.Thread(target=httpd.serve_forever, name="vramradar-lan-migrate", daemon=True)
        self._thread = thread
        thread.start()

        def _auto_stop() -> None:
            time.sleep(ttl + 1)
            status = self.status()
            if status.get("active"):
                self.stop()

        threading.Thread(target=_auto_stop, daemon=True).start()
        return self.status()

    def fetch(self, url: str, *, timeout: float = 8.0) -> str:
        """Pull a LAN migrate payload from another computer on the same network."""
        import urllib.error
        import urllib.request

        cleaned = str(url or "").strip()
        if not cleaned:
            raise ConfigError("请输入传送链接")
        parsed = urlparse(cleaned)
        if parsed.scheme != "http":
            raise ConfigError("同一 Wi‑Fi 传送仅支持 http 链接")
        if not parsed.path.startswith("/m/"):
            raise ConfigError("传送链接无效")
        try:
            with urllib.request.urlopen(cleaned, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ConfigError("传送链接已失效或已取走") from exc
            raise ConfigError("无法从同一 Wi‑Fi 获取迁移内容") from exc
        except Exception as exc:
            raise ConfigError("无法连接，请确认同一 Wi‑Fi，且对方仍在传送") from exc
        text = raw.decode("utf-8", errors="strict")
        parse_migrate_text(text)
        return text


_LAN_TRANSFER = LanMigrateTransfer()


def get_lan_transfer() -> LanMigrateTransfer:
    return _LAN_TRANSFER

