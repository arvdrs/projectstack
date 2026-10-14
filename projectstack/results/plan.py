"""
What a JMeter test plan (JMX) says about correlation: which variables each
sampler sends, and which extractor (on which sampler) sets each variable.

The correlation health check uses it to map a failing label back to the
extractor most likely responsible. Any JMX works, not only ones ProjectStack
generated: Regular Expression, JSON, Boundary, CSS/HTML and XPath extractors
and JSR223 post-processors that call vars.put(…) are recognised.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_PUT_RE = re.compile(r"""vars\.put\(\s*["']([A-Za-z_][A-Za-z0-9_.-]*)["']""")
_SUB_RE = re.compile(r"^(.*)-\d+$")

# element tag → (kind, refname property, expression properties, default property)
_EXTRACTORS = {
    "RegexExtractor": ("regex", "RegexExtractor.refname", ("RegexExtractor.regex",), "RegexExtractor.default"),
    "JSONPostProcessor": ("json", "JSONPostProcessor.referenceNames", ("JSONPostProcessor.jsonPathExprs",),
                          "JSONPostProcessor.defaultValues"),
    "BoundaryExtractor": ("boundary", "BoundaryExtractor.refname",
                          ("BoundaryExtractor.lboundary", "BoundaryExtractor.rboundary"), "BoundaryExtractor.default"),
    "HtmlExtractor": ("css", "HtmlExtractor.refname", ("HtmlExtractor.expr", "HtmlExtractor.attribute"),
                      "HtmlExtractor.default"),
    "XPath2Extractor": ("xpath", "XPathExtractor2.refname", ("XPathExtractor2.xpathQuery",),
                        "XPathExtractor2.default"),
    "XPathExtractor": ("xpath", "XPathExtractor.refname", ("XPathExtractor.xpathQuery",), "XPathExtractor.default"),
}


class PlanError(ValueError):
    pass


@dataclass
class Extractor:
    var: str
    kind: str              # regex | json | boundary | css | xpath | groovy
    expression: str
    default: str
    source: str            # label of the sampler it is attached to
    name: str              # the element's name in the plan

    def describe(self) -> str:
        what = {"regex": "Regular Expression Extractor", "json": "JSON Extractor", "boundary": "Boundary Extractor",
                "css": "CSS Selector Extractor", "xpath": "XPath Extractor", "groovy": "JSR223 script"}[self.kind]
        expr = f" `{self.expression}`" if self.expression and self.kind != "groovy" else ""
        return f"{what} “{self.name}”{expr} on “{self.source}”"


@dataclass
class Plan:
    path: str
    samplers: dict[str, set[str]] = field(default_factory=dict)          # label → variables it sends
    extractors: dict[str, list[Extractor]] = field(default_factory=dict)  # variable → extractors that set it
    transactions: dict[str, list[str]] = field(default_factory=dict)     # transaction label → sampler labels
    order: list[str] = field(default_factory=list)                        # sampler labels in plan order

    def vars_sent(self, label: str) -> set[str]:
        """Correlated variables a label sends (a sub-result counts as its parent; a transaction as its samplers)."""
        if label in self.samplers:
            used = self.samplers[label]
        elif label in self.transactions:
            used = set().union(*(self.samplers.get(s, set()) for s in self.transactions[label]))
        else:
            m = _SUB_RE.match(label)
            used = self.samplers.get(m.group(1), set()) if m else set()
        return {v for v in used if v in self.extractors}


def _prop(el: ET.Element, name: str) -> str:
    node = el.find(f"stringProp[@name='{name}']")
    return (node.text or "") if node is not None else ""


def _pairs(tree: ET.Element) -> Iterator[tuple[ET.Element, ET.Element | None]]:
    children = list(tree)
    i = 0
    while i < len(children):
        el = children[i]
        sub = children[i + 1] if i + 1 < len(children) and children[i + 1].tag == "hashTree" else None
        yield el, sub
        i += 2 if sub is not None else 1


def load_plan(path: str | Path) -> Plan:
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise PlanError(f"Cannot read {path}: {exc}") from exc
    if root.tag != "jmeterTestPlan":
        raise PlanError(f"{path} is not a JMeter test plan (.jmx)")
    plan = Plan(str(path))
    top = root.find("hashTree")
    if top is not None:
        _walk(top, plan, [])
    return plan


def _walk(tree: ET.Element, plan: Plan, txns: list[str]) -> None:
    for el, sub in _pairs(tree):
        if el.get("enabled") == "false":
            continue
        name = el.get("testname") or el.tag
        if el.tag == "TransactionController":
            plan.transactions.setdefault(name, [])
            if sub is not None:
                _walk(sub, plan, txns + [name])
            continue
        if el.tag.endswith("Sampler") or el.tag.endswith("SamplerProxy"):
            _sampler(el, sub, plan, txns)
            continue
        if sub is not None:
            _walk(sub, plan, txns)


def _sampler(el: ET.Element, sub: ET.Element | None, plan: Plan, txns: list[str]) -> None:
    label = el.get("testname") or el.tag
    sent = ET.tostring(el, encoding="unicode")
    children = list(_pairs(sub)) if sub is not None else []
    for child, _ in children:
        if child.tag == "HeaderManager" and child.get("enabled") != "false":
            sent += ET.tostring(child, encoding="unicode")
    plan.samplers.setdefault(label, set()).update(v for v in _VAR_RE.findall(sent) if not v.startswith("__"))
    plan.order.append(label)
    for t in txns:
        plan.transactions[t].append(label)
    for child, _ in children:
        if child.get("enabled") == "false":
            continue
        spec = _EXTRACTORS.get(child.tag)
        if spec:
            kind, ref_prop, expr_props, default_prop = spec
            refs = [r.strip() for r in _prop(child, ref_prop).split(";") if r.strip()]
            exprs = _prop(child, expr_props[0]).split(";") if kind == "json" else [
                " … ".join(_prop(child, p) for p in expr_props if _prop(child, p))]
            defaults = _prop(child, default_prop).split(";") if kind == "json" else [_prop(child, default_prop)]
            for i, ref in enumerate(refs):
                plan.extractors.setdefault(ref, []).append(Extractor(
                    ref, kind, (exprs[i] if i < len(exprs) else exprs[-1]).strip(),
                    (defaults[i] if i < len(defaults) else defaults[-1]).strip(), label,
                    child.get("testname") or child.tag))
        elif child.tag == "JSR223PostProcessor":
            for ref in dict.fromkeys(_PUT_RE.findall(_prop(child, "script"))):
                plan.extractors.setdefault(ref, []).append(
                    Extractor(ref, "groovy", "", "", label, child.get("testname") or child.tag))
