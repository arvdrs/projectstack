"""
Transaction Grouper — Phase 3.

Groups a flat list of HarRequest objects into logical business transactions
(TransactionController blocks in JMeter) using four heuristics applied in order:

  1. Time gap  — >3 s between consecutive requests marks a new transaction
  2. URL module — first two path segments change (e.g. /auth/ → /mdm/)
  3. Keywords   — endpoints with verbs like Login, Token, Search, Create, …
  4. Repeated   — same endpoint called repeatedly (e.g. SaveWizard step 2, 3, 4)

Returns a list of Transaction objects, each with a TC_nn_Name label and the
indices of the requests that belong to it.
"""

import re
from datetime import datetime, timezone
from typing import Optional, cast

from projectstack.parser.har_parser import HarRequest
from projectstack.generator.jmx_generator import Transaction


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIME_GAP_SECONDS = 3.0

# Boundary keywords — an endpoint containing any of these starts a new TC
BOUNDARY_KEYWORDS = re.compile(
    r"(login|sign[-_]?in|logout|sign[-_]?out|"
    r"token|connect|auth|"
    r"search|query|find|filter|"
    r"create|new|add|insert|"
    r"start|begin|init|initiali[sz]e|"
    r"save|update|edit|modify|patch|"
    r"submit|send|publish|"
    r"execute|run|process|"
    r"upload|import|"
    r"approve|reject|authoriz|"
    r"delete|remove|cancel)",
    re.IGNORECASE,
)

# Path segments to ignore when comparing module prefixes
_IGNORE_SEGMENTS = {"api", "v1", "v2", "v3", "rest", "service", "services"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(iso: str) -> Optional[float]:
    """Parse ISO 8601 timestamp to a Unix float. Returns None on failure."""
    if not iso:
        return None
    try:
        # Python 3.11+ handles Z, older versions need replacement
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _module_key(url: str) -> str:
    """
    Return the first meaningful path segment (module) of a URL path.
    E.g. https://host/api/v1/auth/token → 'auth'
         https://host/mdm/users/123     → 'mdm'
    """
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    segments = [s for s in path.split("/") if s and s.lower() not in _IGNORE_SEGMENTS]
    return segments[0].lower() if segments else ""


def _endpoint_stem(url: str) -> str:
    """Return the last non-numeric path segment (e.g. 'SaveWizard')."""
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    segments = path.split("/")
    for seg in reversed(segments):
        if seg and not seg.isdigit() and not re.match(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", seg, re.I
        ):
            return seg
    return path


def _tc_name(index: int, boundary_reason: str, url: str) -> str:
    """Build a TC_nn_BusinessAction label."""
    stem = _endpoint_stem(url)
    # Capitalise each word
    words = re.sub(r"[^a-zA-Z0-9]+", " ", stem).split()
    action = "".join(w.capitalize() for w in words) if words else "Request"
    return f"TC_{index:02d}_{action}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_transaction_name(name: str) -> str:
    """
    Name as it appears after the generator's TC_nn_ prefix: numbering added by
    the auto-grouper or an AI ("TC_03_Login", "T03_Login") is dropped and
    anything outside [A-Za-z0-9] becomes "_" ("Add to cart" → "Add_to_cart").
    """
    s = re.sub(r"^(?:TC_?\d+|T\d+)_", "", (name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def transaction_names(order: list[int], requests: list[HarRequest]) -> dict[str, str]:
    """
    Auto-group the requests in `order` (original indices, in execution order)
    and return {str(index): transaction name}. Names are unique, so two groups
    the heuristics split stay separate controllers even when they share an endpoint.
    """
    groups = group_transactions([requests[i] for i in order])
    names: dict[str, str] = {}
    used: dict[str, int] = {}
    for tc in groups:
        base = clean_transaction_name(tc.name) or "Flow"
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base}_{used[base]}"
        for j in tc.request_indices:
            names[str(order[j])] = name
    return names


def group_transactions(requests: list[HarRequest]) -> list[Transaction]:
    """
    Group requests into Transaction objects.

    Args:
        requests: Ordered list of parsed HAR requests.

    Returns:
        List of Transaction objects, each containing a name and request_indices.
    """
    if not requests:
        return []

    transactions: list[Transaction] = []
    current_indices: list[int] = [0]
    prev_ts = _parse_ts(requests[0].started_datetime)
    prev_module = _module_key(requests[0].url)

    # Track repeated endpoints: endpoint_stem → list of request indices
    endpoint_occurrences: dict[str, list[int]] = {}
    endpoint_occurrences[_endpoint_stem(requests[0].url)] = [0]

    def _flush() -> None:
        """Close the current transaction; main loop handles adding the next index."""
        if not current_indices:
            return
        tc_idx = len(transactions) + 1
        name = _tc_name(tc_idx, "", requests[current_indices[0]].url)
        transactions.append(Transaction(name=name, request_indices=list(current_indices)))
        current_indices.clear()

    for i in range(1, len(requests)):
        req = requests[i]
        curr_ts = _parse_ts(req.started_datetime)
        curr_module = _module_key(req.url)
        stem = _endpoint_stem(req.url)

        new_tc = False
        reason = ""

        # Heuristic 1: Time gap
        if prev_ts is not None and curr_ts is not None:
            gap = cast(float, curr_ts) - cast(float, prev_ts)
            if gap > TIME_GAP_SECONDS:
                new_tc = True
                reason = f"time_gap={gap:.1f}s"

        # Heuristic 2: URL module change
        if not new_tc and curr_module and prev_module and curr_module != prev_module:
            new_tc = True
            reason = f"module_change={prev_module}→{curr_module}"

        # Heuristic 3: Boundary keyword in URL
        if not new_tc and BOUNDARY_KEYWORDS.search(stem):
            new_tc = True
            reason = f"keyword={stem}"

        if new_tc:
            _flush()

        current_indices.append(i)

        # Track repeated endpoints (for later naming, not splitting)
        endpoint_occurrences.setdefault(stem, []).append(i)

        prev_ts = curr_ts if curr_ts is not None else prev_ts
        prev_module = curr_module if curr_module else prev_module

    # Flush last transaction
    if current_indices:
        tc_idx = len(transactions) + 1
        name = _tc_name(tc_idx, "", requests[current_indices[0]].url)
        transactions.append(Transaction(name=name, request_indices=list(current_indices)))

    # Merge single-request TCs that represent repeated calls to the same endpoint
    # into the previous TC (avoids lots of TC_nn_SaveWizard groups)
    transactions = _merge_repeated(transactions, requests)

    return transactions


def _merge_repeated(
    transactions: list[Transaction],
    requests: list[HarRequest],
) -> list[Transaction]:
    """
    Merge consecutive TCs that all resolve to the same endpoint stem.
    E.g. TC_05_SaveWizard, TC_06_SaveWizard, TC_07_SaveWizard → TC_05_SaveWizard_Steps
    """
    if len(transactions) <= 1:
        return transactions

    merged: list[Transaction] = []
    i = 0
    while i < len(transactions):
        tc = transactions[i]
        if len(tc.request_indices) == 1:
            stem = _endpoint_stem(requests[tc.request_indices[0]].url)
            # Look ahead: collect consecutive single-request TCs with same stem
            group_indices = list(tc.request_indices)
            j = i + 1
            while j < len(transactions) and len(transactions[j].request_indices) == 1:
                if _endpoint_stem(requests[transactions[j].request_indices[0]].url) == stem:
                    group_indices.extend(transactions[j].request_indices)
                    j += 1
                else:
                    break
            if j > i + 1:
                # More than one consecutive same-stem TC → merge
                tc_idx = len(merged) + 1
                name = f"TC_{tc_idx:02d}_{stem.capitalize()}_Steps"
                merged.append(Transaction(name=name, request_indices=group_indices))
                i = j
                continue
        # Re-number to avoid gaps
        tc_idx = len(merged) + 1
        old_name = tc.name
        # Replace the numeric portion
        new_name = re.sub(r"^TC_\d+_", f"TC_{tc_idx:02d}_", old_name)
        merged.append(Transaction(name=new_name, request_indices=tc.request_indices))
        i += 1

    return merged
