"""
Service level agreement (SLA) checks.

An SLA exists only when the user defines one before running the test — there
are no built-in targets. Without one, a run is reported as measured and is
never marked pass or fail.

SLA config (all fields optional, at least one target must be set):
    response_time_metric: "avg" | "p90" | "p95" | "p99"
    response_time_ms:     target for every transaction (or request, when the
                          script has no Transaction Controllers)
    transaction_targets:  {transaction name: ms} — per-transaction override
    error_rate_pct:       maximum share of failed requests
    throughput_rps:       minimum requests per second
"""

from __future__ import annotations

import re
from typing import Any

METRICS = {"avg": "average", "p90": "90th percentile", "p95": "95th percentile", "p99": "99th percentile"}

# Transaction Controller labels written by the generator: TC_01_Login, TC_02_Search …
TRANSACTION_LABEL_RE = re.compile(r"^TC_\d+_")


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _key(name: str) -> str:
    """Compare transaction names the way the generator writes them (TC_01_Add_to_cart ~ 'Add to cart')."""
    from projectstack.generator.transaction_grouper import clean_transaction_name
    return clean_transaction_name(TRANSACTION_LABEL_RE.sub("", name)).lower()


def normalize_sla(raw: dict | None) -> dict | None:
    """Validated SLA config, or None when no target is set."""
    if not raw:
        return None
    metric = raw.get("response_time_metric") or "p90"
    if metric not in METRICS:
        raise ValueError(f"Unknown response-time metric {metric!r} — use one of {', '.join(METRICS)}")
    out: dict[str, Any] = {"response_time_metric": metric}

    rt = _num(raw.get("response_time_ms"))
    if rt is not None:
        if rt <= 0:
            raise ValueError("Response-time target must be greater than 0 ms")
        out["response_time_ms"] = rt

    targets: dict[str, float] = {}
    for name, ms in (raw.get("transaction_targets") or {}).items():
        v = _num(ms)
        if not str(name).strip() or v is None:
            continue
        if v <= 0:
            raise ValueError(f"Response-time target for {name!r} must be greater than 0 ms")
        targets[str(name).strip()] = v
    if targets:
        out["transaction_targets"] = targets

    err = _num(raw.get("error_rate_pct"))
    if err is not None:
        if not 0 <= err <= 100:
            raise ValueError("Error-rate target must be between 0 and 100%")
        out["error_rate_pct"] = err

    tps = _num(raw.get("throughput_rps"))
    if tps is not None:
        if tps <= 0:
            raise ValueError("Throughput target must be greater than 0 req/s")
        out["throughput_rps"] = tps

    return out if len(out) > 1 else None


def describe(sla: dict) -> list[str]:
    """Human-readable targets, e.g. ['p90 ≤ 2000 ms per transaction', 'errors ≤ 1%']."""
    metric = sla.get("response_time_metric", "p90")
    parts = []
    if "response_time_ms" in sla:
        parts.append(f"{metric} ≤ {sla['response_time_ms']:g} ms per transaction")
    for name, ms in (sla.get("transaction_targets") or {}).items():
        parts.append(f"{name}: {metric} ≤ {ms:g} ms")
    if "error_rate_pct" in sla:
        parts.append(f"errors ≤ {sla['error_rate_pct']:g}%")
    if "throughput_rps" in sla:
        parts.append(f"throughput ≥ {sla['throughput_rps']:g} req/s")
    return parts


def evaluate_sla(sla: dict | None, by_label: dict[str, dict], duration_sec: float,
                 transaction_labels: list[str] | None = None) -> dict:
    """
    Check run results against the SLA.

    by_label: {label: {total, failed, avg_ms, p90_ms, p95_ms, p99_ms}} from the JTL.
    Response-time targets apply to Transaction Controllers when the script has
    them, otherwise to each request. Error rate and throughput are measured on
    requests only, so transaction samples are not counted twice.
    """
    if not sla:
        return {"defined": False}

    metric = sla["response_time_metric"]
    # Transaction Controller labels: given by the caller (detected from the samples), or
    # recognised by the generator's TC_nn_ naming
    if transaction_labels is not None:
        txn_labels = [lb for lb in by_label if lb in set(transaction_labels)]
    else:
        txn_labels = [lb for lb in by_label if TRANSACTION_LABEL_RE.match(lb)]
    request_labels = [lb for lb in by_label if lb not in txn_labels]
    rt_scope = txn_labels or request_labels
    targets = {_key(k): (k, v) for k, v in (sla.get("transaction_targets") or {}).items()}

    checks: list[dict] = []
    matched: set[str] = set()
    for label in rt_scope:
        override = targets.get(_key(label))
        target = override[1] if override else sla.get("response_time_ms")
        if override:
            matched.add(_key(label))
        if target is None:
            continue
        actual = _num(by_label[label].get(f"{metric}_ms"))
        checks.append({
            "kind": "response_time", "label": label, "metric": metric, "unit": "ms",
            "target": target, "actual": actual, "passed": actual is not None and actual <= target,
        })
    for key, (name, target) in targets.items():
        if key not in matched:
            checks.append({
                "kind": "response_time", "label": name, "metric": metric, "unit": "ms",
                "target": target, "actual": None, "passed": False,
                "note": "No transaction with this name ran — group requests into transactions to measure it",
            })

    total = sum(int(by_label[lb].get("total") or 0) for lb in request_labels)
    failed = sum(int(by_label[lb].get("failed") or 0) for lb in request_labels)
    if "error_rate_pct" in sla:
        actual = round(failed / total * 100, 2) if total else None
        checks.append({
            "kind": "error_rate", "label": None, "metric": "error_rate", "unit": "%",
            "target": sla["error_rate_pct"], "actual": actual,
            "passed": actual is not None and actual <= sla["error_rate_pct"],
        })
    if "throughput_rps" in sla:
        actual = round(total / duration_sec, 2) if duration_sec > 0 and total else None
        checks.append({
            "kind": "throughput", "label": None, "metric": "throughput", "unit": "req/s",
            "target": sla["throughput_rps"], "actual": actual,
            "passed": actual is not None and actual >= sla["throughput_rps"],
        })

    passed_count = sum(c["passed"] for c in checks)
    return {
        "defined": True,
        "config": sla,
        "targets": describe(sla),
        "checks": checks,
        "passed": bool(checks) and passed_count == len(checks),
        "passed_count": passed_count,
        "failed_count": len(checks) - passed_count,
    }
