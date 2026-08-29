from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import getpass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile


MAX_PUBLIC_KEY_BYTES = 16 * 1024
MAX_PRIVATE_KEY_BYTES = 1024 * 1024
MAX_KEY_PATH_BYTES = 4096
PUBLIC_KEY_TYPE_RE = re.compile(
    r"^(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp(?:256|384|521)|"
    r"sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)$"
)


class SshKeySetupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedSshKey:
    private_path: Path
    public_path: Path | None
    public_line: str
    generated: bool


def _bounded_path(raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise SshKeySetupError("key_path_required", f"请选择{label}")
    value = raw.strip()
    if len(value.encode("utf-8")) > MAX_KEY_PATH_BYTES or any(
        character in value for character in ("\x00", "\r", "\n")
    ):
        raise SshKeySetupError("key_path_invalid", f"{label}路径无效")
    try:
        return Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SshKeySetupError("key_not_found", f"找不到{label}") from exc


def normalize_public_key(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SshKeySetupError("public_key_invalid", "SSH 公钥不是有效的 UTF-8 文本") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise SshKeySetupError("public_key_invalid", "SSH 公钥格式无效")
    if len(text.encode("utf-8")) > MAX_PUBLIC_KEY_BYTES:
        raise SshKeySetupError("public_key_too_large", "SSH 公钥超过安全长度限制")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or any(ord(character) < 32 for character in lines[0]):
        raise SshKeySetupError("public_key_invalid", "SSH 公钥必须是单行文本")
    fields = lines[0].split()
    if len(fields) < 2 or not PUBLIC_KEY_TYPE_RE.fullmatch(fields[0]):
        raise SshKeySetupError("public_key_invalid", "SSH 公钥类型不受支持或格式无效")
    try:
        decoded = base64.b64decode(fields[1].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise SshKeySetupError("public_key_invalid", "SSH 公钥内容不是有效的 Base64") from exc
    if len(decoded) < 32:
        raise SshKeySetupError("public_key_invalid", "SSH 公钥内容过短")
    try:
        embedded_length = struct.unpack(">I", decoded[:4])[0]
        embedded_type = decoded[4 : 4 + embedded_length].decode("ascii", errors="strict")
    except (struct.error, UnicodeDecodeError) as exc:
        raise SshKeySetupError("public_key_invalid", "SSH 公钥二进制结构无效") from exc
    if embedded_length <= 0 or 4 + embedded_length > len(decoded) or embedded_type != fields[0]:
        raise SshKeySetupError("public_key_invalid", "SSH 公钥类型与内容不一致")
    # Comments are deliberately omitted. The remote installer receives only
    # the key type and public blob, never a path, username, or private material.
    return f"{fields[0]} {fields[1]}"


def _read_public_key(path: Path) -> str:
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        with path.open("rb") as handle:
            content = handle.read(MAX_PUBLIC_KEY_BYTES + 1)
    except OSError as exc:
        raise SshKeySetupError("public_key_unreadable", "无法读取 SSH 公钥文件") from exc
    if len(content) > MAX_PUBLIC_KEY_BYTES:
        raise SshKeySetupError("public_key_too_large", "SSH 公钥文件超过安全长度限制")
    return normalize_public_key(content)


def _ssh_keygen() -> str:
    executable = shutil.which("ssh-keygen")
    if executable:
        return executable
    raise SshKeySetupError(
        "ssh_keygen_missing",
        "本机未找到 ssh-keygen；请先安装或启用系统 OpenSSH 客户端",
    )


def _run_keygen(argv: list[str], *, timeout: float = 20) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            close_fds=os.name != "nt",
            check=False,
        )
    except FileNotFoundError as exc:
        raise SshKeySetupError("ssh_keygen_missing", "本机未找到 ssh-keygen") from exc
    except subprocess.TimeoutExpired as exc:
        raise SshKeySetupError("ssh_keygen_timeout", "本地 SSH Key 操作超时") from exc
    except OSError as exc:
        raise SshKeySetupError("ssh_keygen_failed", "无法启动本机 ssh-keygen") from exc


def _derive_public_key(private_path: Path) -> str | None:
    result = _run_keygen([_ssh_keygen(), "-y", "-f", str(private_path)])
    if result.returncode != 0:
        return None
    return normalize_public_key(result.stdout)


def prepare_existing_key(private_path: object, public_path: object = "") -> PreparedSshKey:
    private = _bounded_path(private_path, "SSH 私钥")
    try:
        details = private.stat()
    except OSError as exc:
        raise SshKeySetupError("private_key_unreadable", "无法读取 SSH 私钥") from exc
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0 or details.st_size > MAX_PRIVATE_KEY_BYTES:
        raise SshKeySetupError("private_key_invalid", "SSH 私钥文件无效或大小异常")
    if os.name != "nt" and details.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SshKeySetupError(
            "private_key_permissions",
            "SSH 私钥权限过宽；请先在终端执行 chmod 600 后再试",
        )

    selected_public: Path | None = None
    if isinstance(public_path, str) and public_path.strip():
        selected_public = _bounded_path(public_path, "SSH 公钥")
    else:
        default_public = Path(f"{private}.pub")
        if default_public.is_file():
            selected_public = default_public.resolve()

    public_line = _read_public_key(selected_public) if selected_public else None
    try:
        derived = _derive_public_key(private)
    except SshKeySetupError as exc:
        if public_line is None or exc.code != "ssh_keygen_missing":
            raise
        derived = None
    if public_line is None and derived is None:
        raise SshKeySetupError(
            "public_key_unavailable",
            "无法从私钥读取公钥；如私钥带口令，请选择对应的 .pub 公钥文件并先加载 ssh-agent",
        )
    if public_line is not None and derived is not None and public_line != derived:
        raise SshKeySetupError("key_pair_mismatch", "选择的公钥与私钥不匹配")
    return PreparedSshKey(
        private_path=private,
        public_path=selected_public,
        public_line=public_line or derived or "",
        generated=False,
    )


def _current_windows_account() -> str:
    try:
        result = subprocess.run(
            ["whoami"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        account = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and account:
            return account
    except (OSError, subprocess.SubprocessError):
        pass
    account = getpass.getuser().strip()
    if account:
        return account
    raise SshKeySetupError("key_permissions_failed", "无法识别当前 Windows 用户")


def protect_private_key(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path.parent, stat.S_IRWXU)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise SshKeySetupError("key_permissions_failed", "无法设置 SSH 私钥权限") from exc
        return

    icacls = shutil.which("icacls")
    if not icacls:
        raise SshKeySetupError("key_permissions_failed", "Windows 缺少私钥权限管理工具 icacls")
    result = subprocess.run(
        [icacls, str(path), "/inheritance:r", "/grant:r", f"{_current_windows_account()}:F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode != 0:
        raise SshKeySetupError("key_permissions_failed", "Windows 无法收紧 SSH 私钥访问权限")


def _copy_exclusive(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=64 * 1024)


def prepare_generated_key(keys_root: Path, profile_id: str, server_id: str) -> PreparedSshKey:
    digest = hashlib.sha256(f"{profile_id}\x00{server_id}".encode("utf-8")).hexdigest()[:16]
    try:
        keys_root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(keys_root, stat.S_IRWXU)
    except OSError as exc:
        raise SshKeySetupError("key_directory_failed", "无法创建本机 SSH Key 保存目录") from exc

    private = keys_root / f"vram-radar-{digest}-ed25519"
    public = Path(f"{private}.pub")
    if private.exists() or public.exists():
        if private.is_file() and public.is_file():
            return prepare_existing_key(str(private), str(public))
        raise SshKeySetupError(
            "key_path_conflict",
            "VRAM Radar 专用密钥位置已有不完整文件；为避免覆盖，请改用现有密钥或先人工检查",
        )

    temporary_root = Path(tempfile.mkdtemp(prefix=".key-setup-", dir=keys_root))
    temporary_private = temporary_root / "key"
    created: list[Path] = []
    completed = False
    try:
        generated = _run_keygen(
            [
                _ssh_keygen(),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "VRAM Radar dedicated key",
                "-f",
                str(temporary_private),
            ]
        )
        if generated.returncode != 0:
            raise SshKeySetupError("ssh_keygen_failed", "ssh-keygen 无法生成本地 Ed25519 密钥")
        public_line = _read_public_key(Path(f"{temporary_private}.pub"))
        _copy_exclusive(temporary_private, private)
        created.append(private)
        _copy_exclusive(Path(f"{temporary_private}.pub"), public)
        created.append(public)
        protect_private_key(private)
        if os.name != "nt":
            os.chmod(public, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        prepared = PreparedSshKey(
            private_path=private.resolve(),
            public_path=public.resolve(),
            public_line=public_line,
            generated=True,
        )
        completed = True
        return prepared
    except FileExistsError as exc:
        raise SshKeySetupError("key_path_conflict", "专用密钥位置已存在，未覆盖任何文件") from exc
    finally:
        if not completed:
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        shutil.rmtree(temporary_root, ignore_errors=True)


def remove_generated_key(key: PreparedSshKey) -> bool:
    if not key.generated:
        return True
    removed = True
    for path in (key.public_path, key.private_path):
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            removed = False
    return removed


INSTALL_AUTHORIZED_KEY_SCRIPT = r"""
set -eu
umask 077
ssh_created=0
tmp=''
fail() {
  code="${2:-40}"
  [ -z "${tmp:-}" ] || rm -f "$tmp"
  if [ "${ssh_created:-0}" -eq 1 ]; then rmdir "$HOME/.ssh" 2>/dev/null || true; fi
  printf '%s\n' "$1" >&2
  exit "$code"
}
[ -n "${HOME:-}" ] && [ "$HOME" != "/" ] && [ -d "$HOME" ] && [ -O "$HOME" ] || fail VRAM_RADAR_KEY_UNSAFE_HOME 41
IFS=' ' read -r key_type key_blob key_extra || fail VRAM_RADAR_KEY_INVALID 42
[ -n "$key_type" ] && [ -n "$key_blob" ] && [ -z "${key_extra:-}" ] || fail VRAM_RADAR_KEY_INVALID 42
case "$key_type" in
  ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) ;;
  *) fail VRAM_RADAR_KEY_INVALID 42 ;;
esac
case "$key_blob" in ''|*[!A-Za-z0-9+/=]*) fail VRAM_RADAR_KEY_INVALID 42 ;; esac
ssh_dir="$HOME/.ssh"
auth="$ssh_dir/authorized_keys"
ssh_existed=0
auth_existed=0
auth_mode=600
if [ -e "$ssh_dir" ] || [ -L "$ssh_dir" ]; then
  ssh_existed=1
  [ -d "$ssh_dir" ] && [ ! -L "$ssh_dir" ] && [ -O "$ssh_dir" ] || fail VRAM_RADAR_KEY_UNSAFE_SSH_DIR 43
  ssh_mode=$(stat -c '%a' "$ssh_dir" 2>/dev/null || stat -f '%Lp' "$ssh_dir" 2>/dev/null) || fail VRAM_RADAR_KEY_WRITE_FAILED 44
  case "$ssh_mode" in ''|*[!0-7]*) fail VRAM_RADAR_KEY_WRITE_FAILED 44 ;; esac
  (( (8#$ssh_mode & 0022) == 0 )) || fail VRAM_RADAR_KEY_UNSAFE_SSH_DIR 43
else
  mkdir "$ssh_dir" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
  ssh_created=1
  chmod 700 "$ssh_dir" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
fi
if [ -e "$auth" ] || [ -L "$auth" ]; then
  auth_existed=1
  [ -f "$auth" ] && [ ! -L "$auth" ] && [ -O "$auth" ] || fail VRAM_RADAR_KEY_UNSAFE_AUTHORIZED_KEYS 45
  auth_mode=$(stat -c '%a' "$auth" 2>/dev/null || stat -f '%Lp' "$auth" 2>/dev/null) || fail VRAM_RADAR_KEY_WRITE_FAILED 44
  case "$auth_mode" in ''|*[!0-7]*) fail VRAM_RADAR_KEY_WRITE_FAILED 44 ;; esac
  (( (8#$auth_mode & 0022) == 0 )) || fail VRAM_RADAR_KEY_UNSAFE_AUTHORIZED_KEYS 45
  if awk -v kt="$key_type" -v kb="$key_blob" '{ for (i=1; i<NF; i++) if ($i==kt && $(i+1)==kb) found=1 } END { exit(found ? 0 : 1) }' "$auth"; then
    printf 'VRAM_RADAR_KEY_SETUP|already_present|1|1|%s\n' "$auth_mode"
    exit 0
  fi
fi
tmp=$(mktemp "$ssh_dir/.vram-radar-authorized-keys.XXXXXX") || fail VRAM_RADAR_KEY_WRITE_FAILED 44
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT HUP INT TERM
if [ "$auth_existed" -eq 1 ]; then cat "$auth" > "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44; fi
if [ -s "$tmp" ]; then printf '\n' >> "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44; fi
printf '%s %s\n' "$key_type" "$key_blob" >> "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
chmod "$auth_mode" "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
mv -f "$tmp" "$auth" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
trap - EXIT HUP INT TERM
printf 'VRAM_RADAR_KEY_SETUP|installed|%s|%s|%s\n' "$ssh_existed" "$auth_existed" "$auth_mode"
""".strip()


VERIFY_SSH_KEY_SCRIPT = "printf '%s\\n' 'VRAM_RADAR_KEY_VERIFY|ok'"


def rollback_authorized_key_script(
    *,
    ssh_existed: bool,
    auth_existed: bool,
    auth_mode: str = "600",
) -> str:
    if not re.fullmatch(r"[0-7]{3,4}", auth_mode):
        raise SshKeySetupError("ssh_key_remote_protocol", "服务器返回了无效的 authorized_keys 权限")
    return rf"""
set -eu
umask 077
fail() {{ printf '%s\n' "$1" >&2; exit "${{2:-40}}"; }}
[ -n "${{HOME:-}}" ] && [ "$HOME" != "/" ] && [ -d "$HOME" ] && [ -O "$HOME" ] || fail VRAM_RADAR_KEY_UNSAFE_HOME 41
IFS=' ' read -r key_type key_blob key_extra || fail VRAM_RADAR_KEY_INVALID 42
[ -n "$key_type" ] && [ -n "$key_blob" ] && [ -z "${{key_extra:-}}" ] || fail VRAM_RADAR_KEY_INVALID 42
ssh_dir="$HOME/.ssh"
auth="$ssh_dir/authorized_keys"
[ -d "$ssh_dir" ] && [ ! -L "$ssh_dir" ] && [ -O "$ssh_dir" ] || fail VRAM_RADAR_KEY_UNSAFE_SSH_DIR 43
[ -f "$auth" ] && [ ! -L "$auth" ] && [ -O "$auth" ] || fail VRAM_RADAR_KEY_UNSAFE_AUTHORIZED_KEYS 45
tmp=$(mktemp "$ssh_dir/.vram-radar-authorized-keys.XXXXXX") || fail VRAM_RADAR_KEY_WRITE_FAILED 44
cleanup() {{ rm -f "$tmp"; }}
trap cleanup EXIT HUP INT TERM
awk -v kt="$key_type" -v kb="$key_blob" '{{ keep=1; for (i=1; i<NF; i++) if ($i==kt && $(i+1)==kb) keep=0; if (keep) print }}' "$auth" > "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
chmod {auth_mode} "$tmp" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
if [ {1 if auth_existed else 0} -eq 0 ] && [ ! -s "$tmp" ]; then
  rm -f "$auth" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
  rm -f "$tmp"
else
  mv -f "$tmp" "$auth" || fail VRAM_RADAR_KEY_WRITE_FAILED 44
fi
trap - EXIT HUP INT TERM
if [ {1 if ssh_existed else 0} -eq 0 ]; then rmdir "$ssh_dir" 2>/dev/null || true; fi
printf '%s\n' 'VRAM_RADAR_KEY_ROLLBACK|ok'
""".strip()
