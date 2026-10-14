"""
Analysis windows.

Statistics can be limited to a window of the run, usually the steady state
(load at its target level, ramp-up and ramp-down excluded):

  --steady 5m:35m       offsets from the first sample (s, m, h; plain numbers are seconds)
  --steady 300:2100     the same in seconds
  --steady 5m:          from 5 minutes to the end; :35m from the start to 35 minutes
  --steady 1760000000000:1760001800000   absolute epoch milliseconds
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_OFFSET_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?$")


@dataclass
class Window:
    start_ms: int          # inclusive (sample start timestamps)
    end_ms: int            # exclusive
    source: str            # full | manual | auto
    duration_s: float      # seconds used for throughput

    def as_dict(self) -> dict:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms, "source": self.source,
                "duration_s": round(self.duration_s, 3)}


def full_run(start_ms: int, last_ts_ms: int, end_ms: int) -> Window:
    """The whole run. Throughput uses JMeter's definition: first sample start to last sample end."""
    return Window(start_ms, last_ts_ms + 1, "full", max((end_ms - start_ms) / 1000.0, 0.001))


def _point(text: str, run_start_ms: int) -> int:
    text = text.strip().lower()
    if re.fullmatch(r"\d{12,}", text):
        return int(text)                                  # absolute epoch ms
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return run_start_ms + int(float(text) * 1000)     # plain seconds
    m = _OFFSET_RE.match(text)
    if not m or not any(m.groups()):
        raise ValueError(f"Cannot read {text!r} — use offsets such as 90s, 5m, 1h5m, or epoch milliseconds")
    h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return run_start_ms + int(((h * 60 + mi) * 60 + s) * 1000 + ms)


def parse_steady(spec: str, run_start_ms: int, run_end_ms: int) -> Window:
    """Parse --steady START:END against the run's first sample start and last sample end."""
    if spec.count(":") != 1:
        raise ValueError("--steady must be START:END, e.g. 5m:35m")
    a, b = spec.split(":")
    start = _point(a, run_start_ms) if a.strip() else run_start_ms
    end = _point(b, run_start_ms) if b.strip() else run_end_ms + 1
    if end <= start:
        raise ValueError(f"--steady {spec}: the end must be after the start")
    if start >= run_end_ms or end <= run_start_ms:
        raise ValueError(f"--steady {spec} lies outside the run "
                         f"(it lasted {(run_end_ms - run_start_ms) / 1000:.0f}s)")
    start, end = max(start, run_start_ms), min(end, run_end_ms + 1)
    return Window(start, end, "manual", (end - start) / 1000.0)
