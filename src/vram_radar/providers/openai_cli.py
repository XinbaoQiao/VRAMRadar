"""Codex: installation facts only.  Quota comes from the existing
``CodexUsageMonitor`` (local ``codex app-server``), unchanged."""
from __future__ import annotations

from pathlib import Path

from .base import Detection, Environment, base_state, detect_install, exists, pair, running_pair

ID, NAME, SHORT = "codex", "Codex", "Codex"


DETECT = dict(
    uninstall=[r"^Codex(?!\w)", r"^OpenAI Codex"], executables=["Codex.exe", "codex.exe"],
    packages=[r"^OpenAI\.Codex_"], folders=["{localappdata}/Programs/Codex"],
    mac_apps=["Codex.app"])


def probe(env: Environment, *, executable: str = "") -> dict:
    # Prefer the desktop app (MSIX/registry) for install facts; the running
    # codex.exe processes may belong to other tools' private runtimes.
    detection = detect_install(env, **DETECT)
    processes = env.processes()
    detection.running = any(p.name == "codex.exe" for p in processes) if processes else None
    try:
        from ..usage_monitor import find_codex
        cli = find_codex(executable)
    except Exception:
        cli = None
    if cli is not None and not detection.installed:
        detection = Detection(True, str(cli), "", "cli", 1, detection.running, detection.pids)
    state = base_state(ID, NAME, SHORT, detection)
    if cli is not None:
        state["facts"].append(pair(f"命令行：{Path(cli).name}", f"CLI: {Path(cli).name}"))
    codex_home = Path(env.environ.get("CODEX_HOME") or env.home / ("." + "codex"))
    auth = codex_home / "auth.json"
    if exists(auth):
        # Existence only; Codex's own app-server reports the real account state.
        state["facts"].append(pair("存在本地登录文件（未读取）", "Local sign-in file present (not read)"))
    state["quota_available"] = cli is not None
    state["quota_reason"] = "" if cli is not None else "codex_cli_missing"
    state["headline"] = pair("额度", "Quota")
    state["subline"] = running_pair(detection.running)
    return state
