"""
Detectors: deterministic checks that turn a run's metrics into findings.

Each detector receives a Context and returns findings with exact time windows,
affected labels and the evidence values it used. Detectors decide; the report's
LLM only explains what they found. A detector that lacks the data it needs
(for example no Connect column) reports why it was skipped instead of guessing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from projectstack.results.db import ResultsDB


@dataclass
class Thresholds:
    """Detector sensitivity. Defaults favour few, solid findings over many weak ones."""
    latency_rel_increase: float = 0.25      # p95 change point: at least +25% …
    latency_abs_increase_ms: float = 50     # … and +50 ms
    error_rate_abs_increase: float = 2.0    # error-rate change point: +2 percentage points …
    error_rate_rel_increase: float = 1.0    # … and doubled
    burst_factor: float = 3.0               # error burst: bucket error % ≥ 3× the typical rate …
    burst_min_increase: float = 5.0         # … and ≥ typical + 5 points
    burst_min_errors: int = 5               # … with at least 5 failed samples
    burst_max_share: float = 0.3            # longer than 30% of the run = a level change, not a burst
    saturation_efficiency: float = 0.25     # throughput gains < 25% of the user increase …
    saturation_latency_rise: float = 0.25   # … while p95 rises ≥ 25%
    connect_rel_increase: float = 1.0       # Connect time at least doubles …
    connect_abs_increase_ms: float = 20     # … and rises ≥ 20 ms
    injector_tps_drop: float = 0.2          # throughput ≥ 20% below the steady median …
    injector_latency_band: float = 0.15     # … while average latency stays within ±15%
    min_bucket_share: float = 0.2           # buckets with < 20% of the median sample count are ignored

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "Thresholds":
        import json
        overrides = json.loads(settings.get("results_detectors") or "{}")
        known = {k: v for k, v in overrides.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Finding:
    type: str                         # sla_breach | latency_change | error_rate_change | saturation |
                                      # error_burst | injector_connect | injector_throughput | regression |
                                      # correlation
    severity: str                     # critical | warning | info
    title: str
    detail: str
    start_ms: int | None = None
    end_ms: int | None = None
    labels: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, run_start_ms: int) -> dict[str, Any]:
        d = asdict(self)
        if self.start_ms is not None:
            d["start_offset_s"] = round((self.start_ms - run_start_ms) / 1000)
        if self.end_ms is not None:
            d["end_offset_s"] = round((self.end_ms - run_start_ms) / 1000)
        return d


@dataclass
class Context:
    db: ResultsDB
    run_id: str
    bounds: dict[str, Any]
    kinds: dict[str, str]
    metrics: dict[str, Any]           # from metrics.compute (stats cover the steady window)
    steady: dict[str, Any]
    columns: list[str]
    sla: dict | None
    thresholds: Thresholds
    extra: dict[str, Any] = field(default_factory=dict)   # detector results others need (e.g. the SLA result)

    @property
    def points(self) -> list[dict]:
        return self.metrics["timeline"]["points"]

    @property
    def bucket_ms(self) -> int:
        return self.metrics["timeline"]["bucket_s"] * 1000

    @property
    def requests(self) -> list[str]:
        return sorted(l for l, k in self.kinds.items() if k == "request")

    def dense_points(self) -> list[dict]:
        """Timeline buckets with enough samples to be meaningful (drops thin edge buckets)."""
        counts = sorted(p["samples"] for p in self.points)
        if not counts:
            return []
        floor = counts[len(counts) // 2] * self.thresholds.min_bucket_share
        return [p for p in self.points if p["samples"] >= max(floor, 1)]

    def offset(self, ms: int) -> str:
        s = round((ms - self.bounds["start_ms"]) / 1000)
        return f"t+{s // 60}m{s % 60:02d}s" if s % 60 else f"t+{s // 60}m"


Detector = Callable[[Context], tuple[list[Finding], str | None]]


def run_all(ctx: Context) -> tuple[list[Finding], list[dict]]:
    """Run every detector. Returns (findings sorted by severity then time, skipped detectors with reasons)."""
    from projectstack.results import health
    from projectstack.results.detectors import changes, errors, injector, saturation, sla
    detectors: list[tuple[str, Detector]] = [
        ("sla", sla.detect),
        ("latency_change", changes.detect_latency),
        ("error_rate_change", changes.detect_error_rate),
        ("saturation", saturation.detect),
        ("error_burst", errors.detect_bursts),
        ("injector_connect", injector.detect_connect),
        ("injector_throughput", injector.detect_throughput),
        ("correlation", health.detect),
    ]
    findings: list[Finding] = []
    skipped: list[dict] = []
    for name, fn in detectors:
        found, why = fn(ctx)
        findings += found
        if why:
            skipped.append({"detector": name, "reason": why})
    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.start_ms or 0))
    return findings, skipped
