"""Grok Bot / Grok desktop.

Evidence (checked on a real install, v0.63.0): the app keeps
``%APPDATA%/Grok Bot/desktop-status.json`` = {version, pid, appVersion,
startedAtMs, signedIn}.  Its other local state (``sand-client-persistence``
blobs, LevelDB) contains UI/transcript slices and encrypted gateway secrets but
no quota, credit or rate-limit records, so quota is not available locally.
"""
from __future__ import annotations

from .base import (Environment, base_state, detect_install, newest_mtime, pair, pid_alive, read_json,
                   running_pair, text)

ID, NAME, SHORT = "grok", "Grok", "Grok"
PROCESSES = ["grok bot.exe", "grok.exe"]


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
    state["facts"].append(pair("Grok 本地不保存额度/用量数据，只能显示登录与运行状态",
                               "Grok keeps no quota/usage data locally; showing sign-in and run state"))
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
