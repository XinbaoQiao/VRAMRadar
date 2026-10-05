"""Hover detail card content for the Windows taskbar usage strip.

Pure helpers only: no WinForms, no polling.  The strip already holds a
snapshot; hover only formats what is already known.  Fields without real
data are omitted (never invented).
"""
from __future__ import annotations

import math
import time
from typing import Any

from .reset_format import reset_full, reset_short, valid_epoch

# Thin space between day and hour parts in the card ("1d 8h").
THIN = "\u2009"
HOVER_DELAY_MS = 400

def window_title(window: dict, english: bool) -> str:
    """Localized Codex window label; prefer duration when the name is a stock English title."""
    minutes = window.get("window_minutes")
    name = window.get("name")
    stock = {
        300: ("5 hour", "5 小时"),
        10080: ("Weekly", "每周"),
    }
    if isinstance(minutes, (int, float)) and int(minutes) in stock:
        en, zh = stock[int(minutes)]
        # Use stock title when the provider name is missing or is the English stock form.
        if not (isinstance(name, str) and name.strip()) or name.strip().lower() in {
            en.lower(), "5h", "5 hours", "week", "weekly", "7d", "7 day", "7 days"
        }:
            return en if english else zh
    if isinstance(name, str) and name.strip():
        key = name.strip().lower()
        if key in {"5 hour", "5h", "5 hours"}:
            return "5 hour" if english else "5 小时"
        if key in {"weekly", "week", "7d", "7 day", "7 days"}:
            return "Weekly" if english else "每周"
        return name.strip()
    if isinstance(minutes, (int, float)) and minutes > 0:
        if minutes % 1440 == 0:
            return f"{minutes / 1440:g}d"
        if minutes % 60 == 0:
            return f"{minutes / 60:g}h"
        return f"{minutes:g}m"
    return "Window" if english else "窗口"



def _pair(value, english: bool) -> str:
    if isinstance(value, dict):
        return (value.get("en") if english else value.get("zh")) or ""
    return str(value) if value else ""


def thin_reset(seconds, english: bool = False) -> str:
    """Countdown with a thin space ('1d 8h'); '' when unknown."""
    text = reset_short(seconds, english)
    return text.replace(" ", THIN) if text else ""


def updated_ago(epoch, now: float, english: bool) -> str:
    """'Updated 3 min ago' / '3 分钟前更新'; '' when unknown or in the future."""
    if not valid_epoch(epoch):
        return ""
    age = now - float(epoch)
    if age < 0 or age > 30 * 86400:
        return ""
    if age < 45:
        return "Updated just now" if english else "刚刚更新"
    if age < 3600:
        minutes = max(1, int(age // 60))
        return (f"Updated {minutes} min ago" if english
                else f"{minutes} 分钟前更新")
    if age < 86400:
        hours = max(1, int(age // 3600))
        return (f"Updated {hours} h ago" if english
                else f"{hours} 小时前更新")
    days = max(1, int(age // 86400))
    return (f"Updated {days} d ago" if english
            else f"{days} 天前更新")


def inactive_status(state: dict | None, english: bool) -> str:
    """Grey status when there is no live quota/balance to show."""
    if not isinstance(state, dict):
        return "Scanning" if english else "检测中"
    if state.get("stale"):
        return "Stale" if english else "过期"
    if state.get("session_relogin") or state.get("needs_action"):
        return "Sign-in required" if english else "需登录"
    if state.get("signed_in") is False:
        return "Not signed in" if english else "未登录"
    if state.get("installed") is False:
        return "Not installed" if english else "未安装"
    if state.get("running") is False and state.get("installed") is True:
        # Only when the provider exposes running and it is false.
        return "Not running" if english else "未运行"
    if state.get("state") == "error":
        code = state.get("code")
        if code in {"login_required", "unsupported_account"}:
            return "Sign-in required" if english else "需登录"
        if code in {"not_installed", "invalid_executable"}:
            return "Not installed" if english else "未安装"
        return "Unavailable" if english else "暂不可用"
    return ""


def _reset_line(reset_at, now: float, english: bool) -> str:
    if not valid_epoch(reset_at) or reset_at <= now:
        return ""
    short = thin_reset(reset_at - now, english)
    absolute = reset_full(reset_at, english)
    if short and absolute:
        return f"{short} · {absolute}"
    return short or absolute


def _timestamp(state: dict | None) -> float | None:
    if not isinstance(state, dict):
        return None
    for key in ("fetched_at", "as_of"):
        value = state.get(key)
        if valid_epoch(value):
            return float(value)
    return None


def codex_hover_row(state: dict, language: str = "zh-CN", *, now: float | None = None,
                    icon_path: str | None = None) -> dict:
    """One hover block for Codex from the usage-monitor snapshot."""
    english = language == "en"
    now = time.time() if now is None else now
    lines: list[dict[str, str]] = []
    windows = state.get("windows") if isinstance(state.get("windows"), list) else []
    for window in windows:
        if not isinstance(window, dict):
            continue
        title = window_title(window, english)
        value = window.get("remaining_percent")
        reset = window.get("resets_at")
        expired = valid_epoch(reset) and reset <= now
        # Same validity rules as quota_lines: never invent a percentage.
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and not expired and not state.get("stale")
                 and state.get("state") in {"ready", "loading"})
        parts = [title]
        if valid:
            remaining = f"{max(0, min(100, value)):.0f}%"
            parts.append(f"{remaining} left" if english else f"剩余 {remaining}")
        line = " · ".join(parts)
        if line:
            lines.append({"text": line, "tone": "body"})
        reset_text = _reset_line(reset, now, english) if valid_epoch(reset) and not expired else ""
        if reset_text:
            lines.append({"text": reset_text, "tone": "secondary"})
    if not lines:
        status = inactive_status(state, english)
        if not status:
            if state.get("state") == "loading":
                status = "Reading…" if english else "正在读取…"
            elif state.get("state") == "error":
                status = inactive_status(state, english) or ("Unavailable" if english else "暂不可用")
            else:
                status = "Usage unavailable" if english else "额度暂不可用"
        lines.append({"text": status, "tone": "status"})
    updated = updated_ago(_timestamp(state), now, english)
    if updated:
        lines.append({"text": updated, "tone": "note"})
    return {
        "id": "codex",
        "name": "Codex",
        # Canonical id/name so provider_icon hits the bundled OpenAI art (same as the strip).
        "icon_name": "Codex",
        "icon_path": icon_path or (state.get("install_path") if isinstance(state.get("install_path"), str) else None),
        "lines": lines,
        "inactive": any(line.get("tone") == "status" for line in lines),
    }


def provider_hover_row(state: dict | None, *, provider_id: str, name: str,
                       language: str = "zh-CN", now: float | None = None,
                       icon_path: str | None = None) -> dict:
    """One hover block for a non-Codex provider; only verified facts."""
    english = language == "en"
    now = time.time() if now is None else now
    lines: list[dict[str, str]] = []
    has_quota = False
    if isinstance(state, dict):
        quota = _pair(state.get("quota"), english) or _pair(state.get("headline"), english)
        percent = state.get("quota_percent")
        brief = _pair(state.get("brief"), english)
        # DeepSeek / balance-style: quota text that is not a bare status word.
        status_words = {
            "Not running", "未运行", "Not signed in", "未登录", "Sign-in required", "需登录",
            "Stale", "过期", "Not installed", "未安装", "Scanning", "检测中",
            "Unavailable", "暂不可用", "Reading…", "正在读取…", "Reading", "查询中",
            "Installed", "已安装", "Running", "运行中",
        }
        if quota and quota not in status_words and not state.get("stale"):
            if provider_id == "deepseek" or (isinstance(quota, str) and any(ch in quota for ch in "¥$€£")):
                label = f"Balance {quota}" if english else f"余额 {quota}"
            elif isinstance(percent, (int, float)) and math.isfinite(percent):
                left = f"{percent:.0f}% left" if english else f"剩余 {percent:.0f}%"
                if english and isinstance(brief, str) and brief.endswith("used"):
                    label = f"{brief} · {left}"
                elif not english and isinstance(brief, str) and brief.startswith("已用"):
                    label = f"{brief} · {left}"
                else:
                    label = left
            else:
                label = quota
            lines.append({"text": label, "tone": "body"})
            has_quota = True
        reset_text = _reset_line(state.get("reset_at"), now, english)
        if reset_text and has_quota:
            lines.append({"text": reset_text, "tone": "secondary"})
        if state.get("stale") and has_quota:
            lines.append({"text": "Stale" if english else "过期", "tone": "status"})
    if not has_quota:
        status = inactive_status(state, english)
        if status:
            lines.append({"text": status, "tone": "status"})
        elif isinstance(state, dict) and _pair(state.get("headline"), english):
            lines.append({"text": _pair(state.get("headline"), english), "tone": "body"})
        else:
            lines.append({"text": "Scanning" if english else "检测中", "tone": "status"})
    updated = updated_ago(_timestamp(state), now, english)
    if updated:
        lines.append({"text": updated, "tone": "note"})
    return {
        "id": provider_id,
        "name": name,
        "icon_name": name,
        "icon_path": icon_path,
        "lines": lines,
        "inactive": any(line.get("tone") == "status" for line in lines) and not has_quota,
    }


def build_hover_rows(codex_state: dict | None, provider_states: dict | None,
                     selected: list[str] | tuple[str, ...], *,
                     language: str = "zh-CN", now: float | None = None,
                     names: dict[str, str] | None = None,
                     icon_paths: dict[str, str | None] | None = None,
                     icon_names: dict[str, str] | None = None) -> list[dict]:
    """Rows for every selected app, in selection order.

    ``icon_paths`` / ``icon_names`` feed the same ``provider_icon`` pipeline as
    the taskbar strip (installed app icon, else bundled brand art, else letter).
    """
    now = time.time() if now is None else now
    provider_states = provider_states if isinstance(provider_states, dict) else {}
    names = names or {}
    icon_paths = icon_paths or {}
    icon_names = icon_names or {}
    rows = []
    for pid in selected:
        if not isinstance(pid, str):
            continue
        if pid == "codex":
            if isinstance(codex_state, dict) and codex_state.get("enabled", True):
                rows.append(codex_hover_row(
                    codex_state, language, now=now, icon_path=icon_paths.get("codex")))
            continue
        name = names.get(pid) or pid
        row = provider_hover_row(
            provider_states.get(pid), provider_id=pid, name=name, language=language,
            now=now, icon_path=icon_paths.get(pid))
        # Prefer the registry English name for icon lookup (matches strip/menu).
        row["icon_name"] = icon_names.get(pid) or row.get("icon_name") or name
        rows.append(row)
    return rows


def hover_card_spec(rows: list[dict], language: str = "zh-CN") -> dict:
    """Spec consumed by ``ui_dialogs.render_hover`` / ``show_hover_card``."""
    return {
        "kind": "hover",
        "language": language,
        "rows": [
            {
                "name": row.get("name") or "",
                "icon_name": row.get("icon_name") or row.get("name") or "",
                "icon_path": row.get("icon_path"),
                "lines": list(row.get("lines") or []),
                "inactive": bool(row.get("inactive")),
            }
            for row in rows
            if row.get("name")
        ],
    }


def hover_tooltip_text(rows: list[dict]) -> str:
    """Plain multi-line text for the macOS menu-bar tooltip (natural equivalent)."""
    blocks = []
    for row in rows:
        name = row.get("name") or ""
        parts = [name] if name else []
        for line in row.get("lines") or []:
            text = (line.get("text") or "").strip()
            if text:
                parts.append(text)
        if len(parts) > 1:
            blocks.append(parts[0] + "\n  " + "\n  ".join(parts[1:]))
        elif parts:
            blocks.append(parts[0])
    return "\n".join(blocks)


def card_position(anchor: tuple[int, int, int, int], size: tuple[int, int],
                  work_area: tuple[int, int, int, int], gap: int = 8) -> tuple[int, int]:
    """Place the card above the strip inside the monitor work area."""
    left, top, right, bottom = work_area
    width, height = size
    ax0, ay0, ax1, ay1 = anchor
    x = min(max(left + gap, ax0), max(left + gap, right - width - gap))
    y = ay0 - height - gap
    if y < top:
        y = min(ay1 + gap, bottom - height - gap)
    y = max(top + gap, min(y, bottom - height - gap))
    return x, y


def hover_may_show(*, armed: bool, pointer_over: bool, menu_open: bool,
                   dialog_open: bool, has_spec: bool) -> bool:
    """Whether the Windows hover card is allowed to appear right now."""
    return bool(
        armed
        and pointer_over
        and has_spec
        and not menu_open
        and not dialog_open
    )


def hover_rearm_allowed(*, menu_open: bool, dialog_open: bool) -> bool:
    """Pointer re-enter/move may re-arm only when menu and dialogs are closed."""
    return not bool(menu_open) and not bool(dialog_open)
