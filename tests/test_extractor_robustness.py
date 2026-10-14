"""
Regressions from a JPetStore replay: a page name ("Order.action") was treated as a
dynamic value, its boundary extractor relied on "the 12th link", and on replay
it captured a chunk of HTML — JMeter then failed with URISyntaxException.
"""

from projectstack.correlator.engine import CorrelatedValue, _is_route_segment, run_correlation
from projectstack.correlator.matching import apply_extractor, correlatable_header_items, infer_extractor
from projectstack.parser.har_parser import HarRequest
from projectstack.runner.replay import ReplaySample, _plausible, analyze_replay

CART_PAGE = (
    '<div id="Menu"><a href="/actions/Cart.action;jsessionid=AB12">c</a>\n'
    '<a href="/actions/Account.action?signoff=">Sign Out</a>\n'
    '<a href="/actions/Catalog.action">Return to Main Menu</a>\n'
    '<a class="Button" href="/actions/Order.action?newOrderForm=">Proceed to Checkout</a></div>'
)
# Same page on replay: a different cart adds a form and a table with a "?" in it
CART_PAGE_REPLAY = (
    '<form action="/actions/Cart.action" method="post">\n\t<table>\n\t\t<tr>\n'
    '\t\t\t<th><b>Item ID</b></th><th><b>In Stock?</b></th></tr></table></form>\n' + CART_PAGE
)


def _req(url, body="", method="GET", status=200, mime="text/html"):
    return HarRequest(method=method, url=url, headers={}, query_params=[], body=None, body_mime_type=None,
                      status_code=status, response_headers={}, response_body=body,
                      response_mime_type=mime, cookies=[])


def test_page_names_are_not_dynamic():
    for seg in ("Order.action", "Catalog.action", "checkout", "product-list", "index.jsp"):
        assert _is_route_segment(seg), seg
    for seg in ("FI-SW-01", "12345", "a1b2c3d4", "9f8e7d6c-1234-4abc-8def-001122334455"):
        assert not _is_route_segment(seg), seg

    reqs = [
        _req("https://shop.example/actions/Cart.action?addItemToCart=&workingItemId=EST-1", CART_PAGE),
        _req("https://shop.example/actions/Order.action?newOrderForm="),
        _req("https://shop.example/actions/Catalog.action"),
    ]
    assert not any(cv.extract_value.endswith(".action") for cv in run_correlation(reqs, use_ai=False))


def test_token_extractor_survives_a_changed_page():
    spec = infer_extractor(_req("https://shop.example/x", CART_PAGE), "Order.action")
    assert spec["extractor"] == "regex"
    assert apply_extractor("regex", spec["config"], CART_PAGE_REPLAY, {}) == "Order.action"


def test_browser_headers_are_not_correlation_candidates():
    headers = {"sec-ch-ua-platform": '"Linux"', "sec-fetch-site": "same-origin", "X-Request-Id": "abc123def456"}
    assert [k for k, _ in correlatable_header_items(headers)] == ["X-Request-Id"]


def test_replay_treats_extracted_markup_as_a_broken_extractor():
    assert not _plausible("Order.action", 'Cart.action">\n\t<table>')
    assert _plausible("EST-1", "EST-4")

    reqs = [_req("https://shop.example/actions/Cart.action", CART_PAGE), _req("https://shop.example/actions/Order.action?newOrderForm=")]
    fragile = CorrelatedValue(
        var_name="C_PATH_ORDER", source_request_index=0, extract_from="body", extract_key="actions/…?",
        extract_value="Order.action", used_in_requests=[1], detection_pass="pass_request_scan",
        extractor="boundary", extractor_config={"lb": "actions/", "rb": "?", "match_no": 3},
    )
    # The old boundary extractor returns HTML from the changed page
    assert not _plausible("Order.action", apply_extractor("boundary", fragile.extractor_config, CART_PAGE_REPLAY, {}))
    samples = {
        0: ReplaySample(idx=0, label="[0]", status="200", success=True, response_body=CART_PAGE_REPLAY),
        1: ReplaySample(idx=1, label="[1]", status="Non HTTP response code: java.net.URISyntaxException", success=False,
                        message="Illegal character in path"),
    }
    garbage = apply_extractor("boundary", fragile.extractor_config, CART_PAGE_REPLAY, {})
    out = analyze_replay(reqs, [fragile], samples, {0: {"C_PATH_ORDER": garbage}}, [0, 1])

    assert "C_PATH_ORDER" not in out["verified"]          # garbage is not "confirmed dynamic"
    assert [r["var_name"] for r in out["repaired"]] == ["C_PATH_ORDER"]
    fixed = out["correlations"][0]
    assert apply_extractor(fixed.extractor, fixed.extractor_config, CART_PAGE_REPLAY, {}) == "Order.action"
    hints = next(r for r in out["results"] if r["index"] == 1)["hints"]
    assert any("matched the wrong text" in h for h in hints)
    assert not any("sec-ch-ua" in h for h in hints)
