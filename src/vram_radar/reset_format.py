"""Shared reset-time wording for every usage provider.

Strip: a short countdown -- hours below two days ("33.9h"), days after
("3.2d").  Tooltip / floating window: the local wall-clock time
("\u91cd\u7f6e: 10\u67085\u65e5 14:00" / "Resets Oct 5 14:00").
"""
from __future__ import annotations

import math
import re
import time

DAY_AFTER_HOURS = 48
# A strip countdown as produced by reset_short (also legacy "47.6h").
RESET_RE = re.compile(r"<?\d+(?:\.\d)?[hd]")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def valid_epoch(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def reset_short(seconds) -> str:
    """'<0.1h', '33.9h' (< 48 h) or '3.2d'; '' when unknown or past."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        return ""
    hours = seconds / 3600
    if hours < 0.1:
        return "<0.1h"
    if hours < DAY_AFTER_HOURS:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


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