"""DeepSeek Harness (``dsh``) desktop.

Evidence (real install, v0.2.0-rc.2): after DeepSeek sign-in the app stores a
platform grant in ``$DSH_HOME/.credentials.yaml`` (default ``~/.dsh``) and
reads the account wallet with a read-only ``GET /api/v0/users/get_user_summary``
(package ``@deepseek-ai/dsh-deepseek-account-platform``).  ``deepseek_balance``
mirrors exactly that query (or the public ``GET /user/balance`` for a plain
API-key record) with conservative polling; the secret never leaves that module.
It also keeps per-session token accounting in
``storages/session_projcache/sessions/*.json`` (``rows.tokenUsage.val.totals``
and ``rows.sessionStats``), which we aggregate as local usage.
"""
from __future__ import annotations

from pathlib import Path
import re
import time

from . import deepseek_balance
from .base import (Environment, base_state, detect_install, format_tokens, newest_mtime, nonempty_file, pair,
                   read_json, running_pair, safe_stat)

ID, NAME, SHORT = "deepseek", "DeepSeek", "DeepSeek"
MAX_SESSIONS = 400
STALE_BALANCE_SECONDS = 2 * 3600
TOKEN_KEYS = ("uncachedInputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
_cache: dict[str, tuple[tuple, dict]] = {}
BALANCE = deepseek_balance.BalanceCache()
# Network balance reads are enabled by the monitor (never by tests/imports).
NETWORK = {"enabled": False}


def money(amount, currency: str) -> str:
    symbol = {"CNY": "¥", "USD": "$"}.get(currency, currency + " ")
    return f"{symbol}{amount.quantize(deepseek_balance.Decimal('0.01'))}"


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
    files = []
    try:
        for p in folder.glob("*.json"):
            try:
                files.append((p, p.stat()))
            except OSError:   # deleted between listing and stat: skip only this one
                continue
    except OSError:
        files = []
    files = [item for item in files if item[1].st_size <= 2_000_000]
    files.sort(key=lambda item: item[1].st_mtime, reverse=True)
    files = files[:MAX_SESSIONS]
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    # The day is part of the key: "today" must roll over at midnight even
    # when no session file changes.
    signature = (midnight, tuple((str(p), s.st_mtime_ns, s.st_size) for p, s in files))
    cached = _cache.get(str(folder))
    if cached and cached[0] == signature:
        return dict(cached[1])
    totals = dict.fromkeys(TOKEN_KEYS, 0)
    sessions = active = 0
    last_prompt = None
    today_tokens = 0
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


def short_money(amount, currency: str) -> str:
    """Strip amount: "¥6.00" -> "¥6", "$66.50" stays."""
    return re.sub(r"\.00$", "", money(amount, currency))


def balance_labels(wallets, shown):
    """(headline, brief).  The strip shows only the usable total (paid and
    gift money are both spendable, so they are not told apart there); the
    tooltip adds the paid/gift split.  An empty account reads 无余额."""
    kinds: dict[str, dict] = {}
    for wallet in wallets:
        bucket = kinds.setdefault(wallet["currency"], {"topped_up": 0, "granted": 0})
        bucket[wallet["kind"] if wallet["kind"] in bucket else "topped_up"] += wallet["amount"]
    funded = [c for c, b in kinds.items() if b["topped_up"] + b["granted"] != 0]
    if not funded:
        return pair("无余额", "Empty"), pair("余额为 0（充值与赠送均为 0）", "Balance 0 (paid and gift both 0)")
    if len(funded) > 1:
        text = " + ".join(short_money(kinds[c]["topped_up"] + kinds[c]["granted"], c) for c in funded)
        return pair(text, text), pair("余额 " + text, "Balance " + text)
    currency = funded[0]
    paid, gift = kinds[currency]["topped_up"], kinds[currency]["granted"]
    total = short_money(paid + gift, currency)
    if paid and gift:
        split_zh = f"充值 {short_money(paid, currency)} + 赠送 {short_money(gift, currency)}"
        split_en = f"paid {short_money(paid, currency)} + gift {short_money(gift, currency)}"
    elif gift:
        split_zh, split_en = "赠送余额", "gift balance"
    else:
        split_zh, split_en = "充值余额", "paid balance"
    return pair(total, total), pair(f"{total}（{split_zh}）", f"{total} ({split_en})")


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
    state["facts"].append(pair("已登录 DeepSeek（凭据由 DeepSeek Harness 保存）" if configured else "未发现 DeepSeek 凭据",
                               "Signed in to DeepSeek (credential kept by DeepSeek Harness)" if configured else "No DeepSeek credential"))
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
    balance = None
    if configured and NETWORK["enabled"]:
        stat = safe_stat(credentials)
        balance = BALANCE.get(home, (stat.st_mtime_ns, stat.st_size) if stat else None)
    state["balance"] = None
    if balance and balance.get("status") == "ok":
        sums = deepseek_balance.totals(balance.get("wallets", []))
        order = sorted(sums, key=lambda c: (c != "CNY", c))
        # Compact strip value: currencies that hold money (all shown in the tooltip).
        shown = [money(sums[c], c) for c in order if sums[c] != 0] or [money(sums[order[0]], order[0])] if order else []
        state["balance"] = {c: str(sums[c]) for c in order}
        state["quota_available"] = True
        state["quota_reason"] = ""
        for wallet in balance.get("wallets", []):
            label = ("赠送余额", "Granted") if wallet["kind"] == "granted" else ("充值余额", "Topped up")
            state["facts"].append(pair(f"{label[0]} {money(wallet['amount'], wallet['currency'])}",
                                       f"{label[1]} {money(wallet['amount'], wallet['currency'])}"))
        fetched = balance.get("fetched_at")
        when = time.strftime("%H:%M", time.localtime(fetched)) if fetched else "?"
        source = "DeepSeek 平台账户" if balance.get("source") == "platform" else "DeepSeek API"
        source_en = "DeepSeek platform account" if balance.get("source") == "platform" else "DeepSeek API"
        note = ""
        if balance.get("stale_error"):
            note = "（最近一次刷新失败，显示上次结果）"
        state["facts"].append(pair(f"余额来自{source}（只读查询，{when} 更新）{note}",
                                   f"Balance from {source_en} (read-only, updated {when})" +
                                   (" · last refresh failed, showing previous value" if note else "")))
        # A balance that could not be refreshed for hours is shown as stale.
        state["stale"] = bool(balance.get("stale_error") and isinstance(fetched, (int, float))
                              and time.time() - fetched > STALE_BALANCE_SECONDS)
        total_zero = all(v == 0 for v in sums.values())
        state["low"] = total_zero
        headline, brief = balance_labels(balance.get("wallets", []), shown)
        state["headline"], state["brief"] = headline, brief
        state["subline"] = pair(f"{format_tokens(total)} tok", f"{format_tokens(total)} tok")
        return state
    reason = (balance or {}).get("status")
    if reason == "unauthorized":
        state["signed_in"] = False
        state["facts"].append(pair("DeepSeek 登录已失效，请在 DeepSeek Harness 中重新登录",
                                   "DeepSeek sign-in expired; sign in again in DeepSeek Harness"))
    elif reason in {"network", "unavailable", "rate_limited"}:
        state["facts"].append(pair("暂时无法读取 DeepSeek 余额（网络或服务不可用，稍后自动重试）",
                                   "DeepSeek balance temporarily unavailable (will retry later)"))
    elif configured and not NETWORK["enabled"]:
        state["facts"].append(pair("余额查询未启用", "Balance query not enabled"))
    elif not configured:
        state["facts"].append(pair("未登录 DeepSeek 账户，无法读取余额",
                                   "Not signed in to a DeepSeek account; balance unavailable"))
    state["quota_reason"] = reason or "no_credential"
    if home.exists() or state["installed"]:
        state["headline"] = pair(f"{format_tokens(total)} tok", f"{format_tokens(total)} tok")
    else:
        state["headline"] = pair("未安装", "Missing")
    state["subline"] = (pair("需登录", "Sign in") if reason == "unauthorized" else
                        running_pair(state["running"]) if state["running"] else
                        pair("已配置", "Configured") if configured else
                        pair("未配置", "No key") if home.exists() else running_pair(state["running"]))
    return state
