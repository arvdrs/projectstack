"""
findings.json: the compact, self-contained summary of a run that reports and the
LLM work from. Everything in it is computed by code; it stays under 30 KB so it
can be sent to a model as-is (raw samples never leave the database).

When a run has many labels or a long timeline, the document is reduced step by
step — fewer labels (those named in findings always stay), a coarser timeline,
shorter evidence lists — and `truncated` records what was cut. Merging timeline
buckets never averages percentiles: a merged bucket reports the highest p95 of
the buckets it covers, and its error rate is recomputed from the counts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from projectstack.results.detectors import Finding

SCHEMA = "projectstack.findings/1"
BUDGET_BYTES = 30_000
_LABEL_KEYS = ("label", "samples", "avg_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms", "error_pct", "tps")


def verdict(sla_result: dict, findings: list[Finding]) -> str:
    """fail: SLA breached · at_risk: SLA met but problems found · pass · no_sla: nothing to judge against."""
    if not sla_result.get("defined"):
        return "no_sla"
    if not sla_result.get("passed"):
        return "fail"
    if any(f.type != "sla_breach" and f.severity in ("critical", "warning") for f in findings):
        return "at_risk"
    return "pass"


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _merge(points: list[dict], k: int) -> list[dict]:
    out = []
    for i in range(0, len(points), k):
        g = points[i:i + k]
        samples = sum(p["samples"] for p in g)
        errors = sum(p["errors"] for p in g)
        out.append({
            "t_offset_s": g[0]["t_offset_s"], "users": max(p["users"] for p in g),
            "tps": round(sum(p["tps"] for p in g) / len(g), 2), "p95_ms": max(p["p95_ms"] for p in g),
            "samples": samples, "errors": errors, "error_pct": round(errors / samples * 100, 2) if samples else 0.0,
        })
    return out


def build(meta: dict[str, Any], metrics: dict[str, Any], steady: dict[str, Any], findings: list[Finding],
          skipped: list[dict], sla_result: dict, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    run = meta["run"]
    start = run["start_ms"]
    named = {lb for f in findings for lb in f.labels}
    points = [{"t_offset_s": round((p["t_ms"] - start) / 1000), "users": p["users"], "tps": p["tps"],
               "p95_ms": p["p95_ms"], "samples": p["samples"], "errors": p["errors"], "error_pct": p["error_pct"]}
              for p in metrics["timeline"]["points"]]
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "run": {
            "run_id": meta["run_id"], "scenario": meta["scenario"], "tag": meta.get("tag", ""),
            "start": _iso(start), "end": _iso(run["end_ms"]), "duration_s": run["duration_s"],
            "samples": run["samples"], "peak_users": run["peak_users"],
            "users_source": meta["columns"]["users_source"], "columns": meta["columns"]["present"],
            "labels": meta.get("labels", {}),
        },
        "steady": {**steady, "start": _iso(steady["start_ms"]), "end": _iso(steady["end_ms"]),
                   "start_offset_s": round((steady["start_ms"] - start) / 1000),
                   "end_offset_s": round((steady["end_ms"] - start) / 1000)},
        "verdict": verdict(sla_result, findings),
        "sla": sla_result,
        **({"baseline": baseline} if baseline else {}),
        "overall": metrics["overall"],
        "labels": {k: [{key: s[key] for key in _LABEL_KEYS} for s in v] for k, v in metrics["labels"].items()},
        "timeline": {"bucket_s": metrics["timeline"]["bucket_s"], "users_source": metrics["timeline"]["users_source"],
                     "points": points},
        "errors": [{**{k: e[k] for k in ("label", "response_code", "response_message", "failure", "count")},
                    "first_offset_s": round((e["first_ms"] - start) / 1000),
                    "last_offset_s": round((e["last_ms"] - start) / 1000)} for e in metrics.get("errors", [])[:10]],
        "findings": [f.as_dict(start) for f in findings],
        "skipped": skipped,
        "warnings": list((meta.get("ingest") or {}).get("warnings") or []),   # about the file itself
        "truncated": {},
        "notes": [
            "Percentiles are exact over the samples in scope (JMeter Aggregate Report definition); "
            "they are never averaged across labels or time buckets.",
            "Label and overall statistics cover the steady-state window; the timeline and errors cover the full run.",
        ],
    }
    return _fit(doc, named, metrics["timeline"]["bucket_s"])


def _size(doc: dict) -> int:
    return len(json.dumps(doc, separators=(",", ":"), default=str).encode())


def _fit(doc: dict, named: set[str], bucket_s: int) -> dict:
    """Reduce the document until it fits the budget, recording what was cut."""
    steps = [("labels", n) for n in (60, 30, 15, 8)] + [("timeline", n) for n in (120, 60, 30)] + \
            [("evidence", 3), ("errors", 5), ("sla", 20)]
    for what, n in steps:
        if _size(doc) <= BUDGET_BYTES:
            break
        if what == "labels":
            for kind, rows in doc["labels"].items():
                if len(rows) <= n:
                    continue
                keep = [r for r in rows if r["label"] in named]
                rest = sorted((r for r in rows if r["label"] not in named), key=lambda r: -r["samples"])
                kept = keep + rest[:max(0, n - len(keep))]
                order = {r["label"]: i for i, r in enumerate(rows)}
                doc["labels"][kind] = sorted(kept, key=lambda r: order[r["label"]])
                doc["truncated"][f"labels.{kind}"] = {"shown": len(kept), "total": len(rows),
                                                     "kept": "labels named in findings, then the busiest"}
        elif what == "timeline":
            pts = doc["timeline"]["points"]
            if len(pts) > n:
                k = -(-len(pts) // n)
                doc["timeline"]["points"] = _merge(pts, k)
                doc["timeline"]["merged_buckets"] = k
                doc["timeline"]["bucket_s"] = bucket_s * k
                doc["truncated"]["timeline"] = {"shown": len(doc["timeline"]["points"]), "total": len(pts),
                                                "merge": f"{k} buckets per point; p95 is the highest in the group"}
        elif what == "evidence":
            for f in doc["findings"]:
                for key, val in list(f["evidence"].items()):
                    if isinstance(val, list) and len(val) > n:
                        f["evidence"][key] = val[:n]
                        doc["truncated"]["finding_evidence"] = f"lists cut to {n} items"
        elif what == "errors":
            doc["errors"] = doc["errors"][:n]
        elif what == "sla" and doc["sla"].get("checks"):
            checks = doc["sla"]["checks"]
            if len(checks) > n:
                failed = [c for c in checks if not c["passed"]]
                doc["sla"]["checks"] = failed + [c for c in checks if c["passed"]][:max(0, n - len(failed))]
                doc["truncated"]["sla.checks"] = {"shown": len(doc["sla"]["checks"]), "total": len(checks),
                                                  "kept": "every failed check, then passed ones"}
    doc["size_bytes"] = _size(doc)
    return doc
