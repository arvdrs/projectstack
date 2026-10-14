"""Regression tests for the correlation upgrade: matching, new passes, generator fixes."""

import json
import xml.etree.ElementTree as ET

from projectstack.correlator.confidence import score
from projectstack.correlator.engine import (
    CorrelatedValue, NUM_ID_IN_PATH_RE, _is_dynamic, _pass1_jwt_bearer, _pass2_json_keys,
    _pass3_cookie_to_header, _pass3_redirect_params, _pass4_html, _pass_client_generated,
    _pass_rules, cv_from_dict, cv_to_dict, run_correlation,
)
from projectstack.correlator.matching import (
    apply_extractor, contains_value, eval_jsonpath, find_encoding, infer_boundaries,
    infer_extractor, smart_jsonpath,
)
from projectstack.correlator.rule_packs import load_packs, validate_pack
from projectstack.generator.jmx_generator import _apply_subs, generate_jmx
from projectstack.parser.har_parser import HarRequest

JWT_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMzQifQ.c2lnbmF0dXJlLWE"
JWT_B = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMzQiLCJ0eXAiOiJpZCJ9.c2lnbmF0dXJlLWI"


def _req(method="GET", url="https://app.example.com/api", headers=None, body=None, status=200,
         res_headers=None, res_body=None, mime="application/json", started="", raw=None):
    return HarRequest(
        method=method, url=url, headers=headers or {}, query_params=[], body=body,
        body_mime_type="application/json" if body else None, status_code=status,
        response_headers=res_headers or {}, response_body=res_body, response_mime_type=mime,
        cookies=[], started_datetime=started, raw=raw or {},
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_contains_value_respects_boundaries():
    assert contains_value("/orders/1234/items", "1234")
    assert not contains_value("/orders/91234567", "1234")
    assert contains_value('{"id":"ab12cd"}', "ab12cd")


def test_find_encoding_detects_url_encoded_values():
    assert find_encoding("__VIEWSTATE=%2FwEPDw%2B%3D%3D&x=1", "/wEPDw+==") == "url"
    assert find_encoding("token=abc123def", "abc123def") == "raw"


def test_smart_jsonpath_selects_list_item_by_identity_field():
    data = {"items": [{"name": "Gold", "id": "g-1"}, {"name": "Silver", "id": "s-2"}]}
    path, note = smart_jsonpath(data, ("items", 1, "id"))
    assert path == "$.items[?(@.name == 'Silver')].id"
    assert eval_jsonpath({"items": [{"name": "Silver", "id": "new"}, {"name": "Gold", "id": "x"}]}, path) == "new"


def test_infer_extractor_is_verified_against_the_recorded_response():
    r = _req(res_headers={"Location": "/callback?code=Qw3rTy99&state=s1"}, res_body="")
    spec = infer_extractor(r, "Qw3rTy99")
    assert spec["extractor"] == "header_regex"
    assert apply_extractor(spec["extractor"], spec["config"], "", r.response_headers) == "Qw3rTy99"


def test_infer_boundaries_finds_match_number():
    text = 'a="x1" b="tok-2" c="tok-3"'
    b = infer_boundaries(text, "tok-3")
    assert b and apply_extractor("boundary", b, text, {}) == "tok-3"


# ---------------------------------------------------------------------------
# Engine fixes
# ---------------------------------------------------------------------------

def test_numeric_path_regex_finds_consecutive_ids():
    assert NUM_ID_IN_PATH_RE.findall("/a/1234/5678/b") == ["1234", "5678"]


def test_is_dynamic_rules():
    assert _is_dynamic("9876543")                  # long numeric id
    assert _is_dynamic("123", "orderId")           # short id with id-like key
    assert not _is_dynamic("123", "page")
    assert not _is_dynamic("callback")             # plain word
    assert not _is_dynamic("2024-01-31")           # date
    assert _is_dynamic("aB3dE5fG")


def test_bearer_detected_with_lowercase_http2_header():
    token = "tok_" + "x" * 20
    reqs = [
        _req(res_body=json.dumps({"access_token": token})),
        _req(headers={"authorization": f"Bearer {token}"}),
    ]
    found = _pass1_jwt_bearer(reqs)
    assert any(cv.detection_pass == "pass1_bearer" and cv.used_in_requests == [1] for cv in found)


def test_two_jwts_in_one_response_are_both_found():
    reqs = [_req(res_body=json.dumps({"access_token": JWT_A, "id_token": JWT_B}))]
    values = {cv.extract_value for cv in _pass1_jwt_bearer(reqs) if cv.detection_pass == "pass1_jwt"}
    assert {JWT_A, JWT_B} <= values


def test_numeric_id_in_json_is_correlated():
    reqs = [
        _req(res_body=json.dumps({"orderId": 9876543})),
        _req(method="POST", body=json.dumps({"order": 9876543})),
    ]
    cvs = run_correlation(reqs, use_ai=False)
    assert any(cv.extract_value == "9876543" and cv.used_in_requests == [1] for cv in cvs)


def test_html_hidden_inputs_and_meta_tags():
    html = ('<html><meta name="csrf-token" content="meta-token-123456">'
            '<form><input type="hidden" name="__VIEWSTATE" value="/wEPDw+=="></form></html>')
    found = _pass4_html([_req(res_body=html, mime="text/html")])
    sels = {(cv.extractor_config["selector"], cv.extractor_config["attr"]) for cv in found}
    assert ("input[name=__VIEWSTATE]", "value") in sels
    assert ("meta[name=csrf-token]", "content") in sels


def test_redirect_code_is_extracted_from_location():
    reqs = [_req(status=302, res_headers={"Location": "https://app/cb?code=AbC123xyz&state=q"})]
    found = _pass3_redirect_params(reqs)
    assert any(cv.extract_value == "AbC123xyz" and cv.extractor == "header_regex" for cv in found)


def test_cookie_to_header_double_submit():
    raw = {"response": {"cookies": [{"name": "XSRF-TOKEN", "value": "abc%2Fdef123456"}]}}
    reqs = [_req(raw=raw), _req(headers={"X-XSRF-TOKEN": "abc/def123456"})]
    found = _pass3_cookie_to_header(reqs)
    assert found and found[0].extractor_config == {"cookie": "XSRF-TOKEN", "url_decode": True}


def test_client_timestamp_becomes_function():
    started = "2026-10-08T10:00:00.000Z"
    ts = "1791453600123"  # within the recording day
    reqs = [_req(url=f"https://app/api?_={ts}", started=started)]
    found = _pass_client_generated(reqs, set())
    assert found and found[0].extractor == "function" and found[0].extractor_config["function"] == "time"


def test_rule_packs_load_and_validate():
    packs = load_packs()
    assert {"aspnet", "spring", "oauth", "saml"} <= {p["id"] for p in packs}
    assert validate_pack({"id": "Bad Id", "rules": []})
    html = '<input type="hidden" name="__EVENTVALIDATION" value="evt-123456">'
    found = _pass_rules([_req(res_body=html, mime="text/html")])
    assert any(cv.var_name == "C_EVENTVALIDATION" for cv in found)


def test_confidence_rewards_confirmed_values():
    base = CorrelatedValue("C_X", 0, "body", "x", "abcdef123456", [1], detection_pass="pass2_json")
    confirmed = CorrelatedValue("C_X", 0, "body", "x", "abcdef123456", [1],
                                detection_pass="pass2_json,confirmed_dynamic")
    ai = CorrelatedValue("C_X", 0, "body", "x", "abcdef123456", [1], detection_pass="pass6_groq")
    assert score(confirmed) > score(base)
    assert score(ai) > 50


def test_cv_dict_roundtrip_keeps_extractor():
    cv = CorrelatedValue("C_X", 2, "body", "k", "v123456", [3], "pass2_json", "r",
                         extractor="json", extractor_config={"path": "$.k"})
    back = cv_from_dict(cv_to_dict(cv))
    assert (back.extractor, back.extractor_config, back.used_in_requests) == ("json", {"path": "$.k"}, [3])


# ---------------------------------------------------------------------------
# Generator fixes
# ---------------------------------------------------------------------------

def test_apply_subs_is_boundary_aware_and_longest_first():
    out = _apply_subs("id=1234&ref=12345&x=91234", [("1234", "C_A"), ("12345", "C_B")])
    assert out == "id=${C_A}&ref=${C_B}&x=91234"


def test_apply_subs_wraps_url_encoded_occurrences():
    out = _apply_subs("vs=%2FwEPDw%2B%3D%3D", [("/wEPDw+==", "C_VS")])
    assert out == "vs=${__urlencode(${C_VS})}"


def test_apply_subs_never_rewrites_inserted_text():
    out = _apply_subs("a=C_TOKEN_VALUE&b=C_TOKEN", [("C_TOKEN_VALUE", "X"), ("C_TOKEN", "Y")])
    assert out == "a=${X}&b=${Y}"


def _gen(tmp_path, reqs, cvs=(), **kw):
    out = tmp_path / "t.jmx"
    generate_jmx(reqs, list(cvs), output_path=out, **kw)
    return ET.parse(out).getroot()


def test_query_params_are_not_duplicated(tmp_path):
    r = _req(url="https://app.example.com/search?q=shoes&page=2")
    r.query_params = [{"name": "q", "value": "shoes"}, {"name": "page", "value": "2"}]
    root = _gen(tmp_path, [r])
    sampler = root.find(".//HTTPSamplerProxy")
    assert sampler.find("stringProp[@name='HTTPSampler.path']").text == "/search?q=shoes&page=2"
    assert sampler.findall(".//elementProp[@elementType='HTTPArgument']") == []


def test_recorded_redirect_target_is_not_followed_twice(tmp_path):
    reqs = [
        _req(method="POST", url="https://app/login", status=302, res_headers={"Location": "/home"}),
        _req(url="https://app/home"),
    ]
    root = _gen(tmp_path, reqs)
    follows = [s.find("boolProp[@name='HTTPSampler.follow_redirects']").text for s in root.iter("HTTPSamplerProxy")]
    assert follows == ["false", "true"]


def test_pseudo_headers_are_dropped_and_static_auth_kept(tmp_path):
    reqs = [_req(headers={":authority": "app", "Authorization": "Basic dXNlcjpwYXNz", "X-Api": "k"})]
    root = _gen(tmp_path, reqs)
    names = [h.find("stringProp[@name='Header.name']").text for h in root.iter("elementProp")
             if h.get("elementType") == "Header"]
    assert ":authority" not in names and "Authorization" in names


def test_payload_params_keep_distinct_values(tmp_path):
    reqs = [
        _req(method="POST", body=json.dumps({"quantity": 10, "name": "Ann"})),
        _req(method="POST", body=json.dumps({"quantity": 50, "name": "Ann"})),
    ]
    root = _gen(tmp_path, reqs)
    bodies = [a.find("stringProp[@name='Argument.value']").text
              for a in root.iter("elementProp") if a.get("elementType") == "HTTPArgument"]
    assert bodies[0] == '{"quantity": ${P_QUANTITY}, "name": "${P_NAME}"}'
    assert bodies[1] == '{"quantity": ${P_QUANTITY_2}, "name": "${P_NAME}"}'


def test_new_extractor_types_render(tmp_path):
    reqs = [_req(res_body="<html></html>", mime="text/html"), _req(url="https://app/x?t=tok-123456")]
    cvs = [
        CorrelatedValue("C_CSS", 0, "body", "s", "tok-123456", [1], "pass_html",
                        extractor="css", extractor_config={"selector": "input[name=t]", "attr": "value"}),
        CorrelatedValue("C_TS", -1, "client", "q", "1791453600123", [1], "pass_client",
                        extractor="function", extractor_config={"function": "time"}),
    ]
    root = _gen(tmp_path, reqs, cvs)
    assert root.find(".//HtmlExtractor/stringProp[@name='HtmlExtractor.expr']").text == "input[name=t]"


def test_multi_domain_requests_keep_their_host(tmp_path):
    reqs = [_req(url="https://api.example.com/a"), _req(url="https://auth.example.com/token")]
    root = _gen(tmp_path, reqs, base_url="api.example.com", base_urls=["api.example.com", "auth.example.com"])
    domains = [s.find("stringProp[@name='HTTPSampler.domain']").text for s in root.iter("HTTPSamplerProxy")]
    assert domains == [None, "${BASE_URL_2}"]
