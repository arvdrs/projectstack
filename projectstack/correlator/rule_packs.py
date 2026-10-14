"""
Correlation rule packs.

A rule pack is a JSON file describing how to extract framework-specific
dynamic values (ASP.NET ViewState, Spring CSRF, OAuth codes …). Built-in
packs ship in correlator/rules/; users can add their own packs to
~/.projectstack/rules/ (or through the API), and share them as files.

Rule schema:
    {"id": "...", "name": "...", "var": "C_NAME", "type": "css" | "regex" | "json" | "boundary",
     "selector": "input[name=x]", "attr": "value",          # css
     "regex": "x=([^&]+)", "where": "body" | "headers",      # regex (group 1 is the value)
     "path": "$.token",                                      # json
     "lb": "...", "rb": "..."}                               # boundary
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from projectstack.correlator.matching import (
    boundary_extract_all, css_select, html_elements, safe_json,
)

BUILTIN_DIR = Path(__file__).parent / "rules"
USER_DIR = Path.home() / ".projectstack" / "rules"

_VALID_TYPES = {"css", "regex", "json", "boundary"}


def validate_pack(pack: dict) -> list[str]:
    """Return a list of problems with a rule pack (empty when valid)."""
    errors = []
    if not isinstance(pack, dict):
        return ["Pack must be a JSON object"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(pack.get("id", ""))):
        errors.append("Pack 'id' must be lowercase letters, digits, '-' or '_'")
    rules = pack.get("rules")
    if not isinstance(rules, list) or not rules:
        return errors + ["Pack needs a non-empty 'rules' list"]
    for i, r in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: must be an object")
            continue
        t = r.get("type")
        if t not in _VALID_TYPES:
            errors.append(f"{where}: type must be one of {sorted(_VALID_TYPES)}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(r.get("var", ""))):
            errors.append(f"{where}: 'var' must be a valid JMeter variable name")
        if t == "css" and not r.get("selector"):
            errors.append(f"{where}: css rules need 'selector'")
        if t == "json" and not str(r.get("path", "")).startswith("$"):
            errors.append(f"{where}: json rules need a 'path' starting with '$'")
        if t == "boundary" and not (r.get("lb") and r.get("rb")):
            errors.append(f"{where}: boundary rules need 'lb' and 'rb'")
        if t == "regex":
            try:
                if re.compile(r.get("regex", "")).groups < 1:
                    errors.append(f"{where}: regex needs a capture group")
            except re.error as exc:
                errors.append(f"{where}: invalid regex ({exc})")
    return errors


def load_packs() -> list[dict]:
    """All rule packs: built-in first, then user packs (a user pack may override a built-in id)."""
    packs: dict[str, dict] = {}
    for directory, source in ((BUILTIN_DIR, "builtin"), (USER_DIR, "user")):
        if not directory.exists():
            continue
        for f in sorted(directory.glob("*.json")):
            try:
                pack = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if validate_pack(pack):
                continue
            pack["source"] = source
            packs[pack["id"]] = pack
    return list(packs.values())


def save_user_pack(pack: dict) -> Path:
    errors = validate_pack(pack)
    if errors:
        raise ValueError("; ".join(errors))
    USER_DIR.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in pack.items() if k != "source"}
    path = USER_DIR / f"{pack['id']}.json"
    path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return path


def delete_user_pack(pack_id: str) -> bool:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pack_id):
        return False
    path = USER_DIR / f"{pack_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _headers_text(req) -> str:
    return "\n".join(f"{k}: {v}" for k, v in (req.response_headers or {}).items())


def apply_rule(rule: dict, req) -> str | None:
    """Run one rule against a recorded response; return the extracted value or None."""
    t = rule.get("type")
    body = req.response_body or ""
    try:
        if t == "css":
            hits = css_select(html_elements(body), rule["selector"])
            if hits:
                return hits[0].get(rule.get("attr") or "value") or None
        elif t == "regex":
            text = _headers_text(req) if rule.get("where") == "headers" else body
            m = re.search(rule["regex"], text)
            if m:
                return m.group(1) or None
        elif t == "json":
            data = safe_json(body)
            if data is None:
                return None
            node = data
            for part in re.findall(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]|\['([^']+)'\]", rule["path"][1:]):
                key = part[0] or part[2]
                node = node[int(part[1])] if part[1] else node.get(key) if isinstance(node, dict) else None
                if node is None:
                    return None
            return None if isinstance(node, (dict, list)) else str(node)
        elif t == "boundary":
            hits = boundary_extract_all(body, rule["lb"], rule["rb"])
            if hits:
                return hits[0] or None
    except (KeyError, IndexError, TypeError, re.error):
        return None
    return None


def rule_extractor(rule: dict) -> dict:
    """Translate a rule into the extractor spec used by the JMX generator."""
    t = rule["type"]
    if t == "css":
        return {"extractor": "css", "config": {"selector": rule["selector"], "attr": rule.get("attr") or "value"},
                "extract_from": "body", "extract_key": rule["selector"]}
    if t == "json":
        return {"extractor": "json", "config": {"path": rule["path"]}, "extract_from": "body",
                "extract_key": rule["path"]}
    if t == "boundary":
        return {"extractor": "boundary", "config": {"lb": rule["lb"], "rb": rule["rb"], "match_no": 1},
                "extract_from": "body", "extract_key": f"{rule['lb']}…{rule['rb']}"}
    headers = rule.get("where") == "headers"
    return {"extractor": "header_regex" if headers else "regex",
            "config": {"regex": rule["regex"], "match_no": 1},
            "extract_from": "header" if headers else "body", "extract_key": rule["regex"]}
