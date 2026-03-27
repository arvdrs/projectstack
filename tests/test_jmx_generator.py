"""Tests for the JMX generator."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue
from projectstack.generator.jmx_generator import generate_jmx, JmxGenerator


def _req(method="GET", url="https://api.example.com/v1/users", body=None, headers=None, res_body=None):
    return HarRequest(
        method=method,
        url=url,
        headers=headers or {"Content-Type": "application/json"},
        query_params=[],
        body=body,
        body_mime_type="application/json" if body else None,
        status_code=200,
        response_headers={},
        response_body=res_body,
        response_mime_type="application/json",
        cookies=[],
    )


def _corr(var_name, source_idx, extract_from, key, value, used_in):
    return CorrelatedValue(
        var_name=var_name,
        source_request_index=source_idx,
        extract_from=extract_from,
        extract_key=key,
        extract_value=value,
        used_in_requests=used_in,
        detection_pass="pass2_json",
        reason="test",
    )


def _parse_jmx(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------

def test_generates_valid_xml(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req()], [], output_path=out)
    assert out.exists()
    root = _parse_jmx(out)
    assert root.tag == "jmeterTestPlan"


def test_has_test_plan(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req()], [], output_path=out)
    root = _parse_jmx(out)
    assert root.find(".//TestPlan") is not None


def test_has_thread_group(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req()], [], output_path=out, threads=5, ramp_up=10, loops=3)
    root = _parse_jmx(out)
    tg = root.find(".//ThreadGroup")
    assert tg is not None
    assert tg.find("stringProp[@name='ThreadGroup.num_threads']").text == "5"
    assert tg.find("stringProp[@name='ThreadGroup.ramp_time']").text == "10"


def test_one_sampler_per_request(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req(), _req(url="https://api.example.com/v1/orders")], [], output_path=out)
    root = _parse_jmx(out)
    assert len(root.findall(".//HTTPSamplerProxy")) == 2


def test_sampler_domain_and_path(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req(url="https://api.example.com/v1/login")], [], output_path=out)
    root = _parse_jmx(out)
    sampler = root.find(".//HTTPSamplerProxy")
    domain = sampler.find("stringProp[@name='HTTPSampler.domain']").text
    path = sampler.find("stringProp[@name='HTTPSampler.path']").text
    assert domain == "api.example.com"
    assert path == "/v1/login"


def test_post_body_in_sampler(tmp_path):
    out = tmp_path / "out.jmx"
    body = json.dumps({"username": "alice", "password": "secret"})
    generate_jmx([_req(method="POST", body=body)], [], output_path=out)
    root = _parse_jmx(out)
    sampler = root.find(".//HTTPSamplerProxy")
    raw_flag = sampler.find("boolProp[@name='HTTPSampler.postBodyRaw']").text
    assert raw_flag == "true"


def test_header_manager_present(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req()], [], output_path=out)
    root = _parse_jmx(out)
    assert root.find(".//HeaderManager") is not None


# ---------------------------------------------------------------------------
# Correlation tests
# ---------------------------------------------------------------------------

def test_extractor_generated_for_correlated_value(tmp_path):
    out = tmp_path / "out.jmx"
    cv = _corr("VAR_token", 0, "body", "access_token", "tok_abc123xyz", [1])
    generate_jmx([_req(), _req()], [cv], output_path=out)
    root = _parse_jmx(out)
    extractors = root.findall(".//JSR223PostProcessor")
    assert len(extractors) == 1
    assert extractors[0].attrib["testname"] == "Extract VAR_token"


def test_variable_substitution_in_header(tmp_path):
    out = tmp_path / "out.jmx"
    token = "tok_supersecretvalue1234"
    cv = _corr("VAR_token", 0, "body", "access_token", token, [1])
    reqs = [
        _req(res_body=json.dumps({"access_token": token})),
        _req(headers={"Authorization": f"Bearer {token}"}),
    ]
    generate_jmx(reqs, [cv], output_path=out)
    root = _parse_jmx(out)
    # Second sampler's HeaderManager should have ${VAR_token}
    header_managers = root.findall(".//HeaderManager")
    second_hm = header_managers[1]
    auth_header = None
    for hdr in second_hm.findall(".//elementProp[@elementType='Header']"):
        name = hdr.find("stringProp[@name='Header.name']")
        if name is not None and name.text == "Authorization":
            auth_header = hdr.find("stringProp[@name='Header.value']").text
    assert auth_header == "Bearer ${VAR_token}"


def test_groovy_script_contains_var_name(tmp_path):
    out = tmp_path / "out.jmx"
    cv = _corr("VAR_access_token", 0, "body", "access_token", "tok_abc123xyz", [1])
    generate_jmx([_req(), _req()], [cv], output_path=out)
    root = _parse_jmx(out)
    script = root.find(".//JSR223PostProcessor//stringProp[@name='script']").text
    assert "VAR_access_token" in script
    assert "access_token" in script


def test_no_timers_in_output(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req(), _req()], [], output_path=out)
    content = out.read_text()
    assert "Timer" not in content


def test_test_name_set_correctly(tmp_path):
    out = tmp_path / "out.jmx"
    generate_jmx([_req()], [], output_path=out, test_name="My API Test")
    root = _parse_jmx(out)
    tp = root.find(".//TestPlan")
    assert tp.attrib["testname"] == "My API Test"
