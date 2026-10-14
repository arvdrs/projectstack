"""
Steady-state detection from the active-thread timeline.

The steady state is the longest stretch where the number of active threads stays
within 5% of its peak. Its edges are trimmed (10% of its length, at most 60 s
each side) so the last ramp-up threads warming up and the first ramp-down
threads stopping are not counted. Stepping profiles therefore get their top
step; every level the load held for a minute or more is listed too, for the
saturation detector.

Users come from the allThreads column; without it, from the number of distinct
thread names per 10 seconds (an approximation that undercounts long think times).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from projectstack.results.db import ResultsDB
from projectstack.results.window import Window

PEAK_TOLERANCE = 0.05     # within 5% of peak users
MAX_DIP_S = 5             # dips shorter than this do not end the plateau
MAX_TRIM_S = 60
MIN_PLATEAU_S = 60
MIN_PLATEAU_SHARE = 0.2   # of the run, below which the run has no usable steady state
LEVEL_MIN_S = 60


@dataclass
class Steady:
    window: Window
    method: str                       # allThreads | threadName | full-run fallback
    plateau_users: int
    peak_users: int
    levels: list[dict] = field(default_factory=list)   # [{users, start_ms, end_ms}]
    note: str = ""

    def as_dict(self) -> dict:
        return {**self.window.as_dict(), "method": self.method, "plateau_users": self.plateau_users,
                "peak_users": self.peak_users, "levels": self.levels, "note": self.note}


def users_series(db: ResultsDB, run_id: str, from_threads: bool) -> tuple[list[tuple[int, int]], int]:
    """[(bucket start ms, active users)] and the bucket size in seconds."""
    if from_threads:
        rows = db.rows(
            f"""SELECT toUnixTimestamp(toStartOfInterval(ts, INTERVAL 10 SECOND)) * 1000, uniqExact(thread_name)
                FROM {db.db}.samples WHERE run_id = {{run:String}} GROUP BY 1 ORDER BY 1""", {"run": run_id})
        return [(int(t), int(u)) for t, u in rows], 10
    rows = db.rows(
        f"""SELECT toUnixTimestamp(toStartOfSecond(ts)) * 1000, max(all_threads)
            FROM {db.db}.samples WHERE run_id = {{run:String}} GROUP BY 1 ORDER BY 1""", {"run": run_id})
    return [(int(t), int(u)) for t, u in rows], 1


def detect(series: list[tuple[int, int]], step_s: int, bounds: dict, method: str) -> Steady:
    """Find the steady state in a users-over-time series (see module docstring)."""
    run_s = (bounds["end_ms"] - bounds["start_ms"]) / 1000
    if not series:
        return _fallback(bounds, method, 0, "no samples")
    filled = _fill(series, step_s)
    peak = max(u for _, u in filled)
    if peak <= 0:
        return _fallback(bounds, method, 0, "the file has no active-thread information")
    threshold = peak * (1 - PEAK_TOLERANCE)

    best, start, dip = None, None, 0
    for i, (t, u) in enumerate(filled):
        if u >= threshold:
            if start is None:
                start = i
            dip = 0
            end = i
        elif start is not None:
            dip += step_s
            if dip >= MAX_DIP_S:
                best = _longer(best, (start, end))
                start, dip = None, 0
    if start is not None:
        best = _longer(best, (start, end))

    a, b = best
    plateau_start = filled[a][0]
    plateau_end = filled[b][0] + step_s * 1000
    length_s = (plateau_end - plateau_start) / 1000
    levels = _levels(filled, step_s)
    if length_s < max(MIN_PLATEAU_S, MIN_PLATEAU_SHARE * run_s):
        return _fallback(bounds, method, peak,
                         f"load never held near its peak of {peak} users for long enough "
                         f"(longest stretch {length_s:.0f}s)", levels)
    trim_ms = int(min(MAX_TRIM_S, 0.1 * length_s) * 1000)
    start_ms = max(plateau_start + trim_ms, bounds["start_ms"])
    end_ms = min(plateau_end - trim_ms, bounds["last_ts_ms"] + 1)
    plateau_users = round(sorted(u for _, u in filled[a:b + 1])[(b - a) // 2])
    return Steady(Window(start_ms, end_ms, "auto", (end_ms - start_ms) / 1000), method, plateau_users, peak, levels,
                  f"{peak} users held from t+{(plateau_start - bounds['start_ms']) / 1000:.0f}s to "
                  f"t+{(plateau_end - bounds['start_ms']) / 1000:.0f}s; {trim_ms / 1000:.0f}s trimmed at each end")


def _fill(series: list[tuple[int, int]], step_s: int) -> list[tuple[int, int]]:
    """Carry the last value through buckets with no samples (threads are still active)."""
    out, step = [], step_s * 1000
    for t, u in series:
        while out and t - out[-1][0] > step:
            out.append((out[-1][0] + step, out[-1][1]))
        out.append((t, u))
    return out


def _longer(best, cand):
    return cand if best is None or (cand[1] - cand[0]) > (best[1] - best[0]) else best


def _levels(filled: list[tuple[int, int]], step_s: int) -> list[dict]:
    """Load levels held for at least a minute (users within 2%)."""
    levels, i = [], 0
    while i < len(filled):
        j, u0 = i, filled[i][1]
        while j + 1 < len(filled) and abs(filled[j + 1][1] - u0) <= max(1, 0.02 * u0):
            j += 1
        if (j - i + 1) * step_s >= LEVEL_MIN_S and u0 > 0:
            levels.append({"users": u0, "start_ms": filled[i][0], "end_ms": filled[j][0] + step_s * 1000})
        i = j + 1
    return levels


def _fallback(bounds: dict, method: str, peak: int, why: str, levels: list | None = None) -> Steady:
    from projectstack.results.window import full_run
    return Steady(full_run(bounds["start_ms"], bounds["last_ts_ms"], bounds["end_ms"]), "full-run fallback",
                  peak, peak, levels or [], f"No steady state found ({why}); statistics cover the full run")


def detect_for_run(db: ResultsDB, run_id: str, bounds: dict, from_threads: bool) -> Steady:
    series, step = users_series(db, run_id, from_threads)
    return detect(series, step, bounds, "threadName" if from_threads else "allThreads")
