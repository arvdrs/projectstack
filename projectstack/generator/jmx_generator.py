"""
JMX file generator.

Takes HarRequest and CorrelatedValue objects and produces a valid
jMeter 5.6 JMX file with:
  - User Defined Variables (all C_* correlated variables)
  - HTTP Request Defaults
  - Cookie Manager
  - JSR223 PreProcessor for automatic Authorization header injection
  - Standard, Stepping, Concurrency or Ultimate thread group (the last three
    need the JMeter Plugins "Custom Thread Groups" jar)
  - Optional TransactionControllers (pass transactions from transaction_grouper)
  - One HTTP Sampler per request with Header Manager
  - Native extractors chosen per correlation: JSON (JSONPath, incl. list
    filters), CSS/JQuery (hidden inputs, meta tags), Boundary, Regex (body and
    headers) and small JSR223 scripts for JWT claims and cookie → header tokens
  - ${C_VAR} substitution in headers, bodies, query strings and URL paths —
    boundary-aware, and encoding-aware (URL-encoded uses get ${__urlencode()})
  - JMeter functions for client-generated values (__time, __UUID)
"""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.dom import minidom

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue, JWT_RE
from projectstack.correlator.matching import (
    _boundary_regex, encoded_variants, header_get, jregex_escape,
)


# Headers that jMeter manages automatically — exclude from Header Manager
SKIP_HEADERS = {
    "content-length", "host", "connection", "accept-encoding",
    "accept-language", "sec-fetch-dest", "sec-fetch-mode",
    "sec-fetch-site", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests", "cookie",
}


# ---------------------------------------------------------------------------
# Data model for transactions
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    """A logical business transaction grouping one or more requests."""
    name: str                               # e.g. TC_01_Login
    request_indices: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# XML helper functions
# ---------------------------------------------------------------------------

def _java_hash(text: str) -> int:
    """Java String.hashCode — JMeter's naming scheme for collection entries."""
    h = 0
    for ch in text:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h - 0x100000000 if h & 0x80000000 else h


def _sp(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, "stringProp", name=name)
    el.text = value
    return el


def _bp(parent: ET.Element, name: str, value: bool) -> ET.Element:
    el = ET.SubElement(parent, "boolProp", name=name)
    el.text = "true" if value else "false"
    return el


_SENTINEL_OPEN, _SENTINEL_CLOSE, _SENTINEL_DIGIT0 = "\ue000", "\ue001", 0xE010


def _apply_subs(text: str, subs: list[tuple[str, str]]) -> str:
    """
    Replace recorded dynamic values with JMeter expressions.

    subs is a list of (recorded_value, replacement). A replacement that is a bare
    variable name becomes ${NAME}; anything starting with "${" is used verbatim
    (JMeter function calls). Longest values are replaced first, matches respect
    alphanumeric boundaries, and URL-encoded occurrences are wrapped in
    ${__urlencode()} so the runtime value is encoded the same way. Replacements
    are inserted through sentinels so a later, shorter value can never match
    inside text that was already substituted.
    """
    if not text or not subs:
        return text
    forms: list[tuple[str, str]] = []
    seen: set[str] = set()
    for actual, target in subs:
        if not actual or len(actual) < 3:
            continue
        expr = target if target.startswith("${") else f"${{{target}}}"
        for form, enc in encoded_variants(actual):
            if form in seen:
                continue
            seen.add(form)
            forms.append((form, f"${{__urlencode({expr})}}" if enc == "url" else expr))
    forms.sort(key=lambda f: -len(f[0]))

    inserted: list[str] = []

    def _token(expr: str) -> str:
        inserted.append(expr)
        digits = "".join(chr(_SENTINEL_DIGIT0 + int(d)) for d in str(len(inserted) - 1))
        return _SENTINEL_OPEN + digits + _SENTINEL_CLOSE

    for form, expr in forms:
        if form in text:
            text = _boundary_regex(form).sub(lambda _m, e=expr: _token(e), text)
    if not inserted:
        return text
    return re.sub(
        _SENTINEL_OPEN + "([\ue010-\ue019]+)" + _SENTINEL_CLOSE,
        lambda m: inserted[int("".join(str(ord(c) - _SENTINEL_DIGIT0) for c in m.group(1)))],
        text,
    )


def _field_sub(body: str, key: str, raw_val, var_name: str) -> str:
    """Replace one top-level JSON field value with ${var_name}, keeping JSON valid."""
    k = re.escape(json.dumps(key))
    if isinstance(raw_val, str):
        pat = re.compile(f"({k}\\s*:\\s*)" + re.escape(json.dumps(raw_val)))
        return pat.sub(lambda m: m.group(1) + f'"${{{var_name}}}"', body, count=1)
    pat = re.compile(f"({k}\\s*:\\s*)" + re.escape(json.dumps(raw_val)) + r"(?=\s*[,}\]])")
    return pat.sub(lambda m: m.group(1) + f"${{{var_name}}}", body, count=1)


def _sampler_label(tc_num: int, seq: int, req: HarRequest) -> str:
    """Generate sampler name: {tc_num:02d}_{seq:02d}_{ActionName}."""
    path = urlparse(req.url).path.rstrip("/")
    segment = path.split("/")[-1] if path else "Request"
    # Capitalise first letter of each word split by non-alphanum
    parts = re.sub(r"[^a-zA-Z0-9]", " ", segment).split()
    action = "".join(p.capitalize() for p in parts) or "Request"
    return f"{tc_num:02d}_{seq:02d}_{action}"


# ---------------------------------------------------------------------------
# Groovy extractor scripts
# ---------------------------------------------------------------------------

def _groovy_json_extractor(var_name: str, key: str) -> str:
    """
    Groovy script to extract a value from a JSON response body.
    Handles both object keys (.get("field")) and array indices ([0]).
    """
    parts = [p for p in re.split(r"[\.\[\]]+", key) if p]
    nav = "def val = json"
    for part in parts:
        if part.isdigit():
            nav += f"[{part}]"           # list index: json[0]
        else:
            nav += f'?.get("{part}")'    # map key:   json?.get("field")
    return (
        "import groovy.json.JsonSlurper\n"
        "try {\n"
        "    def json = new JsonSlurper().parseText(prev.getResponseDataAsString())\n"
        f"    {nav}\n"
        f'    if (val != null) {{ vars.put("{var_name}", val.toString()) }}\n'
        "} catch(Exception e) { /* non-JSON response, skip */ }\n"
    )


def _groovy_jwt_claim_extractor(var_name: str, claim_name: str) -> str:
    """
    Groovy script that finds every JWT in the response (body and headers),
    decodes the payload and stores the first value of the given claim.
    Self-contained: it does not depend on another extractor having run first.
    """
    return (
        "import groovy.json.JsonSlurper\n"
        "import java.util.Base64\n"
        "def text = prev.getResponseDataAsString() + '\\n' + prev.getResponseHeaders()\n"
        "def m = text =~ /eyJ[A-Za-z0-9_-]+\\.([A-Za-z0-9_-]+)\\.[A-Za-z0-9_-]+/\n"
        "while (m.find()) {\n"
        "    try {\n"
        "        def seg = m.group(1)\n"
        '        while (seg.length() % 4 != 0) seg += "="\n'
        '        def claims = new JsonSlurper().parseText(new String(Base64.urlDecoder.decode(seg), "UTF-8"))\n'
        f'        def claimVal = claims instanceof Map ? claims.get("{claim_name}") : null\n'
        f'        if (claimVal != null) {{ vars.put("{var_name}", claimVal.toString()); break }}\n'
        "    } catch (Exception e) { /* not a decodable JWT — try the next one */ }\n"
        "}\n"
    )


def _groovy_cookie_header_extractor(var_name: str, cookie_name: str, url_decode: bool) -> str:
    """Groovy script: read a cookie set by this response (Set-Cookie or Cookie Manager)."""
    name = cookie_name.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "import java.util.regex.Pattern\n"
        f'def name = "{name}"\n'
        "def val = null\n"
        "def results = [prev] + (prev.getSubResults() as List)\n"
        "for (r in results) {\n"
        "    def m = Pattern.compile('(?im)^set-cookie:\\\\s*' + Pattern.quote(name) + '=([^;\\\\r\\\\n]*)').matcher(r.getResponseHeaders())\n"
        "    while (m.find()) { val = m.group(1) }\n"
        "}\n"
        "if (val == null && sampler.getCookieManager() != null) {\n"
        "    def cm = sampler.getCookieManager()\n"
        "    for (int i = 0; i < cm.getCookieCount(); i++) { if (cm.get(i).getName() == name) val = cm.get(i).getValue() }\n"
        "}\n"
        + ("if (val != null && val.contains('%')) val = URLDecoder.decode(val, 'UTF-8')\n" if url_decode else "")
        + f'if (val != null) vars.put("{var_name}", val)\n'
    )


def _groovy_var_log(req_idx: int, names: list[str]) -> str:
    """Append {idx, vars} as a JSON line to the file named by the pstack.varlog property."""
    quoted = ", ".join('"' + n + '"' for n in names)
    return (
        "def f = props.get('pstack.varlog')\n"
        "if (f) {\n"
        f"    def names = [{quoted}]\n"
        f"    def out = [idx: {req_idx}, vars: names.collectEntries {{ [(it): vars.get(it)] }}]\n"
        "    new File(f).append(groovy.json.JsonOutput.toJson(out) + '\\n', 'UTF-8')\n"
        "}\n"
    )


def _groovy_url_decode(var_name: str) -> str:
    return (
        f'def v = vars.get("{var_name}")\n'
        f"if (v != null && v.contains('%')) vars.put(\"{var_name}\", URLDecoder.decode(v, 'UTF-8'))\n"
    )


def _groovy_jwt_extractor(var_name: str) -> str:
    """Groovy script to extract a JWT token from a response body."""
    return (
        "def response = prev.getResponseDataAsString()\n"
        "def matcher = response =~ "
        "/eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/\n"
        f'if (matcher.find()) {{ vars.put("{var_name}", matcher.group()) }}\n'
    )


def _groovy_cookie_extractor(var_name: str, cookie_name: str) -> str:
    """Groovy script to extract a cookie value (via jMeter cookie variable)."""
    return (
        f'def val = vars.get("COOKIE_{cookie_name}")\n'
        f'if (val != null) {{ vars.put("{var_name}", val) }}\n'
    )


def _groovy_uuid_extractor(var_name: str, uuid_prefix: str) -> str:
    """Groovy script to extract a UUID from a response body by its prefix."""
    prefix = re.escape(uuid_prefix[:8])  # type: ignore[index]
    return (
        "def response = prev.getResponseDataAsString()\n"
        f'def matcher = response =~ /({prefix}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}})/\n'
        f'if (matcher.find()) {{ vars.put("{var_name}", matcher.group(1)) }}\n'
    )


# ---------------------------------------------------------------------------
# Extractor element builders
# ---------------------------------------------------------------------------

def _key_to_jsonpath(key: str) -> str:
    if key.startswith("$"):
        return key
    return f"$.{key}" if not key.startswith("[") else f"${key}"


def _json_extractor(var_name: str, path: str) -> ET.Element:
    jp = ET.Element("JSONPostProcessor", guiclass="JSONPostProcessorGui",
                    testclass="JSONPostProcessor", testname=f"Extract {var_name}", enabled="true")
    _sp(jp, "JSONPostProcessor.referenceNames", var_name)
    _sp(jp, "JSONPostProcessor.jsonPathExprs", path)
    _sp(jp, "JSONPostProcessor.match_numbers", "1")
    _sp(jp, "JSONPostProcessor.defaultValues", "NOT_FOUND")
    return jp


def _css_extractor(var_name: str, selector: str, attr: str) -> ET.Element:
    el = ET.Element("HtmlExtractor", guiclass="HtmlExtractorGui", testclass="HtmlExtractor",
                    testname=f"Extract {var_name}", enabled="true")
    _sp(el, "HtmlExtractor.refname", var_name)
    _sp(el, "HtmlExtractor.expr", selector)
    _sp(el, "HtmlExtractor.attribute", attr)
    _sp(el, "HtmlExtractor.default", "NOT_FOUND")
    _bp(el, "HtmlExtractor.default_empty_value", False)
    _sp(el, "HtmlExtractor.match_number", "1")
    _sp(el, "HtmlExtractor.extractor_impl", "")
    return el


def _boundary_extractor(var_name: str, lb: str, rb: str, match_no: int = 1) -> ET.Element:
    el = ET.Element("BoundaryExtractor", guiclass="BoundaryExtractorGui", testclass="BoundaryExtractor",
                    testname=f"Extract {var_name}", enabled="true")
    _sp(el, "BoundaryExtractor.useHeaders", "false")
    _sp(el, "BoundaryExtractor.refname", var_name)
    _sp(el, "BoundaryExtractor.lboundary", lb)
    _sp(el, "BoundaryExtractor.rboundary", rb)
    _sp(el, "BoundaryExtractor.default", "NOT_FOUND")
    _bp(el, "BoundaryExtractor.default_empty_value", False)
    _sp(el, "BoundaryExtractor.match_number", str(match_no))
    return el


def _regex_extractor(var_name: str, regex: str, use_headers: str = "false",
                     match_no: int = 1, scope_all: bool = False) -> ET.Element:
    rx = ET.Element("RegexExtractor", guiclass="RegexExtractorGui", testclass="RegexExtractor",
                    testname=f"Extract {var_name}", enabled="true")
    _sp(rx, "RegexExtractor.useHeaders", use_headers)
    _sp(rx, "RegexExtractor.refname", var_name)
    _sp(rx, "RegexExtractor.regex", regex)
    _sp(rx, "RegexExtractor.template", "$1$")
    _sp(rx, "RegexExtractor.default", "NOT_FOUND")
    _sp(rx, "RegexExtractor.match_no", str(match_no))
    if scope_all:
        # Also search redirect sub-samples (a followed redirect's headers live there)
        _sp(rx, "Sample.scope", "all")
    return rx


def _jsr223_post(name: str, script: str) -> ET.Element:
    pp = ET.Element("JSR223PostProcessor", guiclass="TestBeanGUI", testclass="JSR223PostProcessor",
                    testname=name, enabled="true")
    _sp(pp, "scriptLanguage", "groovy")
    _sp(pp, "parameters", "")
    _sp(pp, "filename", "")
    _sp(pp, "cacheKey", "true")
    script_el = ET.SubElement(pp, "stringProp", name="script")
    script_el.text = script
    return pp


def _groovy_correlation_check(var_names: list[str]) -> str:
    names = ", ".join(f"'{v}'" for v in var_names)
    return (
        "// ProjectStack correlation check: fail this sample when it was sent with an extractor's\n"
        "// default value (NOT_FOUND), so the results show which correlation broke.\n"
        "// Delete this assertion (or generate with correlation markers off) to opt out.\n"
        "def sent = (prev.getSamplerData() ?: '') + '\\n' + (prev.getRequestHeaders() ?: '')\n"
        "if (sent.contains('NOT_FOUND')) {\n"
        f"    def missing = [{names}].findAll {{ (vars.get(it) ?: 'NOT_FOUND') == 'NOT_FOUND' }}\n"
        "    AssertionResult.setFailure(true)\n"
        "    AssertionResult.setFailureMessage('PS_CORRELATION_MISSING: ' + (missing ? missing.join(',') : 'NOT_FOUND'))\n"
        "}\n"
    )


def _jsr223_assertion(name: str, script: str) -> ET.Element:
    el = ET.Element("JSR223Assertion", guiclass="TestBeanGUI", testclass="JSR223Assertion",
                    testname=name, enabled="true")
    _sp(el, "scriptLanguage", "groovy")
    _sp(el, "parameters", "")
    _sp(el, "filename", "")
    _sp(el, "cacheKey", "true")
    script_el = ET.SubElement(el, "stringProp", name="script")
    script_el.text = script
    return el


def _function_expr(cv: CorrelatedValue) -> str:
    """JMeter expression that generates a client-side value and stores it in cv.var_name."""
    fn = (getattr(cv, "extractor_config", None) or {}).get("function")
    if fn == "time":
        return f"${{__time(,{cv.var_name})}}"
    if fn == "time_s":
        return f"${{__time(/1000,{cv.var_name})}}"
    return f"${{__groovy(UUID.randomUUID().toString(),{cv.var_name})}}"


# ---------------------------------------------------------------------------
# Main generator class
# ---------------------------------------------------------------------------

class JmxGenerator:
    def __init__(
        self,
        requests: list[HarRequest],
        correlated_values: list[CorrelatedValue],
        test_name: str = "ProjectStack Test",
        threads: int = 1,
        ramp_up: int = 1,
        loops: int = 1,
        transactions: list[Transaction] | None = None,
        base_url: str = "",
        base_urls: list[str] | None = None,
        test_vars: list[dict] | None = None,
        test_data_files: list[dict] | None = None,
        include_view_tree: bool = True,
        grafana_url: str = "",
        no_transactions: bool = True,
        include_assertions: bool = True,
        parameterize_payload: bool = True,
        selected_indices: list[int] | None = None,
        custom_assertions: dict[int, list] | None = None,
        include_think_times: bool = False,
        duration_seconds: int = 0,
        label_with_index: bool = False,
        thread_group: dict | None = None,
        correlation_markers: bool = True,
    ):
        # Fail samples sent with an extractor's NOT_FOUND default ("PS_CORRELATION_MISSING: <var>"),
        # which the results health check maps back to the extractor
        self.correlation_markers = correlation_markers
        # Thread group type and its parameters ({"type": "stepping", ...}); None = standard
        self.thread_group: dict = dict(thread_group or {"type": "standard"})
        self.include_think_times = include_think_times
        # Replay mode: prefix sampler names with "[idx] " so results map back to HAR entries
        self.label_with_index = label_with_index
        self.test_vars: list[dict] = test_vars or []
        self.test_data_files: list[dict] = test_data_files or []
        # loops = -1 means infinite; duration_seconds activates the scheduler
        self.duration_seconds = duration_seconds
        self.requests = requests
        self.correlated_values = correlated_values
        self.test_name = test_name
        self.threads = threads
        self.ramp_up = ramp_up
        self.loops = loops
        self.base_url = base_url
        # Normalize extra base URLs: deduplicate and strip the primary so we don't double-emit
        _extra = [u for u in (base_urls or []) if u and u != base_url]
        self.extra_base_urls: list[str] = list(dict.fromkeys(_extra))
        self.include_view_tree = include_view_tree
        self.grafana_url = grafana_url
        self.no_transactions = no_transactions
        self.include_assertions = include_assertions
        # None = include all; list = only these original request indices (kept as list for Pyright compat)
        self._selected_list: list[int] | None = list(selected_indices) if selected_indices is not None else None
        # custom_assertions: {orig_req_idx -> [{type, value, enabled}]}
        self._custom_assertions: dict[int, list] = custom_assertions or {}

        # Parse base_url into components for HTTP Defaults
        if base_url:
            _p = urlparse(base_url if "://" in base_url else f"https://{base_url}")
            self._base_protocol = _p.scheme or "https"
            self._base_host = _p.hostname or ""
            self._base_port = str(_p.port) if _p.port else ""
        else:
            self._base_protocol = ""
            self._base_host = ""
            self._base_port = ""
        # Extra selected domains map to ${BASE_URL_2}, ${BASE_URL_3} … by hostname
        self._extra_hosts: dict[str, str] = {}
        for n, extra in enumerate(self.extra_base_urls, start=2):
            _e = urlparse(extra if "://" in extra else f"https://{extra}")
            if _e.hostname:
                self._extra_hosts[_e.hostname] = f"BASE_URL_{n}"

        # Transaction structure: if no_transactions, skip TC wrappers
        if no_transactions:
            self.transactions = []  # unused when no_transactions=True
        elif transactions:
            self.transactions = transactions
        else:
            self.transactions = [
                Transaction(name="TC_01_Flow", request_indices=list(range(len(requests))))
            ]

        # Order in which samplers are emitted (request_order arrives as an ordered selection)
        self._order: list[int] = (
            [i for i in self._selected_list if 0 <= i < len(requests)]
            if self._selected_list is not None else list(range(len(requests)))
        )
        position = {idx: pos for pos, idx in enumerate(self._order)}

        # Build per-request substitution map: {req_idx: [(actual_value, var_name | ${expr})]}
        # Skip cookie correlations — Cookie Manager handles all cookie state.
        self._subs: dict[int, list[tuple[str, str]]] = {}
        for cv in correlated_values:
            if cv.extract_from == "cookie" or cv.detection_pass == "pass3_cookie":
                continue
            if getattr(cv, "extractor", "") == "function":
                # Client-generated value: the first request that sends it generates it and
                # stores it; later requests reuse the stored variable.
                uses = sorted(cv.used_in_requests, key=lambda i: position.get(i, len(position) + i))
                for n, req_idx in enumerate(uses):
                    expr = _function_expr(cv) if n == 0 else cv.var_name
                    self._subs.setdefault(req_idx, []).append((cv.extract_value, expr))
                continue
            for req_idx in cv.used_in_requests:
                self._subs.setdefault(req_idx, []).append(
                    (cv.extract_value, cv.var_name)
                )

        # User-defined test variable substitutions: apply ${P_VARNAME} across all requests
        for tv in self.test_vars:
            tv_name = (tv.get("name") or "").strip()
            tv_val = (tv.get("value") or "").strip()
            if tv_name and tv_val and len(tv_val) >= 3:
                for req_idx in range(len(requests)):
                    existing = {v for _, v in self._subs.get(req_idx, [])}
                    if tv_name not in existing:
                        self._subs.setdefault(req_idx, []).append((tv_val, tv_name))

        self._parameterize_payload = parameterize_payload

        # P_* payload parameters: {var_name -> representative_value}
        self._payload_params: dict[str, str] = {}
        # Field-targeted P_* substitutions: {req_idx: [(json_key, raw_value, var_name)]}
        self._field_subs: dict[int, list[tuple[str, object, str]]] = {}
        if parameterize_payload:
            self._build_payload_subs()

        # Pre-compute think times (ms gap between consecutive request start times)
        self._think_times: dict[int, int] = {}
        if include_think_times:
            self._think_times = self._compute_think_times(requests)

        # Redirects whose target was recorded as its own entry must not be followed
        # automatically — JMeter would request the target twice (burning one-time
        # values such as OAuth codes) and the Location header would be hidden in a
        # sub-sample where extractors cannot see it.
        self._no_follow: set[int] = set()
        for pos, idx in enumerate(self._order):
            req = requests[idx]
            if not (300 <= (req.status_code or 0) < 400):
                continue
            loc = header_get(req.response_headers or {}, "Location")
            if not loc:
                continue
            target = urljoin(req.url, loc).split("#")[0]
            for nxt in self._order[pos + 1:pos + 4]:
                if requests[nxt].url.split("#")[0] == target:
                    self._no_follow.add(idx)
                    break

    def generate(self, output_path: str | Path) -> None:
        """Build and write the JMX file to output_path."""
        root = ET.Element(
            "jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3"
        )
        top_tree = ET.SubElement(root, "hashTree")

        top_tree.append(self._build_test_plan())
        plan_tree = ET.SubElement(top_tree, "hashTree")

        # User Defined Variables (all C_* variables with empty defaults)
        plan_tree.append(self._build_user_defined_variables())
        ET.SubElement(plan_tree, "hashTree")

        # HTTP Request Defaults
        plan_tree.append(self._build_http_defaults())
        ET.SubElement(plan_tree, "hashTree")

        # Cookie Manager
        plan_tree.append(self._build_cookie_manager())
        ET.SubElement(plan_tree, "hashTree")

        # CSV Data Set configs (from Test Data panel)
        for csv_el in self._build_csv_datasets():
            plan_tree.append(csv_el)
            ET.SubElement(plan_tree, "hashTree")

        # Thread Group
        plan_tree.append(self._build_thread_group())
        tg_tree = ET.SubElement(plan_tree, "hashTree")

        if self.no_transactions:
            # Flat sampler list — no TransactionController wrappers
            for req_idx in self._order:
                req = self.requests[req_idx]
                label = f"{req.method} {self._label_path(req_idx, req)}"
                sampler_el = self._build_sampler(req_idx, req, label)
                tg_tree.append(sampler_el)
                sampler_tree = ET.SubElement(tg_tree, "hashTree")
                header_el = self._build_header_manager(req_idx, req)
                sampler_tree.append(header_el)
                ET.SubElement(sampler_tree, "hashTree")
                self._append_assertions(sampler_tree, req_idx, req)
                self._append_correlation_check(sampler_tree, sampler_el, header_el)
                self._append_extractors(sampler_tree, req_idx)
                # Think time after this sampler
                if self.include_think_times and req_idx in self._think_times:
                    delay = self._think_times[req_idx]
                    if delay > 0:
                        tg_tree.append(self._build_constant_timer(delay))
                        ET.SubElement(tg_tree, "hashTree")
        else:
            # TransactionControllers with their samplers
            for tc in self.transactions:
                tg_tree.append(self._build_transaction_controller(tc.name))
                tc_tree = ET.SubElement(tg_tree, "hashTree")

                tc_num = int(tc.name.split("_")[1]) if "_" in tc.name else 1
                for seq, req_idx in enumerate(tc.request_indices, 1):
                    if req_idx >= len(self.requests):
                        continue
                    if not self._is_selected(req_idx):
                        continue
                    req = self.requests[req_idx]
                    label = _sampler_label(tc_num, seq, req)

                    sampler_el = self._build_sampler(req_idx, req, label)
                    tc_tree.append(sampler_el)
                    sampler_tree = ET.SubElement(tc_tree, "hashTree")

                    header_el = self._build_header_manager(req_idx, req)
                    sampler_tree.append(header_el)
                    ET.SubElement(sampler_tree, "hashTree")
                    self._append_assertions(sampler_tree, req_idx, req)
                    self._append_correlation_check(sampler_tree, sampler_el, header_el)
                    self._append_extractors(sampler_tree, req_idx)
                    # Think time after this sampler
                    if self.include_think_times and req_idx in self._think_times:
                        delay = self._think_times[req_idx]
                        if delay > 0:
                            tc_tree.append(self._build_constant_timer(delay))
                            ET.SubElement(tc_tree, "hashTree")

        # View Results Tree listener (disabled by default — user enables in JMeter GUI)
        if self.include_view_tree:
            plan_tree.append(self._build_view_results_tree())
            ET.SubElement(plan_tree, "hashTree")

        # Grafana InfluxDB Backend Listener
        if self.grafana_url:
            plan_tree.append(self._build_grafana_listener())
            ET.SubElement(plan_tree, "hashTree")

        self._write(root, output_path)

    # ------------------------------------------------------------------
    # Test Plan
    # ------------------------------------------------------------------
    # Think time helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dt(dt_str: str) -> datetime | None:
        """Parse ISO-8601 started_datetime from HAR (handles Z suffix)."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _compute_think_times(self, requests: list[HarRequest]) -> dict[int, int]:
        """Return {req_idx: think_time_ms} for the gap before the NEXT request."""
        times: dict[int, int] = {}
        parsed: list[tuple[int, datetime]] = []
        for i, req in enumerate(requests):
            dt = self._parse_dt(req.started_datetime)
            if dt:
                parsed.append((i, dt))
        for j in range(len(parsed) - 1):
            idx_a, dt_a = parsed[j]
            _,     dt_b = parsed[j + 1]
            gap_ms = int((dt_b - dt_a).total_seconds() * 1000)
            # Clamp: 0 – 5000 ms to avoid unrealistic waits
            times[idx_a] = max(0, min(gap_ms, 5000))
        return times

    def _build_constant_timer(self, delay_ms: int) -> ET.Element:
        """Build a JMeter ConstantTimer element."""
        el = ET.Element(
            "ConstantTimer",
            guiclass="ConstantTimerGui",
            testclass="ConstantTimer",
            testname=f"Think Time ({delay_ms} ms)",
            enabled="true",
        )
        _sp(el, "ConstantTimer.delay", str(delay_ms))
        return el

    # ------------------------------------------------------------------

    def _build_test_plan(self) -> ET.Element:
        tp = ET.Element(
            "TestPlan",
            guiclass="TestPlanGui",
            testclass="TestPlan",
            testname=self.test_name,
            enabled="true",
        )
        _sp(tp, "TestPlan.comments", "Generated by ProjectStack")
        _bp(tp, "TestPlan.functional_mode", False)
        _bp(tp, "TestPlan.tearDown_on_shutdown", True)
        _bp(tp, "TestPlan.serialize_threadgroups", False)
        args = ET.SubElement(
            tp,
            "elementProp",
            name="TestPlan.user_defined_variables",
            elementType="Arguments",
            guiclass="ArgumentsPanel",
            testclass="Arguments",
            testname="User Defined Variables",
            enabled="true",
        )
        ET.SubElement(args, "collectionProp", name="Arguments.arguments")
        return tp

    # ------------------------------------------------------------------
    # User Defined Variables (standalone Arguments element)
    # ------------------------------------------------------------------

    def _build_user_defined_variables(self) -> ET.Element:
        args = ET.Element(
            "Arguments",
            guiclass="ArgumentsPanel",
            testclass="Arguments",
            testname="User Defined Variables",
            enabled="true",
        )
        coll = ET.SubElement(args, "collectionProp", name="Arguments.arguments")

        # Always add BASE_URL first (primary domain). Values are host names — they go
        # into the HTTP "Server Name or IP" field, which must not contain a scheme.
        ep = ET.SubElement(coll, "elementProp", name="BASE_URL", elementType="Argument")
        _sp(ep, "Argument.name", "BASE_URL")
        _sp(ep, "Argument.value", self._base_host)
        _sp(ep, "Argument.metadata", "=")

        # Additional BASE_URL_N variables for each extra selected domain
        for host, var in self._extra_hosts.items():
            ep2 = ET.SubElement(coll, "elementProp", name=var, elementType="Argument")
            _sp(ep2, "Argument.name", var)
            _sp(ep2, "Argument.value", host)
            _sp(ep2, "Argument.metadata", "=")

        seen: set[str] = set()
        for cv in self.correlated_values:
            if cv.var_name in seen:
                continue
            seen.add(cv.var_name)
            ep = ET.SubElement(coll, "elementProp", name=cv.var_name, elementType="Argument")
            _sp(ep, "Argument.name", cv.var_name)
            _sp(ep, "Argument.value", "")
            _sp(ep, "Argument.metadata", "=")

        # P_* static payload parameters (hardcoded values from request bodies)
        for var_name, default_value in sorted(self._payload_params.items()):
            ep = ET.SubElement(coll, "elementProp", name=var_name, elementType="Argument")
            _sp(ep, "Argument.name", var_name)
            _sp(ep, "Argument.value", default_value)
            _sp(ep, "Argument.metadata", "=")

        # User-defined test variables (from Test Data panel)
        _udv_seen: set[str] = {ep.get("name", "") for ep in coll}
        for tv in self.test_vars:
            name = (tv.get("name") or "").strip()
            if not name or name in _udv_seen:
                continue
            _udv_seen.add(name)
            ep = ET.SubElement(coll, "elementProp", name=name, elementType="Argument")
            _sp(ep, "Argument.name", name)
            _sp(ep, "Argument.value", tv.get("value") or "")
            _sp(ep, "Argument.metadata", "=")

        return args

    def _build_csv_datasets(self) -> list[ET.Element]:
        """Build CSVDataSet Config elements for each test data file."""
        elements: list[ET.Element] = []
        for df in self.test_data_files:
            filename = (df.get("filename") or "data.csv").strip()
            access = df.get("accessMethod") or "unique"
            sharing = "false" if access == "unique" else "true"
            csv_el = ET.Element(
                "CSVDataSet",
                guiclass="TestBeanGUI",
                testclass="CSVDataSet",
                testname=f"CSV Data Set — {filename}",
                enabled="true",
            )
            _sp(csv_el, "filename", filename)
            _sp(csv_el, "fileEncoding", "UTF-8")
            var_names = [str(v).strip() for v in (df.get("variables") or []) if str(v).strip()]
            _sp(csv_el, "variableNames", ",".join(var_names))
            _sp(csv_el, "ignoreFirstLine", "true" if not var_names else "false")
            _sp(csv_el, "delimiter", ",")
            _sp(csv_el, "quotedData", "false")
            _sp(csv_el, "recycle", "true")
            _sp(csv_el, "stopThread", "false")
            _sp(csv_el, "shareMode", "shareMode.all" if sharing == "true" else "shareMode.thread")
            elements.append(csv_el)
        return elements

    # ------------------------------------------------------------------
    # HTTP Request Defaults
    # ------------------------------------------------------------------

    def _build_http_defaults(self) -> ET.Element:
        cfg = ET.Element(
            "ConfigTestElement",
            guiclass="HttpDefaultsGui",
            testclass="ConfigTestElement",
            testname="HTTP Request Defaults",
            enabled="true",
        )
        _sp(cfg, "HTTPSampler.domain", "${BASE_URL}" if self._base_host else "")
        _sp(cfg, "HTTPSampler.port", self._base_port)
        _sp(cfg, "HTTPSampler.protocol", self._base_protocol)
        _sp(cfg, "HTTPSampler.contentEncoding", "UTF-8")
        _sp(cfg, "HTTPSampler.path", "")
        _sp(cfg, "HTTPSampler.connect_timeout", "")
        _sp(cfg, "HTTPSampler.response_timeout", "")
        args_el = ET.SubElement(
            cfg,
            "elementProp",
            name="HTTPsampler.Arguments",
            elementType="Arguments",
        )
        ET.SubElement(args_el, "collectionProp", name="Arguments.arguments")
        return cfg

    # ------------------------------------------------------------------
    # Thread Group
    # ------------------------------------------------------------------

    def _build_thread_group(self) -> ET.Element:
        tg_type = self.thread_group.get("type") or "standard"
        if tg_type == "stepping":
            return self._build_stepping_thread_group()
        if tg_type == "concurrency":
            return self._build_concurrency_thread_group()
        if tg_type == "ultimate":
            return self._build_ultimate_thread_group()
        tg = ET.Element(
            "ThreadGroup",
            guiclass="ThreadGroupGui",
            testclass="ThreadGroup",
            testname="Thread Group",
            enabled="true",
        )
        _sp(tg, "ThreadGroup.on_sample_error", "continue")
        loop = ET.SubElement(
            tg,
            "elementProp",
            name="ThreadGroup.main_controller",
            elementType="LoopController",
            guiclass="LoopControlPanel",
            testclass="LoopController",
            testname="Loop Controller",
            enabled="true",
        )
        # loops == -1 means infinite (scheduler-controlled)
        infinite = (self.loops == -1)
        _bp(loop, "LoopController.continue_forever", infinite)
        _sp(loop, "LoopController.loops", str(self.loops))
        _sp(tg, "ThreadGroup.num_threads", str(self.threads))
        _sp(tg, "ThreadGroup.ramp_time", str(self.ramp_up))
        # Enable scheduler when duration is set OR loops are infinite
        use_scheduler = self.duration_seconds > 0 or infinite
        _bp(tg, "ThreadGroup.scheduler", use_scheduler)
        _sp(tg, "ThreadGroup.duration", str(self.duration_seconds) if use_scheduler else "")
        _sp(tg, "ThreadGroup.delay", "0" if use_scheduler else "")
        return tg

    # Thread groups from JMeter Plugins (Custom Thread Groups, jpgc-casutg).
    # They decide when threads start and stop, so threads loop until stopped.

    def _tg_int(self, key: str, default: int) -> str:
        try:
            return str(max(0, int(self.thread_group.get(key, default))))
        except (TypeError, ValueError):
            return str(default)

    @staticmethod
    def _plugin_loop_controller(tg: ET.Element) -> None:
        loop = ET.SubElement(
            tg, "elementProp", name="ThreadGroup.main_controller", elementType="LoopController",
            guiclass="LoopControlPanel", testclass="LoopController", testname="Loop Controller", enabled="true",
        )
        _bp(loop, "LoopController.continue_forever", False)
        ip = ET.SubElement(loop, "intProp", name="LoopController.loops")
        ip.text = "-1"

    def _build_stepping_thread_group(self) -> ET.Element:
        tg = ET.Element(
            "kg.apc.jmeter.threads.SteppingThreadGroup",
            guiclass="kg.apc.jmeter.threads.SteppingThreadGroupGui",
            testclass="kg.apc.jmeter.threads.SteppingThreadGroup",
            testname="Stepping Thread Group",
            enabled="true",
        )
        _sp(tg, "ThreadGroup.on_sample_error", "continue")
        _sp(tg, "ThreadGroup.num_threads", self._tg_int("threads", 10))
        _sp(tg, "Threads initial delay", self._tg_int("initial_delay", 0))
        _sp(tg, "Start users count", self._tg_int("step_users", 2))
        _sp(tg, "Start users count burst", self._tg_int("start_burst", 0))
        _sp(tg, "Start users period", self._tg_int("step_period", 30))
        _sp(tg, "Stop users count", self._tg_int("stop_users", 5))
        _sp(tg, "Stop users period", self._tg_int("stop_period", 1))
        _sp(tg, "flighttime", self._tg_int("hold", 60))
        _sp(tg, "rampUp", self._tg_int("step_rampup", 5))
        self._plugin_loop_controller(tg)
        return tg

    def _build_concurrency_thread_group(self) -> ET.Element:
        tg = ET.Element(
            "com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup",
            guiclass="com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroupGui",
            testclass="com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup",
            testname="Concurrency Thread Group",
            enabled="true",
        )
        ET.SubElement(tg, "elementProp", name="ThreadGroup.main_controller",
                      elementType="com.blazemeter.jmeter.control.VirtualUserController")
        _sp(tg, "ThreadGroup.on_sample_error", "continue")
        _sp(tg, "TargetLevel", self._tg_int("target", 10))
        _sp(tg, "RampUp", self._tg_int("ramp_up", 60))
        _sp(tg, "Steps", self._tg_int("steps", 0))
        _sp(tg, "Hold", self._tg_int("hold", 60))
        _sp(tg, "LogFilename", "")
        iterations = self._tg_int("iterations", 0)
        _sp(tg, "Iterations", "" if iterations == "0" else iterations)
        _sp(tg, "Unit", "S")
        return tg

    def _build_ultimate_thread_group(self) -> ET.Element:
        tg = ET.Element(
            "kg.apc.jmeter.threads.UltimateThreadGroup",
            guiclass="kg.apc.jmeter.threads.UltimateThreadGroupGui",
            testclass="kg.apc.jmeter.threads.UltimateThreadGroup",
            testname="Ultimate Thread Group",
            enabled="true",
        )
        data = ET.SubElement(tg, "collectionProp", name="ultimatethreadgroupdata")
        rows = self.thread_group.get("rows") or [{"threads": 10, "initial_delay": 0, "startup": 30, "hold": 60, "shutdown": 10}]
        for row in rows:
            values = []
            for key, default in (("threads", 1), ("initial_delay", 0), ("startup", 0), ("hold", 60), ("shutdown", 0)):
                try:
                    values.append(str(max(0, int(row.get(key, default)))))
                except (TypeError, ValueError):
                    values.append(str(default))
            # JMeter names collection entries by the Java hashCode of their value
            coll = ET.SubElement(data, "collectionProp", name=str(_java_hash("".join(values))))
            for v in values:
                _sp(coll, str(_java_hash(v)), v)
        self._plugin_loop_controller(tg)
        _sp(tg, "ThreadGroup.on_sample_error", "continue")
        return tg

    # ------------------------------------------------------------------
    # Cookie Manager
    # ------------------------------------------------------------------

    def _build_cookie_manager(self) -> ET.Element:
        cm = ET.Element(
            "CookieManager",
            guiclass="CookiePanel",
            testclass="CookieManager",
            testname="HTTP Cookie Manager",
            enabled="true",
        )
        ET.SubElement(cm, "collectionProp", name="CookieManager.cookies")
        _bp(cm, "CookieManager.clearEachIteration", True)
        _sp(cm, "CookieManager.implementation",
            "org.apache.jmeter.protocol.http.control.HC4CookieHandler")
        return cm

    # ------------------------------------------------------------------
    # Selection + assertion helpers
    # ------------------------------------------------------------------

    def _is_selected(self, req_idx: int) -> bool:
        """Return True if this request index should be included in the JMX."""
        sl = self._selected_list
        if sl is None:
            return True
        return req_idx in sl

    def _append_assertions(self, parent: ET.Element, req_idx: int, req: "HarRequest") -> None:
        """Append assertion elements + hashTrees to parent sampler tree."""
        custom = self._custom_assertions.get(req_idx)
        if custom:
            # Use custom assertions exclusively when defined for this request
            for a in custom:
                if not a.get("enabled", True):
                    continue
                atype = a.get("type", "status_code")
                value = str(a.get("value", ""))
                if atype == "response_time_ms":
                    try:
                        ms = int(value)
                    except (ValueError, TypeError):
                        ms = 2000
                    parent.append(self._build_duration_assertion(ms))
                else:
                    parent.append(self._build_custom_assertion(atype, value))
                ET.SubElement(parent, "hashTree")
        elif (
            self.include_assertions and req.status_code and req.status_code < 500
            and not (300 <= req.status_code < 400 and req_idx not in self._no_follow)
        ):
            # Fall back to auto-generated status code assertion (a followed redirect
            # ends on the target's status, so its recorded 3xx is not asserted)
            parent.append(self._build_response_assertion(req))
            ET.SubElement(parent, "hashTree")

    def _build_custom_assertion(self, atype: str, value: str) -> ET.Element:
        """Build a ResponseAssertion for a given type and value."""
        ra = ET.Element(
            "ResponseAssertion",
            guiclass="AssertionGui",
            testclass="ResponseAssertion",
            testname=f"Assert {atype.replace('_', ' ')}",
            enabled="true",
        )
        coll = ET.SubElement(ra, "collectionProp", name="Asserion.test_strings")
        sp = ET.SubElement(coll, "stringProp", name="49586")
        sp.text = value
        _sp(ra, "Assertion.custom_message", "")
        if atype == "status_code":
            _sp(ra, "Assertion.test_field", "Assertion.response_code")
            ip = ET.SubElement(ra, "intProp", name="Assertion.test_type")
            ip.text = "8"   # equals
        elif atype == "response_contains":
            _sp(ra, "Assertion.test_field", "Assertion.response_data")
            ip = ET.SubElement(ra, "intProp", name="Assertion.test_type")
            ip.text = "2"   # substring
        else:  # response_regex
            _sp(ra, "Assertion.test_field", "Assertion.response_data")
            ip = ET.SubElement(ra, "intProp", name="Assertion.test_type")
            ip.text = "1"   # regex
        _bp(ra, "Assertion.assume_success", False)
        return ra

    def _build_duration_assertion(self, max_ms: int) -> ET.Element:
        """DurationAssertion — fails if response time exceeds max_ms."""
        da = ET.Element(
            "DurationAssertion",
            guiclass="DurationAssertionGui",
            testclass="DurationAssertion",
            testname=f"Duration < {max_ms}ms",
            enabled="true",
        )
        _sp(da, "DurationAssertion.duration", str(max_ms))
        return da

    # ------------------------------------------------------------------
    # Payload parameterization  (P_* User Defined Variables)
    # ------------------------------------------------------------------

    def _build_payload_subs(self) -> None:
        """
        Scan all POST/PUT/PATCH request bodies, extract simple scalar values
        that are not already correlated, and register them as P_* UDV variables.
        Adds P_* substitutions to self._subs so bodies are updated automatically.
        """
        correlated_vals: set[str] = {
            cv.extract_value for cv in self.correlated_values if cv.extract_value
        }
        _uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
        )

        for req_idx, req in enumerate(self.requests):
            if not req.body or req.method not in ("POST", "PUT", "PATCH"):
                continue
            try:
                body_json = json.loads(req.body)
            except Exception:
                continue
            if not isinstance(body_json, dict):
                continue

            for key, raw_val in body_json.items():
                if isinstance(raw_val, bool) or not isinstance(raw_val, (str, int, float)):
                    continue
                str_val = str(raw_val)
                # Skip empty, very short, or very long values
                if len(str_val) < 2 or len(str_val) > 100:
                    continue
                # Skip UUID-like values (likely dynamic IDs)
                if _uuid_re.match(str_val):
                    continue
                # Skip long alphanumeric blobs (tokens, hashes)
                if len(str_val) > 30 and re.match(r"^[A-Za-z0-9+/=_\-\.]+$", str_val):
                    continue
                # Skip already-correlated values
                if str_val in correlated_vals:
                    continue

                base_name = "P_" + re.sub(r"[^A-Z0-9]", "_", key.upper()).strip("_")
                # The same field with a different value gets its own variable, so a
                # second request keeps sending what it sent in the recording.
                var_name, n = base_name, 1
                while var_name in self._payload_params and self._payload_params[var_name] != str_val:
                    n += 1
                    var_name = f"{base_name}_{n}"
                self._payload_params.setdefault(var_name, str_val)
                self._field_subs.setdefault(req_idx, []).append((key, raw_val, var_name))

    # ------------------------------------------------------------------
    # Response Assertion
    # ------------------------------------------------------------------

    def _build_response_assertion(self, req: "HarRequest") -> ET.Element:
        """ResponseAssertion that checks the HTTP status code matches the HAR recording."""
        ra = ET.Element(
            "ResponseAssertion",
            guiclass="AssertionGui",
            testclass="ResponseAssertion",
            testname=f"Assert {req.status_code}",
            enabled="true",
        )
        coll = ET.SubElement(ra, "collectionProp", name="Asserion.test_strings")
        sp = ET.SubElement(coll, "stringProp", name="49586")
        sp.text = str(req.status_code)
        _sp(ra, "Assertion.custom_message", "")
        _sp(ra, "Assertion.test_field", "Assertion.response_code")
        _bp(ra, "Assertion.assume_success", False)
        # intProp: 8 = equals
        ip = ET.SubElement(ra, "intProp", name="Assertion.test_type")
        ip.text = "8"
        return ra

    # ------------------------------------------------------------------
    # Authorization PreProcessor (ThreadGroup level)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Transaction Controller
    # ------------------------------------------------------------------

    def _build_transaction_controller(self, name: str) -> ET.Element:
        tc = ET.Element(
            "TransactionController",
            guiclass="TransactionControllerGui",
            testclass="TransactionController",
            testname=name,
            enabled="true",
        )
        _bp(tc, "TransactionController.includeTimers", False)
        _bp(tc, "TransactionController.parent", False)
        return tc

    # ------------------------------------------------------------------
    # HTTP Sampler
    # ------------------------------------------------------------------

    def _label_path(self, index: int, req: HarRequest) -> str:
        """
        URL path for a sampler name with correlated values shown as {VAR}. Names must
        not contain ${...}: JMeter would substitute it per sample, splitting one
        request's statistics into one row per ID, and the recorded ID is meaningless
        once the script runs with fresh values.
        """
        path = urlparse(req.url).path or "/"
        subbed = _apply_subs(path, self._subs.get(index, []))
        return re.sub(r"\$\{(?:__urlencode\(\$\{)?(\w+)(?:\}\))?\}", r"{\1}", re.sub(
            r"\$\{__(?:time|groovy)\([^}]*?,(\w+)\)\}", r"{\1}", subbed))

    def _build_sampler(self, index: int, req: HarRequest, label: str | None = None) -> ET.Element:
        parsed = urlparse(req.url)
        protocol = parsed.scheme
        domain = parsed.hostname or ""
        port = str(parsed.port) if parsed.port else ""
        path = parsed.path or "/"
        query = parsed.query

        subs = self._subs.get(index, [])
        body = req.body or ""
        for key, raw_val, var_name in self._field_subs.get(index, []):
            body = _field_sub(body, key, raw_val, var_name)
        body = _apply_subs(body, subs)
        if label is None:
            label = f"{req.method} {self._label_path(index, req)}"
        if self.label_with_index:
            label = f"[{index}] {label}"

        sampler = ET.Element(
            "HTTPSamplerProxy",
            guiclass="HttpTestSampleGui",
            testclass="HTTPSamplerProxy",
            testname=label,
            enabled="true",
        )
        # Requests to the primary domain inherit domain/port/protocol from HTTP Defaults;
        # other selected domains use their ${BASE_URL_n}; anything else stays literal.
        if self._base_host and domain == self._base_host:
            domain, port, protocol = "", "", ""
        elif domain in self._extra_hosts:
            domain = f"${{{self._extra_hosts[domain]}}}"
        _sp(sampler, "HTTPSampler.domain", domain)
        _sp(sampler, "HTTPSampler.port", port)
        _sp(sampler, "HTTPSampler.protocol", protocol)
        path_subst = _apply_subs(path, subs)
        query_subst = _apply_subs(query, subs) if query else ""
        _sp(sampler, "HTTPSampler.path", path_subst + (f"?{query_subst}" if query_subst else ""))
        _sp(sampler, "HTTPSampler.method", req.method)
        _bp(sampler, "HTTPSampler.follow_redirects", index not in self._no_follow)
        _bp(sampler, "HTTPSampler.auto_redirects", False)
        _bp(sampler, "HTTPSampler.use_keepalive", True)
        _bp(sampler, "HTTPSampler.DO_MULTIPART_POST", False)
        _sp(sampler, "HTTPSampler.embedded_url_re", "")
        _sp(sampler, "HTTPSampler.connect_timeout", "")
        _sp(sampler, "HTTPSampler.response_timeout", "")

        args_el = ET.SubElement(
            sampler,
            "elementProp",
            name="HTTPsampler.Arguments",
            elementType="Arguments",
        )
        args_coll = ET.SubElement(args_el, "collectionProp", name="Arguments.arguments")

        if body and req.method not in ("GET", "HEAD"):
            _bp(sampler, "HTTPSampler.postBodyRaw", True)
            arg = ET.SubElement(
                args_coll, "elementProp", name="", elementType="HTTPArgument"
            )
            _bp(arg, "HTTPArgument.always_encode", False)
            val_el = ET.SubElement(arg, "stringProp", name="Argument.value")
            val_el.text = body
            _sp(arg, "Argument.metadata", "=")
        else:
            # The query string is already in HTTPSampler.path exactly as recorded;
            # adding the HAR queryString as arguments too would send every
            # parameter twice.
            _bp(sampler, "HTTPSampler.postBodyRaw", False)

        return sampler

    # ------------------------------------------------------------------
    # Header Manager
    # ------------------------------------------------------------------

    def _build_header_manager(self, index: int, req: HarRequest) -> ET.Element:
        subs = self._subs.get(index, [])
        hm = ET.Element(
            "HeaderManager",
            guiclass="HeaderPanel",
            testclass="HeaderManager",
            testname="HTTP Header Manager",
            enabled="true",
        )
        coll = ET.SubElement(hm, "collectionProp", name="HeaderManager.headers")
        for name, value in req.headers.items():
            # HTTP/2 pseudo-headers (:authority, :path …) are not real headers
            if name.lower() in SKIP_HEADERS or name.startswith(":"):
                continue
            # Static credentials (Basic, API keys) are kept verbatim; correlated
            # bearer tokens become ${C_BEARER_TOKEN}.
            value = _apply_subs(value, subs)
            hdr = ET.SubElement(coll, "elementProp", name=name, elementType="Header")
            _sp(hdr, "Header.name", name)
            _sp(hdr, "Header.value", value)
        return hm

    # ------------------------------------------------------------------
    # Extractors (JSONPostProcessor for simple, JSR223 for complex)
    # ------------------------------------------------------------------

    def _append_correlation_check(self, tree: ET.Element, sampler_el: ET.Element, header_el: ET.Element) -> None:
        """Mark samples sent with an extractor default, for the correlation health check."""
        if not self.correlation_markers:
            return
        extracted = sorted({cv.var_name for cv in self.correlated_values
                            if (getattr(cv, "extractor", "") or "") != "function"})
        if not extracted:
            return
        sent = ET.tostring(sampler_el, encoding="unicode") + ET.tostring(header_el, encoding="unicode")
        used = [v for v in extracted if "${" + v + "}" in sent]
        if used:
            tree.append(_jsr223_assertion("Correlation check", _groovy_correlation_check(used)))
            ET.SubElement(tree, "hashTree")

    def _append_extractors(self, sampler_tree: ET.Element, req_idx: int) -> None:
        """Add every extractor whose source is this request (each followed by its hashTree)."""
        names = []
        for cv in self.correlated_values:
            if cv.source_request_index != req_idx:
                continue
            names.append(cv.var_name)
            for el in self._build_extractors(cv):
                sampler_tree.append(el)
                ET.SubElement(sampler_tree, "hashTree")
        if self.label_with_index and names:
            # Replay mode: record what each extractor produced so the replay
            # verifier can tell a failed extraction from a failed request.
            sampler_tree.append(_jsr223_post("Log extracted values", _groovy_var_log(req_idx, names)))
            ET.SubElement(sampler_tree, "hashTree")

    def _build_extractor(self, cv: CorrelatedValue) -> tuple[ET.Element, bool]:
        """Backward-compatible single-extractor API: (element, is_groovy)."""
        el = self._build_extractors(cv)[0]
        return el, el.tag == "JSR223PostProcessor"

    def _build_extractors(self, cv: CorrelatedValue) -> list[ET.Element]:
        """
        Build the extractor element(s) for a correlated value.

        Correlations carry an explicit extractor spec (cv.extractor) chosen by the
        engine from the recorded response. Sessions created before that existed
        fall back to the detection_pass based rules.
        """
        kind = getattr(cv, "extractor", "") or ""
        cfg = getattr(cv, "extractor_config", None) or {}

        if kind == "function":
            return []  # generated at the point of use, nothing to extract
        if kind == "json" or (not kind and cv.detection_pass in ("pass2_json", "custom_rule_json")):
            path = cfg.get("path") or _key_to_jsonpath(cv.extract_key)
            return [_json_extractor(cv.var_name, path)]
        if kind == "css":
            return [_css_extractor(cv.var_name, cfg.get("selector", ""), cfg.get("attr", "value"))]
        if kind == "boundary":
            return [_boundary_extractor(cv.var_name, cfg.get("lb", ""), cfg.get("rb", ""),
                                        cfg.get("match_no", 1))]
        if kind == "regex" or (not kind and cv.detection_pass == "custom_rule_regex"):
            regex = cfg.get("regex") or cv.extract_key
            return [_regex_extractor(cv.var_name, regex, "false", cfg.get("match_no", 1))]
        if kind == "header" or (not kind and (cv.extract_from == "header" or cv.detection_pass == "custom_rule_header")):
            name = cfg.get("name") or cv.extract_key
            regex = r"(?i)(?:^|\n)" + jregex_escape(name) + r":\s*([^\r\n]+)"
            return [_regex_extractor(cv.var_name, regex, "true", 1, scope_all=True)]
        if kind == "header_regex":
            els = [_regex_extractor(cv.var_name, cfg.get("regex", ""), "true", cfg.get("match_no", 1),
                                    scope_all=True)]
            if cfg.get("url_decode"):
                els.append(_jsr223_post(f"URL-decode {cv.var_name}", _groovy_url_decode(cv.var_name)))
            return els
        if kind == "cookie_header":
            return [_jsr223_post(
                f"Extract {cv.var_name}",
                _groovy_cookie_header_extractor(cv.var_name, cfg.get("cookie", ""), bool(cfg.get("url_decode"))),
            )]
        if kind == "jwt_claim" or (not kind and cv.detection_pass == "pass1_jwt_claim"):
            claim_name = cfg.get("claim") or cv.extract_key.split(".")[-1]
            return [_jsr223_post(f"Extract {cv.var_name}", _groovy_jwt_claim_extractor(cv.var_name, claim_name))]

        # Legacy sessions — JSR223 PostProcessor (Groovy)
        if cv.detection_pass == "pass1_jwt":
            script = _groovy_jwt_extractor(cv.var_name)
        elif cv.detection_pass == "pass1_bearer":
            if JWT_RE.fullmatch(cv.extract_value or ""):
                script = _groovy_jwt_extractor(cv.var_name)
            else:
                script = _groovy_json_extractor(cv.var_name, cv.extract_key)
        elif cv.detection_pass == "pass_url_id" and "-" in cv.extract_value:
            script = _groovy_uuid_extractor(cv.var_name, cv.extract_value)
        elif cv.extract_from == "cookie" or cv.detection_pass == "pass3_cookie":
            # Cookie Manager handles cookie state automatically — skip extractor
            script = f'// Cookie {cv.extract_key} managed by HTTP Cookie Manager\n'
        else:
            script = _groovy_json_extractor(cv.var_name, cv.extract_key)
        return [_jsr223_post(f"Extract {cv.var_name}", script)]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # View Results Tree listener
    # ------------------------------------------------------------------

    def _build_view_results_tree(self) -> ET.Element:
        """ResultCollector that renders as 'View Results Tree' in JMeter GUI."""
        rc = ET.Element(
            "ResultCollector",
            guiclass="ViewResultsFullVisualizer",
            testclass="ResultCollector",
            testname="View Results Tree",
            enabled="true",
        )
        _bp(rc, "ResultCollector.error_logging", False)
        obj = ET.SubElement(rc, "objProp")
        ET.SubElement(obj, "name").text = "saveConfig"
        val = ET.SubElement(obj, "value")
        val.set("class", "SampleSaveConfiguration")
        for tag, text in [
            ("time", "true"), ("latency", "true"), ("timestamp", "true"),
            ("success", "true"), ("label", "true"), ("code", "true"),
            ("message", "true"), ("threadName", "true"), ("dataType", "true"),
            ("encoding", "false"), ("assertions", "true"), ("subresults", "true"),
            ("responseData", "false"), ("samplerData", "false"), ("xml", "false"),
            ("fieldNames", "true"), ("responseHeaders", "false"),
            ("requestHeaders", "false"), ("responseDataOnError", "false"),
            ("saveAssertionResultsFailureMessage", "true"),
            ("bytes", "true"), ("sentBytes", "true"), ("url", "true"),
            ("threadCounts", "true"), ("idleTime", "true"), ("connectTime", "true"),
        ]:
            ET.SubElement(val, tag).text = text
        _sp(rc, "filename", "")
        return rc

    # ------------------------------------------------------------------
    # Grafana InfluxDB Backend Listener
    # ------------------------------------------------------------------

    def _build_grafana_listener(self) -> ET.Element:
        """BackendListener that streams metrics to Grafana via InfluxDB line protocol."""
        bl = ET.Element(
            "BackendListener",
            guiclass="BackendListenerGui",
            testclass="BackendListener",
            testname="Grafana InfluxDB Listener",
            enabled="true",
        )
        args_el = ET.SubElement(
            bl, "elementProp", name="arguments", elementType="Arguments",
            guiclass="ArgumentsPanel", testclass="Arguments",
            testname="User Defined Variables", enabled="true",
        )
        coll = ET.SubElement(args_el, "collectionProp", name="Arguments.arguments")

        params = [
            ("influxdbMetricsSender",
             "org.apache.jmeter.visualizers.backend.influxdb.InfluxdbBackendListenerClient"),
            ("influxdbUrl", self.grafana_url),
            ("application", self.test_name),
            ("measurement", "jmeter"),
            ("summaryOnly", "false"),
            ("samplersRegex", ".*"),
            ("percentiles", "90;95;99"),
            ("testTitle", self.test_name),
            ("eventTags", ""),
        ]
        for name, value in params:
            ep = ET.SubElement(coll, "elementProp", name=name, elementType="Argument")
            _sp(ep, "Argument.name", name)
            _sp(ep, "Argument.value", value)
            _sp(ep, "Argument.metadata", "=")

        _sp(bl, "classname",
            "org.apache.jmeter.visualizers.backend.influxdb.InfluxdbBackendListenerClient")
        return bl

    @staticmethod
    def _strip_invalid_xml_chars(text: str) -> str:
        """Remove characters that are illegal in XML 1.0."""
        # Valid XML 1.0 chars: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD]
        return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\uFFFE\uFFFF]', '', text)

    @staticmethod
    def _write(root: ET.Element, output_path: str | Path) -> None:
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        xml_str = JmxGenerator._strip_invalid_xml_chars(xml_str)
        try:
            dom = minidom.parseString(xml_str)
            pretty = dom.toprettyxml(indent="  ", encoding=None)
            lines = pretty.splitlines()
            if lines and lines[0].startswith("<?xml"):
                lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
            output = "\n".join(lines)
        except Exception:
            # Fallback: write raw XML without pretty-printing
            output = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
        Path(output_path).write_text(output, encoding="utf-8")


# ---------------------------------------------------------------------------
# Param suggestion helper (used by API to surface static value candidates)
# ---------------------------------------------------------------------------

def suggest_payload_params(
    requests: list[HarRequest],
    correlated_values: list[CorrelatedValue],
) -> list[dict]:
    """
    Scan POST/PUT/PATCH request bodies for static scalar values that are not
    already correlated.  Returns a list of parameterization candidates of the form:
    [{field, value, var_name, occurrences}]
    """
    correlated_vals: set[str] = {cv.extract_value for cv in correlated_values if cv.extract_value}
    _uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
    )

    seen: dict[str, dict] = {}  # var_name -> candidate

    for req in requests:
        if not req.body or req.method not in ("POST", "PUT", "PATCH"):
            continue
        try:
            body_json = json.loads(req.body)
        except Exception:
            continue
        if not isinstance(body_json, dict):
            continue

        for key, raw_val in body_json.items():
            if isinstance(raw_val, bool) or not isinstance(raw_val, (str, int, float)):
                continue
            str_val = str(raw_val)
            if len(str_val) < 2 or len(str_val) > 100:
                continue
            if _uuid_re.match(str_val):
                continue
            if len(str_val) > 30 and re.match(r"^[A-Za-z0-9+/=_\-\.]+$", str_val):
                continue
            if str_val in correlated_vals:
                continue

            var_name = "P_" + re.sub(r"[^A-Z0-9]", "_", key.upper()).strip("_")
            if var_name not in seen:
                seen[var_name] = {"field": key, "value": str_val, "var_name": var_name, "occurrences": 1}
            else:
                seen[var_name]["occurrences"] += 1

    return list(seen.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_jmx(
    requests: list[HarRequest],
    correlated_values: list[CorrelatedValue],
    output_path: str | Path,
    test_name: str = "ProjectStack Test",
    threads: int = 1,
    ramp_up: int = 1,
    loops: int = 1,
    transactions: list[Transaction] | None = None,
    base_url: str = "",
    base_urls: list[str] | None = None,
    test_vars: list[dict] | None = None,
    test_data_files: list[dict] | None = None,
    include_view_tree: bool = True,
    grafana_url: str = "",
    no_transactions: bool = True,
    include_assertions: bool = True,
    parameterize_payload: bool = True,
    selected_indices: list[int] | None = None,
    custom_assertions: dict[int, list] | None = None,
    include_think_times: bool = False,
    duration_seconds: int = 0,
    label_with_index: bool = False,
    thread_group: dict | None = None,
    correlation_markers: bool = True,
) -> None:
    JmxGenerator(
        requests=requests,
        correlated_values=correlated_values,
        test_name=test_name,
        threads=threads,
        ramp_up=ramp_up,
        loops=loops,
        transactions=transactions,
        base_url=base_url,
        base_urls=base_urls,
        test_vars=test_vars,
        test_data_files=test_data_files,
        include_view_tree=include_view_tree,
        grafana_url=grafana_url,
        no_transactions=no_transactions,
        include_assertions=include_assertions,
        parameterize_payload=parameterize_payload,
        selected_indices=selected_indices,
        custom_assertions=custom_assertions,
        include_think_times=include_think_times,
        duration_seconds=duration_seconds,
        label_with_index=label_with_index,
        thread_group=thread_group,
        correlation_markers=correlation_markers,
    ).generate(output_path)
