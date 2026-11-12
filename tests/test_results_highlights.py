"""
Performance Highlights & Observations: the rule-based section (wording follows a
client report's template), test context in context.json and its inheritance,
the AI-written version with its number check and cache, and the reports.
AI providers are replaced by fakes; nothing here calls a real model.
"""

import copy
import json
from datetime import datetime, timezone

import pytest

from projectstack.llm import registry
from projectstack.results import highlights, numbers
from projectstack.results.narrative import from_rules
from projectstack.results.report import render
from tests.test_results_report import FakeProvider, sample_doc

START = datetime(2026, 10, 28, 8, 9, 35, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)


def stepped_doc() -> dict:
    """500 users in 10 steps over about 18 minutes, then 58 minutes at full load (like a real client run)."""
    levels = [{"users": 50 * (i + 1) - 1, "start_ms": START_MS + 55_000 + i * 120_000,
               "end_ms": START_MS + 120_000 + i * 120_000} for i in range(9)]
    levels.append({"users": 494, "start_ms": START_MS + 1_128_000, "end_ms": START_MS + 4_574_000})
    points = [{"t_offset_s": 60 * i, "users": min(500, 50 * (i // 2 + 1)), "tps": 11.0, "p95_ms": 250,
               "samples": 652 if i < 79 else 671, "errors": 0, "error_pct": 0.0} for i in range(80)]

    def row(label, samples, p50, p95, p99=None, max_ms=None, avg=None, run_samples=None):
        return {"label": label, "samples": samples, "avg_ms": avg or p50 * 1.1, "p50_ms": p50, "p90_ms": p95 * 0.9,
                "p95_ms": p95, "p99_ms": p99 or p95 * 1.2, "max_ms": max_ms or p95 * 2, "error_pct": 0.0,
                "tps": samples / 3351, **({"run_samples": run_samples, "run_errors": 0} if run_samples else {})}

    return {
        "schema": "projectstack.findings/1",
        "run": {"run_id": "20261112-065424-93ae", "scenario": "Portal-500users", "tag": "",
                "start": START.isoformat(), "end": "2026-10-28T09:28:40+00:00", "duration_s": 4744.602,
                "samples": 68375, "peak_users": 500, "users_source": "allThreads", "columns": ["timeStamp"],
                "labels": {"request": 4, "transaction": 4, "subresult": 0}},
        "steady": {"start_ms": START_MS + 1_166_000, "end_ms": START_MS + 4_517_000, "source": "auto",
                   "duration_s": 3351.0, "method": "allThreads", "plateau_users": 500, "peak_users": 500,
                   "levels": levels, "note": "500 users held from t+1106s to t+4577s; 60s trimmed at each end",
                   "plateau_start_ms": START_MS + 1_106_000, "plateau_end_ms": START_MS + 4_577_000,
                   "start": "2026-10-28T08:29:01+00:00", "end": "2026-10-28T09:24:52+00:00",
                   "start_offset_s": 1166, "end_offset_s": 4517},
        "verdict": "no_sla",
        "sla": {"defined": False},
        "overall": {"request": {"samples": 40600, "avg_ms": 144.6, "min_ms": 10, "max_ms": 91991, "p50_ms": 96,
                                "p90_ms": 156, "p95_ms": 249, "p99_ms": 1435, "errors": 0, "error_pct": 0.0,
                                "tps": 12.116}},
        "labels": {
            "request": [row("US - SC01 [shop.example.com/] - 001", 5600, 41, 91),
                        row("US - SC02 [shop.example.com/api/search] - 001", 2800, 108, 239),
                        row("US - SC05 [crm.example.com/api/sync/upsert] - 001", 1400, 1322, 1706, 1990, 2100,
                            avg=1450, run_samples=1800),
                        row("US - SC04 [crm.example.com/api/blog-hubs] - 003", 1400, 80, 145, 340, 91991)],
            "transaction": [row("US - SC01 - Launch", 2800, 66, 155), row("US - TC02 - Search", 2800, 679, 915),
                            row("US - SC05 - CRM Sync Webhook", 1400, 1322, 1706),
                            row("US - SC04 - Blog", 1400, 251, 426)],
        },
        "timeline": {"bucket_s": 60, "users_source": "allThreads", "points": points},
        "errors": [], "findings": [], "skipped": [], "warnings": [], "truncated": {},
        "notes": ["Percentiles are exact over the samples in scope."], "size_bytes": 9000,
    }


def test_section_follows_the_report_template():
    h = highlights.rules(stepped_doc(), {"region": "us-east-1"})
    assert h["intro"] == [
        "The test was executed on October 28, 2026, 08:09 - 09:28 AM UTC.",
        "The incremental load test was executed with 500 concurrent users from the us-east-1 region.",
        "The test included an 18-minute stepped ramp-up followed by 58 minutes of steady-state execution.",
    ]
    assert h["stats"] == [{"name": "Maximum users", "value": "500"}, {"name": "Total requests", "value": "52,179"},
                          {"name": "Average throughput", "value": "11.00 hit/sec"},
                          {"name": "Total errors", "value": "0"}]
    obs = h["observations"]
    assert obs[0] == ("Executed 52,179 total requests with zero error occurrences, attaining a combined throughput "
                      "of 11.00 req/sec.")
    assert obs[1] == ("Response times were consistent for most requests, at 145 ms mean and 249 ms p95, but the "
                      "slowest 1% took longer than 1,435 ms (p99). The single slowest request took 92 seconds, on "
                      "/api/blog-hubs (Blog).")
    assert obs[2] == ("The slowest endpoint was /api/sync/upsert (CRM Sync Webhook), at 1,322 ms median (1,706 ms "
                      "p95) over 1,400 invocations at full load, about 14× the overall median.")


def test_older_findings_without_the_plateau_give_the_same_profile():
    doc = stepped_doc()
    for key in ("plateau_start_ms", "plateau_end_ms"):
        doc["steady"].pop(key)
    p = highlights.load_profile(doc)
    assert (p["kind"], p["ramp_up_s"], p["hold_s"], p["steps"]) == ("stepped", 1106, 3471, 10)


def test_consistent_and_degraded_response_times():
    doc = stepped_doc()
    doc["overall"]["request"].update(avg_ms=80, p50_ms=60, p95_ms=205, p99_ms=311, max_ms=900)
    obs = highlights.rules(doc)["observations"]
    assert obs[1] == "Sustained highly consistent response times: 80 ms mean (205 ms p95, along with 311 ms p99)."

    doc["findings"] = [{"type": "latency_change", "severity": "critical", "title": "p95 rose from 210 ms to 900 ms at t+40m",
                        "detail": "", "labels": [], "evidence": {"before": 210, "after": 900}, "start_offset_s": 2400}]
    assert highlights.rules(doc)["observations"][1].startswith(
        "Response times did not hold steady: p95 rose from 210 ms to 900 ms at t+40m. Over the steady state, the mean")


@pytest.mark.parametrize("start,end,text", [
    ("2026-11-05T05:30:00+00:00", "2026-11-05T06:50:00+00:00", "November 05, 2026, 05:30 - 06:50 AM UTC"),
    ("2026-10-28T11:30:00+00:00", "2026-10-28T12:50:00+00:00", "October 28, 2026, 11:30 AM - 12:50 PM UTC"),
    ("2026-10-28T23:30:00+00:00", "2026-10-29T00:20:00+00:00",
     "October 28, 2026, 11:30 PM - October 29, 2026, 12:20 AM UTC"),
])
def test_window_text(start, end, text):
    assert highlights.window_text(start, end) == text


@pytest.mark.parametrize("label,transactions,shown,name", [
    ("US - SC05 [crm.example.com/api/sync/upsert?x=1] - 001", ["US - SC05 - CRM Sync", "US - SC01 - Launch"],
     "/api/sync/upsert", "CRM Sync"),
    ("US - SC01 [shop.example.com/] - 001", ["US - SC01 - Launch", "US - TC01 - Launch"], "shop.example.com/", "Launch"),
    ("US - SC01 [shop.example.com/a] - 001", ["US - SC01 - Launch", "US - SC01 - Login"], "/a", ""),
    ("GET /api/users/42", [], "/api/users/42", ""),
    ("[3] POST /login", [], "/login", ""),
    ("https://shop.example.com/cart", [], "/cart", ""),
    ("01_02_Login", [], "01_02_Login", ""),
])
def test_endpoint_names(label, transactions, shown, name):
    ep = highlights.endpoint(label, transactions)
    assert (ep["shown"], ep["name"]) == (shown, name)


def test_test_type():
    doc = stepped_doc()
    profile = highlights.load_profile(doc)
    assert highlights.test_type(doc, profile) == "incremental load test"
    assert highlights.test_type(doc, profile, "Stress") == "Stress test"
    doc["run"]["scenario"] = "Portal-SmokeTest-10users"
    assert highlights.test_type(doc, profile) == "smoke test"
    flat = {**profile, "kind": "constant"}
    doc["run"]["scenario"] = "portal"
    assert highlights.test_type(doc, flat) == "load test"


def test_baseline_gains_are_reported_only_at_comparable_load():
    doc = stepped_doc()
    doc["baseline"] = {"requested": "previous", "found": True, "run_id": "20261028-080934-base", "tag": "",
                       "load": {"comparable": True},
                       "improvements": [{"kind": "request", "label": "US - SC05 [crm.example.com/api/sync/upsert] - 001",
                                         "metrics": ["p95"], "samples": 628, "p50_before": 1322, "p50_after": 309,
                                         "p95_before": 1706, "p95_after": 520, "p95_change_pct": -69.5,
                                         "err_before": 0.0, "err_after": 0.0}]}
    obs = highlights.rules(doc)["observations"]
    assert ("/api/sync/upsert (CRM Sync Webhook) demonstrated marked gains over the baseline run, averaging 309 ms "
            "median response times (520 ms p95, down from 1,706 ms) over 628 invocations at full load.") in obs
    assert not any(o.startswith("The slowest endpoint was /api/sync/upsert") for o in obs)   # said once
    doc["baseline"]["load"]["comparable"] = False
    assert not any("marked gains" in o for o in highlights.rules(doc)["observations"])


def test_errors_and_sla_in_the_observations():
    doc = sample_doc()                       # SLA failed, a latency step and an error burst
    obs = highlights.rules(doc)["observations"]
    assert obs[0].startswith("Executed 252,000 total requests with 3,960 errors (1.57% of requests)")
    assert "The most frequent was 503 Service Unavailable on Step 1 /app/page/1 (676 occurrences)." in obs[0]
    assert obs[1].startswith("Response times did not hold steady:")
    assert any("slower than agreed" in o for o in obs) and any("before the system recovered" in o for o in obs)


@pytest.mark.parametrize("make", [stepped_doc, sample_doc])
def test_rule_figures_pass_the_number_check(make):
    doc = make()
    ctx = {"region": "us-east-1"}
    h = highlights.rules(doc, ctx)
    check = numbers.check({"intro": h["intro"], "observations": h["observations"]}, doc,
                          highlights.allowed_numbers(doc, highlights.facts(doc, ctx), highlights.clean_context(ctx)))
    assert check.ok, check.unverified


def test_number_check_reads_long_dates_and_am_pm():
    doc = sample_doc()                       # 2025-10-09, 09:00 to 09:42 UTC
    allowed = numbers.Allowed(doc)
    for ok in ("On October 09, 2025, 09:00 - 09:42 AM UTC.", "On 9 October 2025.", "On Oct 9, 2025 at 9:00 a.m."):
        assert numbers.check({"t": ok}, doc, allowed).ok, ok
    assert [u["text"] for u in numbers.check({"t": "On October 10, 2025."}, doc, allowed).unverified] == ["October 10, 2025"]
    assert len(numbers.check({"t": "From 09:00 - 09:42 PM UTC."}, doc, allowed).unverified) == 2
    assert [u["text"] for u in numbers.check({"t": "09:00 AM - 09:42 PM"}, doc, allowed).unverified] == ["09:42 PM"]


def _run(tmp_path, name, scenario, ctx=None, doc=None):
    folder = tmp_path / "runs" / name
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"scenario": scenario}))
    (folder / "findings.json").write_text(json.dumps(doc or stepped_doc()))
    if ctx is not None:
        highlights.save_context(folder, ctx)
    return folder


def test_context_is_inherited_within_a_scenario(tmp_path):
    runs = tmp_path / "runs"
    _run(tmp_path, "20261101-000000-a", "portal", {"region": "eu-west-1", "notes": "SC05 is the CRM sync."})
    _run(tmp_path, "20261102-000000-b", "billing", {"region": "us-east-1"})
    c = _run(tmp_path, "20261103-000000-c", "portal")
    d = _run(tmp_path, "20261104-000000-d", "search")

    got = highlights.resolve_context(c, "portal", runs)
    assert (got["source"], got["from_run"], got["region"]) == ("inherited", "20261101-000000-a", "eu-west-1")
    other = highlights.resolve_context(d, "search", runs)
    assert other["source"] == "none" and other["region"] == ""
    assert other["suggestion"]["from_run"] == "20261102-000000-b" and other["suggestion"]["scenario"] == "billing"

    highlights.save_context(c, {"region": "  ap-south-1 ", "notes": "x" * 5000, "junk": 1})
    own = highlights.resolve_context(c, "portal", runs)
    assert own["source"] == "run" and own["region"] == "ap-south-1" and len(own["notes"]) == 4000 and "junk" not in own
    highlights.save_context(c, {})                      # cleared on purpose: no longer inherits
    assert highlights.resolve_context(c, "portal", runs)["source"] == "run"


def test_ai_section_is_checked_and_cached(tmp_path, monkeypatch):
    doc = stepped_doc()
    folder = _run(tmp_path, doc["run"]["run_id"], "portal", {"region": "us-east-1",
                                                               "notes": "CRM sync has a quota of 5,000 calls per day."})
    ctx = highlights.resolve_context(folder, "portal", tmp_path / "runs")
    answer = {"intro": ["The test was executed on October 28, 2026, 08:09 - 09:28 AM UTC, from us-east-1."],
              "observations": ["The CRM sync handled 1,800 calls, well below its 5,000 daily quota.",
                               "Throughput peaked at 99.9 req/sec."]}
    fake = FakeProvider([answer])
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)

    section, reused, warnings = highlights.get(doc, folder, ctx, use_llm=True, settings={})
    assert section.source == "llm" and not reused and section.prompt == "report_highlights.v1"
    assert section.content["stats"] == highlights.rules(doc, ctx)["stats"]          # never AI-written
    assert [u["text"] for u in section.numbers.unverified] == ["99.9"] and "99.9" in warnings[0]
    sent = fake.calls[0]
    assert "quota of 5,000 calls per day" in sent["prompt"] and '"throughput_per_s": 11.0' in sent["prompt"]
    assert sent["schema"] == highlights.SCHEMA
    assert "Forensis" not in sent["system"] and "Salesforce" not in sent["system"]  # the style example is generic

    again, reused, _ = highlights.get(doc, folder, ctx, use_llm=True, settings={})
    assert reused and len(fake.calls) == 1 and again.content == section.content
    assert highlights.cached(folder, doc, ctx)["content"] == section.content
    changed = {**ctx, "notes": "CRM sync has a quota of 10,000 calls per day."}
    assert highlights.cached(folder, doc, changed) is None                           # notes changed: write again


def test_reports_carry_the_section():
    doc = stepped_doc()
    section = highlights.from_rules(doc, {"region": "us-east-1"})
    md = render(doc, from_rules(doc, "exec"), "exec", "md", section)
    assert "## Performance Highlights & Observations" in md and "## Key points" not in md
    assert "Below are the high-level statistics from the test:" in md and "- Total requests: **52,179**" in md
    assert "from the us-east-1 region." in md
    html = render(doc, from_rules(doc, "tech"), "tech", "html", section)
    assert "Performance Highlights &amp; Observations" in html and "<strong>11.00 hit/sec</strong>" in html

    hostile = copy.deepcopy(doc)
    hostile["labels"]["request"][2]["label"] = "US - SC05 [crm.example.com/<script>x</script>] - 001"
    html = render(hostile, from_rules(hostile, "tech"), "tech", "html")
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
