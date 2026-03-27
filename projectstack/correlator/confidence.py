"""
Correlation confidence scorer.

Assigns a 0-100 score and a label (High / Medium / Low) to each
CorrelatedValue based on how likely it is to be a real dynamic value
that needs parameterisation.
"""

import math
import re
from projectstack.correlator.engine import CorrelatedValue


# Key names that strongly suggest a dynamic value
_TOKEN_KEYWORDS = re.compile(
    r"token|secret|key|auth|session|bearer|jwt|access|refresh|csrf|nonce|otp",
    re.IGNORECASE,
)
_ID_KEYWORDS = re.compile(r"id$|_id|uuid|guid", re.IGNORECASE)


def _entropy(value: str) -> float:
    """Shannon entropy of a string — higher means more random-looking."""
    if not value:
        return 0.0
    freq = {}
    for ch in value:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def score(cv: CorrelatedValue) -> int:
    """
    Return a confidence score 0-100 for a CorrelatedValue.

    Scoring breakdown:
      Base score from detection pass        (40-80)
      +bonus for token/auth key name        (up to +10)
      +bonus for high-entropy value         (up to +8)
      +bonus for number of uses             (up to +10)
      +bonus for known patterns (JWT, UUID) (up to +5)
    """
    # --- Base score by detection pass ---
    base = {
        "pass1_jwt":    85,
        "pass1_bearer": 88,
        "pass2_json":   65,
        "pass3_header": 70,
        "pass3_cookie": 62,
        "pass4_text":   55,
        "pass6_ollama": 75,
    }.get(cv.detection_pass, 50)

    bonus = 0

    # Bonus: key name suggests a token or secret
    if _TOKEN_KEYWORDS.search(cv.extract_key):
        bonus += 10
    elif _ID_KEYWORDS.search(cv.extract_key):
        bonus += 5

    # Bonus: high-entropy value (random-looking)
    ent = _entropy(cv.extract_value)
    if ent > 4.5:
        bonus += 8
    elif ent > 3.5:
        bonus += 4

    # Bonus: used in multiple subsequent requests
    uses = len(cv.used_in_requests)
    bonus += min(uses * 3, 10)

    # Bonus: looks like a UUID
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        cv.extract_value,
        re.IGNORECASE,
    ):
        bonus += 5

    return min(base + bonus, 99)


def label(score_val: int) -> str:
    if score_val >= 75:
        return "High"
    if score_val >= 50:
        return "Medium"
    return "Low"


def annotate(correlated_values: list[CorrelatedValue]) -> list[dict]:
    """
    Return a list of dicts, each containing the CorrelatedValue plus
    'confidence_score' and 'confidence_label' keys.
    """
    result = []
    for cv in correlated_values:
        s = score(cv)
        result.append({
            "value": cv,
            "confidence_score": s,
            "confidence_label": label(s),
        })
    return result
