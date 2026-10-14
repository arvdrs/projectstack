"""
JMeter result-file columns.

A CSV JTL normally starts with a header row. Without one (print_field_names=false)
the columns follow JMeter's fixed order, filtered by the jmeter.save.saveservice.*
flags in user.properties / jmeter.properties — reproduced here so header-less
files can be read the way JMeter wrote them.
"""

from __future__ import annotations

from pathlib import Path

# JTL header name -> (input type for ClickHouse's input(), SELECT expression, samples column)
KNOWN_COLUMNS: dict[str, tuple[str, str, str]] = {
    "timeStamp":       ("Int64",  "fromUnixTimestamp64Milli({c})", "ts"),
    "elapsed":         ("UInt32", "{c}", "elapsed"),
    "label":           ("String", "{c}", "label"),
    "responseCode":    ("String", "{c}", "response_code"),
    "responseMessage": ("String", "{c}", "response_message"),
    "threadName":      ("String", "{c}", "thread_name"),
    "dataType":        ("String", "{c}", "data_type"),
    "success":         ("String", "lower({c}) = 'true'", "success"),
    "failureMessage":  ("String", "{c}", "failure_message"),
    "bytes":           ("UInt64", "{c}", "bytes"),
    "sentBytes":       ("UInt64", "{c}", "sent_bytes"),
    "grpThreads":      ("UInt32", "{c}", "grp_threads"),
    "allThreads":      ("UInt32", "{c}", "all_threads"),
    "URL":             ("String", "{c}", "url"),
    "Latency":         ("UInt32", "{c}", "latency"),
    "IdleTime":        ("UInt32", "{c}", "idle_time"),
    "Connect":         ("UInt32", "{c}", "connect"),
    "Hostname":        ("String", "{c}", "hostname"),
}
REQUIRED_COLUMNS = ("timeStamp", "elapsed", "label")

# Defaults for samples columns the file does not have
DEFAULTS = {
    "elapsed": "0", "latency": "0", "connect": "0", "idle_time": "0", "label": "''", "response_code": "''",
    "response_message": "''", "failure_message": "''", "thread_name": "''", "data_type": "''",
    "success": "true", "bytes": "0", "sent_bytes": "0", "grp_threads": "0", "all_threads": "0",
    "url": "''", "hostname": "''",
}

# JMeter's CSV column order (CSVSaveService) with the property that switches each one
# and its default — `None` means always written (when the group is enabled).
_ORDER: list[tuple[list[str], str, bool]] = [
    (["timeStamp"], "timestamp_format", True),       # off only when timestamp_format=none
    (["elapsed"], "time", True),
    (["label"], "label", True),
    (["responseCode"], "response_code", True),
    (["responseMessage"], "response_message", True),
    (["threadName"], "thread_name", True),
    (["dataType"], "data_type", True),
    (["success"], "successful", True),
    (["failureMessage"], "assertion_results_failure_message", True),
    (["bytes"], "bytes", True),
    (["sentBytes"], "sent_bytes", True),
    (["grpThreads", "allThreads"], "thread_counts", True),
    (["URL"], "url", True),
    (["Filename"], "filename", False),
    (["Latency"], "latency", True),
    (["Encoding"], "encoding", False),
    (["SampleCount", "ErrorCount"], "sample_count", False),
    (["Hostname"], "hostname", False),
    (["IdleTime"], "idle_time", True),
    (["Connect"], "connect_time", True),
]
DEFAULT_HEADER = [c for cols, _, on in _ORDER if on for c in cols]


def read_properties(path: str | Path) -> dict[str, str]:
    """Parse a Java .properties file (key=value or key: value, # and ! comments, \\ continuations)."""
    props: dict[str, str] = {}
    pending = ""
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = pending + raw.strip()
        pending = ""
        if not line or line[0] in "#!":
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending = line[:-1]
            continue
        sep = min((i for i in (line.find("="), line.find(":")) if i >= 0), default=-1)
        key, value = (line[:sep], line[sep + 1:]) if sep >= 0 else (line, "")
        props[key.strip()] = value.strip()
    return props


def header_from_properties(props: dict[str, str]) -> tuple[list[str], str]:
    """Column order and delimiter JMeter uses for a header-less CSV with these properties."""
    prefix = "jmeter.save.saveservice."

    def flag(name: str, default: bool) -> bool:
        v = props.get(prefix + name)
        return default if v is None else v.strip().lower() == "true"

    cols: list[str] = []
    for names, prop, default in _ORDER:
        if prop == "timestamp_format":
            on = props.get(prefix + prop, "ms").strip().lower() != "none"
        else:
            on = flag(prop, default)
        if on:
            cols += names
    cols += [v.strip() for v in props.get("sample_variables", "").split(",") if v.strip()]
    delim = props.get(prefix + "default_delimiter", ",")
    delim = "\t" if delim in ("\\t", "\t") else (delim[:1] or ",")
    return cols, delim


def canonical(name: str) -> str | None:
    """Map a header name to its known JMeter spelling (case-insensitive), or None for custom columns."""
    low = name.strip().lower()
    return next((k for k in KNOWN_COLUMNS if k.lower() == low), None)
