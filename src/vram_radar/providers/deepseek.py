"""DeepSeek Harness (``dsh``) desktop.

Evidence (real install, v0.2.0-rc.2): it is a bring-your-own-key harness.
The API key lives in ``$DSH_HOME/.credentials.yaml`` (default ``~/.dsh``); the
app never queries an account balance itself (no balance endpoint in its
bundle), so there is no quota to mirror without using the key, which we never
read.  It does keep per-session token accounting in
``storages/session_projcache/sessions/*.json`` (``rows.tokenUsage.val.totals``
and ``rows.sessionStats``), which we aggregate as real local usage.
"""
from __future__ import annotations

from pathlib import Path
import time

from .base import (Environment, base_state, detect_install, format_tokens, newest_mtime, nonempty_file, pair,
                   read_json, running_pair, safe_stat)

ID, NAME, SHORT = "deepseek", "DeepSeek", "DS"
MAX_SESSIONS = 400
TOKEN_KEYS = ("uncachedInputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
_cache: dict[str, tuple[tuple, dict]] = {}


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1e15 else 0


def _stamp(value) -> float | None:
    if isinstance(value, str) and value:
        try:
            from datetime import datetime, timezone
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            value = parsed.timestamp()
        except (ValueError, OverflowError):
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        value = value / 1000 if value > 1e11 else value
        return float(value) if value < 253402300799 else None
    return None


def summarize_sessions(folder: Path) -> dict:
    try:
        files = [(p, p.stat()) for p in folder.glob("*.json")]
    except OSError:
        files = []
    files = [item for item in files if item[1].st_size <= 2_000_000]
    files.sort(key=lambda item: item[1].st_mtime, reverse=True)
    files = files[:MAX_SESSIONS]
    signature = tuple((str(p), s.st_mtime_ns, s.st_size) for p, s in files)
    cached = _cache.get(str(folder))
    if cached and cached[0] == signature:
        return dict(cached[1])
    totals = dict.fromkeys(TOKEN_KEYS, 0)
    sessions = active = 0
    last_prompt = None
    today_tokens = 0
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    for path, stat in files:
        document = read_json(path)
        rows = document.get("record", {}).get("rows") if isinstance(document, dict) and isinstance(document.get("record"), dict) else None
        if not isinstance(rows, dict):
            continue
        sessions += 1
        usage = rows.get("tokenUsage", {})
        usage = usage.get("val", {}) if isinstance(usage, dict) else {}
        session_totals = usage.get("totals", {}) if isinstance(usage, dict) else {}
        session_sum = 0
        if isinstance(session_totals, dict):
            for key in TOKEN_KEYS:
                amount = _count(session_totals.get(key))
                totals[key] += amount
                session_sum += amount
        stats = rows.get("sessionStats", {})
        stats = stats.get("val", {}) if isinstance(stats, dict) else {}
        turned = isinstance(stats, dict) and _count(stats.get("turns")) > 0
        if turned:
            active += 1
        meta = rows.get("sessionListMetadata", {})
        meta = meta.get("val", {}) if isinstance(meta, dict) else {}
        prompt = _stamp(meta.get("lastPromptAt")) if isinstance(meta, dict) else None
        if prompt is None and turned:
            prompt = stat.st_mtime
        if prompt and (last_prompt is None or prompt > last_prompt):
            last_prompt = prompt
        if prompt and prompt >= midnight:
            today_tokens += session_sum
    summary = {"totals": totals, "sessions": sessions, "active_sessions": active,
               "last_prompt": last_prompt, "today_tokens": today_tokens,
               "total_tokens": sum(totals.values()), "truncated": len(files) >= MAX_SESSIONS}
    _cache[str(folder)] = (signature, summary)
    return dict(summary)


def probe(env: Environment) -> dict:
    detection = detect_install(
        env, uninstall=[r"^DeepSeek\b"], processes=["deepseek harness.exe", "deepseek.exe"],
        executables=["DeepSeek Harness.exe", "DeepSeek.exe"], packages=[r"DeepSeek"],
        folders=["{localappdata}/Programs/DeepSeek Harness", "{localappdata}/Programs/DeepSeek",
                 "{localappdata}/Programs/dsh-desktop", "{programfiles}/DeepSeek Harness", "{programfiles}/DeepSeek"],
        sibling_names=["DeepSeek Harness", "DeepSeek"])
    state = base_state(ID, NAME, SHORT, detection)
    home = Path(env.environ["DSH_HOME"]) if env.environ.get("DSH_HOME") else env.home / ".dsh"
    credentials = home / ".credentials.yaml"
    configured = nonempty_file(credentials)  # existence and size only; never opened
    state["signed_in"] = True if configured else (False if home.exists() else None)
    state["facts"].append(pair("已配置 API 凭据文件（内容未读取）" if configured else "未发现 API 凭据文件",
                               "API credential file present (not read)" if configured else "No API credential file"))
    summary = summarize_sessions(home / "storages" / "session_projcache" / "sessions")
    state["usage"] = summary
    state["last_used"] = summary["last_prompt"] or newest_mtime(
        [home / "storages" / "workspace.json"] + env.expand("{appdata}/@deepseek-ai/dsh-desktop/Preferences"))
    total = summary["total_tokens"]
    state["facts"].append(pair(
        f"本地会话 {summary['sessions']} 个（有对话 {summary['active_sessions']} 个） · 累计 {format_tokens(total)} tokens",
        f"{summary['sessions']} local sessions ({summary['active_sessions']} with turns) · {format_tokens(total)} tokens total"))
    if summary["today_tokens"]:
        state["facts"].append(pair(f"今日 {format_tokens(summary['today_tokens'])} tokens",
                                   f"Today {format_tokens(summary['today_tokens'])} tokens"))
    state["facts"].append(pair("DeepSeek Harness 使用自带 API Key，余额只在 DeepSeek 平台，本地无额度数据",
                               "DeepSeek Harness uses your own API key; balance lives on the DeepSeek platform, not locally"))
    state["quota_reason"] = "byok_no_local_balance"
    if home.exists() or state["installed"]:
        state["headline"] = pair(f"{format_tokens(total)} tok", f"{format_tokens(total)} tok")
    else:
        state["headline"] = pair("未安装", "Missing")
    state["subline"] = running_pair(state["running"]) if state["running"] else (
        pair("已配置", "Configured") if configured else pair("未配置", "No key") if home.exists() else running_pair(state["running"]))
    return state
