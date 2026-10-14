"""
The words in a report.

An AI provider writes them from findings.json alone (nothing else about the run
is sent), returning JSON that fills fixed slots in the report template. Every
number in that JSON is checked against findings.json. Without an AI provider, or
with --no-llm, rules write the same slots from the findings, so a report never
depends on a model being available.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from projectstack.llm.base import LLMError, Provider
from projectstack.llm.prompts import load_prompt
from projectstack.results import numbers
from projectstack.results.fmt import duration, ms, num, offset, pct_text, rate

AUDIENCES = ("tech", "exec")
CONFIDENCE = ("high", "medium", "low")

_STR = {"type": "string"}
_INT_LIST = {"type": "array", "items": {"type": "integer"}}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


SCHEMAS: dict[str, dict[str, Any]] = {
    "tech": _obj({
        "summary": _STR,
        "timeline": {"type": "array", "items": _STR},
        "findings": {"type": "array", "items": _obj({"finding": {"type": "integer"}, "explanation": _STR})},
        "root_causes": {"type": "array", "items": _obj({
            "cause": _STR, "confidence": {"type": "string", "enum": list(CONFIDENCE)}, "findings": _INT_LIST,
            "reasoning": _STR, "how_to_confirm": _STR})},
        "next_tests": {"type": "array", "items": _obj({"test": _STR, "why": _STR})},
    }),
    "exec": _obj({
        "headline": _STR,
        "key_points": {"type": "array", "items": _STR},
        "business_impact": _STR,
        "recommended_action": _STR,
    }),
}


@dataclass
class Narrative:
    audience: str
    content: dict[str, Any]
    source: str                                  # "llm" | "rules"
    provider: str = ""                           # e.g. claude/claude-opus-5-5
    prompt: str = ""                             # e.g. report_tech.v1
    numbers: numbers.NumberCheck | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    note: str = ""                               # why rules wrote it, when an AI was asked for
    attempts: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {"audience": self.audience, "source": self.source, "provider": self.provider, "prompt": self.prompt,
                "numbers": self.numbers.as_dict() if self.numbers else None, "usage": self.usage, "note": self.note,
                "attempts": self.attempts, "generated_at": self.generated_at, "content": self.content}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Narrative":
        nc = d.get("numbers")
        return cls(d["audience"], d["content"], d["source"], d.get("provider", ""), d.get("prompt", ""),
                   numbers.NumberCheck(nc["checked"], nc["unverified"]) if nc else None, d.get("usage") or {},
                   d.get("note", ""), d.get("attempts", 0), d.get("generated_at", ""))


class NumberCheckFailed(LLMError):
    """--strict-numbers: the AI narrative kept citing numbers that are not in findings.json."""

    def __init__(self, narrative: Narrative):
        super().__init__(f"The AI narrative cites numbers that are not in findings.json "
                         f"({narrative.numbers.describe() if narrative.numbers else ''})")
        self.narrative = narrative


# ---------------------------------------------------------------------------
# AI narrative
# ---------------------------------------------------------------------------

def finding_index(doc: dict[str, Any]) -> str:
    lines = [f"{i}. [{f['severity']}] {f['type']}: {f['title']}" for i, f in enumerate(doc.get("findings", []), 1)]
    return "\n".join(lines) or "(no findings)"


def from_llm(doc: dict[str, Any], audience: str, provider: Provider, *, strict: bool = False) -> Narrative:
    prompt = load_prompt(f"report_{audience}")
    system, user = prompt.render(
        run_id=doc["run"]["run_id"], scenario=doc["run"].get("scenario", ""), finding_index=finding_index(doc),
        findings_json=json.dumps(doc, separators=(",", ":"), ensure_ascii=False),
    )
    allowed = numbers.Allowed(doc)
    usage = {"input_tokens": 0, "output_tokens": 0}

    def ask(text: str) -> tuple[dict[str, Any], numbers.NumberCheck, str]:
        comp = provider.complete(system, text, max_tokens=16000, json_schema=SCHEMAS[audience], effort="medium")
        usage["input_tokens"] += comp.input_tokens or 0
        usage["output_tokens"] += comp.output_tokens or 0
        content = coerce(audience, parse_json(comp.text), doc)
        return content, numbers.check(content, doc, allowed), comp.model

    content, check, model = ask(user)
    attempts = 1
    if strict and not check.ok:
        listed = "\n".join(f"- “{u['text']}” in {u['field']}" for u in check.unverified)
        content, check, model = ask(
            f"{user}\n\nYour previous answer was:\n<previous_answer>\n{json.dumps(content, ensure_ascii=False)}\n"
            f"</previous_answer>\n\nThese numbers in it do not appear in findings.json:\n{listed}\n\n"
            "Return the full JSON again with those statements rewritten: use figures that are in findings.json, "
            "or make the point in words.")
        attempts = 2
    label = f"{provider.name}/{model}" if model else provider.label
    narrative = Narrative(audience, content, "llm", label, prompt.ref, check, usage, attempts=attempts)
    if strict and not check.ok:
        raise NumberCheckFailed(narrative)
    return narrative


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer (tolerates code fences and stray prose around it)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("The model did not return JSON")
    try:
        value = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise LLMError(f"The model returned malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("The model returned JSON that is not an object")
    return value


def _s(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _strs(value: Any, limit: int) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [t for t in (_s(v) for v in items) if t][:limit]


def coerce(audience: str, data: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Shape a model's JSON into exactly the slots the template expects (models without schema
    enforcement drift); finding numbers outside the findings list are dropped."""
    n = len(doc.get("findings", []))

    def refs(value: Any) -> list[int]:
        out: list[int] = []
        for v in value if isinstance(value, list) else [value]:
            try:
                i = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= n and i not in out:
                out.append(i)
        return out

    if audience == "exec":
        return {"headline": _s(data.get("headline")), "key_points": _strs(data.get("key_points"), 5),
                "business_impact": _s(data.get("business_impact")),
                "recommended_action": _s(data.get("recommended_action"))}

    explanations: dict[int, str] = {}
    for item in data.get("findings") or []:
        if isinstance(item, dict):
            idx = refs(item.get("finding"))
            if idx and _s(item.get("explanation")):
                explanations.setdefault(idx[0], _s(item.get("explanation")))
    causes = []
    for item in data.get("root_causes") or []:
        if not isinstance(item, dict) or not _s(item.get("cause")):
            continue
        conf = _s(item.get("confidence")).lower()
        causes.append({"cause": _s(item.get("cause")), "confidence": conf if conf in CONFIDENCE else "low",
                       "findings": refs(item.get("findings")), "reasoning": _s(item.get("reasoning")),
                       "how_to_confirm": _s(item.get("how_to_confirm"))})
    causes.sort(key=lambda c: CONFIDENCE.index(c["confidence"]))     # stable: keeps the model's order within a level
    tests = [{"test": _s(t.get("test")), "why": _s(t.get("why"))}
             for t in data.get("next_tests") or [] if isinstance(t, dict) and _s(t.get("test"))]
    return {"summary": _s(data.get("summary")), "timeline": _strs(data.get("timeline"), 8),
            "findings": [{"finding": i, "explanation": explanations[i]} for i in sorted(explanations)],
            "root_causes": causes[:4], "next_tests": tests[:5]}


# ---------------------------------------------------------------------------
# Rule-based narrative
# ---------------------------------------------------------------------------

def primary(doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The statistics a headline should quote: transactions when the plan has them, else requests."""
    overall = doc.get("overall") or {}
    for kind in ("transaction", "request"):
        if overall.get(kind):
            return kind, overall[kind]
    return "request", {}


def _numbered(doc: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return list(enumerate(doc.get("findings", []), 1))


def _check_text(c: dict[str, Any], plain: bool = False) -> str:
    unit = c.get("unit", "")
    target, actual = c.get("target"), c.get("actual")
    if c["kind"] == "response_time":
        who = c.get("label") or "Overall"
        if plain:
            return (f"{who} was slower than agreed: 95% of responses took up to {ms(actual)} against a target of "
                    f"{ms(target)}" if c.get("metric") == "p95" else
                    f"{who} was slower than agreed: {c.get('metric')} {ms(actual)} against a target of {ms(target)}")
        return f"{who} {c.get('metric')} {ms(actual)} against a target of {ms(target)}"
    if c["kind"] == "error_rate":
        return (f"{pct_text(actual)} of requests failed, above the {pct_text(target)} limit" if plain else
                f"error rate {pct_text(actual)} against a limit of {pct_text(target)}")
    if c["kind"] == "throughput":
        return (f"the system handled {rate(actual)} requests, below the required {rate(target)}" if plain else
                f"throughput {rate(actual)} against a minimum of {rate(target)}")
    return f"{c.get('metric')} {actual}{unit} against {target}{unit}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _level_load(ev: dict[str, Any]) -> bool:
    a, b = ev.get("users_before"), ev.get("users_after")
    return bool(a and b) and abs(b - a) <= 0.1 * max(a, b)


def _explain(f: dict[str, Any]) -> str:
    ev = f.get("evidence", {})
    t = f["type"]
    if t == "sla_breach":
        over = ""
        if ev.get("target") and ev.get("kind") == "response_time":
            over = f" ({(ev['actual'] / ev['target'] - 1) * 100:.0f}% over)"
        return (f"{_cap(_check_text(ev | {'label': (f.get('labels') or [None])[0]}))}{over}. The SLA is measured "
                "over the steady state, so this is sustained behaviour at full load, not a brief spike.")
    if t == "latency_change":
        if _level_load(ev):
            return ("Response times stepped up while the load stayed level, which points to something degrading "
                    "inside the system over time rather than to the load itself.")
        return (f"Response times rose as the load moved from {num(ev.get('users_before'))} to "
                f"{num(ev.get('users_after'))} users, so the slowdown is tied to the extra load.")
    if t == "error_rate_change":
        return ("The error rate rose and stayed up, so a component started failing under sustained load "
                "rather than briefly.")
    if t == "saturation":
        return (f"Beyond about {num(ev.get('knee_users'))} users, adding load no longer increased throughput while "
                "response times rose: the system hit a capacity limit and requests queued instead of being served.")
    if t == "error_burst":
        causes = ev.get("causes") or []
        what = f" ({causes[0].get('code')} {causes[0].get('message')})".rstrip() if causes else ""
        return (f"A short burst of failures{what} that cleared on its own, typical of a dependency or resource that "
                "was briefly unavailable or overloaded.")
    if t == "injector_connect":
        return ("Connection setup slowed down. This is often the load generator or the network (ephemeral ports, "
                "DNS, TLS handshakes) rather than the application, so rule those out before blaming the server.")
    if t == "correlation":
        return {
            "missing_value": "Requests went out with a placeholder instead of a real value, so the server got "
                             "invalid input. This is a script problem; these results do not reflect real users.",
            "auth_onset": "The users' token or session stopped being accepted partway through, so later requests "
                          "measured rejections rather than the system's real work.",
            "auth_always": "These requests never authenticated, so their timings are for rejection responses.",
            "login_redirect": "The server treated these users as logged out; their timings are for the login page, "
                              "not for the page the script asked for.",
            "auth_message": "The server rejected these users' sessions. The failures come from the script, not "
                            "from the load.",
        }.get(ev.get("pattern"), "A problem in the test script rather than in the system: a dynamic value was not "
                                  "captured or refreshed.")
    if t == "regression":
        if not ev.get("comparable", True):
            return ("The load differed from the baseline run, so this difference is shown for information and is "
                    "not counted as a regression.")
        worse = "got slower" if ev.get("metric") == "p95_ms" else "failed more often"
        return (f"Compared with the baseline run at comparable load, these labels {worse}. With the load the same, "
                "the difference comes from the system under test (code, configuration, data or environment), "
                "not from the test.")
    if t == "injector_throughput":
        return ("Throughput dropped while response times stayed flat, so the load generator most likely stalled "
                "(CPU, garbage collection or network); this period ran below the intended load.")
    return f.get("detail", "")


_CAUSES = {
    # type: (cause, confidence, reasoning, how to confirm)
    "latency_change_level": (
        "Server-side degradation over time at constant load, such as a filling connection or thread pool, "
        "memory pressure and garbage collection, or a background job",
        "medium",
        "Response times stepped up while users and throughput stayed level, so the change came from inside the "
        "system, not from more load.",
        "Compare application CPU, GC pauses, heap use, pool usage and database wait times before and after the "
        "change point; check scheduled jobs running at that time."),
    "latency_change_load": (
        "Response times grow with load: a shared resource gets slower as concurrency rises",
        "medium",
        "The step in response times lines up with an increase in users.",
        "Run a stepping test and watch CPU, pool usage and database waits at each step."),
    "saturation": (
        "A capacity limit: a bottleneck resource (CPU, database connections, thread pool or a downstream "
        "service) is fully used at the saturation point",
        "high",
        "Throughput stopped scaling with users while response times rose, the classic queueing pattern.",
        "Find the resource at 100% at that load: server CPU, pool sizes and their wait counts, database "
        "connections, downstream latency."),
    "error_burst": (
        "A dependency or server resource was briefly unavailable or overloaded",
        "medium",
        "Failures appeared in a short window with the same response code and cleared without the load changing.",
        "Check server, load balancer and dependency logs for that window: restarts, failovers, pool "
        "exhaustion, rate limiting."),
    "error_rate_change": (
        "A component started failing under sustained load and did not recover",
        "medium",
        "The error rate stepped up and stayed high for the rest of the run.",
        "Group the errors by response code and label in the errors table, then check the logs of the service "
        "behind the failing requests."),
    "injector": (
        "The load generator or the network between it and the system, not the application",
        "medium",
        "Connection times or throughput changed in a way that points at the client side of the test.",
        "Check CPU, memory, GC and network on the JMeter machine for that window; re-run with more load "
        "generators or fewer threads each."),
    "correlation": (
        "Broken correlation in the test script: a token, session or ID was not extracted or refreshed",
        "high",
        "Requests were sent with a missing value or rejected as unauthenticated, which a real user's session would "
        "not do.",
        "Apply the fix named in the correlation finding (pstack health shows the extractor and the sampler it sits "
        "on), then run a short smoke test before the next load test."),
    "regression": (
        "A change since the baseline run: application code, configuration, data volume or the test environment",
        "medium",
        "The same scenario at comparable load did better in the baseline run.",
        "Diff the builds and configuration between the two runs, and re-run the baseline build in this "
        "environment to rule out environment drift."),
    "sla_only": (
        "The system is consistently slower or less reliable than the targets at this load, with no single event "
        "to blame",
        "low",
        "The SLA failed over the steady state but no change point, burst or saturation was detected.",
        "Profile the slowest transaction at low load to separate its inherent latency from load effects."),
}


def _causes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = {}
    for i, f in _numbered(doc):
        t = f["type"]
        key = (("latency_change_level" if _level_load(f.get("evidence", {})) else "latency_change_load")
               if t == "latency_change" else "injector" if t.startswith("injector") else t)
        if t == "regression" and f["severity"] == "info":
            continue        # load differed: not evidence of a change in the system
        if key in _CAUSES:
            groups.setdefault(key, []).append(i)
    if not groups and any(f["type"] == "sla_breach" for f in doc.get("findings", [])):
        groups["sla_only"] = [i for i, f in _numbered(doc) if f["type"] == "sla_breach"]
    out = [{"cause": _CAUSES[k][0], "confidence": _CAUSES[k][1], "findings": refs, "reasoning": _CAUSES[k][2],
            "how_to_confirm": _CAUSES[k][3]} for k, refs in groups.items()]
    out.sort(key=lambda c: CONFIDENCE.index(c["confidence"]))
    return out[:4]


def _next_tests(doc: dict[str, Any]) -> list[dict[str, str]]:
    types = {f["type"] for f in doc.get("findings", [])}
    level = any(f["type"] == "latency_change" and _level_load(f.get("evidence", {})) for f in doc.get("findings", []))
    tests: list[dict[str, str]] = []
    if "saturation" in types:
        tests.append({"test": "A stepping test from half the saturation load up to just past it, with server "
                              "monitoring at every step",
                      "why": "Shows which resource runs out first and the highest load that still meets the targets."})
    if level:
        tests.append({"test": "A soak test at the same load for several hours, collecting heap, GC and pool metrics",
                      "why": "Shows whether the degradation keeps growing (a leak or exhaustion) or levels off."})
    if types & {"error_burst", "error_rate_change"}:
        tests.append({"test": "Repeat the run with server and dependency logs captured at the time of the errors",
                      "why": "Identifies the component that returned the errors."})
    if "correlation" in types:
        tests.insert(0, {"test": "Fix the correlation, then run a one-user smoke test of the script",
                         "why": "Until requests carry valid tokens and sessions, the load test measures error pages."})
    if any(f["type"] == "regression" and f["severity"] != "info" for f in doc.get("findings", [])):
        tests.append({"test": "Re-run the baseline build in the same environment",
                      "why": "Separates a change in the application from drift in the environment or test data."})
    if any(t.startswith("injector") for t in types):
        tests.append({"test": "Re-run with the load spread over more load generators, monitoring their CPU and network",
                      "why": "Separates load-generator limits from real system behaviour."})
    if "sla_breach" in types and len(tests) < 2:
        kind, _ = primary(doc)
        tests.append({"test": f"Profile the slowest {kind}s at low load",
                      "why": "Separates their inherent latency from the effect of concurrency."})
    if not doc.get("sla", {}).get("defined"):
        tests.append({"test": "Re-run with SLA targets (pstack analyze --sla …)",
                      "why": "Gives the run a pass or fail verdict that can gate releases."})
    if not tests:
        tests.append({"test": "Raise the load in steps beyond the peak of this run",
                      "why": "Finds how much headroom the system has before targets are missed."})
    return tests[:4]


def _tech(doc: dict[str, Any]) -> dict[str, Any]:
    run, sla, steady = doc["run"], doc.get("sla", {}), doc.get("steady", {})
    kind, stats = primary(doc)
    verdict = doc.get("verdict")
    lead = {
        "fail": f"The run failed its SLA: {num(sla.get('failed_count'))} check(s) missed "
                f"({'; '.join(_check_text(c) for c in sla.get('checks', []) if not c['passed'])}).",
        "at_risk": "The run met its SLA, but the detectors found problems that put it at risk.",
        "pass": "The run met its SLA and the detectors found no significant problems.",
        "no_sla": "No SLA was defined, so the run has no pass or fail verdict.",
    }.get(verdict, "")
    req = (doc.get("overall") or {}).get("request") or stats
    load = (f"It reached {num(run.get('peak_users'))} users over {duration(run.get('duration_s'))}; in the steady "
            f"state {kind} p95 was {ms(stats.get('p95_ms'))} at {num(stats.get('tps'), 1)} {kind}s per second, and "
            f"{pct_text(req.get('error_pct'))} of requests failed.") if stats else ""
    others = [f for f in doc.get("findings", []) if f["type"] != "sla_breach" and f["severity"] in ("critical", "warning")]
    top = f"Most significant event: {others[0]['title'][0].lower()}{others[0]['title'][1:]}." if others else ""
    summary = " ".join(x for x in (lead, load, top) if x)

    timeline: list[str] = []
    points = doc.get("timeline", {}).get("points", [])
    peak = run.get("peak_users")
    if points and peak:
        first = next((p for p in points if p["users"] == peak), None)
        start_users = points[0]["users"]
        if first and start_users < peak:
            timeline.append(f"Load ramped from {num(start_users)} to {num(peak)} users, reaching the peak at "
                            f"{offset(first['t_offset_s'])}.")
    if steady:
        timeline.append(f"Statistics use the steady state from {offset(steady.get('start_offset_s'))} to "
                        f"{offset(steady.get('end_offset_s'))} ({duration(steady.get('duration_s'))}).")
    events = sorted(((i, f) for i, f in _numbered(doc) if f["type"] != "sla_breach"),
                    key=lambda x: x[1].get("start_offset_s") or 0)
    for i, f in events[:4]:
        timeline.append(f"{f['title']} (finding {i}).")
    if not events:
        timeline.append("Response times and error rates stayed stable: no change points, error bursts or "
                        "saturation were detected.")
    return {
        "summary": summary,
        "timeline": timeline,
        "findings": [{"finding": i, "explanation": _explain(f)} for i, f in _numbered(doc)],
        "root_causes": _causes(doc),
        "next_tests": _next_tests(doc),
    }


def _exec(doc: dict[str, Any]) -> dict[str, Any]:
    run, sla = doc["run"], doc.get("sla", {})
    kind, stats = primary(doc)
    verdict = doc.get("verdict")
    users = num(run.get("peak_users"))
    failed = [c for c in sla.get("checks", []) if not c["passed"]]
    problems = [f for f in doc.get("findings", []) if f["type"] != "sla_breach" and f["severity"] in ("critical", "warning")]
    scenario = run.get("scenario") or "the tested journey"

    slow = any(c["kind"] == "response_time" for c in failed) or any(
        f["type"] in ("latency_change", "saturation") or (f["type"] == "regression" and
                                                          f["evidence"].get("metric") == "p95_ms") for f in problems)
    errs = any(c["kind"] == "error_rate" for c in failed) or any(
        f["type"].startswith("error") or (f["type"] == "regression" and f["evidence"].get("metric") == "error_pct")
        for f in problems)
    regressed = {f["evidence"].get("metric") for f in problems if f["type"] == "regression"}
    missed = {c["kind"] for c in failed}
    if verdict == "fail":
        why = " and ".join(text for k, text in (("response_time", "responses were slower than agreed"),
                                                ("error_rate", "too many requests failed"),
                                                ("throughput", "throughput was below target")) if k in missed)
        headline = f"The test missed its performance targets at {users} users: {why or 'see the key points'}."
    elif verdict == "at_risk" and regressed:
        worse = " and ".join(x for x in ("got slower" if "p95_ms" in regressed else "",
                                         "failed more often" if "error_pct" in regressed else "") if x)
        headline = f"The targets were met at {users} users, but the system {worse} than in the baseline run."
    elif verdict == "at_risk":
        headline = f"The targets were met at {users} users, but the test found problems that could break them."
    elif verdict == "pass":
        headline = f"The system met its performance targets at {users} users."
    else:
        headline = (f"No performance targets were set; at {users} users the system "
                    f"{'showed serious problems' if problems else 'ran without serious problems'}.")

    points = [f"{_cap(_check_text(c, plain=True))}." for c in failed]
    points += [_plain(f) for f in problems]
    if stats:
        failed_kinds = {c["kind"] for c in failed}
        if "response_time" not in failed_kinds:
            points.append(f"95% of {kind}s completed within {ms(stats.get('p95_ms'))} at full load.")
        if "error_rate" not in failed_kinds:
            req = (doc.get("overall") or {}).get("request") or stats
            points.append(f"{pct_text(req.get('error_pct'))} of requests failed.")
        if "throughput" not in failed_kinds:
            points.append(f"The system handled {num(stats.get('tps'), 1)} {kind}s per second at {users} users.")
    points = points[:5]

    if verdict in ("fail", "at_risk") or problems:
        wait = ("would wait longer than agreed" if "response_time" in missed else
                "would get slower responses than in the baseline run" if "p95_ms" in regressed else
                "would get slower responses")
        fails = ("would see more requests fail than agreed" if "error_rate" in missed else
                 "would see some requests fail")
        effects = " and ".join(x for x in (wait if slow else "", fails if errs else "") if x)
        consequence = ("If production traffic reaches this level, expect a slower experience and possibly lost or "
                       "abandoned transactions until the causes are fixed." if missed or errs or not sla.get("defined")
                       else "The targets still hold, but with less margin, so a further slowdown could break them.")
        impact = (f"At the tested load of {users} users, people using {scenario} {effects or 'could be affected'}. "
                  f"{consequence}")
    elif verdict == "pass":
        impact = (f"At the tested load of {users} users, people using {scenario} would get responses within the "
                  "agreed targets. Higher traffic than tested has not been verified.")
    else:
        impact = ("Without agreed targets there is no objective line between acceptable and unacceptable "
                  "performance, so the business risk cannot be judged from this run.")
    action = {
        "fail": "Hold the release for this load level until the issues are fixed, then re-run this test to confirm.",
        "at_risk": ("Find what changed since the baseline build and fix it before releasing, then re-run against the "
                    "same baseline." if regressed else
                    "Release only with close monitoring in place, and fix the issues found before traffic grows."),
        "pass": "No performance blocker at the tested load; test at higher load to learn the remaining headroom.",
        "no_sla": "Agree response-time and error-rate targets with the business, then re-run so the result can "
                  "be judged.",
    }.get(verdict, "")
    return {"headline": headline, "key_points": points, "business_impact": impact, "recommended_action": action}


def _minutes_in(seconds: Any) -> str:
    return f"{round((seconds or 0) / 60)} minutes"


def _plain(f: dict[str, Any]) -> str:
    """A finding in words for the executive summary."""
    ev, t = f.get("evidence", {}), f["type"]
    if t == "latency_change":
        steady_load = " even though the load did not change" if _level_load(ev) else " as the load increased"
        return (f"About {_minutes_in(f.get('start_offset_s'))} into the test, responses became much slower (from "
                f"{ms(ev.get('before'))} to {ms(ev.get('after'))} for 95% of requests){steady_load}.")
    if t == "error_burst":
        causes = ev.get("causes") or []
        what = f" ({causes[0].get('code')} {causes[0].get('message')})" if causes else ""
        span = (f.get("end_offset_s") or 0) - (f.get("start_offset_s") or 0)
        return (f"For {_minutes_in(span)}, about {_minutes_in(f.get('start_offset_s'))} in, up to "
                f"{pct_text(ev.get('peak_error_pct'))} of requests failed{what} before the system recovered.")
    if t == "error_rate_change":
        return (f"From about {_minutes_in(f.get('start_offset_s'))} in, the share of failing requests rose to "
                f"{pct_text(ev.get('after'))} and stayed there.")
    if t == "saturation":
        return (f"The system stopped scaling at about {num(ev.get('knee_users'))} users: adding users no longer "
                "increased the work done, it only made responses slower.")
    if t == "correlation":
        return ("Part of the test did not behave like a real user: the script failed to carry a login token or "
                "session along, so some results show errors that real users would not see. The script needs a fix "
                "before these results can be trusted.")
    if t == "regression":
        labels = ev.get("labels") or []
        n = ev.get("count") or len(labels)
        what = f"{n} {'step' if n == 1 else 'steps'} of the journey"
        if not labels:
            return f"{f['title']}."
        w = labels[0]
        if ev.get("metric") == "p95_ms":
            return (f"Compared with the baseline run, {what} got slower; the worst, {w['label']}, went from "
                    f"{ms(w.get('p95_before'))} to {ms(w.get('p95_after'))}.")
        return (f"Compared with the baseline run, {what} failed more often; the worst, {w['label']}, went "
                f"from {pct_text(w.get('err_before'))} to {pct_text(w.get('err_after'))} of requests failing.")
    if t.startswith("injector"):
        return ("Part of the test was limited by the machine generating the load, so those minutes ran below the "
                "intended load and say little about the system.")
    return f"{f['title']}."


def from_rules(doc: dict[str, Any], audience: str, note: str = "") -> Narrative:
    content = _tech(doc) if audience == "tech" else _exec(doc)
    return Narrative(audience, content, "rules", note=note)
