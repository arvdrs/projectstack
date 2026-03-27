"""
JTL results parser and metrics calculator.

Reads the CSV JTL file produced by jMeter and computes a MetricsSummary.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SamplerMetrics:
    """Per-sampler breakdown."""
    label: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    times: list[int] = field(default_factory=list)

    @property
    def error_rate(self) -> float:
        return (self.failed / self.total * 100) if self.total else 0.0

    @property
    def avg_ms(self) -> float:
        return sum(self.times) / len(self.times) if self.times else 0.0

    @property
    def min_ms(self) -> int:
        return min(self.times) if self.times else 0

    @property
    def max_ms(self) -> int:
        return max(self.times) if self.times else 0

    @property
    def p90_ms(self) -> int:
        return _percentile(self.times, 90)

    @property
    def p95_ms(self) -> int:
        return _percentile(self.times, 95)


@dataclass
class MetricsSummary:
    """Aggregated test metrics."""
    total_requests: int = 0
    passed: int = 0
    failed: int = 0
    duration_sec: float = 0.0
    all_times: list[int] = field(default_factory=list)
    samplers: dict[str, SamplerMetrics] = field(default_factory=dict)
    start_epoch_ms: int = 0
    end_epoch_ms: int = 0

    @property
    def error_rate(self) -> float:
        return (self.failed / self.total_requests * 100) if self.total_requests else 0.0

    @property
    def throughput(self) -> float:
        """Requests per second."""
        return self.total_requests / self.duration_sec if self.duration_sec > 0 else 0.0

    @property
    def avg_ms(self) -> float:
        return sum(self.all_times) / len(self.all_times) if self.all_times else 0.0

    @property
    def min_ms(self) -> int:
        return min(self.all_times) if self.all_times else 0

    @property
    def max_ms(self) -> int:
        return max(self.all_times) if self.all_times else 0

    @property
    def p90_ms(self) -> int:
        return _percentile(self.all_times, 90)

    @property
    def p95_ms(self) -> int:
        return _percentile(self.all_times, 95)


def _percentile(times: list[int], pct: int) -> int:
    if not times:
        return 0
    sorted_times = sorted(times)
    idx = int(len(sorted_times) * pct / 100)
    idx = min(idx, len(sorted_times) - 1)
    return sorted_times[idx]


def parse_jtl(jtl_path: str | Path) -> MetricsSummary:
    """
    Parse a jMeter JTL (CSV) results file and return a MetricsSummary.

    Args:
        jtl_path: Path to the .jtl file.

    Returns:
        MetricsSummary with aggregated and per-sampler metrics.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is empty or has no recognised columns.
    """
    path = Path(jtl_path)
    if not path.exists():
        raise FileNotFoundError(f"JTL file not found: {path}")

    summary = MetricsSummary()
    rows_read = 0

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less JTL file: {path}")

        # Normalise column names (jMeter sometimes varies capitalisation)
        fieldnames_lower = [c.lower() for c in reader.fieldnames]
        col = {
            "ts":      _find_col(fieldnames_lower, ["timestamp"]),
            "elapsed": _find_col(fieldnames_lower, ["elapsed"]),
            "label":   _find_col(fieldnames_lower, ["label"]),
            "success": _find_col(fieldnames_lower, ["success"]),
        }

        for row in reader:
            rows_read += 1
            fields = list(row.values())

            try:
                ts = int(fields[col["ts"]]) if col["ts"] is not None else 0
                elapsed = int(fields[col["elapsed"]]) if col["elapsed"] is not None else 0
                label = fields[col["label"]] if col["label"] is not None else "unknown"
                success = (
                    fields[col["success"]].strip().lower() == "true"
                    if col["success"] is not None
                    else True
                )
            except (IndexError, ValueError):
                continue

            summary.total_requests += 1
            if success:
                summary.passed += 1
            else:
                summary.failed += 1
            summary.all_times.append(elapsed)

            if summary.start_epoch_ms == 0 or ts < summary.start_epoch_ms:
                summary.start_epoch_ms = ts
            if ts > summary.end_epoch_ms:
                summary.end_epoch_ms = ts

            sm = summary.samplers.setdefault(label, SamplerMetrics(label=label))
            sm.total += 1
            if success:
                sm.passed += 1
            else:
                sm.failed += 1
            sm.times.append(elapsed)

    if rows_read == 0:
        raise ValueError(f"JTL file has no data rows: {path}")

    if summary.end_epoch_ms > summary.start_epoch_ms:
        summary.duration_sec = (summary.end_epoch_ms - summary.start_epoch_ms) / 1000.0
    elif summary.all_times:
        summary.duration_sec = sum(summary.all_times) / 1000.0

    return summary


def _find_col(fieldnames: list[str], candidates: list[str]) -> int | None:
    for c in candidates:
        if c in fieldnames:
            return fieldnames.index(c)
    return None


def print_summary(summary: MetricsSummary) -> None:
    """Print a formatted metrics summary to stdout."""
    bar = "=" * 60
    print(f"\n{bar}")
    print("  TEST RESULTS")
    print(bar)
    print(f"  Total Requests : {summary.total_requests}")
    print(f"  Passed         : {summary.passed}")
    print(f"  Failed         : {summary.failed}")
    print(f"  Error Rate     : {summary.error_rate:.1f}%")
    print(f"  Duration       : {summary.duration_sec:.1f}s")
    print(f"  Throughput     : {summary.throughput:.2f} req/s")
    print(f"\n  Response Times (ms)")
    print(f"    Min  : {summary.min_ms}")
    print(f"    Avg  : {summary.avg_ms:.0f}")
    print(f"    P90  : {summary.p90_ms}")
    print(f"    P95  : {summary.p95_ms}")
    print(f"    Max  : {summary.max_ms}")

    if len(summary.samplers) > 1:
        print(f"\n  Per-Sampler Breakdown")
        print(f"  {'Label':<35} {'Total':>6} {'Pass':>6} {'Fail':>6} {'Avg ms':>8} {'Err%':>6}")
        print(f"  {'-'*35} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*6}")
        for sm in summary.samplers.values():
            print(
                f"  {sm.label[:35]:<35} {sm.total:>6} {sm.passed:>6} "
                f"{sm.failed:>6} {sm.avg_ms:>8.0f} {sm.error_rate:>5.1f}%"
            )
    print(bar)
