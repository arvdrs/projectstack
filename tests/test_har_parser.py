"""Tests for the HAR file parser."""

import json
import pytest
from pathlib import Path
from projectstack.parser.har_parser import parse_har, HarRequest


def _write_har(tmp_path: Path, entries: list) -> Path:
    """Helper: write a minimal HAR file to a temp path."""
    har = {"log": {"version": "1.2", "entries": entries}}
    p = tmp_path / "test.har"
    p.write_text(json.dumps(har))
    return p


def _minimal_entry(
    method="GET",
    url="https://example.com/api",
    status=200,
    req_body=None,
    res_body=None,
):
    entry = {
        "request": {
            "method": method,
            "url": url,
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "queryString": [],
            "cookies": [],
        },
        "response": {
            "status": status,
            "headers": [{"name": "X-Token", "value": "abc123"}],
            "content": {"mimeType": "application/json", "text": res_body or ""},
        },
    }
    if req_body:
        entry["request"]["postData"] = {
            "mimeType": "application/json",
            "text": req_body,
        }
    return entry


# --- Happy path ---

def test_parse_single_get(tmp_path):
    path = _write_har(tmp_path, [_minimal_entry()])
    result = parse_har(path)
    assert len(result) == 1
    r = result[0]
    assert r.method == "GET"
    assert r.url == "https://example.com/api"
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/json"
    assert r.response_headers["X-Token"] == "abc123"


def test_parse_post_with_body(tmp_path):
    path = _write_har(tmp_path, [_minimal_entry(method="POST", req_body='{"user":"test"}')])
    result = parse_har(path)
    assert result[0].method == "POST"
    assert result[0].body == '{"user":"test"}'
    assert result[0].body_mime_type == "application/json"


def test_parse_multiple_entries(tmp_path):
    entries = [
        _minimal_entry(url="https://example.com/login", method="POST"),
        _minimal_entry(url="https://example.com/dashboard", method="GET"),
        _minimal_entry(url="https://example.com/logout", method="POST"),
    ]
    result = parse_har(tmp_path / "x.har" if False else _write_har(tmp_path, entries))
    assert len(result) == 3
    assert result[0].url.endswith("/login")
    assert result[2].url.endswith("/logout")


def test_parse_response_body(tmp_path):
    path = _write_har(tmp_path, [_minimal_entry(res_body='{"token":"xyz"}')])
    result = parse_har(path)
    assert result[0].response_body == '{"token":"xyz"}'


def test_empty_entries(tmp_path):
    path = _write_har(tmp_path, [])
    assert parse_har(path) == []


# --- Error cases ---

def test_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_har("/nonexistent/path/file.har")


def test_invalid_json(tmp_path):
    p = tmp_path / "bad.har"
    p.write_text("not json at all")
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_har(p)


def test_missing_log_key(tmp_path):
    p = tmp_path / "bad.har"
    p.write_text('{"wrong": "structure"}')
    with pytest.raises(ValueError, match="valid HAR"):
        parse_har(p)
