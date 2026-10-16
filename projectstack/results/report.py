"""
`pstack report`: a technical or executive report for an analysed run, as
Markdown or a self-contained HTML page.

A report is built from the run's findings.json and nothing else, so it works
without the database (CI can render reports from published artifacts). The
narrative comes from the AI provider configured for the "report" task, or from
rules with --no-llm. An AI narrative is kept in narrative_<audience>.json next to
the report and reused while findings.json and the prompt are unchanged, so
rendering the other format does not pay for a second call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.results import chart, fmt
from projectstack.results.narrative import AUDIENCES, Narrative, from_llm, from_rules, primary

FORMATS = ("html", "md")
TEMPLATE_DIR = Path(__file__).parent / "templates"

VERDICTS = {
    "fail": ("✕", "Failed", "critical"),
    "at_risk": ("!", "At risk", "warning"),
    "pass": ("✓", "Passed", "good"),
    "no_sla": ("–", "No SLA defined", "neutral"),
}
SEVERITIES = {"critical": ("✕", "Critical"), "warning": ("!", "Warning"), "info": ("i", "Info")}


class ReportError(RuntimeError):
    pass


@dataclass
class Report:
    run_id: str
    audience: str
    narrative: Narrative
    files: list[Path] = field(default_factory=list)
    stdout: str | None = None              # the report text, with --out -
    reused: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Finding the run
# ---------------------------------------------------------------------------

def locate(ref: str, runs_dir: Path, resolve_in_db: Callable[[str], str] | None = None) -> tuple[Path, dict]:
    """(run folder, findings.json) from a run id, a unique prefix of one, or a path to a findings.json."""
    path = Path(ref).expanduser()
    if path.is_file():
        return path.parent, _load(path)
    if path.is_dir() and (path / "findings.json").exists():
        return path, _load(path / "findings.json")
    matches = sorted(d for d in runs_dir.glob(f"{ref}*") if (d / "findings.json").exists()) if runs_dir.is_dir() else []
    exact = [d for d in matches if d.name == ref]
    if exact or len(matches) == 1:
        folder = (exact or matches)[0]
        return folder, _load(folder / "findings.json")
    if len(matches) > 1:
        names = ", ".join(d.name for d in matches[:5])
        raise ReportError(f"{ref!r} matches {len(matches)} runs: {names}{' …' if len(matches) > 5 else ''}")
    if resolve_in_db:
        run_id = resolve_in_db(ref)
        folder = runs_dir / run_id
        if (folder / "findings.json").exists():
            return folder, _load(folder / "findings.json")
        raise ReportError(f"Run {run_id} has no findings.json in {folder} — analyse it again with: pstack analyze")
    raise ReportError(f"No run matches {ref!r} in {runs_dir} — see: pstack runs list")


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ReportError(f"Cannot read {path}: {exc}") from exc
    if not str(doc.get("schema", "")).startswith("projectstack.findings/"):
        raise ReportError(f"{path} is not a ProjectStack findings.json")
    return doc


def _sha(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Narrative (with reuse)
# ---------------------------------------------------------------------------

def get_narrative(doc: dict, folder: Path, audience: str, *, use_llm: bool, provider_spec: str | None,
                  strict: bool, fresh: bool, settings: dict[str, str],
                  progress: Callable[[str], None]) -> tuple[Narrative, bool, list[str]]:
    """(narrative, reused from cache, warnings)."""
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.prompts import load_prompt
    from projectstack.llm.registry import provider_for_task
    from projectstack.results.narrative import NumberCheckFailed

    if not use_llm:
        return from_rules(doc, audience), False, []
    cache = folder / f"narrative_{audience}.json"
    sha, prompt_ref = _sha(doc), load_prompt(f"report_{audience}").ref
    try:
        provider = provider_for_task("report", settings, provider_spec)
    except LLMConfigError as exc:
        if provider_spec:
            raise ReportError(str(exc)) from exc
        note = f"AI narrative unavailable: {exc}"
        return from_rules(doc, audience, note=note), False, [f"{note} — wrote the rule-based narrative instead"]

    if not fresh and cache.exists():
        try:
            saved = json.loads(cache.read_text())
            if (saved.get("findings_sha256") == sha and saved.get("prompt") == prompt_ref
                    and saved.get("provider_requested") == provider.label and saved.get("source") == "llm"
                    and (not strict or (saved.get("numbers") or {}).get("ok"))):
                return Narrative.from_dict(saved), True, []
        except (OSError, ValueError, KeyError):
            pass

    progress(f"Writing the {audience} narrative with {provider.label} …")
    try:
        narrative = from_llm(doc, audience, provider, strict=strict)
    except NumberCheckFailed:
        raise
    except LLMError as exc:
        if provider_spec or strict:
            raise ReportError(str(exc)) from exc
        note = f"AI narrative failed: {exc}"
        return from_rules(doc, audience, note=note), False, [f"{note} — wrote the rule-based narrative instead"]
    cache.write_text(json.dumps({**narrative.as_dict(), "findings_sha256": sha,
                                 "provider_requested": provider.label}, indent=2, ensure_ascii=False))
    warnings = []
    if narrative.numbers and not narrative.numbers.ok:
        warnings.append(f"Number check: {narrative.numbers.describe()} (flagged in the report; "
                        "--strict-numbers makes this an error)")
    return narrative, False, warnings


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _env():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=lambda n: bool(n and n.endswith(".html.j2")),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters.update(num=fmt.num, ms=fmt.ms, pct=fmt.pct, rate=fmt.rate, duration=fmt.duration,
                       offset=fmt.offset, clock=fmt.clock, day=fmt.day, kib=fmt.kib, md=_md_cell, value=_value)
    return env


def _server_value(v: Any, unit: str) -> str:
    if v is None:
        return "—"
    from projectstack.results.cloud.analysis import _fmt
    return _fmt(float(v), unit)


def _md_cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if v.is_integer() and abs(v) >= 1000 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return "—" if v is None else str(v)


def _evidence(ev: dict[str, Any]) -> tuple[list[tuple[str, Any]], list[dict[str, Any]]]:
    """Scalars as (name, value) pairs, and lists of records as small tables."""
    scalars, tables = [], []
    for k, v in ev.items():
        if k in ("mapping", "fixes"):
            continue            # correlation findings: shown as "variable ← extractor" lines instead
        name = k.replace("_pct", " %").replace("_ms", " (ms)").replace("_", " ")
        if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
            cols = list(dict.fromkeys(c for r in v for c in r))
            tables.append({"name": name, "cols": [c.replace("_pct", " %").replace("_", " ") for c in cols],
                           "rows": [[r.get(c) for c in cols] for r in v]})
        elif isinstance(v, list):
            scalars.append((name, ", ".join(_value(x) for x in v)))
        else:
            scalars.append((name, v))
    return scalars, tables


def context(doc: dict, narrative: Narrative, audience: str) -> dict[str, Any]:
    run, sla = doc["run"], doc.get("sla", {})
    icon, label, cls = VERDICTS.get(doc.get("verdict"), ("?", str(doc.get("verdict")), "neutral"))
    content = narrative.content
    explanations = {e["finding"]: e["explanation"] for e in content.get("findings", [])} if audience == "tech" else {}
    findings = []
    for i, f in enumerate(doc.get("findings", []), 1):
        sev_icon, sev_label = SEVERITIES.get(f["severity"], ("?", f["severity"]))
        scalars, tables = _evidence(f.get("evidence", {}))
        mapping = [f"{m['var']} ← {x['description']}"
                   + (f" (that request failed {x['source_error_pct']:g}%)" if x.get("source_error_pct") else "")
                   for m in f.get("evidence", {}).get("mapping", []) for x in m.get("extractors", [])]
        findings.append({**f, "n": i, "sev_icon": sev_icon, "sev_label": sev_label, "evidence_rows": scalars,
                         "evidence_tables": tables, "explanation": explanations.get(i, ""), "mapping": mapping})
    failed_by_label: dict[str, list[str]] = {}
    for c in sla.get("checks", []) or []:
        if c.get("label") and not c["passed"]:
            failed_by_label.setdefault(c["label"], []).append(f"{c['metric']} over {fmt.num(c['target'])} ms")
    kinds = [("transaction", "Transactions"), ("request", "Requests"), ("subresult", "Sub-results (redirects)")]
    tables = [{"kind": k, "title": t, "rows": doc.get("labels", {}).get(k, []),
               "overall": doc.get("overall", {}).get(k),
               "truncated": doc.get("truncated", {}).get(f"labels.{k}")}
              for k, t in kinds if doc.get("labels", {}).get(k)]
    kind, stats = primary(doc)
    req = doc.get("overall", {}).get("request") or stats
    tiles = [
        ("Peak users", fmt.num(run.get("peak_users")), ""),
        (f"p95 response time ({kind}s)", fmt.ms(stats.get("p95_ms")), "steady state"),
        ("Error rate", fmt.pct(req.get("error_pct")), "requests, steady state"),
        ("Throughput", fmt.rate(stats.get("tps")), f"{kind}s per second"),
    ] if stats else [("Peak users", fmt.num(run.get("peak_users")), "")]
    check = narrative.numbers
    return {
        "doc": doc, "run": run, "sla": sla, "steady": doc.get("steady", {}), "audience": audience,
        "verdict": {"key": doc.get("verdict"), "icon": icon, "label": label, "cls": cls},
        "n": content, "narrative": narrative, "findings": findings, "tables": tables, "kind": kind,
        "failed_by_label": failed_by_label, "tiles": tiles, "errors": doc.get("errors", []),
        "skipped": doc.get("skipped", []), "truncated": doc.get("truncated", {}), "notes": doc.get("notes", []),
        "unverified": check.unverified if check else [], "number_check": check.describe() if check else "",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "timeline_rows": chart.series(doc),
        "baseline": doc.get("baseline"),
        "server": doc.get("server"),
        "server_value": _server_value,
        "regressions": [f for f in findings if f["type"] == "regression"],
    }


def render(doc: dict, narrative: Narrative, audience: str, out_format: str) -> str:
    from markupsafe import Markup
    ctx = context(doc, narrative, audience)
    if out_format == "html":
        ctx["chart"] = Markup(chart.timeline_svg(doc)) if audience == "tech" else ""
        ctx["chart_script"] = Markup(chart.SCRIPT)
    return _env().get_template(f"report_{audience}.{out_format}.j2").render(**ctx)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_report(ref: str, *, audience: str = "tech", formats: tuple[str, ...] = ("html",), use_llm: bool = True,
                 provider_spec: str | None = None, strict: bool = False, out: str | None = None,
                 fresh: bool = False, settings: dict[str, str] | None = None,
                 resolve_in_db: Callable[[str], str] | None = None,
                 progress: Callable[[str], None] = lambda _: None) -> Report:
    from projectstack.config import load_settings
    if audience not in AUDIENCES:
        raise ReportError(f"--audience must be one of {', '.join(AUDIENCES)}")
    settings = settings or load_settings()
    folder, doc = locate(ref, Path(settings["runs_dir"]), resolve_in_db)
    narrative, reused, warnings = get_narrative(doc, folder, audience, use_llm=use_llm, provider_spec=provider_spec,
                                                strict=strict, fresh=fresh, settings=settings, progress=progress)
    report = Report(doc["run"]["run_id"], audience, narrative, reused=reused, warnings=warnings)
    for f in formats:
        text = render(doc, narrative, audience, f)
        target = _target(out, folder, audience, f, len(formats))
        if target is None:
            report.stdout = text
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        report.files.append(target)
    return report


def _target(out: str | None, folder: Path, audience: str, out_format: str, n_formats: int) -> Path | None:
    name = f"report_{audience}.{out_format}"
    if not out:
        return folder / name
    if out == "-":
        if n_formats > 1:
            raise ReportError("--out - (standard output) takes a single --format")
        return None
    path = Path(out).expanduser()
    if path.is_dir() or out.endswith(("/", "\\")):
        return path / name
    return path.with_suffix(f".{out_format}") if n_formats > 1 else path
