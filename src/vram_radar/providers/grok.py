"""Grok Bot / Grok desktop.

Evidence (checked on a real install, v0.63.0): the app keeps
``%APPDATA%/Grok Bot/desktop-status.json`` = {version, pid, appVersion,
startedAtMs, signedIn}.  Its weekly usage (``usage_percent`` /
``next_reset_timestamp_utc``) is fetched on demand by the main process through
the Connect RPC ``aiserver.v1.DashboardService/GetSandUsageStatus`` (a POST)
authenticated with an access token kept in Electron safeStorage (DPAPI).  The
result is only held in memory and never written to disk: its persisted
``sand-client-persistence`` slices are UI layout, drafts, roster and
transcripts.  Reproducing the query would require decrypting the app's token
and impersonating it with a non-GET call, which this project does not do.

What *is* legitimately readable: Grok renders that usage in its own account
menu (avatar > usage row "NN%" + reset hint).  ``screen_usage`` reads that
text through Windows UI Automation while the menu is open (read-only, only
when Grok is the foreground window, only inside menus).  The last reading is
shown with the time it was seen; without one the strip says "已登录".
"""
from __future__ import annotations

import time

from .base import (Environment, base_state, detect_install, newest_mtime, pair, pid_alive, read_json,
                   running_pair, text)

ID, NAME, SHORT = "grok", "Grok", "Grok"
PROCESSES = ["grok bot.exe", "grok.exe"]
SCREEN_MAX_AGE = 7 * 86400  # weekly window: older readings are meaningless


def _screen_watcher():
    global WATCHER
    if WATCHER is None:
        from .screen_usage import ScreenUsageWatcher
        WATCHER = ScreenUsageWatcher(PROCESSES)
    return WATCHER


WATCHER = None


def overlay(state: dict, reading: dict | None, now: float | None = None) -> dict:
    """Merge an on-screen usage reading (see screen_usage) into a probe state."""
    now = time.time() if now is None else now
    seen = reading.get("seen_at") if isinstance(reading, dict) else None
    if not isinstance(seen, (int, float)) or not 0 <= now - seen <= SCREEN_MAX_AGE:
        if state.get("installed") and state.get("signed_in") is not False:
            state["brief"] = pair("已登录（用量只显示在 Grok 自己的头像菜单里，打开一次即可读取）",
                                  "Signed in (usage appears only in Grok's own account menu; open it once to read)")
        return state
    used = reading.get("percent_used")
    if not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return state
    stamp = time.strftime("%H:%M" if now - seen < 86400 else "%m-%d %H:%M", time.localtime(seen))
    value = f"{used:.0f}%"
    reset = (reading.get("reset_text") or "").strip()
    state["headline"] = pair(f"已用 {value}", f"{value} used")
    state["subline"] = pair(f"读于 {stamp}", f"read {stamp}")
    state["brief"] = pair(f"已用 {value}" + (f" · {reset}" if reset else "") + f"（{stamp} 读取）",
                          f"{value} used" + (f" · {reset}" if reset else "") + f" (read {stamp})")
    state["low"] = used >= 90
    state["quota_available"] = True
    state["quota_source"] = "screen"
    state["facts"].insert(0, pair(f"用量读自 Grok 头像菜单（{stamp}），菜单再次打开时更新",
                                  f"Usage read from Grok's account menu at {stamp}; updates when it is opened again"))
    return state


def probe(env: Environment) -> dict:
    detection = detect_install(
        env, uninstall=[r"^Grok\b"], processes=PROCESSES, executables=["Grok Bot.exe", "Grok.exe"],
        packages=[r"^(xAI|X\.AI|XAI)\.", r"\bGrok"],
        folders=["{localappdata}/Programs/Grok Bot", "{localappdata}/Programs/grok-bot",
                 "{localappdata}/Programs/Grok", "{programfiles}/Grok Bot", "{programfiles}/Grok"],
        sibling_names=["Grok Bot", "Grok"])
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
    state["facts"].append(pair("Grok 的周用量只在应用内存中，由加密登录令牌在线获取，本地不落盘；"
                               "为保护账户，雷达不解密令牌，只显示登录与运行状态",
                               "Grok fetches weekly usage online with its encrypted sign-in token and never stores it; "
                               "Radar does not decrypt that token, so it shows sign-in and run state"))
    if state["signed_in"] is True:
        headline = pair("已登录", "Signed in")
    elif state["signed_in"] is False:
        headline = pair("未登录", "Signed out")
    else:
        headline = pair("已安装", "Installed") if state["installed"] else pair("未安装", "Missing")
    if state["signed_in"] is not None and state["running"] is False:
        state["facts"].append(pair("登录状态来自上次运行", "Sign-in state is from the last run"))
    state["headline"] = headline
    state["subline"] = running_pair(state["running"])
    return state
