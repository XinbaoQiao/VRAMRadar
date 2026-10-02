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
import json
import re
import time
from pathlib import Path

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


LAST = {"reading": None, "at": None, "loaded": False}   # last good reading (non-secret fields only)
_KEEP = {"used_percent": (int, float), "reset_at": (int, float), "plan": str, "level": str,
         "is_member": bool, "exhausted": bool, "overdrawn": bool, "send_blocked": bool}


def _last_path():
    from ..storage import storage_paths
    return Path(storage_paths().cache) / "kimi-quota.json"


def _save_last(reading: dict, at: float, path=None) -> None:
    """Persist only the non-secret quota fields so a restart while Kimi is
    closed (its access token lives ~15 min) still shows the last value."""
    try:
        from ..storage import atomic_write_text
        keep = {k: reading.get(k) for k, kinds in _KEEP.items()
                if isinstance(reading.get(k), kinds) and not (kinds is not bool and isinstance(reading.get(k), bool))}
        atomic_write_text(path or _last_path(), json.dumps({"at": float(at), "reading": keep}))
    except Exception:
        pass


def _load_last(path=None) -> None:
    if LAST["loaded"]:
        return
    LAST["loaded"] = True
    try:
        target = path or _last_path()
        if target.stat().st_size > 4096:
            return
        data = json.loads(target.read_text(encoding="utf-8"))
        reading = {k: v for k, v in (data.get("reading") or {}).items()
                   if k in _KEEP and isinstance(v, _KEEP[k])}
        at = data.get("at")
        if reading and isinstance(at, (int, float)):
            LAST["reading"], LAST["at"] = {"status": "ok", **reading}, float(at)
    except (OSError, ValueError, AttributeError, TypeError):
        pass


def _usable_last(now: float) -> dict | None:
    """The stored reading, unless its quota window has already reset."""
    reading = LAST["reading"]
    if not reading:
        return None
    reset = reading.get("reset_at")
    if isinstance(reset, (int, float)) and reset <= now:
        return None
    return reading


def _show_reading(state: dict, reading: dict, now: float, *, read_at: float | None = None) -> dict:
    """Strip: the bare usable amount -- time until the allowance resets (free
    and paid plans alike), 已用尽 when used up.  Tooltip: usage + reset."""
    used = reading.get("used_percent")
    exhausted = (bool(reading.get("exhausted")) or bool(reading.get("overdrawn"))
                 or bool(reading.get("send_blocked")) or (isinstance(used, (int, float)) and used >= 100))
    reset = reading.get("reset_at")
    if exhausted:
        state["headline"] = pair("已用尽", "Used up")
    elif reading.get("active") is False and not reading.get("is_member") and used is None:
        state["headline"] = pair("无额度", "None")
    elif isinstance(used, (int, float)):
        state["headline"] = pair(f"{100 - used:.0f}%", f"{100 - used:.0f}%")
    else:
        state["headline"] = pair("可用", "OK")
    if exhausted or isinstance(used, (int, float)) or state["headline"].get("en") == "None":
        state["quota"] = state["headline"]   # remaining share / used up / none
        state["quota_percent"] = 0 if exhausted else (100 - used if isinstance(used, (int, float)) else None)
    brief_zh = f"已用 {used:.0f}%" if isinstance(used, (int, float)) else ("已用尽" if exhausted else "可用")
    brief_en = f"{used:.0f}% used" if isinstance(used, (int, float)) else ("used up" if exhausted else "available")
    if isinstance(reset, (int, float)) and reset > now:
        state["reset_at"] = reset
    if read_at is None:
        state["subline"] = pair("自动读取", "auto")
        state["stale"] = False
    else:
        when = time.strftime("%H:%M" if now - read_at < 86400 else "%m-%d %H:%M", time.localtime(read_at))
        state["subline"] = pair(f"读于 {when}", f"read {when}")
        brief_zh += f"（{when} 读取）"
        brief_en += f" (read {when})"
        state["stale"] = True
    state["brief"] = pair(brief_zh, brief_en)
    state["low"] = exhausted or (isinstance(used, (int, float)) and used >= 90)
    state["quota_available"] = True
    state["quota_source"] = "session"
    return state


def apply_session(state: dict, session: dict | None, now: float | None = None) -> dict:
    """Merge a live membership reading; it takes precedence over the
    log-derived status.  An expired *login* reads as re-login; an expired
    short-lived access token with a valid login keeps the last reading."""
    if not session:
        return state
    now = time.time() if now is None else now
    status = session.get("status")
    if status == "ok":
        fetched = session.get("fetched_at") if isinstance(session.get("fetched_at"), (int, float)) else now
        LAST["reading"], LAST["at"] = dict(session), fetched
        if not session.get("stale_error"):
            _save_last(session, fetched)
        stale = bool(session.get("stale_error"))   # cache served the last good reading
        state = _show_reading(state, session, now, read_at=fetched if stale else None)
        state["session_quota"] = "ok"
        state["facts"].insert(0, pair("额度经授权，通过 Kimi 本机登录只读查询获取（新登录立即读取，否则每 5 分钟最多一次）",
                                      "Quota read with your consent via Kimi's local login, read-only (immediately after a new sign-in, else at most every 5 min)"))
        return state
    if status == "unauthorized" and session.get("login_valid"):
        state["session_quota"] = "waiting_app"
        _load_last()
        last = _usable_last(now)
        if state.get("running"):
            # Kimi is running and renews its 15-min access credential itself:
            # keep the last reading (marked with its time) until it does.
            if last:
                state = _show_reading(state, last, now, read_at=LAST["at"])
            else:
                state["headline"] = pair("\u5f85\u5237\u65b0", "Pending")
                state["subline"] = pair("Kimi \u8fd0\u884c\u4e2d", "Kimi running")
                state["quota_available"] = False
        else:
            # 10-02: Kimi closed at 23:53, its access credential expired 00:07
            # (login valid until 12-30) -> the strip showed the 17 h old
            # reading in quiet grey.  Only Kimi itself can renew the
            # credential (we never run the refresh flow), so say so plainly.
            state["headline"] = pair("\u672a\u8fd0\u884c", "Not running")
            state["quota"] = state["headline"]
            state["subline"] = pair("\u542f\u52a8\u540e\u81ea\u52a8\u66f4\u65b0", "updates when Kimi starts")
            state["needs_action"], state["stale"] = True, False
            state["quota_available"] = False
            state.pop("reset_at", None)
            state.pop("quota_percent", None)
            zh = "Kimi \u672a\u8fd0\u884c\uff0c\u542f\u52a8\u540e\u81ea\u52a8\u66f4\u65b0\u989d\u5ea6\u3002"
            en = "Kimi is not running; the quota updates when Kimi starts."
            if last:
                when = time.strftime("%m-%d %H:%M", time.localtime(LAST["at"]))
                used = last.get("used_percent")
                if isinstance(used, (int, float)):
                    zh += f"\u4e0a\u6b21\u8bfb\u53d6 {when}\uff1a\u5269\u4f59 {100 - used:.0f}%"
                    en += f" Last read {when}: {100 - used:.0f}% left."
            state["brief"] = pair(zh, en)
        state["facts"].insert(0, pair("Kimi \u7684\u8bbf\u95ee\u51ed\u8bc1\u4ec5\u5728 Kimi \u8fd0\u884c\u65f6\u7eed\u671f\uff1b\u542f\u52a8 Kimi \u540e\u5c06\u7acb\u5373\u91cd\u65b0\u8bfb\u53d6",
                                      "Kimi renews its access credential only while running; the quota is re-read as soon as Kimi starts"))
        return state
    if status == "unauthorized":
        LAST["reading"] = LAST["at"] = None
        state["headline"] = pair("需登录", "Sign in")
        state["subline"] = pair("请启动 Kimi", "start Kimi")
        state["brief"] = pair("需重新登录（请启动 Kimi）", "Sign-in required (start Kimi)")
        state["session_quota"], state["session_relogin"] = "expired", True
        state["quota"], state["needs_action"] = state["headline"], True
        state["quota_available"] = False
        expired = session.get("expired_at")
        when = time.strftime("%m-%d %H:%M", time.localtime(expired)) if isinstance(expired, (int, float)) else ""
        state["facts"].insert(0, pair("自动读取额度失败：Kimi 登录已过期" + (f"（{when}）" if when else "")
                                      + "，请启动 Kimi 以续期或重新登录",
                                      "Automatic quota read failed: Kimi sign-in expired" + (f" ({when})" if when else "")
                                      + "; start Kimi to renew or sign in"))
        return state
    _load_last()
    if _usable_last(now):   # network hiccup: keep the last real reading
        state = _show_reading(state, LAST["reading"], now, read_at=LAST["at"])
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
        if exhausted:
            state["quota"] = state["headline"]
        if reset and reset > now and not stale:
            state["subline"] = running_pair(state["running"])
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
