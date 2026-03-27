"""
JMX file generator.

Takes HarRequest and CorrelatedValue objects and produces a valid
jMeter 5.6 JMX file with:
  - One HTTP Sampler per request
  - Header Manager per sampler
  - JSR223 Groovy PostProcessors to extract correlated values
  - ${VAR_name} substitution in headers and request bodies
  - Configurable ThreadGroup (threads, ramp-up, loops)
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse
from xml.dom import minidom

from projectstack.parser.har_parser import HarRequest
from projectstack.correlator.engine import CorrelatedValue


# Headers that jMeter manages automatically — exclude from Header Manager
SKIP_HEADERS = {
    "content-length", "host", "connection", "accept-encoding",
    "accept-language", "sec-fetch-dest", "sec-fetch-mode",
    "sec-fetch-site", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests",
}


# ---------------------------------------------------------------------------
# XML helper functions
# ---------------------------------------------------------------------------

def _sp(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, "stringProp", name=name)
    el.text = value
    return el


def _bp(parent: ET.Element, name: str, value: bool) -> ET.Element:
    el = ET.SubElement(parent, "boolProp", name=name)
    el.text = "true" if value else "false"
    return el


def _apply_subs(text: str, subs: list[tuple[str, str]]) -> str:
    """Replace actual dynamic values with ${VAR_name} placeholders."""
    for actual, var_name in subs:
        if actual and len(actual) >= 8 and actual in text:
            text = text.replace(actual, f"${{{var_name}}}")
    return text


# ---------------------------------------------------------------------------
# Groovy extractor scripts
# ---------------------------------------------------------------------------

def _groovy_json_extractor(var_name: str, key: str) -> str:
    """Groovy script to extract a value from a JSON response body."""
    parts = [p for p in re.split(r"[\.\[\]]+", key) if p and not p.isdigit()]
    nav = "def val = json"
    for part in parts:
        nav += f'?.get("{part}")'
    return (
        "import groovy.json.JsonSlurper\n"
        "try {\n"
        "    def json = new JsonSlurper().parseText(prev.getResponseDataAsString())\n"
        f"    {nav}\n"
        f'    if (val != null) {{ vars.put("{var_name}", val.toString()) }}\n'
        "} catch(Exception e) { /* non-JSON response, skip */ }\n"
    )


def _groovy_jwt_extractor(var_name: str) -> str:
    """Groovy script to extract a JWT token from a response body."""
    return (
        "def response = prev.getResponseDataAsString()\n"
        "def matcher = response =~ "
        "/eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/\n"
        f'if (matcher.find()) {{ vars.put("{var_name}", matcher.group()) }}\n'
    )


def _groovy_header_extractor(var_name: str, header_name: str) -> str:
    """Groovy script to extract a value from a response header."""
    return (
        f'def hdr = prev.getResponseHeader("{header_name}")\n'
        f'if (hdr != null) {{ vars.put("{var_name}", hdr.getValue()) }}\n'
    )


def _groovy_cookie_extractor(var_name: str, cookie_name: str) -> str:
    """Groovy script to extract a cookie value (via jMeter cookie variable)."""
    return (
        f'def val = vars.get("COOKIE_{cookie_name}")\n'
        f'if (val != null) {{ vars.put("{var_name}", val) }}\n'
    )


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
    ):
        self.requests = requests
        self.correlated_values = correlated_values
        self.test_name = test_name
        self.threads = threads
        self.ramp_up = ramp_up
        self.loops = loops

        # Build per-request substitution map: {req_idx: [(actual_value, var_name)]}
        self._subs: dict[int, list[tuple[str, str]]] = {}
        for cv in correlated_values:
            for req_idx in cv.used_in_requests:
                self._subs.setdefault(req_idx, []).append(
                    (cv.extract_value, cv.var_name)
                )

    def generate(self, output_path: str | Path) -> None:
        """Build and write the JMX file to output_path."""
        root = ET.Element(
            "jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3"
        )
        top_tree = ET.SubElement(root, "hashTree")

        top_tree.append(self._build_test_plan())
        plan_tree = ET.SubElement(top_tree, "hashTree")

        plan_tree.append(self._build_thread_group())
        tg_tree = ET.SubElement(plan_tree, "hashTree")

        for i, req in enumerate(self.requests):
            tg_tree.append(self._build_sampler(i, req))
            sampler_tree = ET.SubElement(tg_tree, "hashTree")

            # Header Manager
            sampler_tree.append(self._build_header_manager(i, req))
            ET.SubElement(sampler_tree, "hashTree")

            # One PostProcessor per correlated value sourced from this response
            for cv in self.correlated_values:
                if cv.source_request_index == i:
                    sampler_tree.append(self._build_extractor(cv))
                    ET.SubElement(sampler_tree, "hashTree")

        self._write(root, output_path)

    # ------------------------------------------------------------------
    # Test Plan
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
    # Thread Group
    # ------------------------------------------------------------------

    def _build_thread_group(self) -> ET.Element:
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
        _bp(loop, "LoopController.continue_forever", False)
        _sp(loop, "LoopController.loops", str(self.loops))
        _sp(tg, "ThreadGroup.num_threads", str(self.threads))
        _sp(tg, "ThreadGroup.ramp_time", str(self.ramp_up))
        _bp(tg, "ThreadGroup.scheduler", False)
        _sp(tg, "ThreadGroup.duration", "")
        _sp(tg, "ThreadGroup.delay", "")
        return tg

    # ------------------------------------------------------------------
    # HTTP Sampler
    # ------------------------------------------------------------------

    def _build_sampler(self, index: int, req: HarRequest) -> ET.Element:
        parsed = urlparse(req.url)
        protocol = parsed.scheme
        domain = parsed.hostname or ""
        port = str(parsed.port) if parsed.port else ""
        path = parsed.path or "/"
        query = parsed.query

        subs = self._subs.get(index, [])
        body = _apply_subs(req.body or "", subs)
        label = f"{req.method} {path}"

        sampler = ET.Element(
            "HTTPSamplerProxy",
            guiclass="HttpTestSampleGui",
            testclass="HTTPSamplerProxy",
            testname=label,
            enabled="true",
        )
        _sp(sampler, "HTTPSampler.domain", domain)
        _sp(sampler, "HTTPSampler.port", port)
        _sp(sampler, "HTTPSampler.protocol", protocol)
        _sp(sampler, "HTTPSampler.path", path + (f"?{query}" if query else ""))
        _sp(sampler, "HTTPSampler.method", req.method)
        _bp(sampler, "HTTPSampler.follow_redirects", True)
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

        if req.method in ("POST", "PUT", "PATCH") and body:
            _bp(sampler, "HTTPSampler.postBodyRaw", True)
            arg = ET.SubElement(
                args_coll, "elementProp", name="", elementType="HTTPArgument"
            )
            _bp(arg, "HTTPArgument.always_encode", False)
            val_el = ET.SubElement(arg, "stringProp", name="Argument.value")
            val_el.text = body
            _sp(arg, "Argument.metadata", "=")
        else:
            _bp(sampler, "HTTPSampler.postBodyRaw", False)
            for qp in req.query_params:
                arg = ET.SubElement(
                    args_coll,
                    "elementProp",
                    name=qp.get("name", ""),
                    elementType="HTTPArgument",
                )
                _bp(arg, "HTTPArgument.always_encode", True)
                _sp(arg, "Argument.name", qp.get("name", ""))
                val = _apply_subs(qp.get("value", ""), subs)
                _sp(arg, "Argument.value", val)
                _sp(arg, "Argument.metadata", "=")

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
            if name.lower() in SKIP_HEADERS:
                continue
            value = _apply_subs(value, subs)
            hdr = ET.SubElement(coll, "elementProp", name=name, elementType="Header")
            _sp(hdr, "Header.name", name)
            _sp(hdr, "Header.value", value)
        return hm

    # ------------------------------------------------------------------
    # PostProcessor extractor
    # ------------------------------------------------------------------

    def _build_extractor(self, cv: CorrelatedValue) -> ET.Element:
        """Build a JSR223 PostProcessor (Groovy) for one correlated value."""
        if cv.detection_pass in ("pass1_jwt", "pass1_bearer"):
            script = _groovy_jwt_extractor(cv.var_name)
        elif cv.extract_from == "header":
            script = _groovy_header_extractor(cv.var_name, cv.extract_key)
        elif cv.extract_from == "cookie":
            script = _groovy_cookie_extractor(cv.var_name, cv.extract_key)
        else:
            script = _groovy_json_extractor(cv.var_name, cv.extract_key)

        pp = ET.Element(
            "JSR223PostProcessor",
            guiclass="TestBeanGUI",
            testclass="JSR223PostProcessor",
            testname=f"Extract {cv.var_name}",
            enabled="true",
        )
        _sp(pp, "scriptLanguage", "groovy")
        _sp(pp, "parameters", "")
        _sp(pp, "filename", "")
        _sp(pp, "cacheKey", "true")
        script_el = ET.SubElement(pp, "stringProp", name="script")
        script_el.text = script
        return pp

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _write(root: ET.Element, output_path: str | Path) -> None:
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        dom = minidom.parseString(xml_str)
        pretty = dom.toprettyxml(indent="  ", encoding=None)
        lines = pretty.splitlines()
        if lines and lines[0].startswith("<?xml"):
            lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
        Path(output_path).write_text("\n".join(lines), encoding="utf-8")


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
) -> None:
    """
    Generate a jMeter JMX file from parsed HAR requests and correlated values.

    Args:
        requests:          Parsed HAR requests.
        correlated_values: Output from run_correlation().
        output_path:       Where to write the .jmx file.
        test_name:         Label shown in jMeter GUI.
        threads:           Virtual user count.
        ramp_up:           Ramp-up period in seconds.
        loops:             Loop iteration count.
    """
    JmxGenerator(
        requests=requests,
        correlated_values=correlated_values,
        test_name=test_name,
        threads=threads,
        ramp_up=ramp_up,
        loops=loops,
    ).generate(output_path)
