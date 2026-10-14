"""
Live statistics for a running JMeter test.

JMeter appends one CSV row per sample to the results file (autoflush is
turned on for this), so the file can be tailed while the test runs. Each
poll reads only the bytes added since the previous one and folds them into
per-second buckets and per-label totals.
"""

from __future__ import annotations

import csv
import io
import threading
import time
from collections import deque
from pathlib import Path


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


class LiveRun:
    def __init__(self, jtl_path: str | Path, config: dict, expected_requests: int | None = None):
        self.jtl = Path(jtl_path)
        self.config = config
        self.expected_requests = expected_requests
        self.started = time.time()
        self.status = "starting"          # starting | running | stopping | finishing | done | failed
        self.error = ""
        self._lock = threading.Lock()
        self._offset = 0
        self._partial = ""
        self._columns: list[str] | None = None
        self._first_ts: int | None = None
        self._last_sample_wall: float | None = None
        self._buckets: dict[int, dict] = {}
        self._labels: dict[str, dict] = {}
        self._recent: deque = deque(maxlen=25)
        self._all_ms: list[int] = []
        self.total = 0
        self.errors = 0
        self.users = 0

    # ------------------------------------------------------------------
    def poll(self) -> None:
        """Read rows appended since the last poll."""
        with self._lock:
            try:
                size = self.jtl.stat().st_size
            except FileNotFoundError:
                return
            if size < self._offset:          # file was recreated
                self._offset, self._partial, self._columns = 0, "", None
            if size == self._offset:
                return
            with open(self.jtl, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
            text = self._partial + chunk
            lines = text.split("\n")
            self._partial = lines.pop()       # last piece may be an incomplete row
            if not lines:
                return
            for row in csv.reader(io.StringIO("\n".join(lines))):
                if not row:
                    continue
                if self._columns is None:
                    if row[0] == "timeStamp":
                        self._columns = row
                        continue
                    self._columns = ["timeStamp", "elapsed", "label", "responseCode", "responseMessage",
                                     "threadName", "dataType", "success", "failureMessage", "bytes",
                                     "sentBytes", "grpThreads", "allThreads"]
                self._add(dict(zip(self._columns, row)))

    def _add(self, r: dict) -> None:
        try:
            ts = int(r.get("timeStamp", "0"))
            ms = int(r.get("elapsed", "0"))
        except ValueError:
            return
        ok = r.get("success", "true") == "true"
        label = r.get("label", "?")
        try:
            users = int(r.get("allThreads") or 0)
        except ValueError:
            users = 0
        if self._first_ts is None:
            self._first_ts = ts
            if self.status == "starting":
                self.status = "running"
        sec = max(0, (ts - self._first_ts) // 1000)
        b = self._buckets.setdefault(sec, {"n": 0, "err": 0, "ms": [], "users": 0})
        b["n"] += 1
        b["err"] += 0 if ok else 1
        b["ms"].append(ms)
        b["users"] = max(b["users"], users)
        lab = self._labels.setdefault(label, {"n": 0, "err": 0, "sum": 0, "max": 0, "ms": deque(maxlen=200)})
        lab["n"] += 1
        lab["err"] += 0 if ok else 1
        lab["sum"] += ms
        lab["max"] = max(lab["max"], ms)
        lab["ms"].append(ms)
        self._recent.append({
            "t": round((ts - self._first_ts) / 1000, 1), "label": label, "code": r.get("responseCode", ""),
            "ms": ms, "ok": ok, "thread": r.get("threadName", ""),
        })
        self._all_ms.append(ms)
        self.total += 1
        self.errors += 0 if ok else 1
        self.users = users
        self._last_sample_wall = time.time()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        self.poll()
        with self._lock:
            now = time.time()
            last_sec = max(self._buckets) if self._buckets else -1
            timeline = []
            for sec in range(0, last_sec + 1):
                b = self._buckets.get(sec)
                if b is None:
                    timeline.append({"t": sec, "rps": 0, "errors": 0, "avg_ms": None, "p95_ms": None, "users": None})
                    continue
                timeline.append({
                    "t": sec, "rps": b["n"], "errors": b["err"],
                    "avg_ms": round(sum(b["ms"]) / len(b["ms"]), 1), "p95_ms": _pct(b["ms"], 95),
                    "users": b["users"],
                })
            # The newest second is still filling up — "current" figures use the last complete ones
            done = [p for p in timeline[:-1] if p["rps"]] or timeline
            window = done[-3:]
            rps_now = round(sum(p["rps"] for p in window) / len(window), 1) if window else 0
            window_ms = [ms for p in window for ms in self._buckets.get(p["t"], {}).get("ms", [])]
            status = self.status
            idle = now - self._last_sample_wall if self._last_sample_wall else 0
            if status == "running" and idle > 3:
                status = "finishing"           # samples stopped; JMeter is writing the HTML report
            labels = sorted(self._labels.items(), key=lambda kv: -kv[1]["n"])[:15]
            return {
                "status": status,
                "error": self.error,
                "elapsed_s": round(now - self.started, 1),
                "test_elapsed_s": round((self._last_sample_wall or now) - self.started, 1) if self._first_ts else 0,
                "config": self.config,
                "expected_requests": self.expected_requests,
                "totals": {
                    "requests": self.total,
                    "errors": self.errors,
                    "error_pct": round(100 * self.errors / self.total, 2) if self.total else 0,
                    "avg_ms": round(sum(self._all_ms) / len(self._all_ms), 1) if self._all_ms else 0,
                    "p95_ms": _pct(self._all_ms, 95),
                    "rps_now": rps_now,
                    "avg_ms_now": round(sum(window_ms) / len(window_ms), 1) if window_ms else 0,
                    "active_users": self.users if status in ("running", "starting") else 0,
                },
                "timeline": timeline[-600:],
                "labels": [
                    {"label": k, "requests": v["n"], "errors": v["err"],
                     "avg_ms": round(v["sum"] / v["n"], 1), "p95_ms": _pct(list(v["ms"]), 95), "max_ms": v["max"]}
                    for k, v in labels
                ],
                "recent": list(self._recent)[-12:][::-1],
            }
