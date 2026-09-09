"""Conservative, session-local detection of frozen remote process clocks."""
from __future__ import annotations


class ProcessTimingTracker:
    def __init__(self) -> None:
        self.observations: dict[tuple[str, str], tuple[float, float, int]] = {}

    def clear(self) -> None:
        self.observations.clear()

    def update(self, payload: dict, now: float, max_gap: float) -> None:
        next_observations = {}
        for process in payload.get("processes", {}).get("active", []):
            # Do not change positive durations, missing metadata, or Slurm jobs.
            if process.get("elapsed_seconds") != 0:
                continue
            identity = process.get("process_identity")
            if not identity:
                process.update(elapsed_seconds=None, started_at=None,
                               timing_status="unverified")
                continue
            key = (str(process.get("pid")), identity)
            first, last, count = self.observations.get(key, (now, now, 0))
            if now < last or now - last > max_gap:
                first, count = now, 0
            count += 1
            next_observations[key] = (first, now, count)
            if count >= 3 and now - first >= 30:
                process.update(elapsed_seconds=None, started_at=None,
                               timing_status="unavailable",
                               observed_running_seconds=int(now - first))
        self.observations = next_observations
