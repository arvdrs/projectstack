"""
Number check for AI-written report text: every number the narrative states must
come from findings.json.

The allowed values are every number in the document, numbers inside its own
texts (finding titles, notes), and a few values derived from them — window
lengths, before/after changes and ratios, SLA overshoots, list lengths. A number
in the narrative matches when one of those values, possibly converted (ms → s,
s → min or h, thousands → "k", fraction → %), rounds to the number as written.
Round figures may be approximate within 5% ("about 1,100 ms" for 1,066 ms).

Before checking, label names, the run id, tag and scenario are taken out of the
text, so "Step 3 /app/page/3" or "build-119" are not mistaken for figures.
Clock times (09:17 UTC) and offsets (t+17m59s) are checked against the run's
timestamps.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

ALWAYS_ALLOWED = {0, 1, 2, 3, 4, 5, 50, 90, 95, 99, 100}     # small counts, percentile names, 100%
_FACTORS = (1, 1 / 1000, 1 / 60, 1 / 3600, 1 / 60_000, 1 / 1_000_000, 100)   # ×100 only turns fractions into %

_CLOCK_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Offsets and durations: t+17m59s, t+18m, 41m 59s, 1h 05m — read as seconds
_OFFSET_RES = (
    re.compile(r"\bt\s*\+\s*(?:(?P<h>\d+)\s*h\s*)?(?P<m>\d+)\s*m(?:in)?(?:\s*(?P<s>\d{1,2})\s*s)?\b"),
    re.compile(r"\b(?:(?P<h>\d+)\s*h\s*)?(?P<m>\d+)\s*m\s*(?P<s>\d{1,2})\s*s\b"),
    re.compile(r"\b(?P<h>\d+)\s*h\s*(?P<m>\d{1,2})\s*m(?:in)?\b"),
)
_NUMBER_RE = re.compile(r"(?<![\w.,/-])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
                        r"(?:st|nd|rd|th|ms|min|x|×|k|K|s|h|m)?(?![\w.,]\d|\w)")


@dataclass
class NumberCheck:
    checked: int = 0
    unverified: list[dict[str, str]] = field(default_factory=list)   # {"field": …, "text": …}

    @property
    def ok(self) -> bool:
        return not self.unverified

    def as_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "unverified": self.unverified, "ok": self.ok}

    def describe(self, source: str = "findings.json") -> str:
        if not self.checked:
            return "no numbers to check"
        if self.ok:
            return f"all {self.checked} numbers found in {source}"
        bad = ", ".join(f"“{u['text']}”" for u in self.unverified[:6])
        return f"{len(self.unverified)} of {self.checked} numbers not found in {source}: {bad}"


# ---------------------------------------------------------------------------
# What the document allows
# ---------------------------------------------------------------------------

class Allowed:
    def __init__(self, doc: dict[str, Any]):
        self.values: set[float] = set(ALWAYS_ALLOWED)
        self.protected: set[str] = set()
        self.minutes: set[int] = set()          # minutes since midnight UTC
        self.dates: set[str] = set()
        self._offsets: set[float] = set()
        run = doc.get("run", {})
        self.start = _parse_iso(run.get("start", ""))
        for key in ("run_id", "tag", "scenario"):
            if run.get(key):
                self.protected.add(str(run[key]))
        self.values.update(range(1, len(doc.get("findings", [])) + 1))
        self._walk(doc, "")
        self._derive(doc)
        if self.start:
            for v in self._offsets:
                self._add_clock(self.start + timedelta(seconds=v))
        self._index()

    def add(self, extra: Any) -> "Allowed":
        """Also allow the numbers in `extra` (for example query results)."""
        self._walk(extra, "")
        self._index()
        return self

    def _index(self) -> None:
        self._scaled = [sorted(v * f for v in self.values if f != 100 or abs(v) <= 1.5) for f in _FACTORS]

    def _walk(self, node: Any, key: str) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            self.values.add(float(node))
            if key.endswith("offset_s"):
                self._offsets.add(float(node))
            return
        if isinstance(node, str):
            self._walk_text(node, key)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("label", "labels") or k.endswith("_label"):
                    for name in (v if isinstance(v, list) else [v]):
                        if isinstance(name, str) and name:
                            self.protected.add(name)
                        elif isinstance(name, dict):
                            self._walk(name, k)
                    continue
                self._walk(v, str(k))
        elif isinstance(node, list):
            self.values.add(float(len(node)))
            for item in node:
                self._walk(item, key)

    def _walk_text(self, text: str, key: str) -> None:
        dt = _parse_iso(text)
        if dt:
            self._add_clock(dt)
            return
        if re.fullmatch(r"-?\d+(\.\d+)?", text.strip()):
            self.values.add(float(text))
            return
        # Numbers inside the document's own sentences (finding titles, notes) are trusted
        for _, value, _ in _numbers(text):
            self.values.add(value)
        for _, secs, _ in _offsets(text):
            self.values.add(secs)
            self._offsets.add(secs)

    def _add_clock(self, dt: datetime) -> None:
        self.minutes.add(dt.hour * 60 + dt.minute)
        self.dates.add(dt.strftime("%Y-%m-%d"))

    def _derive(self, doc: dict[str, Any]) -> None:
        def window(item: dict, a: str, b: str) -> None:
            if isinstance(item.get(a), (int, float)) and isinstance(item.get(b), (int, float)):
                self.values.add(float(item[b] - item[a]))

        for f in doc.get("findings", []):
            window(f, "start_offset_s", "end_offset_s")
            self._pairs(f.get("evidence", {}))
        for e in doc.get("errors", []):
            window(e, "first_offset_s", "last_offset_s")
        window(doc.get("steady", {}), "start_offset_s", "end_offset_s")
        for c in doc.get("sla", {}).get("checks", []) or []:
            self._change(c.get("target"), c.get("actual"))
        self.values.update({abs(v) for v in self.values if v < 0})

    def _pairs(self, ev: dict[str, Any]) -> None:
        for k in list(ev):
            for a, b in (("before", "after"), ("_typical", "_during"), ("_before_ms", "_after_ms")):
                if k.endswith(a) and k[: len(k) - len(a)] + b in ev:
                    self._change(ev[k], ev[k[: len(k) - len(a)] + b])
            if k.startswith("typical_") and "peak_" + k[8:] in ev:
                self._change(ev[k], ev["peak_" + k[8:]])
        for item in ev.values():
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict):
                        self._pairs(sub)

    def _change(self, before: Any, after: Any) -> None:
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            return
        self.values.add(float(after - before))
        if before:
            self.values.update((after / before, (after / before - 1) * 100))
        if after:
            self.values.add(before / after)

    # ------------------------------------------------------------------

    def number_ok(self, value: float, decimals: int, raw: str) -> bool:
        tol = 0.5 * 10 ** -decimals + 1e-9
        digits = raw.replace(",", "").split(".")[0]
        if decimals == 0 and len(digits) > 1 and digits.endswith("0"):
            zeros = len(digits) - len(digits.rstrip("0"))
            tol = max(tol, min(0.5 * 10 ** zeros, 0.05 * value))
        for scaled in self._scaled:
            i = bisect_left(scaled, value - tol)
            if i < len(scaled) and scaled[i] <= value + tol:
                return True
        return False

    def offset_ok(self, secs: float, has_seconds: bool) -> bool:
        tol = 2 if has_seconds else 31
        return any(abs(v - secs) <= tol for v in self.values)

    def clock_ok(self, minute: int) -> bool:
        return any(min(abs(minute - m), 1440 - abs(minute - m)) <= 1 for m in self.minutes)


def _parse_iso(text: str) -> datetime | None:
    if not isinstance(text, str) or not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# What the text states
# ---------------------------------------------------------------------------

def _numbers(text: str) -> Iterable[tuple[str, float, int]]:
    for m in _NUMBER_RE.finditer(text):
        whole, frac = m.group(1), m.group(2) or ""
        yield m.group(0), float(whole.replace(",", "") + frac), max(0, len(frac) - 1)


def _offsets(text: str) -> Iterable[tuple[str, float, bool]]:
    """(text, seconds, has seconds) for each offset or duration, without overlaps."""
    taken: list[tuple[int, int]] = []
    for rx in _OFFSET_RES:
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append(m.span())
            h, mins, secs = m.group("h"), m.group("m"), m.groupdict().get("s")
            yield m.group(0), float(int(h or 0) * 3600 + int(mins) * 60 + int(secs or 0)), secs is not None


def _blank_offsets(text: str) -> str:
    for rx in _OFFSET_RES:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def check_text(text: str, allowed: Allowed, field_name: str, result: NumberCheck) -> None:
    for name in sorted(allowed.protected, key=len, reverse=True):
        if len(name) >= 2 and name in text:
            text = text.replace(name, " ")

    def blank(m: re.Match) -> str:
        return " " * len(m.group(0))

    for m in _DATE_RE.finditer(text):
        result.checked += 1
        if m.group(0) not in allowed.dates:
            result.unverified.append({"field": field_name, "text": m.group(0)})
    text = _DATE_RE.sub(blank, text)

    for m in _CLOCK_RE.finditer(text):
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            continue
        result.checked += 1
        if not allowed.clock_ok(hh * 60 + mm):
            result.unverified.append({"field": field_name, "text": m.group(0)})
    text = _CLOCK_RE.sub(blank, text)

    for raw, secs, has_seconds in _offsets(text):
        result.checked += 1
        if not allowed.offset_ok(secs, has_seconds):
            result.unverified.append({"field": field_name, "text": raw.strip()})
    text = _blank_offsets(text)

    for raw, value, decimals in _numbers(text):
        result.checked += 1
        if not allowed.number_ok(value, decimals, raw):
            result.unverified.append({"field": field_name, "text": raw})


def _strings(node: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")


def check(narrative: dict[str, Any], doc: dict[str, Any], allowed: Allowed | None = None) -> NumberCheck:
    """Check every string in a narrative (any nesting) against findings.json."""
    allowed = allowed or Allowed(doc)
    result = NumberCheck()
    for path, text in _strings(narrative, ""):
        check_text(text, allowed, path, result)
    return result
