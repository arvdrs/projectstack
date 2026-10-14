"""
--sla on the command line, translated to the SLA format the web app already uses
(projectstack.analyzer.sla), so both share one definition and one evaluator.

  p95=3000             response time (avg, p90, p95 or p99) per transaction, ms
  p95[Login]=2000      target for one transaction
  err=1                maximum error rate, %
  tps>=50              minimum throughput, requests/s
"""

from __future__ import annotations

import re

from projectstack.analyzer.sla import normalize_sla

_TERM_RE = re.compile(r"\s*([A-Za-z0-9_]+)(?:\[([^\]]+)\])?\s*(>=|<=|=)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:,|$)")
_METRICS = {"avg", "p90", "p95", "p99"}


def parse_sla(spec: str | None) -> dict | None:
    if not spec or not spec.strip():
        return None
    raw: dict = {"transaction_targets": {}}
    metric = None
    pos = 0
    for m in _TERM_RE.finditer(spec):
        if m.start() != pos:
            break
        pos = m.end()
        key, label, op, value = m.group(1).lower(), m.group(2), m.group(3), float(m.group(4))
        if key in _METRICS:
            if metric and metric != key:
                raise ValueError(f"--sla uses both {metric} and {key}; use one response-time metric")
            metric = key
            if label:
                raw["transaction_targets"][label.strip()] = value
            else:
                raw["response_time_ms"] = value
        elif key in ("err", "error", "errors"):
            raw["error_rate_pct"] = value
        elif key in ("tps", "throughput"):
            raw["throughput_rps"] = value
        else:
            raise ValueError(f"--sla: unknown target {key!r} (use avg, p90, p95, p99, err, tps)")
    if pos != len(spec.strip()) and spec[pos:].strip():
        raise ValueError(f"--sla: cannot read {spec[pos:].strip()!r} — e.g. p95=3000,err=1,tps>=50")
    raw["response_time_metric"] = metric or "p90"
    return normalize_sla(raw)
