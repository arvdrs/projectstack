"""Tests for Phase 5: confidence scoring, Postman exporter, Grafana exporter."""

import csv
import json
import time
from pathlib import Path

import pytest

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue
from projectstack.correlator.confidence import score, label, annotate
from projectstack.exporters.postman_exporter import export_postman
from projectstack.exporters.grafana_exporter import export_influxdb, export_grafana_dashboard
from projectstack.runner.metrics_parser import parse_jtl, MetricsSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cv(var_name, extract_from, key, value, used_in, detection_pass):
    return CorrelatedValue(
        var_name=var_name,
        source_request_index=0,
        extract_from=extract_from,
        extract_key=key,
        extract_value=value,
        used_in_requests=used_in,
        detection_pass=detection_pass,
        reason="test",
    )


def _req(method="GET", url="https://api.example.com/v1/login", body=None, headers=None):
    return HarRequest(
        method=method, url=url,
        headers=headers or {"Content-Type": "application/json", "Authorization": "Bearer tok123"},
        query_params=[],
        body=body, body_mime_type="application/json" if body else None,
        status_code=200, response_headers={},
        response_body=None, response_mime_type=None, cookies=[],
    )


def _summary(total=100, passed=95, failed=5, duration=10.0) -> MetricsSummary:
    s = MetricsSummary()
    s.total_requests = total
    s.passed = passed
    s.failed = failed
    s.duration_sec = duration
    s.all_times = [100, 200, 300, 400, 500] * (total // 5)
    s.start_epoch_ms = int(time.time() * 1000) - int(duration * 1000)
    s.end_epoch_ms = int(time.time() * 1000)

    from projectstack.runner.metrics_parser import SamplerMetrics
    sm = SamplerMetrics(label="GET /login")
    sm.total, sm.passed, sm.failed = total, passed, failed
    sm.times = s.all_times[:]
    s.samplers["GET /login"] = sm
    return s


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def test_jwt_gets_high_score():
    cv = _cv("VAR_jwt", "body", "jwt_token", "eyJhbGciOiJIUzI1NiJ9.payload.sig", [1, 2], "pass1_jwt")
    assert score(cv) >= 75


def test_bearer_gets_high_score():
    cv = _cv("VAR_bearer", "body", "(bearer token)", "eyJhbGciOiJIUzI1NiJ9.payload.sig", [1], "pass1_bearer")
    assert score(cv) >= 75


def test_token_key_name_boosts_score():
    cv1 = _cv("VAR_token", "body", "access_token", "tok_abc123xyz789", [1], "pass2_json")
    cv2 = _cv("VAR_other", "body", "something_else", "tok_abc123xyz789", [1], "pass2_json")
    assert score(cv1) > score(cv2)


def test_more_uses_boosts_score():
    cv_few = _cv("VAR_t", "body", "token", "tok_abc123xyz789", [1], "pass2_json")
    cv_many = _cv("VAR_t", "body", "token", "tok_abc123xyz789", [1, 2, 3, 4], "pass2_json")
    assert score(cv_many) > score(cv_few)


def test_score_capped_at_99():
    cv = _cv("VAR_jwt", "body", "access_token",
             "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWxpY2UifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
             [1, 2, 3, 4, 5], "pass1_jwt")
    assert score(cv) <= 99


def test_label_high():
    assert label(80) == "High"


def test_label_medium():
    assert label(60) == "Medium"


def test_label_low():
    assert label(30) == "Low"


def test_annotate_returns_all_fields():
    cv = _cv("VAR_token", "body", "token", "tok_abc123xyz789", [1], "pass2_json")
    result = annotate([cv])
    assert len(result) == 1
    assert "confidence_score" in result[0]
    assert "confidence_label" in result[0]
    assert result[0]["value"] is cv


# ---------------------------------------------------------------------------
# Postman exporter
# ---------------------------------------------------------------------------

def test_postman_valid_json(tmp_path):
    out = tmp_path / "collection.json"
    export_postman([_req()], out)
    data = json.loads(out.read_text())
    assert data["info"]["schema"].startswith("https://schema.getpostman.com")


def test_postman_one_item_per_request(tmp_path):
    out = tmp_path / "collection.json"
    export_postman([_req(), _req(url="https://api.example.com/v1/users")], out)
    data = json.loads(out.read_text())
    assert len(data["item"]) == 2


def test_postman_preserves_method(tmp_path):
    out = tmp_path / "collection.json"
    export_postman([_req(method="POST", body='{"x":1}')], out)
    data = json.loads(out.read_text())
    assert data["item"][0]["request"]["method"] == "POST"


def test_postman_body_included(tmp_path):
    out = tmp_path / "collection.json"
    export_postman([_req(method="POST", body='{"username":"alice"}')], out)
    data = json.loads(out.read_text())
    body = data["item"][0]["request"]["body"]
    assert body["mode"] == "raw"
    assert "alice" in body["raw"]


def test_postman_skips_noisy_headers(tmp_path):
    out = tmp_path / "collection.json"
    req = _req(headers={"Content-Type": "application/json", "content-length": "42"})
    export_postman([req], out)
    data = json.loads(out.read_text())
    header_names = [h["key"].lower() for h in data["item"][0]["request"]["header"]]
    assert "content-length" not in header_names


def test_postman_collection_name(tmp_path):
    out = tmp_path / "collection.json"
    export_postman([_req()], out, collection_name="My API Tests")
    data = json.loads(out.read_text())
    assert data["info"]["name"] == "My API Tests"


# ---------------------------------------------------------------------------
# Grafana / InfluxDB exporter
# ---------------------------------------------------------------------------

def test_influxdb_export_creates_file(tmp_path):
    out = tmp_path / "metrics.lp"
    export_influxdb(_summary(), out)
    assert out.exists()
    content = out.read_text()
    assert "jmeter_results" in content


def test_influxdb_contains_aggregate_line(tmp_path):
    out = tmp_path / "metrics.lp"
    export_influxdb(_summary(), out)
    content = out.read_text()
    assert "__aggregate__" in content


def test_influxdb_contains_sampler_line(tmp_path):
    out = tmp_path / "metrics.lp"
    export_influxdb(_summary(), out, test_name="mytest")
    content = out.read_text()
    assert "GET\\ /login" in content or "GET /login" in content.replace("\\ ", " ")


def test_influxdb_contains_key_fields(tmp_path):
    out = tmp_path / "metrics.lp"
    export_influxdb(_summary(), out)
    content = out.read_text()
    assert "avg_ms=" in content
    assert "error_rate=" in content
    assert "total=" in content


def test_grafana_dashboard_valid_json(tmp_path):
    out = tmp_path / "dashboard.json"
    export_grafana_dashboard(_summary(), out)
    data = json.loads(out.read_text())
    assert "dashboard" in data
    assert data["dashboard"]["title"] is not None


def test_grafana_dashboard_has_panels(tmp_path):
    out = tmp_path / "dashboard.json"
    export_grafana_dashboard(_summary(), out)
    data = json.loads(out.read_text())
    panels = data["dashboard"]["panels"]
    assert len(panels) >= 4  # 4 stat panels + table


def test_grafana_dashboard_meta_summary(tmp_path):
    out = tmp_path / "dashboard.json"
    export_grafana_dashboard(_summary(total=50, passed=48, failed=2), out)
    data = json.loads(out.read_text())
    meta = data["dashboard"]["_meta"]["summary"]
    assert meta["total"] == 50
    assert meta["failed"] == 2
