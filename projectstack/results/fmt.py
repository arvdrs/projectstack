"""Number and time formatting shared by report narratives and templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def num(value: Any, decimals: int = 0) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.{decimals}f}" if decimals else f"{round(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


def ms(value: Any) -> str:
    return "—" if value is None else f"{num(value)} ms"


def pct(value: Any) -> str:
    if value is None:
        return "—"
    v = float(value)
    return f"{v:.2f}%" if 0 < abs(v) < 10 else f"{v:.1f}%" if abs(v) < 100 else f"{v:.0f}%"


def pct_text(value: Any) -> str:
    """Percentage for prose: no trailing zeros (1%, 1.86%)."""
    if value is None:
        return "—"
    v = float(value)
    return f"{v:.{2 if abs(v) < 10 else 1}f}".rstrip("0").rstrip(".") + "%"


def rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):,.1f}/s"


def duration(seconds: Any) -> str:
    s = int(round(float(seconds or 0)))
    h, rem = divmod(s, 3600)
    mi, sec = divmod(rem, 60)
    return f"{h}h {mi:02d}m" if h else f"{mi}m {sec:02d}s" if mi else f"{sec}s"


def offset(seconds: Any) -> str:
    """t+17m59s style offset from the first sample."""
    if seconds is None:
        return "—"
    s = max(0, int(round(float(seconds))))
    h, rem = divmod(s, 3600)
    mi, sec = divmod(rem, 60)
    if not h and not mi:
        return f"t+{sec}s" if sec else "t+0"
    head = f"{h}h{mi:02d}m" if h else f"{mi}m"
    return f"t+{head}{sec:02d}s" if sec else f"t+{head}"


def clock(iso: str) -> str:
    """HH:MM:SS UTC from an ISO timestamp."""
    try:
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return str(iso)
    return dt.strftime("%H:%M:%S UTC")


def day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(iso)


def kib(n: Any) -> str:
    return f"{float(n or 0) / 1024:.1f} KB"
