"""
Value matching and extractor inference shared by the correlation engine,
the JMX generator and the replay verifier.

Matching is boundary-aware (a value never matches inside a longer
alphanumeric run, so "1234" does not match "91234567") and encoding-aware
(a value is also found when it was URL-encoded or JSON-escaped on the way
into a request).

Extractor inference answers "given this recorded response and this value,
how should JMeter pull the value out at runtime?" — trying JSON paths,
HTML attributes, response headers and finally left/right text boundaries,
the same strategy LoadRunner uses for web_reg_save_param.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, quote_plus, urlparse

# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

# Request headers whose values never need correlation (the Cookie Manager,
# the HTTP client or JMeter itself owns them).
_IGNORED_REQUEST_HEADERS = {
    "cookie", "host", "content-length", "connection", "accept-encoding",
    "user-agent", "accept", "accept-language", "referer", "origin",
    "content-type", "cache-control", "pragma",
    # Set by the browser itself and the same on every run
    "upgrade-insecure-requests", "dnt", "priority", "te",
}
# Client hints (sec-ch-ua-platform …) and fetch metadata (sec-fetch-site …)
_IGNORED_REQUEST_HEADER_PREFIXES = ("sec-ch-", "sec-fetch-")


def header_get(headers: dict[str, str], name: str, default: str = "") -> str:
    """Case-insensitive header lookup (HTTP/2 HARs use lowercase names)."""
    if name in headers:
        return headers[name]
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return default


def correlatable_header_items(headers: dict[str, str]) -> list[tuple[str, str]]:
    """Request headers that may carry correlated values (no cookies, no pseudo-headers)."""
    return [
        (k, v) for k, v in headers.items()
        if not k.startswith(":") and k.lower() not in _IGNORED_REQUEST_HEADERS
        and not k.lower().startswith(_IGNORED_REQUEST_HEADER_PREFIXES)
    ]


# ---------------------------------------------------------------------------
# Boundary + encoding aware matching
# ---------------------------------------------------------------------------

def _boundary_regex(s: str) -> re.Pattern:
    pre = r"(?<![A-Za-z0-9])" if s[:1].isalnum() else ""
    post = r"(?![A-Za-z0-9])" if s[-1:].isalnum() else ""
    return re.compile(pre + re.escape(s) + post)


def contains_value(text: str | None, value: str) -> bool:
    """True when `value` occurs in `text` and is not part of a longer alphanumeric run."""
    if not text or not value or value not in text:   # fast substring pre-check
        return False
    return _boundary_regex(value).search(text) is not None


@lru_cache(maxsize=65536)
def encoded_variants(value: str) -> tuple[tuple[str, str], ...]:
    """
    Forms a value can take inside a request, as (text, encoding) pairs.
    encoding is "raw", "url" (percent-encoded) or "json" (JSON-string escaped).
    """
    variants = [(value, "raw")]
    seen = {value}
    # Plain tokens (letters, digits, -._~) are identical in every encoding — skip the work
    if _PLAIN_TOKEN_RE.fullmatch(value):
        return tuple(variants)
    for enc, form in (
        ("url", quote(value, safe="")),
        ("url", quote_plus(value, safe="")),
        ("json", json.dumps(value)[1:-1]),
    ):
        if form not in seen:
            seen.add(form)
            variants.append((form, enc))
    return tuple(variants)


_PLAIN_TOKEN_RE = re.compile(r"[A-Za-z0-9._~-]*")


# Byte translate table: ASCII letters/digits kept, everything else becomes a space
_RUN_TABLE = bytes(c if (48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122) else 32 for c in range(256))


def _alnum_runs(text: str) -> list[bytes]:
    """Maximal runs of ASCII letters/digits (same as re.findall('[A-Za-z0-9]+'), much faster)."""
    return text.encode("ascii", "replace").translate(_RUN_TABLE).split()


def _ascii_alnum(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9")


class TextIndex:
    """
    Many documents joined into one string so "which documents contain this
    value?" is a handful of C-level str.find calls instead of a Python loop
    over every document. Matching follows contains_value(): a value never
    matches inside a longer alphanumeric run.
    """

    _SEP = "\x00"

    def __init__(self, texts: list[str]):
        self.starts: list[int] = []
        pos = 0
        for t in texts:
            self.starts.append(pos)
            pos += len(t) + 1
        self.big = self._SEP.join(t.replace(self._SEP, " ") for t in texts)
        self.n = len(texts)
        self._texts = texts
        self._inverted: dict[str, list[int]] | None = None

    def _candidate_docs(self, value: str) -> list[int] | None:
        """
        Documents that can possibly contain a boundary match of `value`, or
        None when the shortcut does not apply. Every alphanumeric run inside an
        ASCII value is bounded (by the value's own separators or by the
        boundary rule), so a matching document must contain each run as a
        complete run — an inverted index of runs narrows the scan to the few
        documents that qualify.
        """
        if not value.isascii():
            return None
        runs = set(_alnum_runs(value))
        if not runs:
            return None
        if self._inverted is None:
            inv: dict[bytes, list[int]] = {}
            for i, t in enumerate(self._texts):
                for run in set(_alnum_runs(t)):
                    inv.setdefault(run, []).append(i)
            self._inverted = inv
            self._inverted_sets: dict[bytes, set[int]] = {}
        lists = [self._inverted.get(r) for r in runs]
        if any(l is None for l in lists):
            return []
        if len(lists) == 1:
            return lists[0]
        order = sorted(zip(lists, runs), key=lambda x: len(x[0]))
        others = []
        for _, run in order[1:]:
            st = self._inverted_sets.get(run)
            if st is None:
                st = self._inverted_sets[run] = set(self._inverted[run])
            others.append(st)
        return [d for d in order[0][0] if all(d in o for o in others)]

    def _doc_start(self, doc: int) -> int:
        return self.starts[doc] if doc < self.n else len(self.big)

    def _match_in(self, value: str, lo: int, hi: int) -> bool:
        big = self.big
        check_pre, check_post = value[0].isalnum(), value[-1].isalnum()
        i = big.find(value, lo, hi)
        while i != -1:
            j = i + len(value)
            if not (check_pre and i > 0 and _ascii_alnum(big[i - 1])) and \
                    not (check_post and j < len(big) and _ascii_alnum(big[j])):
                return True
            i = big.find(value, i + 1, hi)
        return False

    def docs(self, value: str, start_doc: int = 0, end_doc: int | None = None,
             first_only: bool = False) -> list[int]:
        """Sorted indices of documents in [start_doc, end_doc) containing `value`."""
        if not value or start_doc >= self.n:
            return []
        end = self.n if end_doc is None else min(end_doc, self.n)
        cands = self._candidate_docs(value)
        if cands is None:
            cands = range(start_doc, end)
        out: list[int] = []
        for d in cands:
            if d < start_doc:
                continue
            if d >= end:
                break
            if self._match_in(value, self.starts[d], self.starts[d] + len(self._texts[d])):
                out.append(d)
                if first_only:
                    break
        return out

    def docs_any_encoding(self, value: str, start_doc: int = 0, end_doc: int | None = None) -> list[int]:
        """Documents containing `value` raw, URL-encoded or JSON-escaped."""
        found: set[int] = set()
        for form, _ in encoded_variants(value):
            found.update(self.docs(form, start_doc, end_doc))
        return sorted(found)


def response_text(req) -> str:
    """Response body plus header values — everything a value can be extracted from."""
    return (req.response_body or "") + "\n" + "\n".join((req.response_headers or {}).values())


def find_encoding(text: str | None, value: str) -> str | None:
    """Return the encoding under which `value` occurs in `text`, or None."""
    if not text:
        return None
    for form, enc in encoded_variants(value):
        if contains_value(text, form):
            return enc
    return None


def request_haystack(req) -> str:
    """All request text that can carry a correlated value: URL, body, headers."""
    parts = [req.url or "", req.body or ""]
    parts += [v for _, v in correlatable_header_items(req.headers)]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def safe_json(text: str | None):
    if not text:
        return None
    t = text.lstrip()
    if not t or t[0] not in "[{":
        return None
    try:
        return json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None


def json_leaves(obj, path: tuple = ()):
    """Yield (path_tuple, value) for every scalar leaf; path items are str keys or int indices."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from json_leaves(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from json_leaves(v, path + (i,))
    elif obj is not None:
        yield path, obj


def scalar_text(v) -> str:
    """Render a JSON scalar the way it appears in request text (true, not True)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def dotted_key(path: tuple) -> str:
    """('items', 3, 'id') -> 'items[3].id' (the engine's historical key format)."""
    out = ""
    for p in path:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += f".{p}" if out else p
    return out


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def jsonpath(path: tuple) -> str:
    """Build a Jayway JSONPath, quoting keys that are not plain identifiers."""
    out = "$"
    for p in path:
        if isinstance(p, int):
            out += f"[{p}]"
        elif _IDENT_RE.match(p):
            out += f".{p}"
        else:
            out += "['" + p.replace("\\", "\\\\").replace("'", "\\'") + "']"
    return out


# Sibling fields that usually identify a list element in a stable way
_IDENTITY_FIELDS = ("name", "code", "sku", "slug", "title", "type", "key", "label", "email", "username")


def smart_jsonpath(root, path: tuple) -> tuple[str, str]:
    """
    JSONPath for a leaf, replacing a fixed list index with a filter on a
    stable sibling field when one exists:
        $.items[3].id  ->  $.items[?(@.name == 'Gold plan')].id
    Returns (jsonpath, note).
    """
    idx_pos = max((i for i, p in enumerate(path) if isinstance(p, int)), default=-1)
    if idx_pos < 0:
        return jsonpath(path), ""
    try:
        parent = root
        for p in path[:idx_pos]:
            parent = parent[p]
        element = parent[path[idx_pos]]
    except (KeyError, IndexError, TypeError):
        return jsonpath(path), ""
    if not isinstance(parent, list) or not isinstance(element, dict):
        return jsonpath(path), ""
    leaf_field = path[idx_pos + 1] if idx_pos + 1 < len(path) else None
    for f in _IDENTITY_FIELDS:
        if f == leaf_field:
            continue
        val = element.get(f)
        if not isinstance(val, str) or not val or len(val) > 80 or "'" in val:
            continue
        # The identifying value must be unique within the list
        if sum(1 for e in parent if isinstance(e, dict) and e.get(f) == val) != 1:
            continue
        prefix = jsonpath(path[:idx_pos])
        suffix = jsonpath(path[idx_pos + 1:])[1:]
        return f"{prefix}[?(@.{f} == '{val}')]{suffix}", f"list item selected by {f}='{val}'"
    return jsonpath(path), ""


# ---------------------------------------------------------------------------
# HTML scanning
# ---------------------------------------------------------------------------

class _HtmlCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag.lower(), {k.lower(): (v or "") for k, v in attrs}))

    handle_startendtag = handle_starttag


@lru_cache(maxsize=64)
def html_elements(text: str | None) -> list[tuple[str, dict[str, str]]]:
    """Parse HTML into a flat list of (tag, attrs). Returns [] for non-HTML text."""
    if not text or "<" not in text:
        return []
    parser = _HtmlCollector()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass
    return parser.elements


def looks_like_html(text: str | None, mime: str | None = None) -> bool:
    if mime and "html" in mime.lower():
        return True
    if not text:
        return False
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<form" in text[:20000].lower()


_SELECTOR_RE = re.compile(r"^([a-zA-Z0-9]*)((?:\[[^\]]+\])*)$")
_ATTR_COND_RE = re.compile(r"\[([^\]=~*^$|]+)(?:([~*^$|]?=)['\"]?([^'\"\]]*)['\"]?)?\]")


def css_select(elements: list[tuple[str, dict[str, str]]], selector: str) -> list[dict[str, str]]:
    """
    Minimal CSS selector support for `tag[attr=value][attr*=part]` — enough to
    mirror the JMeter CSS/JQuery extractor expressions we emit.
    """
    m = _SELECTOR_RE.match(selector.strip())
    if not m:
        return []
    tag = m.group(1).lower()
    conds = _ATTR_COND_RE.findall(m.group(2) or "")
    out = []
    for etag, attrs in elements:
        if tag and etag != tag:
            continue
        ok = True
        for name, op, val in conds:
            have = attrs.get(name.lower())
            if have is None:
                ok = False
            elif op == "=":
                ok = have == val
            elif op == "*=":
                ok = val in have
            elif op == "^=":
                ok = have.startswith(val)
            elif op == "$=":
                ok = have.endswith(val)
            elif op == "~=":
                ok = val in have.split()
            if not ok:
                break
        if ok:
            out.append(attrs)
    return out


def _css_attr_value(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9_.:-]+", s) else '"' + s.replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Left/right boundary inference
# ---------------------------------------------------------------------------

def boundary_extract_all(text: str, lb: str, rb: str) -> list[str]:
    """Mirror of JMeter's BoundaryExtractor: every lb...rb match, in order."""
    out, start = [], 0
    while True:
        li = text.find(lb, start)
        if li < 0:
            break
        ri = text.find(rb, li + len(lb))
        if ri < 0:
            break
        out.append(text[li + len(lb):ri])
        start = ri + len(rb)
    return out


_RB_STOP = set("\"'&;,<>)}] \t\r\n")


# Following text that is safe to add to a right boundary: a few delimiters, then a
# word (e.g. "?newOrderForm=") — never digits, which are usually per-run IDs
_RB_EXTEND_RE = re.compile(r"[^\sA-Za-z0-9]{1,3}[A-Za-z_\-]{1,32}=?")


def _right_boundaries(text: str, end: int) -> list[str]:
    """Right-boundary options for a value ending at `end`, shortest first ([] at end of line)."""
    if end >= len(text) or text[end] in "\r\n":
        return []
    rb = text[end]
    # Extend the right boundary through following delimiter characters so it is distinctive
    j = end + 1
    while j < len(text) and j - end < 2 and text[j] in _RB_STOP and text[j] not in "\r\n":
        rb += text[j]
        j += 1
    out = [rb]
    m = _RB_EXTEND_RE.match(text, end)
    if m and m.end() - end > len(rb):
        out.append(text[end:m.end()])
    return out


def infer_token_regex(text: str, value: str) -> dict | None:
    """
    Regex `left([^\\s"'<>]+?)right` for a token value (no spaces, quotes or markup).
    Unlike a boundary extractor it cannot run across other links or tags, so a
    short left boundary works and a page that changes makes it fail cleanly
    instead of returning a chunk of HTML.
    """
    if not _TOKEN_RE.fullmatch(value):
        return None
    pos = text.find(value)
    if pos < 0:
        return None
    rbs = _right_boundaries(text, pos + len(value))
    line_start = max(text.rfind("\n", 0, pos), text.rfind("\r", 0, pos)) + 1
    best = None
    for lb_len in (8, 12, 16, 24, 32):
        lb_start = max(line_start, pos - lb_len)
        lb = text[lb_start:pos]
        if not lb:
            break
        for rb in rbs:
            regex = jregex_escape(lb) + _TOKEN_GROUP + jregex_escape(rb)
            hits = [m.group(1) for m in re.finditer(regex, text)]
            if value in hits:
                cand = {"regex": regex, "match_no": hits.index(value) + 1, "lb": lb, "rb": rb}
                if cand["match_no"] == 1:
                    return cand
                if best is None or cand["match_no"] < best["match_no"]:
                    best = cand
        if lb_start == line_start:
            break
    return best


def infer_boundaries(text: str, value: str) -> dict | None:
    """
    Find left/right boundaries that extract `value` from `text`. Prefers short,
    single-line boundaries that make `value` the first match: a match number such
    as 12 counts similar links before it, which shifts when the page content
    changes, so the right boundary is extended with the stable text that follows.
    Returns the 1-based match number to use.
    """
    pos = text.find(value)
    if pos < 0:
        return None
    rbs = _right_boundaries(text, pos + len(value)) or [""]
    line_start = max(text.rfind("\n", 0, pos), text.rfind("\r", 0, pos)) + 1
    best = None
    # Grow the left boundary only when no right boundary makes the value unique:
    # text after the value ("?newOrderForm=") is steadier than a neighbouring link
    for lb_len in (8, 12, 16, 24, 32, 48, 64):
        lb_start = max(line_start, pos - lb_len)
        lb = text[lb_start:pos]
        if not lb:
            break
        for rb in rbs:
            if rb:
                matches = boundary_extract_all(text, lb, rb)
            else:
                matches = [text[m.end():m.end() + len(value)] for m in re.finditer(re.escape(lb), text)]
            if value in matches:
                match_no = matches.index(value) + 1
                cand = {"lb": lb, "rb": rb, "match_no": match_no}
                if match_no == 1:
                    return cand
                if best is None or match_no < best["match_no"]:
                    best = cand
        if lb_start == line_start:
            break
    return best


_TOKEN_RE = re.compile(r"[^\s\"'<>]+")
_TOKEN_GROUP = r"([^\s\"'<>]+?)"


def jregex_escape(s: str) -> str:
    """Escape regex metacharacters for Java / Perl5 regex engines."""
    return re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", s)


# ---------------------------------------------------------------------------
# Extractor inference
# ---------------------------------------------------------------------------

def infer_extractor(req, value: str) -> dict | None:
    """
    Work out how JMeter can extract `value` from the recorded response of `req`.
    Every returned spec is verified: running it on the recorded response must
    reproduce `value` exactly.
    """
    for spec in _infer_candidates(req, value):
        if apply_extractor(spec["extractor"], spec["config"], req.response_body, req.response_headers) == value:
            return spec
    return None


def _infer_candidates(req, value: str):
    """
    Candidate extractors for `value`, most specific first (unverified).

    Returns a dict with keys:
      extract_from: "body" | "header"
      extract_key:  human readable locator (JSON key, header name, selector…)
      extractor:    "json" | "css" | "header" | "header_regex" | "boundary" | "regex"
      config:       extractor specific settings
    or None if the value is not in the response.
    """
    if not value:
        return
    body = req.response_body or ""

    # 1. JSON body — exact leaf match
    parsed = safe_json(body)
    if parsed is not None:
        for path, leaf in json_leaves(parsed):
            if scalar_text(leaf) == value:
                jp, note = smart_jsonpath(parsed, path)
                yield {
                    "extract_from": "body", "extract_key": dotted_key(path),
                    "extractor": "json", "config": {"path": jp, "note": note},
                }

    # 2. HTML attribute (hidden input, meta tag, data-* attribute)
    if body and looks_like_html(body, req.response_mime_type):
        for tag, attrs in html_elements(body):
            for attr, aval in attrs.items():
                if aval != value or attr in ("class", "style", "href", "src", "action"):
                    continue
                for ident in ("name", "id", "property", "data-name"):
                    if attrs.get(ident) and ident != attr:
                        sel = f"{tag}[{ident}={_css_attr_value(attrs[ident])}]"
                        yield {
                            "extract_from": "body", "extract_key": sel,
                            "extractor": "css", "config": {"selector": sel, "attr": attr},
                        }

    # 3. Response headers
    for hname, hval in (req.response_headers or {}).items():
        if hval == value:
            yield {
                "extract_from": "header", "extract_key": hname,
                "extractor": "header", "config": {"name": hname},
            }
    for hname, hval in (req.response_headers or {}).items():
        if not contains_value(hval, value):
            continue
        i = hval.find(value)
        # value is a parameter inside the header (e.g. Location: /cb?code=XYZ&state=…)
        nxt = hval[i + len(value):i + len(value) + 1]
        stop = "&#;\\s" + (jregex_escape(nxt) if nxt and nxt not in "&#; " else "")
        pm = re.search(r"([A-Za-z0-9_.\-]+)=$", hval[:i])
        if pm:
            regex = r"[?&#;\s]" + jregex_escape(pm.group(1)) + r"=([^" + stop + r"]+)"
        else:
            lb = hval[max(0, i - 16):i]
            regex = "(?i)" + jregex_escape(hname) + r":\s*" + (".*?" + jregex_escape(lb) if lb else "") + r"([^" + stop + r"]+)"
        yield {
            "extract_from": "header", "extract_key": hname,
            "extractor": "header_regex", "config": {"regex": regex, "header": hname},
        }

    # 4. Plain text / HTML body — left/right boundaries
    if body and value in body:
        # A token (no spaces, quotes or markup) gets a regex that cannot capture
        # anything else: if the page changes, it fails instead of returning HTML
        t = infer_token_regex(body, value)
        if t:
            yield {
                "extract_from": "body", "extract_key": f"{t['lb']}…{t['rb']}",
                "extractor": "regex", "config": {"regex": t["regex"], "match_no": t["match_no"]},
            }
        b = infer_boundaries(body, value)
        if b:
            if b["rb"]:
                yield {
                    "extract_from": "body", "extract_key": f"{b['lb']}…{b['rb']}",
                    "extractor": "boundary", "config": b,
                }
            yield {
                "extract_from": "body", "extract_key": f"{b['lb']}…",
                "extractor": "regex",
                "config": {"regex": jregex_escape(b["lb"]) + r"([^\r\n]+)", "match_no": b["match_no"]},
            }


def expand_truncated(text: str, prefix: str) -> str:
    """LLMs truncate long values; grow a prefix back to the full token found in text."""
    i = text.find(prefix) if text else -1
    if i < 0:
        return prefix
    j = i + len(prefix)
    while j < len(text) and (text[j].isalnum() or text[j] in "-_.~+/="):
        j += 1
    return text[i:j]


# ---------------------------------------------------------------------------
# Request value scanning
# ---------------------------------------------------------------------------

def request_values(req) -> list[tuple[str, str]]:
    """
    Scalar values a request sends, as (location, value) pairs:
    path segments, query params, form fields, JSON leaves, custom header values.
    """
    out: list[tuple[str, str]] = []
    parsed = urlparse(req.url or "")
    for seg in parsed.path.split("/"):
        if seg:
            out.append(("path", seg))
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        out.append((f"query:{k}", v))
    body = req.body or ""
    if body:
        j = safe_json(body)
        if j is not None:
            for path, leaf in json_leaves(j):
                if isinstance(leaf, bool):
                    continue
                out.append((f"json:{dotted_key(path)}", scalar_text(leaf)))
        elif "=" in body and "\n" not in body[:2000]:
            for k, v in parse_qsl(body, keep_blank_values=False):
                out.append((f"form:{k}", v))
    for k, v in correlatable_header_items(req.headers):
        if k.lower() == "authorization":
            parts = v.split(None, 1)
            if len(parts) == 2:
                out.append((f"header:{k}", parts[1]))
        else:
            out.append((f"header:{k}", v))
    return out


# ---------------------------------------------------------------------------
# Extractor simulation (used by replay verification)
# ---------------------------------------------------------------------------

_JP_TOKEN_RE = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_]*)"                         # .key
    r"|\['((?:[^'\\]|\\.)*)'\]"                            # ['key']
    r"|\[(\d+)\]"                                          # [3]
    r"|\[\?\(@\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*'((?:[^'\\]|\\.)*)'\)\]"  # [?(@.f == 'v')]
)


def eval_jsonpath(data, path: str):
    """Evaluate the JSONPath subset this tool emits; returns the first match or None."""
    if not path.startswith("$"):
        return None
    nodes = [data]
    pos = 1
    while pos < len(path):
        m = _JP_TOKEN_RE.match(path, pos)
        if not m:
            return None
        pos = m.end()
        nxt = []
        for node in nodes:
            if m.group(1) is not None or m.group(2) is not None:
                key = m.group(1) if m.group(1) is not None else re.sub(r"\\(.)", r"\1", m.group(2))
                if isinstance(node, dict) and key in node:
                    nxt.append(node[key])
            elif m.group(3) is not None:
                i = int(m.group(3))
                if isinstance(node, list) and i < len(node):
                    nxt.append(node[i])
            else:
                f, v = m.group(4), re.sub(r"\\(.)", r"\1", m.group(5))
                if isinstance(node, list):
                    nxt += [e for e in node if isinstance(e, dict) and scalar_text(e.get(f, object())) == v]
        nodes = nxt
        if not nodes:
            return None
    node = nodes[0]
    return None if isinstance(node, (dict, list)) or node is None else scalar_text(node)


def headers_text(headers: dict[str, str] | None) -> str:
    return "\n".join(f"{k}: {v}" for k, v in (headers or {}).items())


def apply_extractor(kind: str, cfg: dict, body: str | None, headers: dict[str, str] | None) -> str | None:
    """Run an extractor spec against a response the way JMeter would."""
    body = body or ""
    cfg = cfg or {}
    try:
        if kind == "json":
            data = safe_json(body)
            return eval_jsonpath(data, cfg.get("path", "")) if data is not None else None
        if kind == "css":
            hits = css_select(html_elements(body), cfg.get("selector", ""))
            return hits[0].get(cfg.get("attr") or "value") if hits else None
        if kind == "header":
            return header_get(headers or {}, cfg.get("name", ""), None)
        if kind in ("header_regex", "regex"):
            text = headers_text(headers) if kind == "header_regex" else body
            pattern = cfg.get("regex", "")
            matches = [m.group(1) for m in re.finditer(pattern, text)]
            n = int(cfg.get("match_no", 1) or 1)
            if len(matches) < n:
                return None
            v = matches[n - 1]
            if kind == "header_regex" and cfg.get("url_decode") and "%" in v:
                from urllib.parse import unquote
                v = unquote(v)
            return v
        if kind == "boundary":
            hits = boundary_extract_all(body, cfg.get("lb", ""), cfg.get("rb", ""))
            n = int(cfg.get("match_no", 1) or 1)
            return hits[n - 1] if len(hits) >= n else None
        if kind == "jwt_claim":
            for m in re.finditer(r"eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+", body + "\n" + headers_text(headers)):
                import base64
                seg = m.group(1) + "=" * (-len(m.group(1)) % 4)
                try:
                    claims = json.loads(base64.urlsafe_b64decode(seg))
                except Exception:
                    continue
                if isinstance(claims, dict) and cfg.get("claim") in claims:
                    return scalar_text(claims[cfg["claim"]])
            return None
        if kind == "cookie_header":
            for k, v in (headers or {}).items():
                if k.lower() != "set-cookie":
                    continue
                for line in v.split("\n"):
                    first = line.split(";", 1)[0]
                    if first.split("=", 1)[0].strip() == cfg.get("cookie"):
                        val = first.split("=", 1)[1].strip() if "=" in first else ""
                        if cfg.get("url_decode") and "%" in val:
                            from urllib.parse import unquote
                            val = unquote(val)
                        return val
            return None
    except (re.error, KeyError, IndexError, TypeError, ValueError):
        return None
    return None


def candidate_extractors(req, value: str) -> list[dict]:
    """
    Every reasonable way to extract `value` from req's recorded response, most
    specific first. Replay uses this to find one that also works on the live
    response when the first choice fails.
    """
    out: list[dict] = []
    best = infer_extractor(req, value)
    if best:
        out.append(best)
    body = req.response_body or ""
    parsed = safe_json(body)
    if parsed is not None:
        for path, leaf in json_leaves(parsed):
            if scalar_text(leaf) == value:
                out.append({"extract_from": "body", "extract_key": dotted_key(path), "extractor": "json",
                            "config": {"path": jsonpath(path), "note": ""}})
                break
    if body and value in body:
        pos = body.find(value)
        end = pos + len(value)
        line_start = max(body.rfind("\n", 0, pos), body.rfind("\r", 0, pos)) + 1
        for lb_len in (4, 6, 10, 20):
            lb = body[max(line_start, pos - lb_len):pos]
            if not lb:
                continue
            rb = body[end:end + 1] if end < len(body) and body[end] not in "\r\n" else ""
            if rb:
                hits = boundary_extract_all(body, lb, rb)
                if value in hits:
                    out.append({"extract_from": "body", "extract_key": f"{lb}…{rb}", "extractor": "boundary",
                                "config": {"lb": lb, "rb": rb, "match_no": hits.index(value) + 1}})
            # Regex with a character class instead of a literal right boundary
            cls = r"([A-Za-z0-9_\-.~+/=%]+)" if re.fullmatch(r"[A-Za-z0-9_\-.~+/=%]+", value) else r"([^\"'&<>\s]+)"
            regex = jregex_escape(lb) + cls
            try:
                hits = [m.group(1) for m in re.finditer(regex, body)]
            except re.error:
                hits = []
            if value in hits:
                out.append({"extract_from": "body", "extract_key": f"{lb}…", "extractor": "regex",
                            "config": {"regex": regex, "match_no": hits.index(value) + 1}})
    # De-duplicate while keeping order; keep only extractors that reproduce the value
    seen, uniq = set(), []
    for c in out:
        if apply_extractor(c["extractor"], c["config"], body, req.response_headers) != value:
            continue
        key = (c["extractor"], json.dumps(c["config"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq
