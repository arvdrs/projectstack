"""
CI mode: one exit code, an HTML report and a short Markdown summary that fits a
merge-request comment.

Exit codes of `pstack analyze --ci`:
  0  passed, or nothing to gate on (no SLA and no comparable baseline)
  1  SLA breach
  2  regression against the baseline, at comparable load
  3  SLA breach and regression
  4  the analysis itself failed (unreadable file, database unreachable, bad options)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from projectstack.results.fmt import duration, num, pct_text

EXIT_OK, EXIT_SLA, EXIT_REGRESSION, EXIT_BOTH, EXIT_ERROR = 0, 1, 2, 3, 4
ARTIFACTS = ("report.html", "summary.md", "findings.json", "comparison.json")


def exit_code(doc: dict[str, Any], comparison: dict[str, Any] | None) -> int:
    sla = doc.get("sla") or {}
    breach = bool(sla.get("defined")) and not sla.get("passed")
    regressed = bool(comparison and comparison.get("gating"))
    return (EXIT_SLA if breach else 0) | (EXIT_REGRESSION if regressed else 0)


def status(doc: dict[str, Any], code: int) -> str:
    if code == EXIT_BOTH:
        return "❌ SLA breach and regression"
    if code == EXIT_SLA:
        return "❌ SLA breach"
    if code == EXIT_REGRESSION:
        return "❌ Regression against the baseline"
    return {"pass": "✅ Passed", "at_risk": "⚠️ Passed with warnings",
            "no_sla": "➖ Not gated (no SLA)"}.get(doc.get("verdict"), "✅ Passed")


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def artifact_url(path: Path) -> str | None:
    """A browsable GitLab link to a job artifact, when running in GitLab CI inside the project directory."""
    job, project = os.environ.get("CI_JOB_URL"), os.environ.get("CI_PROJECT_DIR")
    if not job or not project:
        return None
    try:
        rel = Path(path).resolve().relative_to(Path(project).resolve())
    except ValueError:
        return None
    return f"{job.rstrip('/')}/artifacts/file/{rel.as_posix()}"


def _sla_rows(doc: dict[str, Any]) -> list[str]:
    sla = doc.get("sla") or {}
    if not sla.get("defined"):
        return []
    rows = ["| SLA | Target | Measured | Result |", "|---|---:|---:|---|"]
    for c in sla.get("checks") or []:
        what = {"response_time": f"{c.get('label') or 'All requests'} {c['metric']}", "error_rate": "Error rate",
                "throughput": "Throughput"}.get(c["kind"], c["metric"])
        if c["unit"] == "%":
            target, actual = pct_text(c["target"]), pct_text(c["actual"])
        else:
            unit = " ms" if c["unit"] == "ms" else f" {c['unit']}"
            decimals = 0 if c["unit"] == "ms" else 1
            target, actual = num(c["target"], decimals) + unit, num(c["actual"], decimals) + unit
        rows.append(f"| {_cell(what)} | {target} | {actual} | {'✅' if c['passed'] else '❌'} |")
    return rows


def _regression_rows(comparison: dict[str, Any], limit: int) -> list[str]:
    regs = comparison.get("regressions") or []
    if not regs:
        return []
    th = comparison["thresholds"]
    counted = "" if comparison["load"]["comparable"] else " — not counted, the load differed"
    rows = [f"**Regressions against the baseline**{counted} (p95 +{th['p95_pct']:g}% and +{th['p95_ms']:g} ms, "
            f"or errors +{th['err_pp']:g} pt)", "",
            "| Label | p95 baseline → now | Δ p95 | Errors baseline → now |", "|---|---:|---:|---:|"]
    for r in regs[:limit]:
        delta = f"{r['p95_change_pct']:+.0f}%" if r["p95_change_pct"] is not None else "—"
        mark = " ❌" if "p95" in r["metrics"] else ""
        err_mark = " ❌" if "error_rate" in r["metrics"] else ""
        rows.append(f"| {_cell(r['label'])} | {num(r['p95_baseline'])} → {num(r['p95_current'])} ms{mark} | {delta} | "
                    f"{pct_text(r['err_baseline'])} → {pct_text(r['err_current'])}{err_mark} |")
    if len(regs) > limit:
        rows.append(f"| … {len(regs) - limit} more | | | |")
    return rows


def summary_markdown(doc: dict[str, Any], comparison: dict[str, Any] | None, code: int,
                     report_path: Path | None = None, max_rows: int = 10, max_findings: int = 5) -> str:
    run, steady = doc["run"], doc.get("steady") or {}
    lines = [f"### {status(doc, code)} · performance test: {_cell(run.get('scenario'))}", ""]
    facts = [f"`{run['run_id']}`"] + ([_cell(run["tag"])] if run.get("tag") else []) + [
        f"{num(run.get('peak_users'))} users", f"{duration(run.get('duration_s'))} run",
        f"steady state {duration(steady.get('duration_s'))}", f"verdict `{doc.get('verdict')}`", f"exit code {code}"]
    lines.append(" · ".join(facts))
    base = doc.get("baseline")
    if base and base.get("found"):
        load = base["load"]
        tag = f" ({_cell(base['tag'])})" if base.get("tag") else ""
        state = ("load comparable" if load["comparable"] else f"**load not comparable**: {_cell(load['reason'])}")
        lines.append(f"Baseline `{base['run_id']}`{tag} — {state}; {base['summary']['regressed']} regressed, "
                     f"{base['summary']['improved']} improved")
    elif base:
        lines.append(f"Baseline: none — {_cell(base.get('note'))}")
    sla_rows = _sla_rows(doc)
    if sla_rows:
        lines += [""] + sla_rows
    if comparison:
        reg_rows = _regression_rows(comparison, max_rows)
        if reg_rows:
            lines += [""] + reg_rows
    others = [f for f in doc.get("findings", []) if f["type"] not in ("sla_breach", "regression")]
    if others:
        icon = {"critical": "🔴", "warning": "🟠", "info": "⚪"}
        lines += ["", "**Findings**"]
        lines += [f"- {icon.get(f['severity'], '•')} {f['severity']}: {_cell(f['title'])}" for f in others[:max_findings]]
        if len(others) > max_findings:
            lines.append(f"- … {len(others) - max_findings} more in the report")
    if report_path:
        url = artifact_url(report_path)
        lines += ["", f"📄 [Full report]({url})" if url else f"📄 Full report: `{report_path}`"]
    return "\n".join(lines) + "\n"


def failure_markdown(error: str, source: str) -> str:
    return (f"### ❗ Performance test analysis failed\n\n`{_cell(source)}`: {_cell(error)}\n\n"
            f"exit code {EXIT_ERROR}\n")


def copy_artifacts(folder: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ARTIFACTS:
        src = folder / name
        if src.exists() and src.resolve() != (dest / name).resolve():
            shutil.copy2(src, dest / name)
            copied.append(dest / name)
    return copied


def comparison_markdown(comp: dict[str, Any], limit: int | None = 25) -> str:
    """`pstack compare --format md`: the comparison as Markdown tables."""
    b, c, load = comp["baseline"], comp["current"], comp["load"]
    state = "comparable" if load["comparable"] else f"**not comparable**: {_cell(load['reason'])}"
    lines = [f"### Comparison: `{c['run_id']}` against baseline `{b['run_id']}`", "",
             f"Load {load['baseline_users']:g} → {load['current_users']:g} users, "
             f"{load['baseline_tps']:,.1f} → {load['current_tps']:,.1f} {load['kind']}s/s — {state}"]
    for kind, data in comp["kinds"].items():
        rows = sorted(data["labels"], key=lambda r: (r["status"] != "regressed", -(r["p95_change_pct"] or 0)))
        shown = rows if limit is None else rows[:limit]
        lines += ["", f"**{kind.capitalize()}s**", "",
                  "| Label | Samples | p95 (ms) | Δ p95 | Errors | Δ errors | |", "|---|---:|---:|---:|---:|---:|---|"]
        for r in shown + ([data["overall"]] if data["overall"] else []):
            bs, cs = r["baseline"], r["current"]
            mark = {"regressed": "❌ " + ", ".join(r["regressed"]), "improved": "✅ " + ", ".join(r["improved"]),
                    "same": "", "too_few_samples": "too few samples"}[r["status"]]
            dp = f"{r['p95_change_pct']:+.1f}%" if r["p95_change_pct"] is not None else "—"
            de = f"{r['err_change_pp']:+.2f} pt" if r["err_change_pp"] is not None else "—"
            lines.append(f"| {_cell(r['label'])} | {num(bs['samples'])} → {num(cs['samples'])} | "
                         f"{num(bs['p95_ms'])} → {num(cs['p95_ms'])} | {dp} | {pct_text(bs['error_pct'])} → "
                         f"{pct_text(cs['error_pct'])} | {de} | {mark} |")
        if limit is not None and len(rows) > limit:
            lines.append(f"| … {len(rows) - limit} more | | | | | | |")
    s = comp["summary"]
    lines += ["", f"{s['regressed']} regressed, {s['improved']} improved, {s['compared']} compared."]
    return "\n".join(lines) + "\n"
