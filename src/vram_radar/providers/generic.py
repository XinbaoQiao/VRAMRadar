"""Detection-only providers: apps that keep no documented local usage data.

These report installed / version / running / last-used (data-folder mtime)
and an optional sign-in hint from the *existence* of a credential file.
"""
from __future__ import annotations

from pathlib import Path

from .base import ENGLISH_NAMES, Environment, base_state, detect_install, exists, newest_mtime, nonempty_file, pair, running_pair


def make_probe(provider_id: str, name: str, short: str, *, uninstall, processes, executables,
               packages=(), folders=(), sibling_names=(), data_dirs=(), sign_in_files=(), reason_zh="", reason_en=""):
    def probe(env: Environment) -> dict:
        detection = detect_install(env, uninstall=uninstall, processes=processes, executables=executables,
                                   packages=packages, folders=folders, sibling_names=sibling_names)
        state = base_state(provider_id, name, short, detection)
        folders_found = [path for template in data_dirs for path in env.expand(template) if exists(path)]
        if folders_found:
            state["last_used"] = newest_mtime(folders_found + [p / fname for p in folders_found
                                                               for fname in ("Preferences", "Local State", "config.json",
                                                                            "window-state.json", "settings.json")])
        signs = [path for template in sign_in_files for path in env.expand(template)]
        if signs:
            present = any(nonempty_file(path) for path in signs)
            state["signed_in"] = True if present else None
            if present:
                state["facts"].append(pair("存在本地登录文件（未读取内容）", "Local sign-in file present (not read)"))
        if not detection.installed and folders_found:
            state["code"] = "leftover_data"
            state["facts"].append(pair("仅发现旧数据目录，程序可能已卸载", "Only leftover data found; the app may be uninstalled"))
        state["quota_reason"] = "no_local_quota"
        state["facts"].append(pair(reason_zh or f"{name} 本地不提供额度数据，仅显示安装/运行状态",
                                   reason_en or f"{ENGLISH_NAMES.get(provider_id, name)} exposes no local quota data; "
                                                "showing install/run state"))
        if state["installed"]:
            state["headline"] = pair("已登录", "Signed in") if state["signed_in"] else pair("已安装", "Installed")
        else:
            state["headline"] = pair("未安装", "Missing")
        state["subline"] = running_pair(state["running"]) if state["installed"] else pair("未检测到", "Not found")
        return state
    return probe


claude = make_probe(
    "claude", "Claude", "Claude",
    uninstall=[r"^Claude\b", r"Anthropic"], processes=["claude.exe"], executables=["Claude.exe", "claude.exe"],
    packages=[r"^(Claude|Anthropic)"],
    folders=["{localappdata}/AnthropicClaude", "{localappdata}/Programs/Claude", "{localappdata}/Programs/claude-desktop",
             "{programfiles}/Claude", "{programfiles}/Anthropic/Claude"],
    sibling_names=["Claude"], data_dirs=["{appdata}/Claude", "{home}/.claude"],
    sign_in_files=["{home}/.claude/.credentials.json"],
    reason_zh="Claude 桌面版的用量限制只在线显示，本地不保存；仅显示安装/运行状态",
    reason_en="Claude desktop shows usage limits online only; showing install/run state")

glm = make_probe(
    "glm", "GLM 智谱清言", "智谱清言",
    uninstall=[r"智谱", r"清言", r"ChatGLM", r"Zhipu", r"^GLM\b"],
    processes=["智谱清言.exe", "chatglm.exe", "zhipuqingyan.exe", "qingyan.exe", "glm.exe"],
    executables=["智谱清言.exe", "ChatGLM.exe", "zhipuqingyan.exe", "qingyan.exe"],
    packages=[r"Zhipu", r"ChatGLM", r"Qingyan"],
    folders=["{localappdata}/Programs/智谱清言", "{localappdata}/Programs/zhipuqingyan",
             "{localappdata}/Programs/ChatGLM", "{programfiles}/智谱清言", "{programfiles}/ChatGLM"],
    sibling_names=["智谱清言", "ChatGLM", "zhipuqingyan"],
    data_dirs=["{appdata}/智谱清言", "{appdata}/zhipuqingyan", "{appdata}/chatglm", "{appdata}/ChatGLM"])

qwen = make_probe(
    "qwen", "Qwen 通义", "通义千问",
    uninstall=[r"^Qwen\b", r"通义", r"千问"], processes=["qwen.exe", "tongyi.exe"],
    executables=["Qwen.exe", "qwen.exe", "Tongyi.exe"], packages=[r"Qwen", r"Tongyi"],
    folders=["{localappdata}/Programs/Qwen", "{localappdata}/Programs/qwen", "{programfiles}/Qwen"],
    sibling_names=["Qwen"], data_dirs=["{appdata}/Qwen"])

yuanbao = make_probe(
    "yuanbao", "腾讯元宝", "腾讯元宝",
    uninstall=[r"元宝", r"Yuanbao"], processes=["yuanbao.exe"], executables=["yuanbao.exe", "Yuanbao.exe"],
    packages=[r"Yuanbao"], folders=["{localappdata}/Programs/Yuanbao", "{programfiles}/Yuanbao",
                                    "{programfiles}/Tencent/Yuanbao"],
    sibling_names=["Yuanbao"], data_dirs=["{localappdata}/com.tencent.yuanbao"])
