"""
JMeter HTTP Proxy Recorder XML parser.

Reads a recording.xml (JMX format) produced by JMeter's HTTP Proxy Recorder
and returns a list of HarRequest objects compatible with the correlation engine
and JMX generator.

Note: recording.xml does NOT contain response bodies or status codes — those
fields are set to None / 0 for every request. AI correlation (Pass 6) is
therefore the primary detection mechanism for this file format.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from projectstack.parser.har_parser import HarRequest, _classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sp(elem: ET.Element, name: str) -> str:
    """Return the text of a <stringProp name="..."> child, or ''."""
    node = elem.find(f"./stringProp[@name='{name}']")
    if node is None:
        return ""
    return (node.text or "").strip()


def _bp(elem: ET.Element, name: str) -> bool:
    """Return the boolean value of a <boolProp name="..."> child."""
    node = elem.find(f"./boolProp[@name='{name}']")
    if node is None:
        return False
    return (node.text or "").strip().lower() == "true"


def _build_url(sampler: ET.Element) -> tuple[str, list[dict[str, str]]]:
    """
    Construct the full URL from HTTPSampler properties and split off query
    parameters into the HarRequest format.

    Returns:
        (url_without_query, query_params_list)
    """
    protocol = _sp(sampler, "HTTPSampler.protocol") or "https"
    domain = _sp(sampler, "HTTPSampler.domain")
    port = _sp(sampler, "HTTPSampler.port")
    path = _sp(sampler, "HTTPSampler.path")

    # Omit default ports so URLs stay clean
    if port and port not in ("80", "443", ""):
        host = f"{domain}:{port}"
    else:
        host = domain

    full = f"{protocol}://{host}{path}"

    # Split query string (JMeter sometimes embeds it in the path)
    parsed = urlsplit(full)
    query_params: list[dict[str, str]] = [
        {"name": k, "value": v}
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return clean_url, query_params


def _extract_headers(hashtree: ET.Element) -> dict[str, str]:
    """
    Extract request headers from a <HeaderManager> element found inside a
    <hashTree> sibling of an HTTPSamplerProxy.
    """
    headers: dict[str, str] = {}
    hm = hashtree.find(".//HeaderManager")
    if hm is None:
        return headers
    for elem in hm.findall(".//elementProp"):
        name = ""
        value = ""
        for sp in elem.findall("stringProp"):
            prop_name = sp.get("name", "")
            if prop_name == "Header.name":
                name = (sp.text or "").strip()
            elif prop_name == "Header.value":
                value = (sp.text or "").strip()
        if name:
            headers[name] = value
    return headers


def _extract_body(
    sampler: ET.Element,
) -> tuple[str | None, str | None]:
    """
    Extract request body and MIME type from an HTTPSamplerProxy element.

    Returns:
        (body_text, mime_type)
    """
    post_body_raw = _bp(sampler, "HTTPSampler.postBodyRaw")
    args_elem = sampler.find("./elementProp[@name='HTTPsampler.Arguments']")
    if args_elem is None:
        return None, None

    arg_elems = args_elem.findall(".//elementProp")

    if post_body_raw:
        # Raw body — stored as the Argument.value of the first (only) arg
        for arg in arg_elems:
            val_node = arg.find("./stringProp[@name='Argument.value']")
            if val_node is not None and val_node.text:
                return val_node.text, None  # MIME inferred from Content-Type header
        return None, None
    else:
        # Form-encoded params
        pairs: list[tuple[str, str]] = []
        for arg in arg_elems:
            name_node = arg.find("./stringProp[@name='Argument.name']")
            val_node = arg.find("./stringProp[@name='Argument.value']")
            name = (name_node.text or "") if name_node is not None else ""
            val = (val_node.text or "") if val_node is not None else ""
            if name:
                pairs.append((name, val))
        if pairs:
            return urlencode(pairs), "application/x-www-form-urlencoded"
        return None, None


# ---------------------------------------------------------------------------
# Sampler discovery
# ---------------------------------------------------------------------------

def _find_http_samplers(root: ET.Element) -> list[ET.Element]:
    """
    Find all HTTP sampler elements regardless of their tag name.

    Tries in order:
      1. HTTPSamplerProxy  — JMeter 3.x+ standard tag
      2. HTTPSampler       — older versions / some configurations
      3. Any element that owns an HTTPSampler.domain stringProp (fallback)
    """
    for tag in ("HTTPSamplerProxy", "HTTPSampler"):
        found = root.findall(f".//{tag}")
        if found:
            return found

    # Generic fallback: any element with the HTTPSampler.domain property
    result: list[ET.Element] = []
    for elem in root.iter():
        if elem.find("./stringProp[@name='HTTPSampler.domain']") is not None:
            result.append(elem)
    return result


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_recording(path: str | Path) -> list[HarRequest]:
    """
    Parse a JMeter recording.xml file and return a list of HarRequest objects.

    The recording.xml format is a JMX test plan produced by JMeter's HTTP
    Proxy Recorder. Each HTTPSamplerProxy element becomes one HarRequest.

    Response-side fields (status_code, response_headers, response_body,
    response_mime_type) are not available in this format and are set to
    0 / {} / None / None respectively.

    Args:
        path: Path to the recording.xml file.

    Returns:
        Ordered list of HarRequest objects.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid XML or has no HTTPSamplerProxy elements.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"recording.xml not found: {path}")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in recording file: {exc}") from exc

    root = tree.getroot()

    # Find all HTTP sampler elements.
    # JMeter recording.xml uses different element names depending on version:
    #   - HTTPSamplerProxy  (JMeter 3.x+ standard)
    #   - HTTPSampler       (older versions / some configurations)
    # Final fallback: any element that has an HTTPSampler.domain property.
    samplers = _find_http_samplers(root)
    if not samplers:
        # Build a helpful list of top-level element tags for diagnosis
        all_tags: list[str] = sorted({e.tag for e in root.iter() if e.tag != root.tag})
        hint = f"Elements found in file: {', '.join(all_tags) or 'none'}"
        raise ValueError(
            f"No HTTP sampler elements found in this XML file. "
            f"Expected a JMeter recording.xml with HTTPSamplerProxy entries. {hint}"
        )

    # Build a parent-map so we can find the sibling hashTree for each sampler
    parent_map: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_map[child] = parent

    requests: list[HarRequest] = []

    for sampler in samplers:
        method = _sp(sampler, "HTTPSampler.method").upper() or "GET"
        url, query_params = _build_url(sampler)
        body, body_mime = _extract_body(sampler)

        # The sibling hashTree (immediately after the sampler in parent's children)
        # contains HeaderManager, ResponseAssertion, etc.
        headers: dict[str, str] = {}
        parent = parent_map.get(sampler)
        if parent is not None:
            children = list(parent)
            try:
                idx = children.index(sampler)
                # hashTree sibling is the element right after the sampler
                if idx + 1 < len(children) and children[idx + 1].tag == "hashTree":
                    headers = _extract_headers(children[idx + 1])
            except ValueError:
                pass

        # Infer MIME type from Content-Type header if body raw
        if body and not body_mime:
            ct = headers.get("Content-Type", "")
            if ct:
                body_mime = ct.split(";")[0].strip()

        requests.append(
            HarRequest(
                method=method,
                url=url,
                headers=headers,
                query_params=query_params,
                body=body if body else None,
                body_mime_type=body_mime,
                status_code=0,        # not available in recording.xml
                response_headers={},  # not available
                response_body=None,   # not available
                response_mime_type=None,
                cookies=[],
                request_type=_classify(method, url, body_mime),
                started_datetime="",  # not available in recording.xml
                raw={},
            )
        )

    return requests
