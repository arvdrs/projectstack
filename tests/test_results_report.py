"""
Results analysis, phase 3: the shared LLM layer (prompts, per-task providers),
the number check, rule-based and AI narratives, report rendering and the CLI.
AI providers are replaced by fakes; nothing here calls a real model.
"""

import copy
import json
import secrets
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from projectstack.llm import registry
from projectstack.llm.base import Completion, LLMConfigError, LLMError, LLMRefusal, Provider, ToolSpec
from projectstack.llm.prompts import load_prompt
from projectstack.results import numbers
from projectstack.results.narrative import (
    SCHEMAS, NumberCheckFailed, coerce, from_llm, from_rules, parse_json,
)
from projectstack.results.report import build_report, locate, render, ReportError

START = "2025-10-09T09:00:00+00:00"


def sample_doc() -> dict:
    """A findings.json like `pstack analyze` writes: SLA failed, a latency step and an error burst."""
    return {
        "schema": "projectstack.findings/1",
        "run": {"run_id": "20261013-084506-build-119", "scenario": "checkout", "tag": "build-119", "start": START,
                "end": "2025-10-09T09:42:00+00:00", "duration_s": 2520.0, "samples": 346975, "peak_users": 50,
                "users_source": "allThreads", "columns": ["timeStamp", "elapsed"],
                "labels": {"request": 2, "transaction": 1, "subresult": 0}},
        "steady": {"start_ms": 1760000340000, "end_ms": 1760002340000, "source": "auto", "duration_s": 2000.0,
                   "method": "allThreads", "plateau_users": 50, "note": "50 users held from t+280s to t+2400s",
                   "start": "2025-10-09T09:05:40+00:00", "end": "2025-10-09T09:39:00+00:00",
                   "start_offset_s": 340, "end_offset_s": 2340},
        "verdict": "fail",
        "sla": {"defined": True, "targets": ["p95 ≤ 1000 ms per transaction", "errors ≤ 1%"], "passed": False,
                "passed_count": 0, "failed_count": 2,
                "checks": [{"kind": "response_time", "label": "TC Checkout", "metric": "p95", "unit": "ms",
                            "target": 1000.0, "actual": 1801.0, "passed": False},
                           {"kind": "error_rate", "label": None, "metric": "error_rate", "unit": "%",
                            "target": 1.0, "actual": 1.86, "passed": False}]},
        "overall": {"request": {"samples": 200986, "avg_ms": 514.6, "min_ms": 63, "max_ms": 2493, "p50_ms": 511,
                                "p90_ms": 879, "p95_ms": 992, "p99_ms": 1238, "errors": 3739, "error_pct": 1.86,
                                "tps": 100.192},
                    "transaction": {"samples": 100001, "avg_ms": 1029.1, "min_ms": 188, "max_ms": 3242, "p50_ms": 1116,
                                    "p90_ms": 1649, "p95_ms": 1801, "p99_ms": 2108, "errors": 1829,
                                    "error_pct": 1.829, "tps": 49.851}},
        "labels": {
            "request": [
                {"label": "Step 1 /app/page/1", "samples": 100500, "avg_ms": 515.1, "p50_ms": 511, "p90_ms": 879,
                 "p95_ms": 992, "p99_ms": 1239, "max_ms": 2493, "error_pct": 1.857, "tps": 50.1},
                {"label": "<script>alert(1)</script> | pipe", "samples": 100486, "avg_ms": 514.0, "p50_ms": 510,
                 "p90_ms": 876, "p95_ms": 990, "p99_ms": 1239, "max_ms": 1916, "error_pct": 1.891, "tps": 50.09},
            ],
            "transaction": [
                {"label": "TC Checkout", "samples": 100001, "avg_ms": 1029.1, "p50_ms": 1116, "p90_ms": 1649,
                 "p95_ms": 1801, "p99_ms": 2108, "max_ms": 3242, "error_pct": 1.829, "tps": 49.851}],
        },
        "timeline": {"bucket_s": 60, "users_source": "allThreads", "points": [
            {"t_offset_s": 60 * i, "users": min(50, 10 * (i + 1)), "tps": 100.0 if i > 4 else 20.0 * (i + 1),
             "p95_ms": 362 if i < 18 else 1066, "samples": 6000, "errors": 1500 if i in (30, 31) else 24,
             "error_pct": 25.0 if i in (30, 31) else 0.4} for i in range(42)]},
        "errors": [{"label": "Step 1 /app/page/1", "response_code": "503", "response_message": "Service Unavailable",
                    "failure": "", "count": 676, "first_offset_s": 68, "last_offset_s": 2465}],
        "findings": [
            {"type": "sla_breach", "severity": "critical", "title": "SLA breach: TC Checkout p95 1,801 ms > 1,000 ms",
             "detail": "Measured over the steady state (auto window).", "start_ms": 1760000340000,
             "end_ms": 1760002340000, "labels": ["TC Checkout"],
             "evidence": {"kind": "response_time", "metric": "p95", "target": 1000.0, "actual": 1801.0, "unit": "ms"},
             "start_offset_s": 340, "end_offset_s": 2340},
            {"type": "sla_breach", "severity": "critical", "title": "SLA breach: error rate 1.86% > 1%",
             "detail": "Measured over the steady state (auto window).", "start_ms": 1760000340000,
             "end_ms": 1760002340000, "labels": [],
             "evidence": {"kind": "error_rate", "metric": "error_rate", "target": 1.0, "actual": 1.86, "unit": "%"},
             "start_offset_s": 340, "end_offset_s": 2340},
            {"type": "latency_change", "severity": "critical", "title": "p95 rose from 362 ms to 1,066 ms at t+18m",
             "detail": "Response times stepped up 194% while the load stayed at about 50 users.",
             "start_ms": 1760001080000, "end_ms": 1760002520000, "labels": ["Step 1 /app/page/1"],
             "evidence": {"metric": "p95_ms", "before": 362, "after": 1066, "increase_pct": 194.5,
                          "users_before": 50, "users_after": 50, "tps_before": 99.97, "tps_after": 100.08,
                          "labels": [{"label": "Step 1 /app/page/1", "p95_before": 358, "p95_after": 1068}]},
             "start_offset_s": 1080, "end_offset_s": 2520},
            {"type": "error_burst", "severity": "critical",
             "title": "Error burst at t+30m for 2 min: up to 25.4% failed, mostly 503 Service Unavailable",
             "detail": "2,982 of 11,985 requests failed in this window, against a typical error rate of 0.40%.",
             "start_ms": 1760002200000, "end_ms": 1760002320000, "labels": ["Step 1 /app/page/1"],
             "evidence": {"errors": 2982, "samples": 11985, "peak_error_pct": 25.42, "typical_error_pct": 0.4,
                          "causes": [{"code": "503", "message": "Service Unavailable", "count": 2982}]},
             "start_offset_s": 1800, "end_offset_s": 1920},
        ],
        "skipped": [],
        "truncated": {},
        "notes": ["Percentiles are exact over the samples in scope."],
        "size_bytes": 9000,
    }


GOOD_TECH = {
    "summary": "The run failed: TC Checkout p95 was 1,801 ms (1.8 s) against 1,000 ms, about 80% over, and 1.86% of "
               "requests failed at 50 users.",
    "timeline": ["At t+18m (09:18 UTC) p95 rose from 362 ms to 1,066 ms, roughly 2.9x, while throughput held near "
                 "100 requests/s.",
                 "Between t+30m and t+32m up to 25.4% of requests failed with 503 errors."],
    "findings": [{"finding": 3, "explanation": "A step change at constant load."},
                 {"finding": 4, "explanation": "A two-minute outage of a dependency."}],
    "root_causes": [{"cause": "Connection pool exhaustion", "confidence": "medium", "findings": [3],
                     "reasoning": "Latency rose 194% at a flat 50 users.", "how_to_confirm": "Check pool metrics."}],
    "next_tests": [{"test": "Soak test at the same load", "why": "Shows whether degradation continues."}],
}


class FakeProvider(Provider):
    name = "fake"
    default_model = "fake-1"

    def __init__(self, answers, settings=None):
        super().__init__(settings or {}, None)
        self.answers = list(answers)
        self.calls = []

    def complete(self, system, prompt, *, max_tokens=8192, json_schema=None, effort=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema, "effort": effort})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(answer if isinstance(answer, str) else json.dumps(answer), "fake", "fake-1", "end_turn", 100, 50)


# ---------------------------------------------------------------------------
# Prompts and provider selection
# ---------------------------------------------------------------------------

def test_prompts_are_versioned_and_need_every_value():
    for name in ("report_tech", "report_exec", "correlation_detect"):
        p = load_prompt(name)
        assert p.ref == f"{name}.v1" and p.user and (p.system or name == "correlation_detect")
    tech = load_prompt("report_tech.v1")
    assert "only numbers that appear in findings.json" in tech.system
    with pytest.raises(KeyError, match="findings_json"):
        tech.render(run_id="r", scenario="s", finding_index="1. x")
    system, user = tech.render(run_id="r1", scenario="checkout", finding_index="1. x", findings_json='{"a": 1}')
    assert '{"a": 1}' in user and "{{" not in system + user
    with pytest.raises(FileNotFoundError):
        load_prompt("report_tech.v99")


def test_task_resolution(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert registry.resolve("report", {"anthropic_api_key": "k", "gemini_api_key": "g"}) == ("claude", None)
    assert registry.resolve("report", {"gemini_api_key": "g", "ai_provider": "groq"}) == ("gemini", None)
    assert registry.resolve("ask", {"ai_provider": "groq"}) == ("groq", None)
    assert registry.resolve("correlation", {"anthropic_api_key": "k", "ai_provider": "ollama"}) == ("ollama", None)
    tasks = {"llm_tasks": json.dumps({"report": "groq/llama-3.3-70b-versatile"})}
    assert registry.resolve("report", tasks) == ("groq", "llama-3.3-70b-versatile")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert registry.resolve("report", {}) == ("claude", None)
    with pytest.raises(LLMConfigError, match="llm_tasks"):
        registry.resolve("report", {"llm_tasks": "{not json"})


def test_provider_for_task_override_and_models():
    settings = {"groq_api_key": "x", "anthropic_api_key": "y", "llm_models": json.dumps({"claude": "claude-sonnet-5-5"})}
    p = registry.provider_for_task("report", settings, "groq/llama-x")
    assert (p.name, p.model) == ("groq", "llama-x")
    assert registry.provider_named("claude", settings).model == "claude-sonnet-5-5"
    assert registry.provider_named("claude", {"anthropic_api_key": "y"}).model == "claude-opus-5-5"
    with pytest.raises(LLMConfigError, match="Unknown AI provider"):
        registry.provider_for_task("report", settings, "nope")
    with pytest.raises(LLMConfigError, match="Gemini"):
        registry.provider_named("gemini", {})


# ---------------------------------------------------------------------------
# Providers (SDK clients replaced by fakes)
# ---------------------------------------------------------------------------

def _claude_response(text="{}", stop="end_turn", blocks=None):
    content = blocks or [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop, model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_details=None)


def _claude(monkeypatch, responses, fallbacks=False):
    from projectstack.llm.providers import claude
    monkeypatch.setattr(claude, "_sdk_supports_fallbacks", lambda: fallbacks)
    p = claude.ClaudeProvider({"anthropic_api_key": "test-key"})
    calls = []

    def create(**kw):
        calls.append(kw)
        return responses.pop(0)
    p.client = SimpleNamespace(messages=SimpleNamespace(create=create),
                               beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return p, calls


def test_claude_structured_output_request(monkeypatch):
    p, calls = _claude(monkeypatch, [_claude_response('{"headline": "x"}')])
    out = p.complete("sys", "prompt", json_schema=SCHEMAS["exec"], effort="medium")
    assert out.text == '{"headline": "x"}' and out.model == "claude-opus-5-5"
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "sys"
    assert kw["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMAS["exec"]}}
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(kw)


def test_claude_fallbacks_refusal_and_truncation(monkeypatch):
    p, calls = _claude(monkeypatch, [_claude_response()], fallbacks=True)
    p.complete("", "prompt")
    assert calls[0]["fallbacks"] == "default" and calls[0]["betas"] == ["server-side-fallback-2026-07-01"]
    p, _ = _claude(monkeypatch, [_claude_response(stop="refusal")])
    with pytest.raises(LLMRefusal):
        p.complete("", "prompt")
    p, _ = _claude(monkeypatch, [_claude_response(stop="max_tokens")])
    with pytest.raises(LLMError, match="cut off"):
        p.complete("", "prompt")


def test_claude_tool_turn_keeps_content(monkeypatch):
    blocks = [SimpleNamespace(type="thinking", thinking="…"), SimpleNamespace(type="text", text="Looking."),
              SimpleNamespace(type="tool_use", id="t1", name="run_sql", input={"sql": "SELECT 1"})]
    p, calls = _claude(monkeypatch, [_claude_response(stop="tool_use", blocks=blocks)])
    tool = ToolSpec("run_sql", "Run a query", {"type": "object", "properties": {"sql": {"type": "string"}},
                                               "required": ["sql"], "additionalProperties": False})
    turn = p.complete_with_tools("sys", [p.user_message("hi")], [tool])
    assert calls[0]["tools"][0]["strict"] is True
    assert turn.tool_calls[0].arguments == {"sql": "SELECT 1"}
    assert turn.messages == [{"role": "assistant", "content": blocks}]       # thinking block kept, unchanged
    results = p.tool_results([(turn.tool_calls[0], "1", False)])
    assert results[0]["content"][0]["tool_use_id"] == "t1"


def test_openai_compatible_request():
    from projectstack.llm.providers.openai_compat import GroqProvider
    p = GroqProvider({"groq_api_key": "k"})
    calls = []
    msg = SimpleNamespace(content='{"a": 1}', refusal=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="llama",
                           usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or resp)))
    out = p.complete("sys", "prompt", json_schema=SCHEMAS["exec"])
    assert out.text == '{"a": 1}'
    assert calls[0]["temperature"] == 0.0 and calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in calls[0]["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Number check
# ---------------------------------------------------------------------------

def test_number_check_accepts_figures_from_findings():
    doc = sample_doc()
    result = numbers.check(GOOD_TECH, doc)
    assert result.ok, result.unverified
    assert result.checked >= 15


@pytest.mark.parametrize("text, bad", [
    ("Expect 75 users to fail.", "75"),
    ("p95 reached 4,200 ms.", "4,200"),
    ("At 10:45 UTC the errors began.", "10:45"),
    ("The burst started at t+55m.", "t+55m"),
    ("Errors hit 12.5% of requests.", "12.5"),
    ("Run it again on 2025-11-01.", "2025-11-01"),
])
def test_number_check_flags_invented_figures(text, bad):
    result = numbers.check({"summary": text}, sample_doc())
    assert [u["text"] for u in result.unverified] == [bad]
    assert result.unverified[0]["field"] == "summary"


def test_number_check_ignores_labels_and_names():
    text = ("Step 1 /app/page/1 and the <script>alert(1)</script> | pipe label in build-119 "
            "(run 20261013-084506-build-119) were hit by HTTP 503 and 5xx errors; see findings 1, 2 and 4, "
            "the 95th percentile, and p99. About 1,100 ms; 41m 59s long; 347k samples; 33 minutes steady.")
    result = numbers.check({"s": text}, sample_doc())
    assert result.ok, result.unverified


# ---------------------------------------------------------------------------
# Narratives
# ---------------------------------------------------------------------------

def test_parse_and_coerce_model_output():
    raw = "Here you go:\n```json\n" + json.dumps({
        "summary": "  The   run failed. ", "timeline": "one line",
        "findings": [{"finding": 9, "explanation": "out of range"}, {"finding": "3", "explanation": "ok"},
                     {"finding": 3, "explanation": "duplicate"}],
        "root_causes": [{"cause": "B", "confidence": "Low", "findings": [1, 99]},
                        {"cause": "A", "confidence": "certain", "findings": []},
                        {"cause": "C", "confidence": "high", "findings": [2]}],
        "next_tests": [{"test": f"t{i}", "why": ""} for i in range(9)],
    }) + "\n```"
    content = coerce("tech", parse_json(raw), sample_doc())
    assert content["summary"] == "The run failed."
    assert content["timeline"] == ["one line"]
    assert content["findings"] == [{"finding": 3, "explanation": "ok"}]
    assert [c["cause"] for c in content["root_causes"]] == ["C", "B", "A"]
    assert content["root_causes"][1]["findings"] == [1]
    assert len(content["next_tests"]) == 5
    with pytest.raises(LLMError):
        parse_json("no json here")
    exec_content = coerce("exec", {"headline": "h", "key_points": [f"p{i}" for i in range(8)]}, sample_doc())
    assert len(exec_content["key_points"]) == 5 and exec_content["business_impact"] == ""


def test_llm_narrative_sends_only_findings_and_checks_numbers():
    doc = sample_doc()
    fake = FakeProvider([GOOD_TECH])
    n = from_llm(doc, "tech", fake)
    call = fake.calls[0]
    assert call["schema"] == SCHEMAS["tech"] and call["effort"] == "medium"
    assert json.dumps(doc, separators=(",", ":"), ensure_ascii=False) in call["prompt"]
    assert "1. [critical] sla_breach" in call["prompt"]
    assert n.source == "llm" and n.prompt == "report_tech.v1" and n.provider == "fake/fake-1"
    assert n.numbers.ok and n.attempts == 1
    assert n.usage == {"input_tokens": 100, "output_tokens": 50}


def test_unverified_numbers_flagged_or_retried():
    doc = sample_doc()
    bad = {**GOOD_TECH, "summary": "p95 will reach 4,200 ms at 75 users."}
    n = from_llm(doc, "tech", FakeProvider([bad]))
    assert [u["text"] for u in n.numbers.unverified] == ["4,200", "75"]

    fake = FakeProvider([bad, GOOD_TECH])
    n = from_llm(doc, "tech", fake, strict=True)
    assert n.numbers.ok and n.attempts == 2
    assert "do not appear in findings.json" in fake.calls[1]["prompt"] and "4,200" in fake.calls[1]["prompt"]

    with pytest.raises(NumberCheckFailed) as exc:
        from_llm(doc, "tech", FakeProvider([bad, bad]), strict=True)
    assert not exc.value.narrative.numbers.ok


def _variants():
    doc = sample_doc()
    passed = copy.deepcopy(doc)
    passed["verdict"] = "pass"
    passed["sla"].update(passed=True, passed_count=2, failed_count=0)
    for c in passed["sla"]["checks"]:
        c["passed"] = True
    passed["findings"] = []
    no_sla = copy.deepcopy(doc)
    no_sla["verdict"] = "no_sla"
    no_sla["sla"] = {"defined": False}
    no_sla["findings"] = doc["findings"][2:]
    sat = copy.deepcopy(no_sla)
    sat["findings"] = [{"type": "saturation", "severity": "warning", "title": "Saturation at about 60 users",
                        "detail": "", "labels": [], "start_offset_s": 900, "end_offset_s": 1800,
                        "evidence": {"knee_users": 60, "tps_at_knee": 118.2, "max_tps": 120.1}},
                       {"type": "injector_connect", "severity": "warning", "title": "Connect time rose",
                        "detail": "", "labels": [], "start_offset_s": 1200, "end_offset_s": 2520,
                        "evidence": {"connect_before_ms": 5.1, "connect_after_ms": 80.2, "users": 50}}]
    return {"fail": doc, "pass": passed, "no_sla": no_sla, "saturation": sat}


@pytest.mark.parametrize("name", ["fail", "pass", "no_sla", "saturation"])
@pytest.mark.parametrize("audience", ["tech", "exec"])
def test_rule_narratives_only_use_findings_numbers(name, audience):
    doc = _variants()[name]
    n = from_rules(doc, audience)
    result = numbers.check(n.content, doc)
    assert result.ok, result.unverified
    if audience == "exec":
        assert n.content["headline"] and 1 <= len(n.content["key_points"]) <= 5 and n.content["recommended_action"]
    else:
        assert len(n.content["findings"]) == len(doc["findings"]) and n.content["next_tests"]


# ---------------------------------------------------------------------------
# Rendering and the report command
# ---------------------------------------------------------------------------

def test_html_report_is_self_contained_and_escaped():
    doc = sample_doc()
    html = render(doc, from_rules(doc, "tech"), "tech", "html")
    assert "<svg" in html and html.count('class="tl-line"') == 4      # four small multiples, one line each
    assert 'class="tl-band tl-band-critical"' in html and "#3" in html and "#4" in html
    assert "SLA 1%" in html                                          # error-rate target in the error panel
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)" not in html
    assert "prefers-color-scheme: dark" in html and ':root[data-theme="dark"]' in html
    assert "Timeline as a table" in html and "http" not in html.split("<style>")[0]
    assert "nothing was sent to an AI service" in html
    exec_html = render(doc, from_rules(doc, "exec"), "exec", "html")
    assert "<svg" not in exec_html and "Recommended action" in exec_html


def test_markdown_report_escapes_pipes_and_flags_numbers():
    doc = sample_doc()
    n = from_llm(doc, "tech", FakeProvider([{**GOOD_TECH, "summary": "Users hit 75 errors."}]))
    md = render(doc, n, "tech", "md")
    assert "\\| pipe" in md and "| TC Checkout |" in md
    assert "Check these figures" in md and "“75”" in md
    assert "written by fake/fake-1 with prompt report_tech.v1" in md


def _run_folder(tmp_path, doc=None):
    doc = doc or sample_doc()
    folder = tmp_path / "runs" / doc["run"]["run_id"]
    folder.mkdir(parents=True)
    (folder / "findings.json").write_text(json.dumps(doc))
    return folder


def test_locate_by_prefix_path_and_ambiguity(tmp_path):
    folder = _run_folder(tmp_path)
    runs = tmp_path / "runs"
    assert locate("20261013-0845", runs)[0] == folder
    assert locate(str(folder / "findings.json"), runs)[0] == folder
    other = sample_doc()
    other["run"]["run_id"] = "20261013-090000-x"
    _run_folder(tmp_path, other)
    with pytest.raises(ReportError, match="matches 2 runs"):
        locate("20261013", runs)
    with pytest.raises(ReportError, match="No run matches"):
        locate("2099", runs)


def test_build_report_reuses_ai_narrative(tmp_path, monkeypatch):
    folder = _run_folder(tmp_path)
    fake = FakeProvider([GOOD_TECH])
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)
    settings = {"runs_dir": str(tmp_path / "runs")}
    first = build_report("20261013", formats=("md",), settings=settings)
    assert not first.reused and first.files == [folder / "report_tech.md"]
    second = build_report("20261013", formats=("html",), settings=settings)
    assert second.reused and len(fake.calls) == 1
    assert json.loads((folder / "narrative_tech.json").read_text())["prompt"] == "report_tech.v1"


def test_build_report_falls_back_to_rules(tmp_path, monkeypatch):
    _run_folder(tmp_path)
    settings = {"runs_dir": str(tmp_path / "runs")}

    def no_provider(task, settings, override=None):
        raise LLMConfigError("No Anthropic API key")
    monkeypatch.setattr(registry, "provider_for_task", no_provider)
    r = build_report("20261013", formats=("md",), settings=settings)
    assert r.narrative.source == "rules" and "No Anthropic API key" in r.warnings[0]

    monkeypatch.setattr(registry, "provider_for_task",
                        lambda task, settings, override=None: FakeProvider([LLMError("Claude API error 500")]))
    r = build_report("20261013", formats=("md",), settings=settings, fresh=True)
    assert r.narrative.source == "rules" and "API error" in r.narrative.note
    with pytest.raises(ReportError):
        build_report("20261013", formats=("md",), settings=settings, fresh=True, provider_spec="claude")


def test_report_cli(tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    folder = _run_folder(tmp_path)
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_RUNS_DIR", str(tmp_path / "runs"))
    runner = CliRunner()
    out_dir = tmp_path / "out"
    res = runner.invoke(cli, ["report", "20261013-0845", "--no-llm", "--format", "both", "--out", f"{out_dir}/"])
    assert res.exit_code == 0, res.output
    assert (out_dir / "report_tech.html").exists() and (out_dir / "report_tech.md").exists()

    res = runner.invoke(cli, ["report", str(folder / "findings.json"), "--no-llm", "--audience", "exec", "--out", "-"])
    assert res.exit_code == 0 and res.output.startswith("# checkout — load test summary")

    bad = {**GOOD_TECH, "summary": "Expect 4,200 ms at 75 users."}
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: FakeProvider([bad, bad]))
    res = runner.invoke(cli, ["report", "20261013-0845", "--strict-numbers", "--fresh"])
    assert res.exit_code == 3 and "4,200" in res.output


# ---------------------------------------------------------------------------
# End to end with ClickHouse: analyse planted scenarios, then report them without AI
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ch_settings(tmp_path_factory):
    from projectstack.config import load_settings
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    db_name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": db_name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield s
    db.command(f"DROP DATABASE IF EXISTS {db_name}")


@pytest.mark.parametrize("name, sla, scenario", [
    ("knee_burst", "p95=500,err=1", dict(with_tc=True, latency_ms=lambda s, u: 200.0 if s < 18 * 60 else 600.0,
                                         error_code=("503", "Service Unavailable"),
                                         error_rate=lambda s: 0.3 if 30 * 60 <= s < 32 * 60 else 0.005)),
    ("clean", "p95=3000,err=1", dict(with_tc=True)),
    ("saturation", None, dict(capacity_users=60, latency_ms=lambda s, u: 200.0 * max(1.0, u / 60))),
])
def test_reports_for_analysed_runs(ch_settings, tmp_path, name, sla, scenario):
    from projectstack.results.analyze import analyze_file
    from tests.jtl_synth import ramp_steady, scenario_csv, stepping
    users = stepping() if name == "saturation" else ramp_steady()
    path = scenario_csv(tmp_path / f"{name}.jtl", users, **scenario)
    result = analyze_file(path, settings=ch_settings, scenario=name, sla=sla)
    doc = result.findings
    for audience in ("tech", "exec"):
        report = build_report(result.run_id, audience=audience, formats=("html", "md"), use_llm=False,
                              settings=ch_settings)
        assert all(p.exists() and p.stat().st_size > 1000 for p in report.files)
        check = numbers.check(report.narrative.content, doc)
        assert check.ok, check.unverified
    expected = {"knee_burst": "fail", "clean": "pass", "saturation": "no_sla"}[name]
    assert doc["verdict"] == expected
