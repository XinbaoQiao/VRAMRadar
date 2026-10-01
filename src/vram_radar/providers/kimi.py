"""Kimi desktop (Moonshot).

Evidence (real install, v3.2.4): quota/membership is fetched by the app itself
from kimi.com with an encrypted token (``bridge-store/token-store.json`` is
``{encryption, data}``, protected by Electron safeStorage/DPAPI).  We never
decrypt it and never call the service.  The app does, however, write the
*results* of its own refreshes to ``logs/main.log``::

    [SubscriptionManager] refreshed(sub): level=10 isMember=false omniRatio=0 exhausted=false resetAt=...Z
    [SubscriptionManager] refreshed(stats): overdrawn=false sendBlocked=false ...

Those lines contain no identifiers or secrets; we parse only these fields.
They are as fresh as the last time Kimi itself was running.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
import time

from . import grok_usage, kimi_usage
from .base import (Environment, base_state, detect_install, newest_mtime, pair, read_json,
                   read_tail_lines, running_pair, safe_stat, text)

ID, NAME, SHORT = "kimi", "Kimi", "Kimi"

# Session-based quota reading (opt-in, see providers.session_consent): set by
# probe_all each round to ('kimi' selected) and consent granted.  When off,
# Kimi behaves exactly as before (log snapshot only).
SESSION = {"enabled": False}
CACHE = grok_usage.UsageCache(kimi_usage.fetch_usage, signature=kimi_usage.signature)


def _session_usage(data) -> dict | None:
    if data is None or not (data / kimi_usage.TOKEN_STORE).exists():
        return None
    try:
        return CACHE.get(data, kimi_usage.BACKEND)
    except Exception as exc:  # a crypto/HTTP hiccup must not break the probe
        import logging
        logging.getLogger("vram_radar").info("kimi session usage unavailable (%s)", type(exc).__name__)
        return {"status": "error"}


def apply_session(state: dict, session: dict | None, now: float | None = None) -> dict:
    """Merge a live membership reading; an expired login reads as re-login."""
    if not session:
        return state
    now = time.time() if now is None else now
    status = session.get("status")
    if status == "unauthorized":
        state["headline"] = pair("需重新登录", "Sign in again")
        state["subline"] = pair("打开 Kimi 一次", "open Kimi once")
        state["brief"] = pair("需重新登录（打开 Kimi 一次）", "Sign in again (open Kimi once)")
        state["session_quota"], state["session_relogin"] = "expired", True
        expired = session.get("expired_at")
        when = time.strftime("%m-%d %H:%M", time.localtime(expired)) if isinstance(expired, (int, float)) else ""
        state["facts"].insert(0, pair("自动读取额度失败：Kimi 登录已过期" + (f"（{when}）" if when else "")
                                      + "，请打开 Kimi 一次（会自动续期或重新登录）",
                                      "Automatic quota read failed: Kimi sign-in expired" + (f" ({when})" if when else "")
                                      + "; open Kimi once to renew or sign in"))
        return state
    if status != "ok":
        return state
    exhausted = bool(session.get("exhausted")) or bool(session.get("overdrawn")) or bool(session.get("send_blocked"))
    member = session.get("is_member")
    tier_zh = "会员" if member else "免费版" if member is False else ""
    tier_en = "member" if member else "free plan" if member is False else ""
    state["headline"] = pair("已用尽", "Exhausted") if exhausted else pair("可用", "OK")
    reset = session.get("reset_at")
    if isinstance(reset, (int, float)) and reset > now:
        hours = (reset - now) / 3600
        label = f"{hours:.1f}h" if hours >= 0.1 else "<0.1h"
        state["subline"] = pair(label, label)
        state["reset_at"] = reset
    else:
        state["subline"] = pair("自动读取", "auto")
    state["brief"] = pair(("额度已用尽" if exhausted else "额度未用尽") + (f" · {tier_zh}" if tier_zh else "") + "（自动读取）",
                          ("Quota exhausted" if exhausted else "Quota not exhausted") + (f" · {tier_en}" if tier_en else "") + " (auto)")
    state["low"] = exhausted
    state["stale"] = False
    state["quota_available"] = True
    state["quota_source"] = "session"
    state["session_quota"] = "ok"
    state["facts"].insert(0, pair("额度经你授权，使用 Kimi 本机登录只读查询获取（每 5 分钟最多一次）",
                                  "Quota read with your consent via Kimi's local login, read-only (at most every 5 min)"))
    return state
STALE_SECONDS = 6 * 3600
_LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)(?:\.\d+)?\].*?\[SubscriptionManager\]\S*\s+"
                   r"refreshed\((sub|stats)\):\s*(.*)$")
_PAIR = re.compile(r"(\w+)=([^\s]+)")
_ALLOWED = {"level", "isMember", "omniRatio", "exhausted", "resetAt", "overdrawn", "sendBlocked",
            "balanceEvent", "paywall", "notice"}


def _bool(value: str | None) -> bool | None:
    return {"true": True, "false": False}.get((value or "").lower())


def _local_ts(value: str) -> float | None:
    try:
        return time.mktime(time.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        stamp = parsed.timestamp()
        return stamp if 0 < stamp < 253402300799 else None
    except (ValueError, OverflowError):
        return None


def parse_log(lines: list[str]) -> dict:
    """Latest sub/stats refresh records; unknown keys are dropped."""
    latest: dict = {}
    for line in lines:
        match = _LINE.match(re.sub(r"\x1b\[[0-9;]*m", "", line))
        if not match:
            continue
        stamp, kind, body = match.groups()
        values = {k: text(v, 40) for k, v in _PAIR.findall(body) if k in _ALLOWED}
        latest[kind] = {"at": _local_ts(stamp), **values}
    return latest


def probe(env: Environment, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    detection = detect_install(
        env, uninstall=[r"^Kimi\b", r"月之暗面"], processes=["kimi.exe"], executables=["Kimi.exe"],
        packages=[r"Moonshot", r"\bKimi"],
        folders=["{localappdata}/Programs/Kimi", "{localappdata}/Programs/kimi-desktop",
                 "{programfiles}/Kimi"], sibling_names=["Kimi"])
    state = base_state(ID, NAME, SHORT, detection)
    data = next((d for d in env.expand("{appdata}/kimi-desktop") + env.expand("{appdata}/Kimi")
                 if (d / "logs").exists() or (d / "bridge-store").exists()), None)
    state["quota_reason"] = "log_snapshot_only"
    if data is None:
        state["facts"].append(pair("未找到 Kimi 数据目录", "Kimi data folder not found"))
        state["headline"] = pair("已安装", "Installed") if state["installed"] else pair("未安装", "Missing")
        state["subline"] = running_pair(state["running"])
        return state
    token_store = data / "bridge-store" / "token-store.json"
    # Presence/size only: the file holds a safeStorage-encrypted token and is
    # never opened.  An empty or missing store means Kimi is signed out.
    stat = safe_stat(token_store)
    if stat is not None:
        state["signed_in"] = stat.st_size > 64
    else:
        state["signed_in"] = False
    history = read_json(data / "diagnostics" / "session-history.json", limit=262144)
    starts = []
    if isinstance(history, dict) and isinstance(history.get("sessions"), list):
        for session in history["sessions"][-50:]:
            if isinstance(session, dict):
                stamp = _iso_ts(session.get("startedAt"))
                if stamp:
                    starts.append(stamp)
    state["last_used"] = max([*starts, newest_mtime([data / "logs" / "main.log", data / "main-window-state.json"]) or 0]) or None
    lines = []
    for name in ("main.old.log", "main.log"):
        lines.extend(read_tail_lines(data / "logs" / name))
    records = parse_log(lines)
    sub, stats = records.get("sub"), records.get("stats")
    as_of = max([r.get("at") or 0 for r in (sub, stats) if r] or [0]) or None
    state["as_of"] = as_of
    stale = as_of is None or now - as_of > STALE_SECONDS
    state["stale"] = stale
    if sub:
        level = sub.get("level")
        member = _bool(sub.get("isMember"))
        exhausted = _bool(sub.get("exhausted"))
        reset = _iso_ts(sub.get("resetAt"))
        if level:
            state["facts"].append(pair(f"会员等级 {level}" + ("（会员）" if member else "（非会员）" if member is False else ""),
                                       f"Membership level {level}" + (" (member)" if member else " (not a member)" if member is False else "")))
        if exhausted is not None:
            state["facts"].append(pair("额度已用尽" if exhausted else "额度未用尽",
                                       "Quota exhausted" if exhausted else "Quota not exhausted"))
        if sub.get("omniRatio") is not None:
            state["facts"].append(pair(f"omniRatio={sub['omniRatio']}", f"omniRatio={sub['omniRatio']}"))
        if reset:
            state["reset_at"] = reset
    if stats:
        overdrawn, blocked = _bool(stats.get("overdrawn")), _bool(stats.get("sendBlocked"))
        if overdrawn is not None or blocked is not None:
            state["facts"].append(pair(f"透支：{'是' if overdrawn else '否'} · 发送受限：{'是' if blocked else '否'}",
                                       f"Overdrawn: {'yes' if overdrawn else 'no'} · Send blocked: {'yes' if blocked else 'no'}"))
    exhausted = bool(sub and _bool(sub.get("exhausted"))) or bool(
        stats and (_bool(stats.get("overdrawn")) or _bool(stats.get("sendBlocked"))))
    if sub or stats:
        state["quota_available"] = True
        state["headline"] = pair("已用尽", "Exhausted") if exhausted else pair("可用", "OK")
        member = _bool(sub.get("isMember")) if sub else None
        tier_zh = "会员" if member else "免费版" if member is False else ""
        tier_en = "member" if member else "free plan" if member is False else ""
        # Kimi reports only exhausted / not exhausted (no remaining number).
        state["brief"] = pair(("额度已用尽" if exhausted else "额度未用尽") + (f" · {tier_zh}" if tier_zh else ""),
                              ("Quota exhausted" if exhausted else "Quota not exhausted") + (f" · {tier_en}" if tier_en else ""))
        state["low"] = exhausted
        when = time.strftime("%m-%d %H:%M", time.localtime(as_of)) if as_of else "?"
        state["facts"].append(pair(f"数据来自 Kimi 自身日志，记录于 {when}" + ("（Kimi 运行时才会刷新）" if stale else ""),
                                   f"From Kimi's own log, recorded {when}" + (" (refreshes only while Kimi runs)" if stale else "")))
        reset = state.get("reset_at")
        if reset and reset > now and not stale:
            hours = (reset - now) / 3600
            state["subline"] = pair(f"{hours:.1f}h" if hours >= 0.1 else "<0.1h", f"{hours:.1f}h" if hours >= 0.1 else "<0.1h")
        elif stale:
            state["subline"] = pair(f"记录 {time.strftime('%m-%d', time.localtime(as_of))}" if as_of else "旧记录",
                                    f"As of {time.strftime('%m-%d', time.localtime(as_of))}" if as_of else "Old data")
        else:
            state["subline"] = running_pair(state["running"])
        if reset:
            state["facts"].append(pair("重置时间 " + time.strftime("%m-%d %H:%M", time.localtime(reset)),
                                       "Resets " + time.strftime("%m-%d %H:%M", time.localtime(reset))))
    else:
        state["quota_reason"] = "no_log_record"
        state["facts"].append(pair("Kimi 日志中尚无额度刷新记录（额度由 Kimi 加密登录后在线获取）",
                                   "No quota refresh in Kimi's log yet (Kimi fetches it online with its encrypted sign-in)"))
        state["headline"] = (pair("已登录", "Signed in") if state["signed_in"] else
                             pair("未登录", "Signed out") if state["signed_in"] is False else pair("已安装", "Installed"))
        state["subline"] = running_pair(state["running"])
    if SESSION.get("enabled"):
        state = apply_session(state, _session_usage(data))
    return state
