"""
JMX Validator — Phase 6.

Validates the structure of a generated JMX file before it is handed to the user.
Catches the two most common JMeter load errors:

  1. StringProperty cast error  — Arguments.arguments must be <collectionProp>
  2. HashTree cast error        — every test element must be followed by exactly
                                   one <hashTree>, and no <hashTree> may be orphaned

Also checks:
  3. Variable resolution  — every ${C_VAR} used in requests has an extractor
  4. No duplicate C_* variable names

Returns a list of ValidationError dataclasses.  An empty list means the JMX
is structurally valid.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    code: str           # e.g. "ORPHAN_HASHTREE", "MISSING_COLLECTIONPROP"
    message: str
    xpath: str = ""     # approximate location in the tree


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{(C_[A-Z0-9_]+)\}")


def _iter_elements(root: ET.Element):
    """Yield every element in document order."""
    yield root
    for child in root:
        yield from _iter_elements(child)


def _children(el: ET.Element) -> list[ET.Element]:
    return list(el)


def _tag(el: ET.Element) -> str:
    return el.tag


# ---------------------------------------------------------------------------
# Check 1: hashTree placement
# ---------------------------------------------------------------------------

def _check_hashtree_structure(root: ET.Element) -> list[ValidationError]:
    """
    Walk every <hashTree> in the document and verify its children are
    well-formed: each test element is immediately followed by a <hashTree>,
    and no <hashTree> appears at index 0 or after another <hashTree>.

    The rule only applies inside <hashTree> containers — not at the
    jmeterTestPlan root level, which owns exactly one top-level <hashTree>.
    """
    errors: list[ValidationError] = []

    # Leaf-property tags that live inside test elements — not test elements themselves
    _PROP_TAGS = {
        "stringProp", "boolProp", "intProp", "longProp",
        "collectionProp", "elementProp", "objProp",
    }

    def _walk_hashtree(ht: ET.Element, path: str) -> None:
        """Validate the immediate children of one <hashTree>."""
        children = _children(ht)
        for i, child in enumerate(children):
            child_path = f"{path}/{child.tag}[{i}]"
            if child.tag == "hashTree":
                if i == 0:
                    errors.append(ValidationError(
                        code="ORPHAN_HASHTREE",
                        message="<hashTree> has no preceding element (orphaned)",
                        xpath=child_path,
                    ))
                elif children[i - 1].tag == "hashTree":
                    errors.append(ValidationError(
                        code="ORPHAN_HASHTREE",
                        message="Consecutive <hashTree> elements — a test element is missing between them",
                        xpath=child_path,
                    ))
                else:
                    # Valid hashTree — recurse into it
                    _walk_hashtree(child, child_path)
            elif child.tag not in _PROP_TAGS:
                # Test element — must be followed by a hashTree
                if i + 1 >= len(children) or children[i + 1].tag != "hashTree":
                    errors.append(ValidationError(
                        code="MISSING_HASHTREE",
                        message=(
                            f"<{child.tag} testname='{child.get('testname', '')}'>  "
                            f"is not followed by a <hashTree>"
                        ),
                        xpath=child_path,
                    ))

    # Validate that the root has exactly one top-level hashTree
    top_children = _children(root)
    if not top_children or top_children[0].tag != "hashTree":
        errors.append(ValidationError(
            code="MISSING_TOP_HASHTREE",
            message="<jmeterTestPlan> must have a <hashTree> as its first child",
            xpath="/jmeterTestPlan",
        ))
        return errors

    # Validate the top-level hashTree and every nested one
    _walk_hashtree(top_children[0], "/jmeterTestPlan/hashTree")
    return errors


# ---------------------------------------------------------------------------
# Check 2: collectionProp for Arguments.arguments
# ---------------------------------------------------------------------------

def _check_collection_props(root: ET.Element) -> list[ValidationError]:
    """Ensure all Arguments.arguments props are collectionProp, not stringProp."""
    errors: list[ValidationError] = []
    for el in _iter_elements(root):
        if el.tag == "stringProp" and el.get("name") == "Arguments.arguments":
            errors.append(ValidationError(
                code="STRINGPROP_ARGUMENTS",
                message="<stringProp name='Arguments.arguments'> found — "
                        "must be <collectionProp name='Arguments.arguments'>. "
                        "This causes ClassCastException in JMeter.",
                xpath=f"parent={el.tag}",
            ))
    return errors


# ---------------------------------------------------------------------------
# Check 3: Variable resolution
# ---------------------------------------------------------------------------

def _check_variable_resolution(root: ET.Element) -> list[ValidationError]:
    """
    Collect all ${C_VAR} references and all extractor variable names.
    Report any C_VAR that is used but never extracted.
    """
    errors: list[ValidationError] = []

    extracted: set[str] = set()
    used: set[str] = set()

    for el in _iter_elements(root):
        text = el.text or ""
        # Extractor declarations
        if el.tag in ("JSONPostProcessor",):
            ref = el.find("stringProp[@name='JSONPostProcessor.referenceNames']")
            ref_text = (ref.text or "").strip() if ref is not None else ""
            if ref_text:
                extracted.add(ref_text)
        if el.tag == "JSR223PostProcessor":
            # Extract variable names from vars.put("C_VAR", ...) in Groovy scripts
            script_el = el.find("stringProp[@name='script']")
            script_text = script_el.text or "" if script_el is not None else ""
            for m in re.finditer(r'vars\.put\("(C_[A-Z0-9_]+)"', script_text):
                extracted.add(m.group(1))

        # Usage references in any text or attribute value
        for var in _VAR_RE.findall(text):
            used.add(var)
        for attr_val in list(getattr(el, "attrib", {}).values()):
            for var in _VAR_RE.findall(str(attr_val)):
                used.add(var)

    unresolved = used - extracted
    for var in sorted(unresolved):
        errors.append(ValidationError(
            code="UNRESOLVED_VARIABLE",
            message=f"${{{var}}} is used in a request but no extractor defines it",
            xpath="",
        ))

    return errors


# ---------------------------------------------------------------------------
# Check 4: Duplicate variable names
# ---------------------------------------------------------------------------

def _check_duplicate_variables(root: ET.Element) -> list[ValidationError]:
    """Report any C_* variable extracted more than once."""
    errors: list[ValidationError] = []
    seen: dict[str, int] = {}

    for el in _iter_elements(root):
        if el.tag == "JSONPostProcessor":
            ref = el.find("stringProp[@name='JSONPostProcessor.referenceNames']")
            ref_text = (ref.text or "").strip() if ref is not None else ""
            if ref_text:
                seen[ref_text] = seen.get(ref_text, 0) + 1
        if el.tag == "JSR223PostProcessor":
            script_el = el.find("stringProp[@name='script']")
            script_text = script_el.text or "" if script_el is not None else ""
            for m in re.finditer(r'vars\.put\("(C_[A-Z0-9_]+)"', script_text):
                vname = m.group(1)
                seen[vname] = seen.get(vname, 0) + 1

    for name, count in seen.items():
        if count > 1:
            errors.append(ValidationError(
                code="DUPLICATE_VARIABLE",
                message=f"{name} is defined by {count} extractors — "
                        "later definitions will silently overwrite earlier ones",
                xpath="",
            ))

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_jmx(path: str | Path) -> list[ValidationError]:
    """
    Parse a JMX file and return a list of ValidationError objects.

    An empty list means the file is structurally valid.

    Args:
        path: Path to the .jmx file.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError:        If the file is not valid XML.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JMX file not found: {path}")

    try:
        tree = ET.parse(str(path))
    except ET.ParseError as exc:
        raise ValueError(f"JMX file is not valid XML: {exc}") from exc

    root = tree.getroot()
    errors: list[ValidationError] = []

    errors += _check_collection_props(root)
    errors += _check_hashtree_structure(root)
    errors += _check_variable_resolution(root)
    errors += _check_duplicate_variables(root)

    return errors


def validate_and_report(path: str | Path) -> bool:
    """
    Validate a JMX file and print a human-readable report.

    Returns True if valid, False if errors were found.
    """
    try:
        errors = validate_jmx(path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return False

    if not errors:
        print(f"✓ {path}: JMX structure is valid.")
        return True

    print(f"✗ {path}: {len(errors)} validation error(s) found:")
    for e in errors:
        loc = f"  at {e.xpath}" if e.xpath else ""
        print(f"  [{e.code}] {e.message}{loc}")
    return False
