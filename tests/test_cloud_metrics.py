"""
Server metrics from AWS CloudWatch and Azure Monitor: the providers (AWS through
botocore's Stubber, which checks every request against the real API model; Azure
through fake SDK clients), the server detectors and their links with test findings,
attaching metrics to an analysed run, the web endpoints and the CLI.
Nothing here calls a real cloud account.
"""

import copy
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import ANY, Stubber

from projectstack.results.cloud import CloudError, FetchResult, Resource, Series, load_targets, period_for, save_targets
from projectstack.results.cloud.analysis import attach, detect, link
from projectstack.results.cloud.aws import AwsMonitor
from projectstack.results.cloud.azure import AzureMonitor
from projectstack.results.cloud.presets import AWS, AZURE, TYPES, catalog

from tests.test_results_report import sample_doc

AWS_SETTINGS = {"aws_access_key_id": "AKIATEST", "aws_secret_access_key": "secret", "aws_region": "ap-south-1"}
NOW_MS = int(time.time() * 1000)


def test_presets_are_complete():
    for key, t in TYPES.items():
        assert t.metrics, key
        for m in t.metrics:
            if t.provider == "aws":
                assert m.namespace and m.name and m.stat, (key, m.key)
            else:
                assert m.az_name and m.az_agg in ("Average", "Total", "Maximum", "Minimum"), (key, m.key)
    cats = {t["category"] for t in catalog()}
    assert cats == {"vm", "containers", "data", "edge"}
    assert {"ec2", "ecs", "eks", "rds", "elasticache", "alb", "lambda"} <= set(AWS)
    assert {"vm", "aks", "containerapp", "sqldb", "postgres", "redis", "appservice", "appgw"} <= set(AZURE)


def test_periods_follow_cloudwatch_retention():
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    ms = lambda d: int((now - timedelta(days=d)).timestamp() * 1000)  # noqa: E731
    assert (period_for(ms(1), now), period_for(ms(30), now), period_for(ms(99), now)) == (60, 300, 3600)


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

def _stubbed(monkeypatch, services):
    clients = {name: boto3.client(name, region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y")
               for name in services}
    stubs = {name: Stubber(c) for name, c in clients.items()}
    monkeypatch.setattr(AwsMonitor, "client", lambda self, service, region="": clients[service])
    return clients, stubs


def test_aws_fetch_queries_and_units(monkeypatch):
    clients, stubs = _stubbed(monkeypatch, ["cloudwatch"])
    resources = [Resource("aws", "ec2", "i-0abc", "app-1", "ap-south-1"),
                 Resource("aws", "ecs", "shop-cluster/checkout", "checkout", "ap-south-1"),
                 Resource("aws", "alb", "app/web/123abc", "web", "ap-south-1")]
    ids, raw = [], {}
    for r in resources:
        for m in AWS[r.type].metrics:
            ids.append((f"m{len(ids)}", r, m))
    values = {("ec2", "cpu"): 50.0, ("ec2", "net_in"): 6_000_000.0, ("ecs", "cpu"): 70.0,
              ("alb", "requests"): 1200.0, ("alb", "response_time"): 0.25}
    t0 = datetime.fromtimestamp((NOW_MS - 3_600_000) / 1000, tz=timezone.utc)
    results = []
    for qid, r, m in ids:
        v = values.get((r.type, m.key))
        results.append({"Id": qid, "Label": m.label, "StatusCode": "Complete",
                        "Timestamps": [t0, t0 + timedelta(minutes=1)] if v is not None else [],
                        "Values": [v, v] if v is not None else []})
    captured = {}
    clients["cloudwatch"].meta.events.register("provide-client-params.cloudwatch.GetMetricData",
                                               lambda params, **kw: captured.update(q=params["MetricDataQueries"]))
    stubs["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": results, "Messages": []},
                                     {"MetricDataQueries": ANY, "StartTime": ANY, "EndTime": ANY, "ScanBy": "TimestampAscending"})
    with stubs["cloudwatch"]:
        res = AwsMonitor(AWS_SETTINGS).fetch(resources, NOW_MS - 3_600_000, NOW_MS)
    q = {x["Id"]: x["MetricStat"] for x in captured["q"]}
    assert len(q) == len(ids) and all(s["Period"] == 60 for s in q.values())
    ecs_dims = next(s for qid, s in q.items() if s["Metric"]["Namespace"] == "AWS/ECS")["Metric"]["Dimensions"]
    assert ecs_dims == [{"Name": "ClusterName", "Value": "shop-cluster"}, {"Name": "ServiceName", "Value": "checkout"}]
    alb_dim = next(s for s in q.values() if s["Metric"]["Namespace"] == "AWS/ApplicationELB")["Metric"]["Dimensions"]
    assert alb_dim == [{"Name": "LoadBalancer", "Value": "app/web/123abc"}]
    got = {(s.resource.type, s.metric.key): s.points[0][1] for s in res.series}
    assert got[("ec2", "cpu")] == 50.0
    assert got[("ec2", "net_in")] == 100_000.0                 # bytes per 1-minute sum → bytes per second
    assert got[("alb", "requests")] == 1200.0                  # per minute at a 60 s period
    assert got[("alb", "response_time")] == 250.0              # seconds → ms
    memory = next(m for m in res.missing if m["resource"] == "app-1" and m["metric"] == "Memory used")
    assert "CloudWatch agent" in memory["why"]


def test_aws_old_run_uses_coarser_data(monkeypatch):
    clients, stubs = _stubbed(monkeypatch, ["cloudwatch"])
    r = Resource("aws", "rds", "orders-db", "orders", "ap-south-1")
    stubs["cloudwatch"].add_response("get_metric_data", {"MetricDataResults": [
        {"Id": "m0", "Label": "CPU", "StatusCode": "Complete",
         "Timestamps": [datetime(2026, 9, 10, tzinfo=timezone.utc)], "Values": [40.0]}]}, None)
    start = NOW_MS - 30 * 86_400_000
    with stubs["cloudwatch"]:
        res = AwsMonitor(AWS_SETTINGS).fetch([r], start, start + 3_600_000)
    assert res.series[0].period_s == 300 and "5-minute resolution" in res.warnings[0]


def test_aws_discovery(monkeypatch):
    clients, stubs = _stubbed(monkeypatch, ["ec2", "elbv2", "rds"])
    stubs["ec2"].add_response("describe_instances", {"Reservations": [{"Instances": [
        {"InstanceId": "i-1", "Tags": [{"Key": "Name", "Value": "api-1"}]}, {"InstanceId": "i-2"}]}]}, None)
    stubs["elbv2"].add_response("describe_load_balancers", {"LoadBalancers": [
        {"LoadBalancerArn": "arn:aws:elasticloadbalancing:ap-south-1:123:loadbalancer/app/web/50dc6c495c0c9188",
         "LoadBalancerName": "web", "Type": "application"}]}, None)
    stubs["rds"].add_client_error("describe_db_instances", service_error_code="AccessDenied",
                                  service_message="not allowed", http_status_code=403)
    with stubs["ec2"], stubs["elbv2"], stubs["rds"]:
        found, warnings = AwsMonitor(AWS_SETTINGS).discover(["ec2", "alb", "rds"], "ap-south-1")
    assert [(r.type, r.id, r.name) for r in found] == [
        ("ec2", "i-1", "api-1"), ("ec2", "i-2", "i-2"), ("alb", "app/web/50dc6c495c0c9188", "web")]
    assert len(warnings) == 1 and "AWS denied" in warnings[0] and "not allowed" in warnings[0]


def test_aws_sign_in_problems_are_explained(monkeypatch):
    from botocore.exceptions import NoCredentialsError
    mon = AwsMonitor({"aws_region": "ap-south-1"})
    monkeypatch.setattr(AwsMonitor, "client", lambda self, service, region="": (_ for _ in ()).throw(NoCredentialsError()))
    with pytest.raises(CloudError, match="aws configure"):
        mon.check()
    with pytest.raises(CloudError, match="was not found"):
        AwsMonitor({"aws_profile": "nope-profile", "aws_region": "ap-south-1"}).session()


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------

class FakeMetrics:
    def __init__(self, data, reject=()):
        self.data, self.reject, self.calls = data, set(reject), []

    def list(self, resource_uri, timespan, interval, metricnames, aggregation):
        from azure.core.exceptions import HttpResponseError
        names = metricnames.split(",")
        self.calls.append(names)
        if any(n in self.reject for n in names):
            raise HttpResponseError(message=f"Failed to find metric configuration for {names}")
        t0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
        value = []
        for n in names:
            if n not in self.data:
                continue
            agg, v = self.data[n]
            points = [SimpleNamespace(time_stamp=t0 + timedelta(minutes=i), **{agg: v}) for i in range(3)]
            value.append(SimpleNamespace(name=SimpleNamespace(value=n), timeseries=[SimpleNamespace(data=points)]))
        return SimpleNamespace(value=value)


VM_ID = "/subscriptions/1111/resourceGroups/rg-shop/providers/Microsoft.Compute/virtualMachines/api-1"
APP_ID = "/subscriptions/1111/resourceGroups/rg-shop/providers/Microsoft.Web/sites/shop-web"


def test_azure_fetch_with_fallback(monkeypatch):
    fake = FakeMetrics({"Percentage CPU": ("average", 88.0), "Network In Total": ("total", 6_000_000.0),
                        "HttpResponseTime": ("average", 0.4), "Http5xx": ("total", 12.0)},
                       reject={"CPU Credits Remaining"})
    monkeypatch.setattr(AzureMonitor, "monitor", lambda self, sub: SimpleNamespace(metrics=fake))
    res = AzureMonitor({}).fetch([Resource("azure", "vm", VM_ID, "api-1"), Resource("azure", "appservice", APP_ID, "shop-web")],
                                 NOW_MS - 3_600_000, NOW_MS)
    got = {(s.resource.type, s.metric.key): s.points[0][1] for s in res.series}
    assert got[("vm", "cpu")] == 88.0
    assert got[("vm", "net_in")] == 100_000.0                  # per-minute total → bytes per second
    assert got[("appservice", "response_time")] == 400.0       # seconds → ms
    assert got[("appservice", "http5xx")] == 12.0              # per minute
    assert len(fake.calls[0]) == len(AZURE["vm"].metrics)      # one call for all metrics …
    assert ["CPU Credits Remaining"] in fake.calls             # … then each on its own after a rejection
    assert any(m["metric"] == "CPU credits remaining" and "B-series" in m["why"] for m in res.missing)


def test_azure_retention_and_discovery(monkeypatch):
    with pytest.raises(CloudError, match="93 days"):
        AzureMonitor({}).fetch([Resource("azure", "vm", VM_ID)], NOW_MS - 100 * 86_400_000, NOW_MS - 99 * 86_400_000)
    items = [SimpleNamespace(id=VM_ID, name="api-1", type="Microsoft.Compute/virtualMachines", location="centralindia"),
             SimpleNamespace(id=APP_ID, name="shop-web", type="Microsoft.Web/sites", location="westeurope"),
             SimpleNamespace(id="/subscriptions/1111/x", name="other", type="Microsoft.Storage/storageAccounts", location="x")]
    seen = {}

    class Res:
        def list(self, filter):
            seen["filter"] = filter
            return items
    monkeypatch.setattr(AzureMonitor, "resources_client", lambda self: SimpleNamespace(resources=Res()))
    found, _ = AzureMonitor({"azure_subscription_id": "1111"}).discover(["vm", "appservice"])
    assert [(r.type, r.name) for r in found] == [("vm", "api-1 (rg-shop)"), ("appservice", "shop-web (rg-shop)")]
    assert "Microsoft.Compute/virtualMachines" in seen["filter"] and "Microsoft.Web/sites" in seen["filter"]
    found, _ = AzureMonitor({"azure_subscription_id": "1111"}).discover(["vm", "appservice"], "westeurope")
    assert [r.type for r in found] == ["appservice"]


# ---------------------------------------------------------------------------
# Detectors and links with test findings
# ---------------------------------------------------------------------------

def _series(doc, rtype, metric, values, provider="aws", rid="r-1", name="orders-db", start_s=0, period=60):
    from projectstack.results.cloud.presets import resource_type
    m = next(x for x in resource_type(provider, rtype).metrics if x.key == metric)
    start = int(datetime.fromisoformat(doc["run"]["start"]).timestamp() * 1000)
    return Series(Resource(provider, rtype, rid, name, "ap-south-1", "database"), m,
                  [(start + (start_s + i * period) * 1000, float(v)) for i, v in enumerate(values)], period)


def test_server_detectors(monkeypatch):
    doc = sample_doc()                                    # 42 minutes; latency_change at t+18m (1080 s)
    flat = lambda v, n=42: [v] * n  # noqa: E731
    cpu = [40] * 18 + [93, 96, 97, 95, 94, 92, 91, 90, 89, 88] + [50] * 14
    series = [
        _series(doc, "rds", "cpu", cpu),
        _series(doc, "ec2", "credits", [100 - i * 4 for i in range(26)] + [0] * 16, rid="i-1", name="api-1"),
        _series(doc, "ecs", "memory", [50 + i * 0.8 for i in range(42)], rid="c/s", name="checkout"),
        _series(doc, "alb", "target_5xx", [0] * 30 + [20, 30] + [0] * 10, rid="app/w/1", name="web"),
        _series(doc, "lambda", "throttles", [0] * 20 + [5] + [0] * 21, rid="fn", name="pricing"),
        _series(doc, "rds", "connections", [60] * 18 + [100] * 12 + [70] * 12),
        _series(doc, "asg", "in_service", [2] * 25 + [4] * 17, rid="asg-1", name="api-asg"),
        _series(doc, "rds", "read_latency", [2] * 18 + [9] * 24),
        _series(doc, "rds", "free_memory", flat(8e9)),
    ]
    found = detect(series, doc)
    by = {(f["evidence"]["pattern"], f["evidence"]["resource"]): f for f in found}
    hot = by[("hot", "orders-db")]
    assert hot["severity"] == "critical" and hot["start_offset_s"] == 1080 and hot["evidence"]["peak"] == 97
    assert by[("credits", "api-1")]["severity"] == "critical"
    assert by[("memory_growth", "checkout")]["evidence"]["after"] > by[("memory_growth", "checkout")]["evidence"]["before"] + 15
    assert by[("errors", "web")]["evidence"]["total"] == 50
    assert by[("throttles", "pricing")]["severity"] == "critical"
    assert by[("connections", "orders-db")]["severity"] == "info"
    assert by[("scaling", "api-asg")]["evidence"]["high"] == 4
    lat = by[("latency", "orders-db")]
    assert lat["evidence"]["before"] == 2 and lat["evidence"]["after"] == 9
    assert ("memory_growth", "orders-db") not in by                      # flat available memory: nothing

    tests = [f for f in copy.deepcopy(doc["findings"])]
    link(tests, found)
    latency = next(f for f in tests if f["type"] == "latency_change")
    assert "At the same time on the servers:" in latency["detail"] and hot["title"] in latency["evidence"]["server"]
    assert latency["title"] in hot["evidence"]["coincides_with"] and "It coincides with" in hot["detail"]
    assert by[("connections", "orders-db")]["severity"] == "warning"    # promoted: coincides with a critical slowdown
    assert by[("scaling", "api-asg")]["severity"] == "info"


# ---------------------------------------------------------------------------
# Attaching to an analysed run (ClickHouse), reports, endpoints, CLI
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from projectstack.config import load_settings
    from projectstack.results.analyze import analyze_file
    from projectstack.results.db import ClickHouseConfig, ResultsDB
    from tests.jtl_synth import ramp_steady, scenario_csv
    name = f"pstack_test_{secrets.token_hex(4)}"
    runs = tmp_path_factory.mktemp("runs")
    s = {**load_settings(), "clickhouse_database": name, "runs_dir": str(runs),
         "anthropic_api_key": "", "gemini_api_key": "", "groq_api_key": "", "openai_api_key": ""}
    try:
        db = ResultsDB(ClickHouseConfig.from_settings(s))
        db.rows("SELECT 1")
    except Exception as exc:
        pytest.skip(f"local ClickHouse not available: {exc}")
    jtl = scenario_csv(tmp_path_factory.mktemp("jtl") / "k.jtl", ramp_steady(users=20, ramp_s=120, steady_s=900, down_s=60),
                       with_tc=True, latency_ms=lambda sec, u: 200.0 if sec < 9 * 60 else 600.0)
    run = analyze_file(jtl, settings=s, scenario="cloudy", tag="c1", sla="p95=3000,err=1")
    yield {"settings": s, "db": db, "run": run, "jtl": jtl}
    db.command(f"DROP DATABASE IF EXISTS {name}")


def _fake_result(doc):
    cpu = [40] * 9 + [92, 95, 97, 96, 94, 93, 91] + [60] * 4
    return FetchResult([_series(doc, "rds", "cpu", cpu, start_s=0),
                        _series(doc, "rds", "connections", [30 + i for i in range(20)])],
                       [{"resource": "orders-db", "metric": "Read latency", "why": "CloudWatch returned no data for this window"}], [])


def test_attach_to_run(env):
    run, db = env["run"], env["db"]
    assert run.findings["verdict"] == "at_risk"            # the latency step is already a warning or worse
    doc = json.loads((run.folder / "findings.json").read_text())
    resources = [Resource("aws", "rds", "r-1", "orders-db", "ap-south-1", "database")]
    for _ in range(2):                                      # attaching twice replaces, never duplicates
        info = attach(db, run.folder, _fake_result(doc), resources)
    after = json.loads((run.folder / "findings.json").read_text())
    server = [f for f in after["findings"] if f["type"] == "server"]
    assert info["findings"] == len(server) == 1 and server[0]["evidence"]["pattern"] == "hot"
    latency = next(f for f in after["findings"] if f["type"] == "latency_change")
    assert latency["detail"].count("At the same time on the servers:") == 1
    assert after["server"]["resources"][0]["name"] == "orders-db" and after["server"]["missing"]
    assert db.rows(f"SELECT count() FROM {db.db}.server_metrics WHERE run_id = {{r:String}}", {"r": run.run_id})[0][0] == 40
    assert json.loads((run.folder / "server.json").read_text())["series"][0]["points"][0]["t_offset_s"] == 0

    from projectstack.results import numbers
    from projectstack.results.narrative import from_rules
    from projectstack.results.report import render
    tech = from_rules(after, "tech")
    top = tech.content["root_causes"][0]
    assert top["cause"].startswith("Processor capacity on orders-db") and top["confidence"] == "high"
    assert any("orders-db" in t["test"] for t in tech.content["next_tests"])
    assert any(p.startswith("On the servers:") for p in from_rules(after, "exec").content["key_points"])
    for n in (tech, from_rules(after, "exec")):
        check = numbers.check(n.content, after)
        assert check.ok, check.unverified
    html = render(after, tech, "tech", "html")
    assert "Server metrics" in html and "orders-db" in html and "No data:" in html


def test_targets_and_fetch_and_attach(env, monkeypatch):
    from projectstack.results import cloud
    from projectstack.results.cloud.analysis import fetch_and_attach
    s = env["settings"]
    with pytest.raises(CloudError, match="No cloud resources are saved"):
        fetch_and_attach(env["db"], env["run"].folder, s)
    res = [Resource("aws", "rds", "r-1", "orders-db", "ap-south-1", "database")]
    save_targets(s, "cloudy", res)
    assert load_targets(s, "cloudy") == res and Path(s["runs_dir"]).parent.joinpath("cloud_targets.json").exists()
    calls = {}

    def fake_fetch(resources, start_ms, end_ms, settings, padding_s=300):
        calls["window"] = (start_ms, end_ms)
        return _fake_result(json.loads((env["run"].folder / "findings.json").read_text()))
    monkeypatch.setattr(cloud, "fetch", fake_fetch)
    info = fetch_and_attach(env["db"], env["run"].folder, s)
    doc = env["run"].findings
    start = int(datetime.fromisoformat(doc["run"]["start"]).timestamp() * 1000)
    assert calls["window"][0] == start and info["series"] == 2
    monkeypatch.setattr(cloud, "fetch", lambda *a, **k: FetchResult([], [], ["AWS denied reading metrics"]))
    with pytest.raises(CloudError, match="No server metrics came back"):
        fetch_and_attach(env["db"], env["run"].folder, s)


def test_cloud_endpoints_and_auto_fetch(env, tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from projectstack.api import main as api
    from projectstack.api import results_api
    from projectstack.results import cloud
    s = env["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    monkeypatch.setattr(results_api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(cloud, "fetch", lambda resources, start_ms, end_ms, settings, padding_s=300:
                        _fake_result(json.loads((env["run"].folder / "findings.json").read_text())))
    client = TestClient(api.app)
    assert client.get("/api/results/cloud/status").json()["aws"]["sdk"] is True
    assert len(client.get("/api/results/cloud/catalog").json()["types"]) == len(TYPES)
    body = {"scenario": "cloudy", "resources": [{"provider": "aws", "type": "rds", "id": "r-1", "name": "orders-db", "role": "database"}]}
    assert client.put("/api/results/cloud/targets", json=body).status_code == 200
    assert client.get("/api/results/cloud/targets", params={"scenario": "cloudy"}).json()["resources"][0]["id"] == "r-1"
    bad = client.put("/api/results/cloud/targets", json={"scenario": "x", "resources": [{"provider": "gcp", "type": "vm", "id": "1"}]})
    assert bad.status_code == 400

    run_id = env["run"].run_id
    res = client.post(f"/api/results/runs/{run_id}/server-metrics", json={})
    assert res.status_code == 200, res.text
    got = client.get(f"/api/results/runs/{run_id}/server-metrics").json()
    assert got["available"] and len(got["series"]) == 2 and got["findings"][0]["evidence"]["pattern"] == "hot"

    # A new upload of the same scenario fetches the saved resources by itself
    with open(env["jtl"], "rb") as f:
        job = client.post("/api/results/analyze", files={"file": ("k.jtl", f)}, data={"scenario": "cloudy"}).json()
    deadline = time.monotonic() + 60
    while job["status"] not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(0.2)
        job = client.get(f"/api/results/jobs/{job['id']}").json()
    assert job["status"] == "done" and any("Server metrics: 2 series" in p for p in job["progress"]), job["progress"]
    assert client.get(f"/api/results/runs/{job['run_id']}/server-metrics").json()["available"]


def test_cloud_cli(env, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from projectstack.cli.main import cli
    from projectstack.results import cloud
    s = env["settings"]
    monkeypatch.setenv("PROJECTSTACK_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("PSTACK_CLICKHOUSE_DATABASE", s["clickhouse_database"])
    monkeypatch.setenv("PSTACK_RUNS_DIR", s["runs_dir"])
    monkeypatch.setattr(cloud, "fetch", lambda resources, start_ms, end_ms, settings, padding_s=300:
                        _fake_result(json.loads((env["run"].folder / "findings.json").read_text())))
    runner = CliRunner()
    res = runner.invoke(cli, ["cloud", "targets", "cli-scn", "--clear", "--add", "aws:rds:r-1:orders-db:database",
                              "--add", f"azure:vm:{VM_ID}:api-1:app"])
    assert res.exit_code == 0 and f"azure:vm:{VM_ID}:api-1:app" in res.output
    assert runner.invoke(cli, ["cloud", "targets", "cli-scn", "--add", "gcp:vm:1"]).exit_code == 2
    res = runner.invoke(cli, ["server-metrics", env["run"].run_id, "--resource", "aws:rds:r-1:orders-db:database"])
    assert res.exit_code == 0 and "2 metric series, 1 server finding(s)" in res.output
