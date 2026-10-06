"""Grok Bot / Grok desktop.

Evidence (checked on a real install, v0.63.0): the app keeps
``%APPDATA%/Grok Bot/desktop-status.json`` = {version, pid, appVersion,
startedAtMs, signedIn}.  Its weekly usage (``usage_percent`` /
``next_reset_timestamp_utc``) is fetched on demand by the main process through
the Connect RPC ``aiserver.v1.DashboardService/GetSandUsageStatus`` (a POST)
authenticated with an access token kept in Electron safeStorage (DPAPI).  The
result is only held in memory and never written to disk: its persisted
``sand-client-persistence`` slices are UI layout, drafts, roster and
transcripts.  With the user's explicit, per-provider consent (providers.session_consent),
Radar reuses that same stored login to call the SAME read-only endpoint the
app itself uses for the avatar menu (see grok_usage): the token is decrypted
in memory only, used for one read-only POST, and never refreshed, logged or
written.  Without consent none of that runs.

Always available, consent or not: Grok renders that usage in its own account
menu (avatar > usage row "NN%" + reset hint).  ``screen_usage`` reads that
text through Windows UI Automation while the menu is open (read-only, only
when Grok is the foreground window, only inside menus).  It is the fallback
when the session read is off or the sign-in expired; without either the strip
says "已登录".
"""
from __future__ import annotations

import logging
import time

from ..reset_format import reset_full

from . import grok_usage
from .base import (Environment, base_state, detect_install, newest_mtime, pair, pid_alive, read_json,
                   running_pair, text)

ID, NAME, SHORT = "grok", "Grok", "Grok"
LOG = logging.getLogger("vram_radar")   # used by _session_usage (was undefined: NameError)
PROCESSES = ["grok bot.exe", "grok.exe"]
DETECT = dict(
    uninstall=[r"^Grok\b"], processes=PROCESSES, executables=["Grok Bot.exe", "Grok.exe"],
    packages=[r"^(xAI|X\.AI|XAI)\.", r"\bGrok"],
    folders=["{localappdata}/Programs/Grok Bot", "{localappdata}/Programs/grok-bot",
             "{localappdata}/Programs/Grok", "{programfiles}/Grok Bot", "{programfiles}/Grok"],
    sibling_names=["Grok Bot", "Grok"], mac_apps=["Grok.app", "Grok Bot.app"], bundle_ids=["ai.x.grok"])
SCREEN_MAX_AGE = 7 * 86400  # weekly window: older readings are meaningless


def _screen_watcher():
    global WATCHER
    if WATCHER is None:
        from .screen_usage import ScreenUsageWatcher
        WATCHER = ScreenUsageWatcher(PROCESSES)
    return WATCHER


WATCHER = None

# Session-based quota reading (opt-in, see providers.session_consent): set by
# probe_all each round to ('grok' selected) and consent granted.  When off,
# Grok behaves exactly as before (screen reading / sign-in state only).
SESSION = {"enabled": False}
CACHE = grok_usage.UsageCache()


def overlay(state: dict, reading: dict | None, now: float | None = None) -> dict:
    """Merge an on-screen usage reading (see screen_usage) into a probe state."""
    now = time.time() if now is None else now
    seen = reading.get("seen_at") if isinstance(reading, dict) else None
    if not isinstance(seen, (int, float)) or not 0 <= now - seen <= SCREEN_MAX_AGE:
        if state.get("quota_source") == "session":
            return state  # keep the consented session reading as primary
        if state.get("installed") and state.get("signed_in") is not False:
            state["brief"] = pair("已登录（用量仅显示在 Grok 头像菜单中，打开该菜单后读取）",
                                  "Signed in (usage is shown only in Grok's account menu; it is read when that menu opens)")
        return state
    used = reading.get("percent_used")
    if not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return state
    stamp = time.strftime("%H:%M" if now - seen < 86400 else "%m-%d %H:%M", time.localtime(seen))
    value = f"{used:.0f}%"
    reset = (reading.get("reset_text") or "").strip()
    left = f"{100 - used:.0f}%"   # strip: bare remaining share, like Codex
    state["headline"] = pair(left, left)
    state["quota"] = pair(left, left)
    state["quota_percent"] = 100 - used
    state["subline"] = pair(f"读于 {stamp}", f"read {stamp}")
    state["brief"] = pair(f"已用 {value}" + (f" · {reset}" if reset else "") + f"（{stamp} 读取）",
                          f"{value} used" + (f" · {reset}" if reset else "") + f" (read {stamp})")
    state["low"] = used >= 90
    state["quota_available"] = True
    state["quota_source"] = "screen"
    state["facts"].insert(0, pair(f"用量读自 Grok 头像菜单（{stamp}），菜单再次打开时更新",
                                  f"Usage read from Grok's account menu at {stamp}; updates when it is opened again"))
    return state


def _session_usage(data_dirs) -> dict | None:
    """Query Grok's own read-only usage endpoint with its stored login.
    Runs only on the monitor thread and only when consented.  Never logs the
    token or the response."""
    for folder in data_dirs:
        if (folder / "sand-secrets.json").exists():
            try:
                return CACHE.get(folder)
            except Exception as exc:  # a crypto/HTTP hiccup must not break the probe
                LOG.info("grok session usage unavailable (%s)", type(exc).__name__)
                return {"status": "error"}
    return None


def _apply_session(state: dict, session: dict) -> dict:
    """Merge a session usage reading into the probe state.  On an expired
    token we surface the re-login notice and keep the screen reading fallback."""
    status = session.get("status")
    if status == "unauthorized":
        state["facts"].insert(0, pair("自动读取额度失败：Grok 登录已过期，请在 Grok 中重新登录",
                                      "Automatic usage read failed: Grok sign-in expired, sign in again in Grok"))
        state["session_quota"] = "expired"
        state["session_relogin"] = True
        return state
    if status != "ok":
        return state
    used = session.get("used_percent")
    if not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return state
    value = f"{used:.0f}%"
    reset_at = session.get("reset_at")
    reset_zh, reset_en = _reset_labels(reset_at)
    if reset_zh:
        state["reset_at"] = reset_at   # weekly nextResetTimestampUtc
    left = f"{100 - used:.0f}%"   # strip: bare remaining share, like Codex
    state["headline"] = pair(left, left)
    state["quota"] = pair(left, left)
    state["quota_percent"] = 100 - used
    state["subline"] = pair(reset_zh or "自动读取", reset_en or "auto")
    brief_zh = f"已用 {value}"
    brief_en = f"{value} used"
    state["brief"] = pair(brief_zh, brief_en)
    state["low"] = used >= 90
    fetched = session.get("fetched_at")
    if session.get("stale_error") and isinstance(fetched, (int, float)):
        # Last refresh failed: keep the last real value, but say when it was read.
        stamp = time.strftime("%H:%M", time.localtime(fetched))
        state["subline"] = pair(f"\u8bfb\u53d6 {stamp}", f"read {stamp}")
        state["brief"] = pair(brief_zh + f"\uff08{stamp} \u8bfb\u53d6\uff09", brief_en + f" (read {stamp})")
        state["stale"] = time.time() - fetched > 2 * grok_usage.MIN_INTERVAL + 60
    state["quota_available"] = True
    state["quota_source"] = "session"
    state["session_quota"] = "ok"
    state["facts"].insert(0, pair("额度经授权，通过 Grok 本机登录只读查询获取（每 5 分钟最多一次）",
                                  "Usage read with your consent via Grok's local login, read-only (at most every 5 min)"))
    return state


def _reset_labels(reset_at) -> tuple[str, str]:
    if not isinstance(reset_at, (int, float)) or reset_at <= 0:
        return "", ""
    return reset_full(reset_at, False), reset_full(reset_at, True)


def probe(env: Environment) -> dict:
    detection = detect_install(env, **DETECT)
    state = base_state(ID, NAME, SHORT, detection)
    data_dirs = [d for d in env.expand("{appdata}/Grok Bot") + env.expand("{appdata}/Grok")]
    status = None
    for folder in data_dirs:
        candidate = read_json(folder / "desktop-status.json", limit=65536)
        if isinstance(candidate, dict):
            status = candidate
            break
    state["last_used"] = newest_mtime([folder / name for folder in data_dirs
                                       for name in ("desktop-status.json", "window-state.json", "Preferences")])
    if isinstance(status, dict):
        signed = status.get("signedIn")
        if isinstance(signed, bool):
            state["signed_in"] = signed
        if not state["version"]:
            state["version"] = text(status.get("appVersion"), 40)
        alive = pid_alive(env, status.get("pid"), PROCESSES)
        if alive is not None and state["running"] is None:
            state["running"] = alive
        if not state["installed"] and alive:
            state["installed"], state["state"] = True, "ready"
    state["quota_reason"] = "no_local_quota"
    session = _session_usage(data_dirs) if SESSION.get("enabled") else None
    if session is not None:
        state = _apply_session(state, session)
    if state.get("quota_source") != "session" and not state.get("session_relogin"):
        state["facts"].append(pair("Grok 的周用量只在应用内存中在线获取，本地不落盘；未授权自动读取时，雷达只显示登录与运行状态",
                                   "Grok fetches weekly usage online and never stores it; without consent for automatic reading, Radar shows only sign-in and run state"))
    if state["signed_in"] is True:
        headline = pair("已登录", "Signed in")
    elif state["signed_in"] is False:
        headline = pair("未登录", "Signed out")
    else:
        headline = pair("已安装", "Installed") if state["installed"] else pair("未安装", "Missing")
    if state["signed_in"] is not None and state["running"] is False:
        state["facts"].append(pair("登录状态来自上次运行", "Sign-in state is from the last run"))
    # A successful consented session read is the primary headline; only fall
    # back to sign-in/run state when there is no live usage to show.
    if state.get("quota_source") != "session":
        state["headline"] = headline
        state["subline"] = running_pair(state["running"])
    return state
