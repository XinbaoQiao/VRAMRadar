"""Shared reset-time wording for every usage provider.

Strip / menus: a countdown in days + hours ("6\u592912\u5c0f\u65f6" / "6d 12h"),
hours only below a day ("5\u5c0f\u65f6" / "5h"), minutes below an hour
("45\u5206\u949f" / "45m"); zero parts are dropped ("2\u5929" / "2d").
Tooltip / floating window: the local wall-clock time
("\u91cd\u7f6e: 10\u67085\u65e5 14:00" / "Resets Oct 5 14:00").
"""
from __future__ import annotations

import math
import re
import time

# A strip countdown as produced by reset_short (either language).
RESET_RE = re.compile(r"\d+\u5929(?:\d+\u5c0f\u65f6)?|\d+\u5c0f\u65f6|\d+\u5206\u949f|\d+d(?: \d+h)?|\d+h|\d+m")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def valid_epoch(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def reset_short(seconds, english: bool = False) -> str:
    """Time until a reset; '' when unknown or already past.

    Days and hours are floored (never promises an earlier reset than the
    real one by more than the shown unit); under an hour, minutes are rounded
    up so the last minute reads 1 min rather than 0.
    """
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        return ""
    if seconds < 3600:
        minutes = min(59, max(1, math.ceil(seconds / 60)))
        return f"{minutes}m" if english else f"{minutes}\u5206\u949f"
    total_hours = int(seconds // 3600)
    days, hours = divmod(total_hours, 24)
    if english:
        return " ".join(part for part in (f"{days}d" if days else "", f"{hours}h" if hours else "") if part)
    return (f"{days}\u5929" if days else "") + (f"{hours}\u5c0f\u65f6" if hours else "")


def reset_full(epoch, english: bool) -> str:
    """Local time of the reset, '' when unknown."""
    if not valid_epoch(epoch):
        return ""
    t = time.localtime(epoch)
    clock = f"{t.tm_hour:02d}:{t.tm_min:02d}"
    if english:
        return f"Resets {_MONTHS[t.tm_mon - 1]} {t.tm_mday} {clock}"
    return f"\u91cd\u7f6e: {t.tm_mon}\u6708{t.tm_mday}\u65e5 {clock}"


def strip_parts(quota: str, reset: str, fallback: str) -> tuple[str, str]:
    """(value, reset) for one strip cell: quota and reset each only when
    known; with neither, the provider's status (fallback) stands alone."""
    quota, reset = (quota or "").strip(), (reset or "").strip()
    if quota or reset:
        return quota, reset
    return (fallback or "").strip(), ""