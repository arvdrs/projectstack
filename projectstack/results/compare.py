"""
Run-versus-baseline comparison and the regression detector.

Two runs are compared label by label on their steady-state statistics (exact
percentiles, as computed by `pstack analyze`). A label has regressed when its p95
or error rate worsened beyond the thresholds; a regression only counts when the
load was comparable (steady-state users within 10% by default). Otherwise the
differences are still listed but marked as not comparable, and CI does not fail
on them.

Statistics come from each run's metrics.json. When a baseline's folder is not on
this machine (a fresh CI runner sharing a team ClickHouse), its statistics are
recomputed from its samples in ClickHouse over the window it was analysed with.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from projectstack.results.detectors import Finding

SCHEMA = "projectstack.comparison/1"
KINDS = ("transaction", "request")


class CompareError(RuntimeError):
    pass


@dataclass
class RegressionThresholds:
    p95_pct: float = 10.0            # p95 at least 10% higher …
    p95_ms: float = 50.0             # … and at least 50 ms higher
    err_pp: float = 1.0              # error rate up by at least 1 percentage point
    min_samples: int = 100           # both runs need this many samples of a label to judge it
    load_pct: float = 10.0           # steady-state users more than 10% apart: load not comparable
    critical_p95_pct: float = 50.0   # severity critical from +50% p95 …
    critical_err_pp: float = 5.0     # … or +5 points of errors

    _KEYS = {"p95": "p95_pct", "p95ms": "p95_ms", "err": "err_pp", "samples": "min_samples", "load": "load_pct"}

    @classmethod
    def from_settings(cls, settings: dict[str, str], spec: str | None = None) -> "RegressionThresholds":
        """Defaults, then the results_regression setting (JSON with field names), then a --regression spec."""
        raw = settings.get("results_regression") or "{}"
        try:
            overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError as exc:
            raise ValueError(f"Setting results_regression is not valid JSON: {exc}") from exc
        th = cls(**{k: v for k, v in overrides.items() if k in cls.__dataclass_fields__})
        return th.with_spec(spec) if spec else th

    def with_spec(self, spec: str) -> "RegressionThresholds":
        """--regression p95=15%,p95ms=100,err=2,samples=50,load=20"""
        values = asdict(self)
        for part in filter(None, (p.strip() for p in spec.split(","))):
            key, _, value = part.partition("=")
            field = self._KEYS.get(key.strip().lower())
            if not field or not value.strip():
                raise ValueError(f"--regression: cannot read {part!r} (use p95=10%, p95ms=50, err=1, samples=100, load=10)")
            try:
                number = float(value.strip().rstrip("%"))
            except ValueError as exc:
                raise ValueError(f"--regression: {part!r} needs a number") from exc
            values[field] = int(number) if field == "min_samples" else number
        return RegressionThresholds(**values)

    def describe(self) -> str:
        return (f"p95 +{self.p95_pct:g}% and +{self.p95_ms:g} ms, errors +{self.err_pp:g} pt, "
                f"labels with ≥{self.min_samples} samples, users within {self.load_pct:g}%")


@dataclass
class RunData:
    run_id: str
    meta: dict[str, Any]
    metrics: dict[str, Any]
    source: str = "local"            # local | clickhouse

    @property
    def users(self) -> float:
        steady = self.meta.get("steady") or {}
        if steady.get("plateau_users"):
            return float(steady["plateau_users"])
        w = self.metrics.get("window") or {}
        pts = [p["users"] for p in self.metrics.get("timeline", {}).get("points", [])
               if w.get("start_ms", 0) <= p["t_ms"] < w.get("end_ms", 1 << 62)]
        return float(max(pts, default=0))

    def overall(self, kind: str) -> dict[str, Any]:
        return (self.metrics.get("overall") or {}).get(kind) or {}

    def info(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "scenario": self.meta.get("scenario", ""), "tag": self.meta.get("tag", ""),
                "created_at": self.meta.get("created_at", ""), "verdict": self.meta.get("verdict", ""),
                "users": self.users, "source": self.source}


# ---------------------------------------------------------------------------
# Loading runs
# ---------------------------------------------------------------------------

def find_run(ref: str, runs_dir: Path, get_db) -> str:
    """A run id from an id or a unique prefix: local run folders first, then the registry (get_db() connects)."""
    names = sorted(d.name for d in runs_dir.glob(f"{ref}*") if (d / "metrics.json").exists()) if runs_dir.is_dir() else []
    if ref in names:
        return ref
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        raise CompareError(f"{ref!r} matches {len(names)} runs: {', '.join(names[:5])}{' …' if len(names) > 5 else ''}")
    from projectstack.results import store
    try:
        return store.resolve_run(get_db(), ref)
    except LookupError as exc:
        raise CompareError(str(exc)) from exc


def load_run(run_id: str, runs_dir: Path, db=None) -> RunData:
    folder = runs_dir / run_id
    meta = _read(folder / "meta.json")
    metrics = _read(folder / "metrics.json")
    if meta is not None and metrics is not None:
        return RunData(run_id, meta, metrics, "local")
    if db is None:
        raise CompareError(f"Run {run_id} has no metrics.json in {folder} and no results database is connected")
    from projectstack.results import store
    meta = meta or store.get_meta(db, run_id)
    return RunData(run_id, meta, _recompute(db, run_id, meta), "clickhouse")


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (OSError, ValueError):
        return None


def _recompute(db, run_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """A run's metrics from its samples, over the window it was analysed with."""
    from projectstack.results import metrics as m
    from projectstack.results.window import Window
    kinds = {label: kind for label, kind in db.rows(
        f"SELECT label, toString(kind) FROM {db.db}.run_labels FINAL WHERE run_id = {{r:String}}", {"r": run_id})}
    bounds = m.run_bounds(db, run_id)
    if not kinds or not bounds.get("samples"):
        raise CompareError(f"The samples of run {run_id} are no longer in ClickHouse (retention) and its "
                           "metrics.json is not on this machine")
    w = meta.get("window") or {}
    if not w:
        raise CompareError(f"Run {run_id} has no analysis window in its metadata")
    window = Window(int(w["start_ms"]), int(w["end_ms"]), w.get("source", "auto"), float(w["duration_s"]))
    users_from_threads = (meta.get("columns") or {}).get("users_source") == "threadName"
    return m.compute(db, run_id, window, kinds, bounds, users_from_threads)


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------

def _diff_pct(a: float, b: float) -> float | None:
    return round((b / a - 1) * 100, 1) if a else None


def _stats(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s.get(k) for k in ("samples", "avg_ms", "p90_ms", "p95_ms", "p99_ms", "error_pct", "tps")}


def _row(kind: str, label: str, base: dict, cur: dict, th: RegressionThresholds) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": kind, "label": label, "baseline": _stats(base), "current": _stats(cur),
                           "p95_change_ms": None, "p95_change_pct": None, "err_change_pp": None,
                           "tps_change_pct": None, "status": "same", "regressed": [], "improved": []}
    if min(base.get("samples") or 0, cur.get("samples") or 0) < th.min_samples:
        row["status"] = "too_few_samples"
        return row
    b95, c95 = float(base["p95_ms"] or 0), float(cur["p95_ms"] or 0)
    row["p95_change_ms"] = round(c95 - b95, 1)
    row["p95_change_pct"] = _diff_pct(b95, c95)
    row["err_change_pp"] = round(float(cur["error_pct"] or 0) - float(base["error_pct"] or 0), 3)
    row["tps_change_pct"] = _diff_pct(float(base.get("tps") or 0), float(cur.get("tps") or 0))
    pct = row["p95_change_pct"] if row["p95_change_pct"] is not None else (100.0 if c95 > b95 else 0.0)
    if pct >= th.p95_pct and row["p95_change_ms"] >= th.p95_ms:
        row["regressed"].append("p95")
    elif pct <= -th.p95_pct and row["p95_change_ms"] <= -th.p95_ms:
        row["improved"].append("p95")
    if row["err_change_pp"] >= th.err_pp:
        row["regressed"].append("error_rate")
    elif row["err_change_pp"] <= -th.err_pp:
        row["improved"].append("error_rate")
    row["status"] = "regressed" if row["regressed"] else "improved" if row["improved"] else "same"
    if row["regressed"]:
        row["severity"] = ("critical" if pct >= th.critical_p95_pct or row["err_change_pp"] >= th.critical_err_pp
                           else "warning")
    return row


def compare(base: RunData, cur: RunData, th: RegressionThresholds | None = None) -> dict[str, Any]:
    th = th or RegressionThresholds()
    bu, cu = base.users, cur.users
    primary = next((k for k in KINDS if base.overall(k) and cur.overall(k)), "request")
    bt, ct = float(base.overall(primary).get("tps") or 0), float(cur.overall(primary).get("tps") or 0)
    users_diff = _diff_pct(bu, cu)
    if bu and cu:
        comparable = abs(users_diff or 0) <= th.load_pct
        reason = "" if comparable else (f"steady-state users differ by {abs(users_diff or 0):.0f}% "
                                        f"({bu:g} in the baseline, {cu:g} now)")
    else:
        tps_diff = _diff_pct(bt, ct)
        comparable = bool(bt and ct) and abs(tps_diff or 0) <= th.load_pct
        reason = "" if comparable else "active users unknown and throughput differs" if bt and ct else \
            "active users unknown"
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "baseline": base.info(),
        "current": cur.info(),
        "load": {"baseline_users": bu, "current_users": cu, "users_change_pct": users_diff,
                 "baseline_tps": bt, "current_tps": ct, "tps_change_pct": _diff_pct(bt, ct), "kind": primary,
                 "comparable": comparable, "reason": reason},
        "thresholds": asdict(th),
        "kinds": {},
        "only_in_baseline": {},
        "only_in_current": {},
    }
    rows_all: list[dict[str, Any]] = []
    for kind in KINDS:
        b_rows = {r["label"]: r for r in (base.metrics.get("labels") or {}).get(kind, [])}
        c_rows = {r["label"]: r for r in (cur.metrics.get("labels") or {}).get(kind, [])}
        if not b_rows and not c_rows:
            continue
        rows = [_row(kind, label, b_rows[label], c_rows[label], th) for label in c_rows if label in b_rows]
        overall = (_row(kind, f"All {kind}s", base.overall(kind), cur.overall(kind), th)
                   if base.overall(kind) and cur.overall(kind) else None)
        doc["kinds"][kind] = {"labels": rows, "overall": overall}
        doc["only_in_baseline"][kind] = sorted(set(b_rows) - set(c_rows))
        doc["only_in_current"][kind] = sorted(set(c_rows) - set(b_rows))
        rows_all += rows
    doc["regressions"] = sorted(
        ({"kind": r["kind"], "label": r["label"], "metrics": r["regressed"], "severity": r["severity"],
          "p95_baseline": r["baseline"]["p95_ms"], "p95_current": r["current"]["p95_ms"],
          "p95_change_pct": r["p95_change_pct"], "err_baseline": r["baseline"]["error_pct"],
          "err_current": r["current"]["error_pct"], "err_change_pp": r["err_change_pp"]}
         for r in rows_all if r["regressed"]),
        key=lambda r: (r["severity"] != "critical", r["kind"] != "transaction", -(r["p95_change_pct"] or 0),
                       -(r["err_change_pp"] or 0)))
    doc["summary"] = {"compared": sum(1 for r in rows_all if r["status"] != "too_few_samples"),
                      "regressed": sum(1 for r in rows_all if r["status"] == "regressed"),
                      "improved": sum(1 for r in rows_all if r["status"] == "improved"),
                      "too_few_samples": sum(1 for r in rows_all if r["status"] == "too_few_samples")}
    doc["gating"] = bool(doc["regressions"]) and comparable      # regressions that fail CI
    return doc


# ---------------------------------------------------------------------------
# Regression findings for findings.json
# ---------------------------------------------------------------------------

def _name(info: dict[str, Any]) -> str:
    return info.get("tag") or info["run_id"]


def regression_findings(comp: dict[str, Any], window: dict[str, Any], limit: int = 10) -> list[Finding]:
    """One finding per regressed metric (p95, error rate), listing the worst labels."""
    out: list[Finding] = []
    base = comp["baseline"]
    comparable = comp["load"]["comparable"]
    for metric, title_metric in (("p95", "p95"), ("error_rate", "Error rate")):
        regs = [r for r in comp["regressions"] if metric in r["metrics"]]
        if not regs:
            continue
        if metric == "p95":
            regs.sort(key=lambda r: -(r["p95_change_pct"] or 0))
            w = regs[0]
            worst = (f"worst {w['label']} {w['p95_baseline']:,.0f} → {w['p95_current']:,.0f} ms "
                     f"(+{w['p95_change_pct']:g}%)")
        else:
            regs.sort(key=lambda r: -(r["err_change_pp"] or 0))
            w = regs[0]
            worst = f"worst {w['label']} {w['err_baseline']:g}% → {w['err_current']:g}%"
        n = len(regs)
        kinds = {r["kind"] for r in regs}
        what = (f"{n} {'transaction' if kinds == {'transaction'} else 'request' if kinds == {'request'} else 'label'}"
                f"{'s' if n > 1 else ''}")
        severity = "critical" if any(r["severity"] == "critical" for r in regs) else "warning"
        detail = (f"Compared with baseline run {base['run_id']} over the steady state at "
                  f"{comp['load']['current_users']:g} users.")
        if not comparable:
            severity = "info"
            detail = (f"Not counted as a regression: {comp['load']['reason']}. Compared with baseline run "
                      f"{base['run_id']}.")
        out.append(Finding(
            type="regression", severity=severity,
            title=f"{title_metric} regressed for {what} vs {_name(base)}: {worst}",
            detail=detail, start_ms=window.get("start_ms"), end_ms=window.get("end_ms"),
            labels=[r["label"] for r in regs[:limit]],
            evidence={"metric": "p95_ms" if metric == "p95" else "error_pct", "baseline_run": base["run_id"],
                      "comparable": comparable, "count": n,
                      "labels": [{"label": r["label"], "kind": r["kind"],
                                  **({"p95_before": r["p95_baseline"], "p95_after": r["p95_current"],
                                      "increase_pct": r["p95_change_pct"]} if metric == "p95" else
                                     {"err_before": r["err_baseline"], "err_after": r["err_current"],
                                      "increase_pp": r["err_change_pp"]})} for r in regs[:limit]]},
        ))
    return out


def baseline_section(comp: dict[str, Any] | None, requested: str | None, note: str = "") -> dict[str, Any] | None:
    """The compact `baseline` block of findings.json."""
    if not requested:
        return None
    if comp is None:
        return {"requested": requested, "found": False, "note": note}
    return {"requested": requested, "found": True, **{k: comp["baseline"][k] for k in ("run_id", "tag", "created_at")},
            "load": comp["load"], "gating": comp["gating"], "summary": comp["summary"],
            "thresholds": comp["thresholds"]}


# ---------------------------------------------------------------------------
# Resolving a baseline
# ---------------------------------------------------------------------------

LAST_GREEN = "last-green"


def resolve_baseline(ref: str, *, db, scenario: str, before_run: str | None = None) -> str | None:
    """A run id for --baseline: an id or prefix, or last-green (latest passing run of the scenario)."""
    from projectstack.results import store
    if ref.strip().lower() == LAST_GREEN:
        return store.last_green(db, scenario, before_run)
    return store.resolve_run(db, ref)


def describe_missing_baseline(scenario: str) -> str:
    return (f"no passing run of scenario {scenario!r} yet, so there is nothing to compare with "
            "(last-green needs an earlier run whose verdict was pass)")

