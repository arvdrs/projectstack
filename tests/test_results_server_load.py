"""
How server metrics followed the load test (server_load): lining server points up with
the test's timeline, the correlations with users, p95 (over the flat-load steady state)
and errors, the steady-state trend, and the observations written from them.
"""

import random

import pytest

from projectstack.results import server_load
from tests.test_results_highlights import stepped_doc


def doc_with_slowdown():
    """The stepped 500-user run with a p95 bump at minutes 40-50 and a few errors at 44-47."""
    doc = stepped_doc()
    rnd = random.Random(1)
    for i, p in enumerate(doc["timeline"]["points"]):
        p["p95_ms"] = 250 + (120 if 40 <= i <= 50 else 0) + rnd.randint(-10, 10)
        p["errors"] = 3 if 44 <= i <= 47 else 0
    return doc


def series(doc, name, unit, f, period_s=60):
    rnd = random.Random(name)
    return {"name": name, "unit": unit, "period_s": period_s,
            "points": [(p["t_offset_s"], f(i, p) + rnd.uniform(-1, 1)) for i, p in enumerate(doc["timeline"]["points"])]}


def test_pearson_and_strength():
    assert server_load.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert server_load.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert server_load.pearson([1, 2, 3], [5, 5, 5]) is None                       # no variance, no r
    assert server_load.pearson([1, 2], [1, 2]) is None
    assert [server_load.strength(r) for r in (0.95, -0.5, 0.2, None)] == ["strong", "moderate", "weak", None]


def test_relation_tells_load_response_times_errors_and_trends_apart():
    doc = doc_with_slowdown()
    rel = server_load.relate([
        series(doc, "CPU · app-server-1", "%", lambda i, p: 6 + p["users"] * 0.11),
        series(doc, "CPU · orders-db", "%", lambda i, p: 30 + (40 if 40 <= i <= 50 else 0)),
        series(doc, "Memory · app-server-1", "%", lambda i, p: 41 + 0.12 * max(0, i - 18)),
        {"name": "Disk queue · orders-db", "unit": "count", "period_s": 60,
         "points": [(p["t_offset_s"], 12.0 + (0.2 if i % 2 else 0)) for i, p in enumerate(doc["timeline"]["points"])]},
    ], doc, source="cloud")
    rows = {r["name"]: r for r in rel["rows"]}
    cpu, db, mem, queue = (rows[n] for n in ("CPU · app-server-1", "CPU · orders-db", "Memory · app-server-1",
                                              "Disk queue · orders-db"))
    assert cpu["load"] == "strong" and cpu["r_users"] > 0.95 and cpu["with_p95"] == "weak"
    assert cpu["steady_avg"] == pytest.approx(61, abs=1.5) and cpu["points"] == 80
    assert db["with_p95"] == "strong" and db["r_p95"] > 0.9 and db["p95_scope"] == "steady" and db["load"] == "weak"
    assert db["with_errors"] == "moderate"
    assert mem["trend"]["direction"] == "rising" and mem["trend"]["from"] < mem["trend"]["to"]
    assert queue["flat"] and queue["r_users"] is None and queue["load"] is None    # noise is not a correlation

    text = "\n".join(rel["observations"])
    assert "CPU · app-server-1 (r 1.00, about 61% at 500 users) followed the load" in text
    assert "the p95 response time and CPU · orders-db rose and fell together (r 0.9" in text
    assert "During the steady state, while the load was flat" in text
    assert "Disk queue · orders-db stayed between 12" in text and "no clear link with the load" in text
    assert "The errors partly coincided with CPU · orders-db" in text
    assert "Memory · app-server-1 rose with the load and kept rising during the steady state although the load " \
           "was flat (about 42% to 47%)" in text
    assert "partly followed" not in text                  # the climbing metric is not said to hold steady


def test_steady_response_times_and_no_link():
    doc = stepped_doc()                                     # p95 flat at 250 ms, no errors
    rel = server_load.relate([series(doc, "CPU · web", "%", lambda i, p: 5 + p["users"] * 0.11)], doc, source="cloud")
    text = " ".join(rel["observations"])
    assert "Response times were steady during the steady state" in text and "errors" not in text
    assert "peaked at about" in text and "headroom" in text                       # busiest % metric under 70%

    slow = doc_with_slowdown()
    rel = server_load.relate([series(slow, "CPU · web", "%", lambda i, p: 5 + p["users"] * 0.15)], slow, source="cloud")
    assert "did not follow any server metric shown (the closest is CPU · web" in " ".join(rel["observations"])
    assert "did not coincide with any server metric" in " ".join(rel["observations"])


def test_coarse_points_and_points_outside_the_test():
    doc = doc_with_slowdown()
    five_min = {"name": "CPU · db", "unit": "%", "period_s": 300,
                "points": [(t, 20 + t / 120) for t in range(0, 4800, 300)]}
    row = server_load.relate([five_min], doc, source="cloud")["rows"][0]
    assert row["points"] == 16 and row["p95_scope"] == "steady"                    # 5-minute values average the test minutes
    sparse = {"name": "CPU · db", "unit": "%", "period_s": 0, "points": [(t, 20 + t / 120) for t in range(0, 4800, 900)]}
    assert server_load.relate([sparse], doc, source="screenshots")["rows"][0]["p95_scope"] == "test"
    before = {"name": "CPU · db", "unit": "%", "points": [(-7200 + 60 * i, 50.0 + i) for i in range(30)]}
    assert server_load.relate([before], doc, source="cloud") is None              # nothing inside the test


def test_from_cloud_reads_server_json():
    doc = doc_with_slowdown()
    data = {"series": [{"resource_id": "i-1", "metric": "cpu", "label": "CPU utilisation", "resource": "app-1",
                        "unit": "%", "period_s": 60,
                        "points": [{"t_offset_s": p["t_offset_s"], "value": 5 + p["users"] * 0.1}
                                   for p in doc["timeline"]["points"]]}]}
    rel = server_load.from_cloud(data, doc)
    assert rel["source"] == "cloud" and rel["rows"][0]["name"] == "CPU utilisation · app-1"
    assert rel["rows"][0]["load"] == "strong"
    assert server_load.from_cloud(None, doc) is None and server_load.from_cloud({"series": []}, doc) is None


@pytest.mark.parametrize("v,unit,text", [(61.2, "%", "61%"), (4.25, "%", "4.2%"), (1322, "ms", "1,322 ms"),
                                         (3 * 2 ** 30, "bytes", "3.0 GB"), (12.04, "count", "12.0"), (None, "%", "—")])
def test_fmt_value(v, unit, text):
    assert server_load.fmt_value(v, unit) == text


def test_technical_report_shows_the_relation_under_the_server_metrics():
    from projectstack.results.narrative import from_rules
    from projectstack.results.report import render
    doc = doc_with_slowdown()
    doc["server"] = {"period_s": 60, "missing": [], "resources": [
        {"name": "orders-db", "provider": "aws", "type": "RDS instance", "role": "database",
         "metrics": [{"metric": "cpu", "label": "CPU", "unit": "%", "steady_avg": 38.0, "max": 71.2, "max_offset_s": 2700}]}]}
    rel = server_load.relate([series(doc, "CPU · orders-db", "%", lambda i, p: 30 + (40 if 40 <= i <= 50 else 0))],
                             doc, source="cloud")
    html = render(doc, from_rules(doc, "tech"), "tech", "html", None, None, rel)
    section = html[html.index("<h2>Server metrics</h2>"):]
    assert "Server metrics vs the load test" in section and "rose and fell together" in section
    assert "read off the screenshots" not in section
    md = render(doc, from_rules(doc, "tech"), "tech", "md", None, None, rel)
    assert md.index("## Server metrics") < md.index("### Server metrics vs the load test")


def test_report_chart_draws_the_servers_under_the_test():
    import re
    from projectstack.results import chart
    doc = doc_with_slowdown()
    rel = server_load.relate([
        series(doc, "CPU · app-server-1", "%", lambda i, p: 6 + p["users"] * 0.11),
        series(doc, "CPU · orders-db (RDS)", "%", lambda i, p: 30 + (40 if 40 <= i <= 50 else 0)),
        series(doc, "Connections · orders-db", "count", lambda i, p: 50 + p["users"] * 0.2),
        series(doc, "Memory · app-server-1", "%", lambda i, p: 41 + 0.12 * max(0, i - 18)),
    ], doc, source="cloud")
    assert [len(x["points"]) for x in rel["lines"]] == [80, 80, 80, 80] and "_points" not in rel["rows"][0]
    svg = chart.relation_svg(doc, rel)
    titles = re.findall(r'class="tl-title"[^>]*>([^<]+)<', svg)
    assert titles == ["Active users (test)", "p95 response time (test, ms)", "CPU (%)", "Connections (count)", "Memory (%)"]
    colours = re.findall(r'class="rl-line" d="[^"]+" style="stroke:var\(--s(\d)\)"', svg)
    assert colours == ["1", "2", "2", "1"]                 # one colour per server in every panel: app-server-1, orders-db
    assert svg.count('class="tl-band-steady"') == 5 and "app-server-1</text>" in svg
    assert chart.relation_svg(doc, None) == "" and chart.relation_svg(doc, {"lines": []}) == ""


def test_flat_metric_wording():
    doc = stepped_doc()
    flat = {"name": "Queue · db", "unit": "%", "period_s": 60,
            "points": [(p["t_offset_s"], 5.0 + (0.01 if i % 2 else 0)) for i, p in enumerate(doc["timeline"]["points"])]}
    text = " ".join(server_load.relate([flat], doc, source="cloud")["observations"])
    assert "Queue · db stayed at about 5.0%: no clear link with the load." in text
