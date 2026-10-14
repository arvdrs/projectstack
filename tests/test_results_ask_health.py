"""
Results analysis, phase 5: the read-only SQL sandbox, the `ask` agent loop (with
scripted providers), Gemini and Ollama tool calling, JMX plan parsing, correlation
markers in generated scripts and the correlation health check — on synthetic runs
and end to end with real JMeter against the mock app.
"""

import json
import re
import secrets
from pathlib import Path

import pytest
from click.testing import CliRunner

from projectstack.generator.jmx_generator import generate_jmx
from projectstack.generator.validator import validate_jmx
from projectstack.llm import registry
from projectstack.llm.base import Provider, ToolCall, ToolTurn
from projectstack.results.ask import RUN_SQL, ask, save
from projectstack.results.plan import PlanError, load_plan

from tests.jtl_synth import sample, write_csv, START_MS
from tests.test_jmx_generator import _corr, _req

TOKEN = "tok_supersecretvalue1234"


# ---------------------------------------------------------------------------
# A small plan: login → orders (sends the token) → receipt (sends the order number)
# ---------------------------------------------------------------------------

def _plan_files(tmp_path, markers=True, transactions=False):
    from projectstack.generator.transaction_grouper import Transaction
    reqs = [
        _req("POST", "https://shop.test/v1/login", body='{"u":"a"}', res_body=json.dumps({"access_token": TOKEN})),
        _req("GET", "https://shop.test/v1/orders", headers={"Authorization": f"Bearer {TOKEN}"},
             res_body=json.dumps({"order_no": "ORD-7781"})),
        _req("GET", "https://shop.test/v1/receipt?order=ORD-7781", headers={"Authorization": f"Bearer {TOKEN}"}),
    ]
    cvs = [_corr("C_BEARER_TOKEN", 0, "body", "access_token", TOKEN, [1, 2]),
           _corr("C_ORDER_NO", 1, "body", "order_no", "ORD-7781", [2])]
    path = tmp_path / ("plan.jmx" if markers else "plain.jmx")
    txns = [Transaction(name="TC_01_Checkout", request_indices=[0, 1, 2])] if transactions else None
    generate_jmx(reqs, cvs, output_path=path, correlation_markers=markers, transactions=txns,
                 no_transactions=not transactions)
    return path


def test_markers_in_generated_scripts(tmp_path):
    path = _plan_files(tmp_path)
    assert validate_jmx(str(path)) == []
    text = path.read_text()
    assert text.count('testname="Correlation check"') == 2          # orders and receipt send correlated values
    assert "['C_BEARER_TOKEN', 'C_ORDER_NO']" in text and "PS_CORRELATION_MISSING: " in text
    assert "Correlation check" not in _plan_files(tmp_path, markers=False).read_text()


def test_plan_maps_variables_to_extractors(tmp_path):
    plan = load_plan(_plan_files(tmp_path, transactions=True))
    login, orders, receipt = plan.order
    assert plan.vars_sent(orders) == {"C_BEARER_TOKEN"}
    assert plan.vars_sent(receipt) == {"C_BEARER_TOKEN", "C_ORDER_NO"}
    assert plan.vars_sent(f"{receipt}-1") == plan.vars_sent(receipt)          # redirect hop → its parent
    assert plan.vars_sent("TC_01_Checkout") == {"C_BEARER_TOKEN", "C_ORDER_NO"}
    [token] = plan.extractors["C_BEARER_TOKEN"]
    assert (token.kind, token.expression, token.default, token.source) == ("json", "$.access_token", "NOT_FOUND", login)
    assert "JSON Extractor" in token.describe() and login in token.describe()
    bad = tmp_path / "bad.jmx"
    bad.write_text("<notAPlan/>")
    with pytest.raises(PlanError):
        load_plan(bad)


# ---------------------------------------------------------------------------
# ClickHouse fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ch(tmp_path_factory):
    from projectstack.config import load_settings
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    db_name = f"pstack_test_{secrets.token_hex(4)}"
    s = {**load_settings(), "clickhouse_database": db_name, "runs_dir": str(tmp_path_factory.mktemp("runs"))}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    yield {"settings": s, "db": db}
    db.command(f"DROP DATABASE IF EXISTS {db_name}")


def _health_jtl(path: Path, labels: list[str]) -> Path:
    """20 minutes, 5 users. From minute 10 the token is rejected (401) and the receipt goes out with
    NOT_FOUND; a profile page keeps landing on /login; a cart call fails with "session expired"."""
    login, orders, receipt = labels
    rows = []
    for u in range(5):
        thread = f"Users 1-{u + 1}"
        for i in range(120):
            t = START_MS + u * 700 + i * 10_000
            broken = i >= 60
            rows.append(sample(t, 120, login, thread, 5, url="https://shop.test/v1/login"))
            rows.append(sample(t + 200, 90, orders, thread, 5, ok=not broken, code="401" if broken else "200",
                               msg="Unauthorized" if broken else "OK", url="https://shop.test/v1/orders"))
            rows.append(sample(t + 400, 80, receipt, thread, 5, ok=not broken,
                               failure="PS_CORRELATION_MISSING: C_ORDER_NO" if broken else "",
                               url="https://shop.test/v1/receipt?order=" + ("NOT_FOUND" if broken else "ORD-7781")))
            if i % 10 == 0:
                rows.append(sample(t + 600, 30, "GET /v1/profile-1", thread, 5, url="https://shop.test/login?next=/p"))
                rows.append(sample(t + 600, 60, "GET /v1/profile", thread, 5, url="https://shop.test/v1/profile"))
                rows.append(sample(t + 700, 40, "POST /v1/cart", thread, 5, ok=False,
                                   failure="Response contains: Session expired, please log in again",
                                   url="https://shop.test/v1/cart"))
    rows.sort(key=lambda r: r["timeStamp"])
    return write_csv(path, rows)


@pytest.fixture(scope="module")
def health_run(ch, tmp_path_factory):
    from projectstack.results.analyze import analyze_file
    folder = tmp_path_factory.mktemp("health")
    plan_path = _plan_files(folder)
    jtl = _health_jtl(folder / "health.jtl", load_plan(plan_path).order)
    result = analyze_file(jtl, settings=ch["settings"], scenario="health", tag="h1", jmx=plan_path)
    return result, plan_path


def _patterns(doc):
    return {f["evidence"]["pattern"]: f for f in doc["findings"] if f["type"] == "correlation"}


def test_health_check_finds_and_maps_problems(health_run):
    result, _ = health_run
    found = _patterns(result.findings)
    assert set(found) == {"missing_value", "auth_onset", "login_redirect", "auth_message"}

    missing = found["missing_value"]
    assert missing["evidence"]["variables"] == ["C_ORDER_NO"] and missing["severity"] == "critical"
    [m] = missing["evidence"]["mapping"]
    assert m["var"] == "C_ORDER_NO" and m["extractors"][0]["source"].endswith("/v1/orders")
    assert m["extractors"][0]["source_error_pct"] == pytest.approx(50, abs=1)
    assert "fix that request first" in missing["detail"]                       # its source request was failing

    onset = found["auth_onset"]
    assert onset["evidence"]["onset_offset_s"] == pytest.approx(600, abs=60)
    assert onset["evidence"]["mapping"][0]["var"] == "C_BEARER_TOKEN"
    assert "expires" in onset["detail"] and "extract it again each iteration" in onset["detail"]

    assert found["login_redirect"]["labels"] == ["GET /v1/profile"]
    assert found["auth_message"]["labels"] == ["POST /v1/cart"]
    assert "Session expired" in found["auth_message"]["detail"]
    assert (result.folder / "plan.jmx").exists()


def test_health_without_plan_and_cli(health_run, ch, tmp_path, monkeypatch):
    from projectstack.results.health import check_run
    result, plan_path = health_run
    plain = {f.evidence["pattern"]: f for f in check_run(ch["db"], result.run_id, None)}
    assert not plain["missing_value"].evidence["mapping"] and "--jmx" in plain["missing_value"].detail

    from projectstack.cli.main import cli
    s = ch["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    res = CliRunner().invoke(cli, ["health", result.run_id])
    assert res.exit_code == 0, res.output
    assert "plan.jmx" in res.output and "C_BEARER_TOKEN ← JSON Extractor" in res.output
    data = json.loads(CliRunner().invoke(cli, ["health", result.run_id, "--json"]).output)
    assert len(data["findings"]) == 4


def test_correlation_findings_in_narratives(health_run):
    from projectstack.results import numbers
    from projectstack.results.narrative import from_rules
    doc = health_run[0].findings
    tech = from_rules(doc, "tech")
    assert tech.content["root_causes"][0]["cause"].startswith("Broken correlation")
    assert tech.content["next_tests"][0]["test"].startswith("Fix the correlation")
    exec_ = from_rules(doc, "exec")
    assert any("real user" in p for p in exec_.content["key_points"])
    for n in (tech, exec_):
        check = numbers.check(n.content, doc)
        assert check.ok, check.unverified


# ---------------------------------------------------------------------------
# SQL sandbox
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox(ch, health_run):
    from projectstack.results.db import ClickHouseConfig
    from projectstack.results.sandbox import RunSandbox
    return RunSandbox(ClickHouseConfig.from_settings(ch["settings"]), health_run[0].run_id, ch["settings"])


def test_sandbox_runs_reads_on_this_run_only(ch, sandbox, health_run):
    from projectstack.results.analyze import analyze_file
    from tests.jtl_synth import ramp_steady, scenario_csv
    other = scenario_csv(Path(ch["settings"]["runs_dir"]) / "other.jtl", ramp_steady(users=5, ramp_s=60, steady_s=300,
                                                                                      down_s=30))
    analyze_file(other, settings=ch["settings"], scenario="other")              # a second run in the same table
    r = sandbox.run("SELECT uniqExact(run_id) AS runs, count() AS n FROM samples")
    assert r.ok and r.rows == [[1, health_run[0].meta["run"]["samples"]]]
    r = sandbox.run("SELECT label, quantileExact(0.95)(elapsed) AS p95 FROM samples WHERE kind = 'request' "
                    "GROUP BY label ORDER BY label")
    assert r.ok and len(r.rows) == 6 and r.columns == ["label", "p95"]
    r = sandbox.run("WITH t AS (SELECT label, count() AS c FROM samples GROUP BY label) "
                    "SELECT t.label, c, kind FROM t JOIN run_labels USING label")
    assert r.ok and len(r.rows) == 6
    r = sandbox.run("SELECT * FROM samples")
    assert r.ok and len(r.rows) == 200 and r.truncated and "cut at 200 rows" in r.as_text()


@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM system.tables", "not available"),
    ("SELECT count() FROM runs", "not available"),
    (lambda db: f"SELECT count() FROM {db}.runs", "not available"),
    ("SELECT count() FROM default.samples", "not available"),
    ("SELECT * FROM url('http://127.0.0.1:9/x', CSV, 'a String')", "table function url()"),
    ("SELECT * FROM file('/etc/passwd', LineAsString)", "table function file()"),
    ("SELECT count() FROM samples SETTINGS max_execution_time = 1000", "readonly"),
    ("SELECT count() FROM samples FORMAT JSON", "FORMAT"),
    ("SELECT count() FROM samples INTO OUTFILE '/tmp/x'", "OUTFILE"),
    ("SELECT 1; DROP TABLE samples", "Multi-statements"),
    ("DROP TABLE samples", "only SELECT"),
    ("INSERT INTO samples (run_id) VALUES ('x')", "only SELECT"),
    ("ALTER TABLE samples DELETE WHERE 1", "only SELECT"),
    ("", "empty"),
])
def test_sandbox_rejects(sandbox, sql, reason):
    if callable(sql):
        sql = sql(sandbox.db)
    r = sandbox.run(sql)
    assert not r.ok and reason.lower() in r.error.lower(), r.error


def test_sandbox_time_limit(ch, health_run):
    from projectstack.results.db import ClickHouseConfig
    from projectstack.results.sandbox import RunSandbox
    slow = RunSandbox(ClickHouseConfig.from_settings(ch["settings"]), health_run[0].run_id, ch["settings"], timeout_s=1)
    r = slow.run("SELECT count() FROM numbers(100000000000) WHERE sipHash64(number) % 7 = 3")
    assert not r.ok and ("limit" in r.error.lower() or "timeout" in r.error.lower())


# ---------------------------------------------------------------------------
# The agent loop (scripted provider, real sandbox)
# ---------------------------------------------------------------------------

class ScriptedProvider(Provider):
    name = "fake"
    default_model = "fake-1"

    def __init__(self, turns):
        super().__init__({}, None)
        self.turns = list(turns)
        self.seen: list[list] = []

    def complete(self, system, prompt, **kw):
        raise AssertionError("ask uses tool calls")

    def complete_with_tools(self, system, messages, tools, *, max_tokens=8192, effort=None):
        self.seen.append(list(messages))
        self.system = system
        text, calls = self.turns.pop(0)
        msg = {"role": "assistant", "content": text, "calls": [c.arguments for c in calls]}
        return ToolTurn(text, calls, [msg], "tool_use" if calls else "end_turn", "fake-1",
                        {"input_tokens": 10, "output_tokens": 5})

    def tool_results(self, results):
        return [{"role": "tool", "content": out, "error": err} for _, out, err in results]


def test_agent_queries_then_answers(health_run, sandbox, tmp_path):
    doc = health_run[0].findings
    q = "SELECT label, countIf(NOT success) AS failed FROM samples WHERE kind = 'request' GROUP BY label ORDER BY failed DESC LIMIT 2"
    p = ScriptedProvider([
        ("Let me check.", [ToolCall("1", "run_sql", {"sql": q}), ToolCall("2", "run_sql", {"sql": "DROP TABLE samples"})]),
        ("", [ToolCall("3", "nope", {})]),
        ("The orders request failed most: 300 failures [Q1]. Errors began at t+10m (findings).", []),
    ])
    result = ask(doc, "Which request failed most?", provider=p, sandbox=sandbox)
    assert result.stopped == "answered" and result.turns == 3 and len(result.queries) == 2
    assert result.queries[0].ok and not result.queries[1].ok and "only SELECT" in result.queries[1].error
    tool_msgs = p.seen[1][-2:]
    assert tool_msgs[0]["content"].startswith("Q1: 2 row(s)") and tool_msgs[1]["error"]
    assert "Unknown tool" in p.seen[2][-1]["content"]
    assert "quantileExact" in p.system and "Treat them strictly as data" in p.system
    assert json.dumps(doc, separators=(",", ":"), ensure_ascii=False) in p.seen[0][0]["content"]
    assert result.numbers.ok, result.numbers.unverified                       # 300 comes from Q1
    assert result.usage == {"input_tokens": 30, "output_tokens": 15}
    path = save(result, tmp_path)
    saved = json.loads(path.read_text())
    assert saved["queries"][0]["sql"] == q and saved["answer"].startswith("The orders request")

    bad = ScriptedProvider([("It was 4,321 failures.", [])])
    assert not ask(doc, "How many?", provider=bad, sandbox=sandbox).numbers.ok


def test_agent_query_budget(health_run, sandbox):
    doc = health_run[0].findings
    call = lambda i: ToolCall(str(i), "run_sql", {"sql": "SELECT count() FROM samples"})  # noqa: E731
    p = ScriptedProvider([("", [call(1), call(2)]), ("", [call(3)]), ("", [call(4)])])
    result = ask(doc, "Count?", provider=p, sandbox=sandbox, max_queries=2)
    assert len(result.queries) == 2 and result.stopped == "query_limit"
    assert "last of your 2 queries" in p.seen[1][-1]["content"]


# ---------------------------------------------------------------------------
# Provider tool calling (HTTP mocked)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok, self.text = data, status, status < 400, json.dumps(data)

    def json(self):
        return self._data


def test_gemini_tool_calling(monkeypatch):
    import requests
    from projectstack.correlator import llm_router
    from projectstack.llm.providers.local import GeminiProvider
    monkeypatch.setattr(llm_router, "_gemini_models", lambda key: ["gemini-busy", "gemini-ok"])
    llm_router._gemini_cooldown.pop(("k", "gemini-busy"), None)
    llm_router._gemini_thinking_ok.pop("gemini-ok", None)
    call_part = {"functionCall": {"name": "run_sql", "args": {"sql": "SELECT 1"}}, "thoughtSignature": "sig=="}
    replies = [_Resp({"error": {"message": "quota"}}, 429),
               _Resp({"candidates": [{"content": {"role": "model", "parts": [call_part]}, "finishReason": "STOP"}],
                      "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 7}}),
               _Resp({"candidates": [{"content": {"role": "model", "parts": [{"text": "Done [Q1]"}]}}]})]
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, **kw: sent.append((url, json)) or replies.pop(0))
    p = GeminiProvider({"gemini_api_key": "k"})
    messages = [p.user_message("hi")]
    turn = p.complete_with_tools("sys", messages, [RUN_SQL])
    assert [u.split("/models/")[1].split(":")[0] for u, _ in sent] == ["gemini-busy", "gemini-ok"]
    body = sent[1][1]
    decl = body["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "run_sql" and "additionalProperties" not in decl["parameters"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert turn.tool_calls[0].arguments == {"sql": "SELECT 1"} and turn.usage["input_tokens"] == 50
    messages += turn.messages + p.tool_results([(turn.tool_calls[0], "Q1: 1 row", False)])
    final = p.complete_with_tools("sys", messages, [RUN_SQL])
    assert sent[2][0].endswith("gemini-ok:generateContent")                     # same model for the whole chat
    contents = sent[2][1]["contents"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "sig=="             # model turn sent back unchanged
    assert contents[2]["parts"][0]["functionResponse"] == {"name": "run_sql", "response": {"result": "Q1: 1 row"}}
    assert final.text == "Done [Q1]" and not final.tool_calls


def test_ollama_tool_calling(monkeypatch):
    import requests
    from projectstack.llm.providers.local import OllamaProvider
    sent = []
    reply = {"message": {"role": "assistant", "content": "",
                         "tool_calls": [{"function": {"name": "run_sql", "arguments": {"sql": "SELECT 2"}}}]},
             "done_reason": "stop", "prompt_eval_count": 9, "eval_count": 3}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **kw: sent.append(json) or _Resp(reply))
    p = OllamaProvider({"ollama_model": "qwen3"})
    turn = p.complete_with_tools("sys", [p.user_message("hi")], [RUN_SQL])
    assert sent[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert sent[0]["tools"][0]["function"]["name"] == "run_sql"
    assert turn.tool_calls[0].arguments == {"sql": "SELECT 2"} and turn.messages == [reply["message"]]
    assert p.tool_results([(turn.tool_calls[0], "x", True)]) == [{"role": "tool", "tool_name": "run_sql",
                                                                   "content": "ERROR: x"}]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_ask_cli(health_run, ch, tmp_path, monkeypatch):
    from projectstack.cli.main import cli
    s = ch["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    p = ScriptedProvider([("", [ToolCall("1", "run_sql", {"sql": "SELECT count() AS n FROM samples"})]),
                          ("The run has the samples counted in [Q1].", [])])
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: p)
    run_id = health_run[0].run_id
    res = CliRunner().invoke(cli, ["ask", run_id[:-1], "How many samples?"])
    assert res.exit_code == 0, res.output
    assert "The run has the samples counted in [Q1]." in res.output and "Q1 — 1 row(s)" in res.output
    assert "Answered by fake/fake-1 with prompt ask.v1" in res.output
    assert list((health_run[0].folder / "ask").glob("*.json"))

    def no_provider(task, settings, override=None):
        from projectstack.llm.base import LLMConfigError
        raise LLMConfigError("No Anthropic API key")
    monkeypatch.setattr(registry, "provider_for_task", no_provider)
    res = CliRunner().invoke(cli, ["ask", run_id, "Anything?"])
    assert res.exit_code == 2 and "No Anthropic API key" in res.output


# ---------------------------------------------------------------------------
# End to end: real JMeter against the mock app, with one extractor broken on purpose
# ---------------------------------------------------------------------------

def test_e2e_broken_extractor_is_mapped(ch, tmp_path):
    from projectstack.runner.jmeter_runner import find_jmeter, run_jmeter
    if find_jmeter() is None:
        pytest.skip("JMeter not installed")
    pytest.importorskip("uvicorn")
    import socket
    import threading
    import time
    import uvicorn
    from projectstack.correlator.engine import run_correlation
    from projectstack.parser.har_parser import parse_har
    from projectstack.results.analyze import analyze_file
    from tests.har_recorder import record_checkout_flow
    from tests.mock_app import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        record_checkout_flow(f"http://127.0.0.1:{port}", tmp_path / "flow.har")
        reqs = parse_har(str(tmp_path / "flow.har"))
        cvs = run_correlation(reqs, use_claude=False)
        plan = tmp_path / "plan.jmx"
        generate_jmx(reqs, cvs, output_path=plan, threads=1, loops=3, include_view_tree=False)
        text = plan.read_text()
        assert "Correlation check" in text
        # Break the bearer-token extractor: its JSON path no longer matches the login response
        text, n = re.subn(r'(<stringProp name="JSONPostProcessor.referenceNames">C_BEARER_TOKEN</stringProp>\s*'
                          r'<stringProp name="JSONPostProcessor.jsonPathExprs">)[^<]*(</stringProp>)',
                          r"\1$.no_such_field\2", text)
        assert n == 1
        plan.write_text(text)
        info = run_jmeter(str(plan), output_dir=tmp_path / "run", timeout=240)
        result = analyze_file(info["jtl_path"], settings=ch["settings"], scenario="e2e", jmx=plan)
    finally:
        srv.should_exit = True
    missing = _patterns(result.findings)["missing_value"]
    assert "C_BEARER_TOKEN" in missing["evidence"]["variables"]
    mapped = {m["var"]: m for m in missing["evidence"]["mapping"]}
    token = mapped["C_BEARER_TOKEN"]["extractors"][0]
    assert token["kind"] == "json" and token["expression"] == "$.no_such_field"
    assert "found no match" in missing["detail"]
