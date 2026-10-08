"""Bounded local usage-trend samples for hover sparklines and urgency sort.

Only real fetched quota values are stored (no secrets, no raw responses).
Crash-safe atomic JSON writes; downsampled to about one point per 30 minutes;
auto-pruned to 30 days. Housekeeping also prunes the store file.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from .storage import atomic_write_text

SCHEMA = 1
SAMPLE_INTERVAL = 30 * 60          # seconds between kept points per series
RETENTION_SECONDS = 30 * 24 * 3600
SPARK_SECONDS = 7 * 24 * 3600
# Show sparkline + 7-day avg only when samples span this long and show real usage.
MIN_SPARK_SPAN_SECONDS = 6 * 3600
MIN_USED_DELTA = 1.0              # used-% points consumed (resets ignored)
HYSTERESIS_POINTS = 5.0           # remaining-% points before reorder
HYSTERESIS_SECONDS = 10 * 60
MAX_POINTS_PER_SERIES = int(RETENTION_SECONDS / SAMPLE_INTERVAL) + 8  # ~1448 + slack

_LOCK = threading.RLock()


def trend_path(cache_dir: Path | str, profile_id: str = "default") -> Path:
    root = Path(cache_dir)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (profile_id or "default"))[:64]
    return root / "usage-trend" / f"{safe}.json"


def _empty() -> dict:
    return {"v": SCHEMA, "series": {}}


def _clamp_pct(value: float) -> float | None:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(max(0.0, min(100.0, value)))


def extract_samples(codex_state: dict | None, provider_states: dict | None,
                    selected: list[str] | tuple[str, ...], *, now: float | None = None
                    ) -> list[tuple[str, float, str]]:
    """(series_key, value, kind) for every selected app with a real fetched metric.

    kind is ``used`` (0–100 used %) or ``balance`` (DeepSeek wallet total).
    """
    now = time.time() if now is None else float(now)
    selected = [x for x in selected if isinstance(x, str)]
    out: list[tuple[str, float, str]] = []
    states = provider_states if isinstance(provider_states, dict) else {}

    if "codex" in selected and isinstance(codex_state, dict) and not codex_state.get("stale"):
        windows = codex_state.get("windows") if isinstance(codex_state.get("windows"), list) else []
        for window in windows:
            if not isinstance(window, dict):
                continue
            rem = window.get("remaining_percent")
            if not isinstance(rem, (int, float)) or not math.isfinite(rem):
                continue
            used = _clamp_pct(100.0 - float(rem))
            if used is None:
                continue
            minutes = window.get("window_minutes")
            if isinstance(minutes, (int, float)) and minutes > 0:
                key = f"codex:w{int(minutes)}"
            else:
                name = str(window.get("name") or "window").strip().lower()[:32] or "window"
                key = f"codex:{name}"
            out.append((key, used, "used"))

    for pid in selected:
        if pid == "codex":
            continue
        state = states.get(pid)
        if not isinstance(state, dict) or state.get("stale"):
            continue
        if pid == "deepseek":
            bal = state.get("balance")
            amount = None
            if isinstance(bal, dict):
                for currency in ("CNY", "USD", "USDT"):
                    raw = bal.get(currency)
                    if raw is None:
                        continue
                    try:
                        amount = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(amount) and amount >= 0:
                        break
                    amount = None
                if amount is None:
                    for raw in bal.values():
                        try:
                            amount = float(raw)
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(amount) and amount >= 0:
                            break
                        amount = None
            elif isinstance(bal, (int, float)) and math.isfinite(bal) and bal >= 0:
                amount = float(bal)
            if amount is not None:
                out.append((f"{pid}:balance", float(amount), "balance"))
            continue
        rem = state.get("quota_percent")
        if isinstance(rem, (int, float)) and math.isfinite(rem):
            used = _clamp_pct(100.0 - float(rem))
            if used is not None:
                out.append((f"{pid}:quota", used, "used"))
    return out


class TrendStore:
    """Thread-safe on-disk trend samples under the app cache directory."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = _empty()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("v") != SCHEMA:
                self._data = _empty()
                return
            series = raw.get("series")
            if not isinstance(series, dict):
                self._data = _empty()
                return
            cleaned = {}
            for key, entry in series.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                points = entry.get("points")
                kind = entry.get("kind") or "used"
                if kind not in {"used", "balance"} or not isinstance(points, list):
                    continue
                kept = []
                for item in points:
                    if (isinstance(item, (list, tuple)) and len(item) >= 2
                            and isinstance(item[0], (int, float)) and isinstance(item[1], (int, float))
                            and math.isfinite(item[0]) and math.isfinite(item[1])):
                        kept.append([float(item[0]), float(item[1])])
                cleaned[key] = {"kind": kind, "points": kept}
            self._data = {"v": SCHEMA, "series": cleaned}
        except FileNotFoundError:
            self._data = _empty()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._data = _empty()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self._data, separators=(",", ":"), ensure_ascii=True))

    def prune(self, *, now: float | None = None, retention: float = RETENTION_SECONDS) -> int:
        """Drop points older than retention; return number of points removed."""
        now = time.time() if now is None else float(now)
        cutoff = now - retention
        removed = 0
        with self._lock:
            for entry in self._data["series"].values():
                before = len(entry["points"])
                entry["points"] = [p for p in entry["points"] if p[0] >= cutoff]
                if len(entry["points"]) > MAX_POINTS_PER_SERIES:
                    entry["points"] = entry["points"][-MAX_POINTS_PER_SERIES:]
                removed += before - len(entry["points"])
            # Drop empty series
            self._data["series"] = {k: v for k, v in self._data["series"].items() if v["points"]}
            try:
                self._save()
            except OSError:
                pass
        return removed

    def record(self, samples: list[tuple[str, float, str]], *, now: float | None = None,
               interval: float = SAMPLE_INTERVAL) -> int:
        """Append downsampled samples. Returns number of new points written."""
        if not samples:
            return 0
        now = time.time() if now is None else float(now)
        added = 0
        changed = False
        with self._lock:
            series = self._data["series"]
            for key, value, kind in samples:
                if not isinstance(key, str) or not key or kind not in {"used", "balance"}:
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    continue
                entry = series.get(key)
                if entry is None:
                    entry = {"kind": kind, "points": []}
                    series[key] = entry
                    changed = True
                if entry["kind"] != kind:
                    entry["kind"] = kind
                    changed = True
                points = entry["points"]
                if points and (now - points[-1][0]) < interval:
                    # Refresh the bucket's value in place (latest fetch wins).
                    if points[-1][1] != float(value):
                        points[-1] = [points[-1][0], float(value)]
                        changed = True
                    continue
                points.append([now, float(value)])
                added += 1
                changed = True
                if len(points) > MAX_POINTS_PER_SERIES:
                    entry["points"] = points[-MAX_POINTS_PER_SERIES:]
            cutoff = now - RETENTION_SECONDS
            for entry in series.values():
                kept = [p for p in entry["points"] if p[0] >= cutoff]
                if len(kept) != len(entry["points"]):
                    entry["points"] = kept
                    changed = True
            # The strip calls this every second; rewrite the file only when a
            # point or value actually changed (it used to be rewritten each tick).
            if changed:
                try:
                    self._save()
                except OSError:
                    pass
        return added

    def series_points(self, key: str, *, since: float | None = None,
                      now: float | None = None) -> list[tuple[float, float]]:
        now = time.time() if now is None else float(now)
        since = (now - SPARK_SECONDS) if since is None else float(since)
        with self._lock:
            entry = self._data["series"].get(key)
            if not entry:
                return []
            return [(t, v) for t, v in entry["points"] if t >= since]

    def provider_spark_points(self, provider_id: str, *, now: float | None = None
                              ) -> list[tuple[float, float]]:
        """Best series for a model sparkline (7-day window)."""
        now = time.time() if now is None else float(now)
        since = now - SPARK_SECONDS
        with self._lock:
            keys = [k for k in self._data["series"] if k == provider_id or k.startswith(provider_id + ":")]
            best: list[tuple[float, float]] = []
            # Prefer shorter Codex window, then quota, then balance.
            def rank(key: str) -> tuple:
                if key.startswith("codex:w"):
                    try:
                        return (0, int(key.split("w", 1)[1]))
                    except ValueError:
                        return (0, 10**9)
                if key.endswith(":quota"):
                    return (1, 0)
                if key.endswith(":balance"):
                    return (2, 0)
                return (3, 0)
            for key in sorted(keys, key=rank):
                pts = [(t, v) for t, v in self._data["series"][key]["points"] if t >= since]
                if len(pts) >= len(best):
                    best = pts
            return best

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))


def sparkline_path(points: list[tuple[float, float]], width: int, height: int,
                   pad: int = 1) -> list[tuple[float, float]]:
    """Map (t, v) samples to pixel coordinates inside a sparkline box."""
    if len(points) < 2 or width < 2 or height < 2:
        return []
    ts = [p[0] for p in points]
    vs = [p[1] for p in points]
    t0, t1 = min(ts), max(ts)
    v0, v1 = min(vs), max(vs)
    if t1 <= t0:
        t1 = t0 + 1.0
    if abs(v1 - v0) < 1e-9:
        v1 = v0 + 1.0
    inner_w = max(1, width - 2 * pad)
    inner_h = max(1, height - 2 * pad)
    out = []
    for t, v in points:
        x = pad + (t - t0) / (t1 - t0) * inner_w
        # Higher used%/balance draws lower? Prefer "usage up = line up" for used;
        # normalize so max value is at top of chart.
        y = pad + (1.0 - (v - v0) / (v1 - v0)) * inner_h
        out.append((x, y))
    return out


def remaining_metric(provider_id: str, codex_state: dict | None, provider_states: dict | None
                     ) -> float | None:
    """Remaining % (0-100) for urgency; None when unknown/inactive/non-percent."""
    if provider_id == "codex":
        if not isinstance(codex_state, dict) or codex_state.get("stale"):
            return None
        windows = codex_state.get("windows") if isinstance(codex_state.get("windows"), list) else []
        best = None
        for window in windows:
            if not isinstance(window, dict):
                continue
            rem = window.get("remaining_percent")
            if isinstance(rem, (int, float)) and math.isfinite(rem):
                rem = float(max(0.0, min(100.0, rem)))
                best = rem if best is None else min(best, rem)
        return best
    state = (provider_states or {}).get(provider_id) if isinstance(provider_states, dict) else None
    if not isinstance(state, dict) or state.get("stale"):
        return None
    rem = state.get("quota_percent")
    if isinstance(rem, (int, float)) and math.isfinite(rem):
        return float(max(0.0, min(100.0, rem)))
    return None


def _balance_amount(state: dict) -> float | None:
    """Wallet / fixed-amount total when present (DeepSeek or similar)."""
    bal = state.get("balance")
    if isinstance(bal, dict):
        for currency in ("CNY", "USD", "USDT"):
            raw = bal.get(currency)
            if raw is None:
                continue
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(amount):
                return amount
        for raw in bal.values():
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(amount):
                return amount
    elif isinstance(bal, (int, float)) and math.isfinite(bal):
        return float(bal)
    return None


def _is_inactive(provider_id: str, codex_state: dict | None, provider_states: dict | None) -> bool:
    """True for stale / not installed / signed-out / not-running without live quota."""
    if provider_id == "codex":
        if not isinstance(codex_state, dict):
            return True
        if codex_state.get("stale"):
            return True
        return False
    state = (provider_states or {}).get(provider_id) if isinstance(provider_states, dict) else None
    if not isinstance(state, dict):
        return True
    if state.get("stale"):
        return True
    if state.get("installed") is False:
        return True
    if state.get("signed_in") is False:
        return True
    if state.get("running") is False:
        if remaining_metric(provider_id, codex_state, provider_states) is not None:
            return False
        if _balance_amount(state) is not None:
            return False
        return True
    return False


# Urgency tiers (lower = earlier in strip / hover when sort-by-urgency is on):
# 1 = resetting percentage quotas (Codex, Grok, Kimi windows)
# 2 = balance / fixed-amount without a reset window (DeepSeek wallet, ...)
# 3 = available-only / unknown
# 4 = inactive (stale / not running / signed-out / not installed)
TIER_PERCENT = 1
TIER_BALANCE = 2
TIER_UNKNOWN = 3
TIER_INACTIVE = 4


def urgency_tier(provider_id: str, codex_state: dict | None, provider_states: dict | None) -> int:
    """Classify a provider into an urgency sort tier (1-4)."""
    if _is_inactive(provider_id, codex_state, provider_states):
        return TIER_INACTIVE
    rem = remaining_metric(provider_id, codex_state, provider_states)
    if rem is not None:
        return TIER_PERCENT
    state = None
    if provider_id == "codex":
        state = codex_state if isinstance(codex_state, dict) else None
    elif isinstance(provider_states, dict):
        state = provider_states.get(provider_id)
    if isinstance(state, dict) and _balance_amount(state) is not None:
        return TIER_BALANCE
    return TIER_UNKNOWN


def projected_remaining(provider_id: str, remaining: float | None, store: TrendStore | None,
                        codex_state: dict | None, *, now: float | None = None) -> float | None:
    """If recent used-% rate is known, project remaining at next reset; else remaining."""
    if remaining is None:
        return None
    now = time.time() if now is None else float(now)
    if store is None:
        return remaining
    pts = store.provider_spark_points(provider_id, now=now)
    if len(pts) < 3:
        return remaining
    t0, v0 = pts[0]
    t1, v1 = pts[-1]
    if t1 <= t0 or v1 <= v0:
        return remaining
    rate = (v1 - v0) / (t1 - t0)  # used % / sec
    if rate <= 1e-9:
        return remaining
    reset_in = None
    if provider_id == "codex" and isinstance(codex_state, dict):
        windows = codex_state.get("windows") if isinstance(codex_state.get("windows"), list) else []
        for window in windows:
            if not isinstance(window, dict):
                continue
            secs = window.get("reset_in_seconds")
            if isinstance(secs, (int, float)) and math.isfinite(secs) and secs > 0:
                reset_in = float(secs) if reset_in is None else min(reset_in, float(secs))
    if reset_in is None:
        return remaining
    used_now = 100.0 - remaining
    used_at_reset = used_now + rate * reset_in
    return float(max(0.0, min(100.0, 100.0 - used_at_reset)))


def urgency_scores(selected: list[str] | tuple[str, ...], codex_state: dict | None,
                   provider_states: dict | None, store: TrendStore | None = None,
                   *, now: float | None = None) -> dict[str, float | None]:
    """Map provider id -> within-tier urgency score (lower = more urgent); None = n/a.

    Callers that need tier-aware ordering should also use `urgency_tiers`.
    Percent tiers use remaining % (optionally projected); balance tiers use the
    wallet amount; unknown/inactive leave score as None.
    """
    now = time.time() if now is None else float(now)
    scores: dict[str, float | None] = {}
    for pid in selected:
        tier = urgency_tier(pid, codex_state, provider_states)
        if tier == TIER_PERCENT:
            rem = remaining_metric(pid, codex_state, provider_states)
            proj = projected_remaining(pid, rem, store, codex_state, now=now)
            scores[pid] = rem if proj is None else proj
        elif tier == TIER_BALANCE:
            state = (provider_states or {}).get(pid) if isinstance(provider_states, dict) else None
            scores[pid] = _balance_amount(state) if isinstance(state, dict) else None
        else:
            scores[pid] = None
    return scores


def urgency_tiers(selected: list[str] | tuple[str, ...], codex_state: dict | None,
                  provider_states: dict | None) -> dict[str, int]:
    """Map provider id -> urgency tier (1-4)."""
    return {pid: urgency_tier(pid, codex_state, provider_states) for pid in selected}


def order_by_urgency(selected: list[str] | tuple[str, ...], scores: dict[str, float | None],
                     previous: list[str] | None = None, *, previous_scores: dict[str, float | None] | None = None,
                     last_reorder_at: float = 0.0, now: float | None = None,
                     min_delta: float = HYSTERESIS_POINTS, min_interval: float = HYSTERESIS_SECONDS,
                     tiers: dict[str, int] | None = None,
                     previous_tiers: dict[str, int] | None = None,
                     ) -> tuple[list[str], bool, float]:
    """Stable urgency order with hysteresis and hard tiers.

    Tier order (always): resetting % -> balance/fixed -> available/unknown -> inactive.
    Within a tier, lower score first; None scores sort last inside the tier.
    Returns (order, did_reorder, reorder_time).
    """
    now = time.time() if now is None else float(now)
    selected = [x for x in selected if isinstance(x, str)]
    tiers = tiers or {}
    previous_tiers = previous_tiers or {}

    def tier_of(pid: str) -> int:
        t = tiers.get(pid)
        if isinstance(t, int):
            return t
        return TIER_INACTIVE if scores.get(pid) is None else TIER_PERCENT

    def sort_key(pid: str):
        score = scores.get(pid)
        return (
            tier_of(pid),
            1 if score is None else 0,
            0.0 if score is None else float(score),
            selected.index(pid),
            pid,
        )

    ideal = sorted(selected, key=sort_key)
    if not previous:
        return ideal, True, now
    if (now - last_reorder_at) < min_interval:
        kept = [pid for pid in previous if pid in selected]
        for pid in ideal:
            if pid not in kept:
                kept.append(pid)
        return kept, False, last_reorder_at
    if previous_scores is None:
        previous_scores = {}
    changed_membership = [pid for pid in selected if pid not in previous] or [
        pid for pid in previous if pid not in selected]
    if changed_membership:
        return ideal, True, now
    for pid in selected:
        cur = tier_of(pid)
        prev_t = previous_tiers.get(pid)
        if prev_t is not None and int(prev_t) != cur:
            return ideal, True, now
    needs = False
    pos = {pid: i for i, pid in enumerate(previous) if pid in selected}
    for i, pid in enumerate(ideal):
        old = pos.get(pid)
        if old is None:
            needs = True
            break
        if abs(old - i) == 0:
            continue
        old_score = previous_scores.get(pid)
        new_score = scores.get(pid)
        if old_score is None or new_score is None:
            if old_score != new_score:
                needs = True
                break
            continue
        if abs(float(old_score) - float(new_score)) >= min_delta or abs(old - i) >= 2:
            needs = True
            break
    if not needs and ideal != [pid for pid in previous if pid in selected]:
        for a, b in zip([p for p in previous if p in selected], ideal):
            if a == b:
                continue
            if tier_of(a) != tier_of(b):
                needs = True
                break
            sa, sb = scores.get(a), scores.get(b)
            if sa is None and sb is None:
                continue
            if sa is None or sb is None:
                needs = True
                break
            if abs(float(sa) - float(sb)) >= min_delta:
                needs = True
                break
        else:
            kept = [pid for pid in previous if pid in selected]
            return kept, False, last_reorder_at
    if needs:
        return ideal, True, now
    kept = [pid for pid in previous if pid in selected]
    for pid in ideal:
        if pid not in kept:
            kept.append(pid)
    return kept, False, last_reorder_at


def balance_daily_burn(points: list[tuple[float, float]]) -> float | None:
    """Average ¥/day consumed from declining segments only (top-ups ignored).

    Returns None when there is not enough decreasing history to be meaningful.
    """
    if len(points) < 4:
        return None
    ordered = sorted((float(t), float(v)) for t, v in points)
    dropped = 0.0
    for (t0, v0), (t1, v1) in zip(ordered, ordered[1:]):
        if t1 <= t0:
            continue
        if v1 < v0:
            dropped += (v0 - v1)
        # upward jump = top-up / refund; ignore
    span_days = (ordered[-1][0] - ordered[0][0]) / 86400.0
    if span_days < 1.5 or dropped <= 0:
        return None
    return dropped / span_days


def trend_worth_showing(points: list[tuple[float, float]], kind: str = "used") -> bool:
    """True when sparkline + 7-day avg should appear for this series.

    Requires a span of at least `MIN_SPARK_SPAN_SECONDS` and real usage in
    that window: used-% rose by at least `MIN_USED_DELTA` (reset drops are
    ignored), or a balance series decreased. Flat / reset-only / short history
    stay hidden.
    """
    if not isinstance(points, list) or len(points) < 2:
        return False
    ordered = sorted((float(t), float(v)) for t, v in points
                     if isinstance(t, (int, float)) and isinstance(v, (int, float))
                     and math.isfinite(t) and math.isfinite(v))
    if len(ordered) < 2:
        return False
    if (ordered[-1][0] - ordered[0][0]) < MIN_SPARK_SPAN_SECONDS:
        return False
    if kind == "balance":
        for (_t0, v0), (_t1, v1) in zip(ordered, ordered[1:]):
            if v1 < v0:
                return True
        return False
    # used %: sum positive steps (consumption); ignore downward resets.
    rise = 0.0
    for (_t0, v0), (_t1, v1) in zip(ordered, ordered[1:]):
        if v1 > v0:
            rise += (v1 - v0)
    return rise >= MIN_USED_DELTA


def avg_summary(points: list[tuple[float, float]], *, english: bool, kind: str = "used") -> str:
    """Compact 7-day summary fragment (no leading 'Updated'); '' when not meaningful."""
    if not trend_worth_showing(points, kind):
        return ""
    if kind == "balance":
        burn = balance_daily_burn(points)
        if burn is None:
            return ""
        text = f"{burn:.2f}"
        return (f"7 d avg spend ¥{text}/day" if english else f"7 日均消耗 ¥{text}/天")
    if len(points) < 4:
        return ""
    values = [v for _, v in points]
    avg = sum(values) / len(values)
    text = f"{avg:.0f}%"
    return (f"7 d avg used {text}" if english else f"7 日均已用 {text}")


def merge_note_with_avg(lines: list[dict], avg: str) -> None:
    """Attach ``avg`` to the existing 'Updated…' note, or append as a note."""
    if not avg:
        return
    for line in lines:
        if line.get("tone") == "note" and isinstance(line.get("text"), str) and line["text"].strip():
            line["text"] = f"{line['text']} · {avg}"
            return
    lines.append({"text": avg, "tone": "note"})

def prune_trend_files(cache: Path, *, now: float | None = None) -> int:
    """Housekeeping entry: prune every trend file under cache/usage-trend."""
    root = Path(cache) / "usage-trend"
    if not root.is_dir():
        return 0
    total = 0
    for path in root.glob("*.json"):
        try:
            total += TrendStore(path).prune(now=now)
        except Exception:
            continue
    return total
