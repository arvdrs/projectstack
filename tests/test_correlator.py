"""Tests for the multi-pass correlation engine."""

import json
import pytest
from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import (
    run_correlation,
    _pass1_jwt_bearer,
    _pass2_json_keys,
    _pass3_headers_cookies,
    _pass5_backward_inference,
    CorrelatedValue,
)


def _req(
    method="GET",
    url="https://example.com/api",
    headers=None,
    body=None,
    status=200,
    res_headers=None,
    res_body=None,
    cookies=None,
):
    return HarRequest(
        method=method,
        url=url,
        headers=headers or {},
        query_params=[],
        body=body,
        body_mime_type="application/json" if body else None,
        status_code=status,
        response_headers=res_headers or {},
        response_body=res_body,
        response_mime_type="application/json",
        cookies=cookies or [],
    )


# ---------------------------------------------------------------------------
# Pass 1: JWT / Bearer
# ---------------------------------------------------------------------------

def test_pass1_detects_jwt_in_response():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWxpY2UifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    reqs = [_req(res_body=json.dumps({"token": jwt}))]
    found = _pass1_jwt_bearer(reqs)
    assert any(jwt[:20] in cv.extract_value for cv in found)


def test_pass1_detects_bearer_usage():
    token = "abc123tokenvalue999"
    login_res = json.dumps({"access_token": token})
    reqs = [
        _req(res_body=login_res),
        _req(headers={"Authorization": f"Bearer {token}"}),
    ]
    found = _pass1_jwt_bearer(reqs)
    assert any(cv.used_in_requests == [1] for cv in found)


# ---------------------------------------------------------------------------
# Pass 2: JSON keys
# ---------------------------------------------------------------------------

def test_pass2_extracts_dynamic_json_fields():
    res = json.dumps({"user_id": "usr_abc123xyz", "name": "Alice"})
    reqs = [_req(res_body=res)]
    found = _pass2_json_keys(reqs)
    keys = [cv.extract_key for cv in found]
    assert "user_id" in keys


def test_pass2_skips_short_values():
    res = json.dumps({"id": "42", "flag": "ok"})
    reqs = [_req(res_body=res)]
    found = _pass2_json_keys(reqs)
    assert len(found) == 0


def test_pass2_handles_nested_json():
    res = json.dumps({"data": {"session": {"token": "tok_abc123xyz789"}}})
    reqs = [_req(res_body=res)]
    found = _pass2_json_keys(reqs)
    assert any("token" in cv.extract_key for cv in found)


# ---------------------------------------------------------------------------
# Pass 3: Headers and cookies
# ---------------------------------------------------------------------------

def test_pass3_extracts_dynamic_header():
    reqs = [_req(res_headers={"X-Auth-Token": "tok_supersecret9999"})]
    found = _pass3_headers_cookies(reqs)
    assert any(cv.extract_key == "X-Auth-Token" for cv in found)


def test_pass3_skips_content_type():
    reqs = [_req(res_headers={"Content-Type": "application/json"})]
    found = _pass3_headers_cookies(reqs)
    assert len(found) == 0


def test_pass3_leaves_cookies_to_cookie_manager():
    # Cookie state is handled by JMeter's HTTP Cookie Manager, not by extractors
    reqs = [_req(cookies=[{"name": "session_id", "value": "sess_abc123xyz789"}],
                 res_headers={"Set-Cookie": "session_id=sess_abc123xyz789; Path=/"})]
    found = _pass3_headers_cookies(reqs)
    assert not any(cv.extract_from == "cookie" for cv in found)


# ---------------------------------------------------------------------------
# Pass 5: Backward inference
# ---------------------------------------------------------------------------

def test_pass5_links_value_to_later_request():
    token = "tok_unique_value_9999"
    candidates = [
        CorrelatedValue(
            var_name="VAR_token",
            source_request_index=0,
            extract_from="body",
            extract_key="token",
            extract_value=token,
        )
    ]
    reqs = [
        _req(res_body=json.dumps({"token": token})),
        _req(headers={"Authorization": f"Bearer {token}"}),
        _req(body=json.dumps({"auth": token})),
    ]
    _pass5_backward_inference(reqs, candidates)
    assert 1 in candidates[0].used_in_requests
    assert 2 in candidates[0].used_in_requests


# ---------------------------------------------------------------------------
# Full pipeline (no Ollama)
# ---------------------------------------------------------------------------

def test_full_pipeline_login_flow():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWxpY2UifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    reqs = [
        _req(
            method="POST",
            url="https://api.example.com/login",
            res_body=json.dumps({"access_token": token, "user_id": "usr_123abc"}),
        ),
        _req(
            method="GET",
            url="https://api.example.com/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        ),
    ]
    result = run_correlation(reqs, use_ai=False)
    var_names = [cv.var_name for cv in result]
    assert len(result) > 0
    assert any("token" in n.lower() or "jwt" in n.lower() for n in var_names)
