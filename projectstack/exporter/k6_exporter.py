"""
Export a ProjectStack session to a k6 JavaScript test script.

Uses the same correlation specs as the JMX generator: every extractor type
(JSONPath, CSS, header, header regex, boundary, regex, JWT claim, cookie →
header) has a k6 equivalent, client-generated values are recreated each
iteration, and substitution is boundary- and encoding-aware.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue
from projectstack.correlator.matching import header_get
from projectstack.generator.jmx_generator import _apply_subs

_SKIP_HEADERS = frozenset({
    "host", "content-length", "connection", "accept-encoding",
    "transfer-encoding", "upgrade-insecure-requests", "cookie",
})

_HELPERS = r'''
// ── Extraction helpers ───────────────────────────────────────────────────────
function hdr(r, name) {
  const want = name.toLowerCase();
  for (const k in r.headers) if (k.toLowerCase() === want) return r.headers[k];
  return null;
}
function headersText(r) {
  return Object.entries(r.headers).map(([k, v]) => `${k}: ${v}`).join("\n");
}
function regexNth(text, pattern, flags, n) {
  const re = new RegExp(pattern, flags.includes("g") ? flags : flags + "g");
  let m, i = 0;
  while ((m = re.exec(text)) !== null) {
    if (++i === n) return m[1];
    if (m[0] === "") re.lastIndex++;
  }
  return null;
}
function boundaryNth(text, lb, rb, n) {
  let start = 0, i = 0;
  for (;;) {
    const li = text.indexOf(lb, start);
    if (li < 0) return null;
    const ri = text.indexOf(rb, li + lb.length);
    if (ri < 0) return null;
    if (++i === n) return text.substring(li + lb.length, ri);
    start = ri + rb.length;
  }
}
function jwtClaim(r, claim) {
  const text = (r.body || "") + "\n" + headersText(r);
  const re = /eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    try {
      const claims = JSON.parse(encoding.b64decode(m[1], "rawurl", "s"));
      if (claims && claims[claim] !== undefined) return String(claims[claim]);
    } catch (_) { /* not a decodable JWT */ }
  }
  return null;
}
function cookieValue(r, name, urlDecode) {
  const c = r.cookies && r.cookies[name];
  if (!c || !c.length) return null;
  const v = c[c.length - 1].value;
  return urlDecode && v.includes("%") ? decodeURIComponent(v) : v;
}
function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const n = (Math.random() * 16) | 0;
    return (c === "x" ? n : (n & 0x3) | 0x8).toString(16);
  });
}
function orEmpty(v) { return v === null || v === undefined ? "" : String(v); }
'''


def _js(s: str) -> str:
    return json.dumps(s)


def _esc_tmpl(s: str) -> str:
    """Escape text for a JS template literal (before correlation placeholders are inserted)."""
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def _to_k6_placeholders(s: str) -> str:
    """JMeter ${__urlencode(${X})} → JS ${encodeURIComponent(X)}; ${X} is already valid JS."""
    return re.sub(r"\$\{__urlencode\(\$\{(\w+)\}\)\}", r"${encodeURIComponent(\1)}", s)


def _jsonpath_to_gjson(path: str) -> str | None:
    """Convert the JSONPath subset we emit into k6's GJSON selector syntax."""
    if not path.startswith("$"):
        return None
    out: list[str] = []
    pos = 1
    token = re.compile(
        r"\.([A-Za-z_][A-Za-z0-9_]*)|\['((?:[^'\\]|\\.)*)'\]|\[(\d+)\]"
        r"|\[\?\(@\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*'((?:[^'\\]|\\.)*)'\)\]"
    )
    while pos < len(path):
        m = token.match(path, pos)
        if not m:
            return None
        pos = m.end()
        if m.group(1) is not None:
            out.append(m.group(1))
        elif m.group(2) is not None:
            out.append(re.sub(r"([.*?|#@\\])", r"\\\1", m.group(2)))
        elif m.group(3) is not None:
            out.append(m.group(3))
        else:
            out.append(f'#({m.group(4)}=={json.dumps(m.group(5))})')
    return ".".join(out)


def _java_regex_to_js(pattern: str) -> tuple[str, str]:
    flags = ""
    m = re.match(r"^\(\?([imsx]+)\)", pattern)
    if m:
        flags = "".join(f for f in m.group(1) if f in "ims")
        pattern = pattern[m.end():]
    return pattern, flags


def _extract_expr(cv: CorrelatedValue, r: str) -> str | None:
    """JS expression that extracts cv from response variable `r` (None for client values)."""
    kind = getattr(cv, "extractor", "") or ""
    cfg = getattr(cv, "extractor_config", None) or {}
    if kind == "function":
        return None
    if kind == "json" or (not kind and cv.extract_from == "body"):
        path = cfg.get("path") or ("$." + cv.extract_key if not cv.extract_key.startswith("$") else cv.extract_key)
        gjson = _jsonpath_to_gjson(path)
        if gjson is None:
            return '""'
        return f"orEmpty((() => {{ try {{ return {r}.json({_js(gjson)}); }} catch (_) {{ return null; }} }})())"
    if kind == "css":
        return f"orEmpty({r}.html().find({_js(cfg.get('selector', ''))}).first().attr({_js(cfg.get('attr', 'value'))}))"
    if kind == "header" or (not kind and cv.extract_from == "header"):
        return f"orEmpty(hdr({r}, {_js(cfg.get('name') or cv.extract_key)}))"
    if kind in ("header_regex", "regex"):
        pattern, flags = _java_regex_to_js(cfg.get("regex", ""))
        text = f"headersText({r})" if kind == "header_regex" else f"{r}.body"
        expr = f"orEmpty(regexNth({text}, {_js(pattern)}, {_js(flags)}, {int(cfg.get('match_no', 1) or 1)}))"
        return f"decodeURIComponent({expr})" if cfg.get("url_decode") else expr
    if kind == "boundary":
        return (f"orEmpty(boundaryNth({r}.body, {_js(cfg.get('lb', ''))}, {_js(cfg.get('rb', ''))}, "
                f"{int(cfg.get('match_no', 1) or 1)}))")
    if kind == "jwt_claim":
        return f"orEmpty(jwtClaim({r}, {_js(cfg.get('claim', ''))}))"
    if kind == "cookie_header":
        return f"orEmpty(cookieValue({r}, {_js(cfg.get('cookie', ''))}, {str(bool(cfg.get('url_decode'))).lower()}))"
    return '""'


def _function_expr(cv: CorrelatedValue) -> str:
    fn = (getattr(cv, "extractor_config", None) or {}).get("function")
    if fn == "time":
        return "String(Date.now())"
    if fn == "time_s":
        return "String(Math.floor(Date.now() / 1000))"
    return "uuidv4()"


def export_k6(
    requests: list[HarRequest],
    correlated_values: list[CorrelatedValue],
    test_name: str = "ProjectStack Test",
    base_url: str = "",
    vus: int = 1,
    duration: str = "30s",
    selected_indices: list[int] | None = None,
) -> str:
    """Return a k6 JavaScript test script as a string."""
    order = (
        [i for i in selected_indices if 0 <= i < len(requests)]
        if selected_indices is not None else list(range(len(requests)))
    )
    position = {idx: n for n, idx in enumerate(order)}

    subs: dict[int, list[tuple[str, str]]] = {}
    first_use: dict[int, list[CorrelatedValue]] = {}
    for cv in correlated_values:
        if cv.extract_from == "cookie":
            continue
        for idx in cv.used_in_requests:
            subs.setdefault(idx, []).append((cv.extract_value, cv.var_name))
        if getattr(cv, "extractor", "") == "function" and cv.used_in_requests:
            first = min(cv.used_in_requests, key=lambda i: position.get(i, len(position) + i))
            first_use.setdefault(first, []).append(cv)

    # Redirects whose target was recorded as its own entry are not followed (see jmx_generator)
    no_follow: set[int] = set()
    for pos, idx in enumerate(order):
        req = requests[idx]
        if 300 <= (req.status_code or 0) < 400:
            loc = header_get(req.response_headers or {}, "Location")
            target = urljoin(req.url, loc).split("#")[0] if loc else ""
            if target and any(requests[n].url.split("#")[0] == target for n in order[pos + 1:pos + 4]):
                no_follow.add(idx)

    def sub(text: str, idx: int) -> str:
        return _to_k6_placeholders(_apply_subs(_esc_tmpl(text), subs.get(idx, [])))

    lines: list[str] = [
        f"// {test_name} — generated by ProjectStack",
        'import http from "k6/http";',
        'import encoding from "k6/encoding";',
        'import { check, sleep } from "k6";',
        "",
        "export const options = {",
        f"  vus: {vus},",
        f'  duration: "{duration}",',
        "};",
        _HELPERS,
        "export default function () {",
    ]
    names = list(dict.fromkeys(cv.var_name for cv in correlated_values if cv.extract_from != "cookie"))
    if names:
        lines.append("  // Correlation variables (reset every iteration)")
        lines.append("  let " + ", ".join(f'{n} = ""' for n in names) + ";")
    lines.append("")

    for i in order:
        req = requests[i]
        for cv in first_use.get(i, []):
            lines.append(f"  {cv.var_name} = {_function_expr(cv)};  // generated client-side")

        url = sub(req.url, i)
        if base_url:
            for scheme in ("https://", "http://"):
                url = url.replace(f"{scheme}{base_url}", "${BASE_URL}")

        headers = {
            k: v for k, v in req.headers.items()
            if k.lower() not in _SKIP_HEADERS and not k.startswith(":")
        }
        lines.append(f"  // ── Request {i + 1}: {req.method} {req.url[:70]}")
        hdr_pairs = ",\n    ".join(f"{_js(k)}: `{sub(v, i)}`" for k, v in headers.items())
        params = f"headers: {{\n    {hdr_pairs}\n  }}" if headers else "headers: {}"
        if i in no_follow:
            params += ", redirects: 0"
        lines.append(f"  const p{i} = {{ {params} }};")

        body = f"`{sub(req.body, i)}`" if req.body and req.method not in ("GET", "HEAD") else "null"
        lines.append(f'  const r{i} = http.request({_js(req.method)}, `{url}`, {body}, p{i});')
        lines.append(
            f'  check(r{i}, {{ "{req.method} #{i + 1} status {req.status_code}": '
            f"(r) => r.status === {req.status_code if i in no_follow or not 300 <= req.status_code < 400 else 200} }});"
        )
        for cv in correlated_values:
            if cv.source_request_index != i:
                continue
            expr = _extract_expr(cv, f"r{i}")
            if expr:
                lines.append(f"  {cv.var_name} = {expr};")
        lines.append("")

    lines += ["  sleep(1);", "}"]
    script = "\n".join(lines)
    if base_url:
        script = script.replace("export default function () {",
                                f'const BASE_URL = __ENV.BASE_URL || "https://{base_url}";\n\nexport default function () {{', 1)
    return script
