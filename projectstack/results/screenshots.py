"""
Server-metric screenshots attached to an analysed run, and what an AI vision model
reads from them.

When the servers behind a test are not in AWS or Azure monitoring that ProjectStack
can query, or the tester simply has the dashboards open, they attach screenshots of
CPU, memory, connections … from any tool (Grafana, CloudWatch, New Relic, Datadog …).
On request, a vision-capable model reads them together with the run's own timeline,
so it can line each chart up with the load steps, and writes observations per
screenshot and for the servers overall.

Files in the run folder:

  screenshots/<id>.<ext>        the images as uploaded
  screenshots/index.json        file name, caption, size and order of each
  narrative_screenshots.json    the AI's reading, reused while the screenshots, captions,
                                results and prompt are unchanged

Nothing is sent anywhere until the tester asks for the reading, and then only to the
provider they choose (a local Ollama vision model keeps everything on the machine).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.results import highlights as hl

DIR = "screenshots"
INDEX = "index.json"
CACHE_FILE = "narrative_screenshots.json"
PROMPT = "server_screenshots"
SUMMARY_PROMPT = "server_screenshots_summary"
MAX_FILES = 20
MAX_BYTES = 10 * 1024 * 1024                    # per uploaded file
MAX_CAPTION = 200
SEND_EDGES = (2048, 1536, 1024)                 # longest side of a picture sent to the model, tried in turn …
SEND_MAX_TOTAL = 14 * 1024 * 1024               # … until all of them fit (Gemini takes 20 MB per request)
SEND_MAX_ONE = int(3.5 * 1024 * 1024)           # Claude takes 5 MB per picture
REPORT_EDGE = 1600
MAX_TIMELINE = 120                              # per-minute points sent; longer runs are thinned
MAX_READ_AT = 30                                # times at which the model reads each line's value
MAX_SERIES = 4                                  # lines read per screenshot
MAX_PER_SHOT = 4
MAX_OVERALL = 6
MAX_CAVEATS = 4
BATCH = 3                                       # screenshots per request to a cloud model …
PARALLEL = 2                                    # … with two requests at a time …
PARALLEL_BY = {"groq": 1, "ollama": 1}           # … except where the limits are tokens per minute, or memory
LOCAL_BATCH = 1                                 # a local model reads one at a time (memory)

COVERAGE = ("yes", "partly", "no", "unclear")      # does a screenshot's time axis span the test window?
CLOCKS = ("utc", "local", "unclear")                 # which of the test's clocks a screenshot's axis uses

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.Lock()

SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["screenshots"],
    "properties": {
        "screenshots": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["image", "title", "readable", "time_axis", "test_on_chart", "covers_test", "clock",
                         "series", "observations", "caveat"],
            "properties": {"image": {"type": "integer"}, "title": {"type": "string"},
                           "readable": {"type": "boolean"},
                           "time_axis": {"type": "string"}, "test_on_chart": {"type": "string"},
                           "covers_test": {"type": "string", "enum": list(COVERAGE)},
                           "clock": {"type": "string", "enum": list(CLOCKS)},
                           "series": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False,
                               "required": ["name", "unit", "values"],
                               "properties": {"name": {"type": "string"}, "unit": {"type": "string"},
                                              "values": {"type": "array",
                                                         "items": {"anyOf": [{"type": "number"}, {"type": "null"}]}}}}},
                           "observations": {"type": "array", "items": {"type": "string"}},
                           "caveat": {"type": "string"}}}},
    },
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["observations", "caveats"],
    "properties": {"observations": {"type": "array", "items": {"type": "string"}},
                   "caveats": {"type": "array", "items": {"type": "string"}}},
}


class ScreenshotError(ValueError):
    """A problem with an attached screenshot, explained for the user."""


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> tuple[str, str] | None:
    """(media type, extension) from the file's first bytes; None for anything but PNG, JPEG, WebP and GIF."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    return None


def _pil():
    try:
        from PIL import Image as PILImage
        return PILImage
    except ImportError:                       # optional: without Pillow, pictures are sent as uploaded
        return None


def _size(data: bytes) -> tuple[int, int] | None:
    pil = _pil()
    if pil is None:
        return None
    try:
        with pil.open(io.BytesIO(data)) as im:
            im.verify()
        with pil.open(io.BytesIO(data)) as im:
            return im.size
    except Exception as exc:
        raise ScreenshotError("The file looks like an image but cannot be read — save the screenshot again") from exc


def _folder(run_folder: Path) -> Path:
    return run_folder / DIR


def list_items(run_folder: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((_folder(run_folder) / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in data.get("items", []) if isinstance(i, dict) and _ID_RE.match(str(i.get("id", "")))
            and (_folder(run_folder) / str(i.get("file", ""))).is_file()]


def _write(run_folder: Path, items: list[dict[str, Any]]) -> None:
    folder = _folder(run_folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{INDEX}.tmp"
    tmp.write_text(json.dumps({"items": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / INDEX)


def _clean_caption(caption: str | None) -> str:
    return " ".join(str(caption or "").split())[:MAX_CAPTION]


def _clean_name(name: str | None) -> str:
    base = re.split(r"[\\/]", str(name or ""))[-1].strip()
    return re.sub(r"[\x00-\x1f]", "", base)[:120] or "screenshot"


def add(run_folder: Path, name: str, data: bytes, caption: str = "") -> dict[str, Any]:
    """Store one uploaded screenshot; returns its entry."""
    if not data:
        raise ScreenshotError(f"{_clean_name(name)} is empty")
    if len(data) > MAX_BYTES:
        raise ScreenshotError(f"{_clean_name(name)} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    kind = sniff(data)
    if kind is None:
        raise ScreenshotError(f"{_clean_name(name)} is not a PNG, JPEG, WebP or GIF image")
    size = _size(data)
    media_type, ext = kind
    with _lock:
        items = list_items(run_folder)
        if len(items) >= MAX_FILES:
            raise ScreenshotError(f"A run takes up to {MAX_FILES} screenshots — remove one first")
        sid = secrets.token_hex(6)
        item = {"id": sid, "file": f"{sid}.{ext}", "name": _clean_name(name), "caption": _clean_caption(caption),
                "media_type": media_type, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "width": size[0] if size else None, "height": size[1] if size else None,
                "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _folder(run_folder).mkdir(parents=True, exist_ok=True)
        (_folder(run_folder) / item["file"]).write_bytes(data)
        _write(run_folder, items + [item])
    return item


def _find(items: list[dict[str, Any]], sid: str) -> dict[str, Any]:
    for item in items:
        if item["id"] == sid:
            return item
    raise KeyError(f"No screenshot {sid} in this run")


def update(run_folder: Path, sid: str, caption: str) -> dict[str, Any]:
    with _lock:
        items = list_items(run_folder)
        item = _find(items, sid)
        item["caption"] = _clean_caption(caption)
        _write(run_folder, items)
    return item


def remove(run_folder: Path, sid: str) -> None:
    with _lock:
        items = list_items(run_folder)
        item = _find(items, sid)
        (_folder(run_folder) / item["file"]).unlink(missing_ok=True)
        _write(run_folder, [i for i in items if i["id"] != sid])


def image(run_folder: Path, sid: str) -> tuple[bytes, str]:
    """(bytes, media type) of one screenshot."""
    if not _ID_RE.match(sid):
        raise KeyError(f"No screenshot {sid} in this run")
    item = _find(list_items(run_folder), sid)
    return (_folder(run_folder) / item["file"]).read_bytes(), item["media_type"]


def shrink(data: bytes, media_type: str, edge: int, max_bytes: int) -> tuple[bytes, str]:
    """The picture with its longest side at most `edge` px and at most max_bytes (PNG, or JPEG when smaller)."""
    pil = _pil()
    if pil is None:
        return data, media_type
    try:
        with pil.open(io.BytesIO(data)) as im:
            if max(im.size) <= edge and len(data) <= max_bytes and media_type != "image/gif":
                return data, media_type
            im.seek(0)
            frame = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        frame.thumbnail((edge, edge))
        out = io.BytesIO()
        frame.save(out, "PNG", optimize=True)
        if out.tell() <= max_bytes:
            return out.getvalue(), "image/png"
        out = io.BytesIO()
        frame.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, media_type


SEND_MAX_TOTAL_BY = {"groq": int(2.8 * 1024 * 1024)}   # base64 adds a third: Groq takes 4 MB per request


def _for_sending(run_folder: Path, items: list[dict[str, Any]], provider: str = ""):
    from projectstack.llm.base import Image
    raw = [((_folder(run_folder) / i["file"]).read_bytes(), i["media_type"]) for i in items]
    limit = SEND_MAX_TOTAL_BY.get(provider, SEND_MAX_TOTAL)
    pictures = []
    for edge in SEND_EDGES:
        pictures = [shrink(data, mt, edge, min(SEND_MAX_ONE, limit // max(len(raw), 1))) for data, mt in raw]
        if sum(len(d) for d, _ in pictures) <= limit:
            break
    return [Image(data, mt, name=_shown(item)) for (data, mt), item in zip(pictures, items)]


def _shown(item: dict[str, Any]) -> str:
    return item.get("caption") or item.get("name") or "screenshot"


# ---------------------------------------------------------------------------
# What the model is told about the test
# ---------------------------------------------------------------------------

def _zone(name: str | None):
    """The tester's time zone (an IANA name from the browser), or None."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception:
        return None


def _at(ms: float | None, zone) -> str | None:
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return (dt.astimezone(zone) if zone else dt).strftime("%H:%M")


def _offset(zone, when: datetime) -> str:
    off = when.astimezone(zone).utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    return f"UTC{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def test_facts(doc: dict[str, Any], ctx: dict[str, Any] | None, tz_name: str = "") -> dict[str, Any]:
    """What the vision model gets besides the pictures: the test's window, load profile and minute-by-minute timeline."""
    run, steady = doc["run"], doc.get("steady") or {}
    f = hl.facts(doc, ctx)
    zone = _zone(tz_name)
    start = hl._dt(run.get("start", ""))
    start_ms = start.timestamp() * 1000 if start else None
    end = hl._dt(run.get("end", ""))

    def both(ms: float | None) -> dict[str, str | None]:
        return {"utc": _at(ms, None), **({"local": _at(ms, zone)} if zone else {})}

    window: dict[str, Any] = {"start_utc": run.get("start"), "end_utc": run.get("end"), "text": f["window"]["text"]}
    if zone and start and end:
        window.update({"local_time_zone": f"{tz_name} ({_offset(zone, start)})",
                       "start_local": start.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
                       "end_local": end.astimezone(zone).strftime("%Y-%m-%d %H:%M")})

    points = (doc.get("timeline") or {}).get("points") or []
    step = max(1, -(-len(points) // MAX_TIMELINE))
    # A table rather than objects: the timeline is most of the request, and local models have small windows
    columns = ["utc", *(["local"] if zone else []), "users", "requests_per_s", "p95_ms", "error_pct"]
    rows = []
    for p in points[::step]:
        at = both(start_ms + p["t_offset_s"] * 1000 if start_ms is not None else None)
        rows.append([at["utc"], *([at["local"]] if zone else []), p.get("users"),
                     round(p["tps"], 1) if p.get("tps") is not None else None, p.get("p95_ms"),
                     round(p["error_pct"], 2) if p.get("error_pct") is not None else None])

    read_rows = [[i, *([at["utc"], at["local"]] if zone else [at["utc"]])]
                 for i, (_, at) in enumerate((t, both(start_ms + t * 1000 if start_ms is not None else None))
                                             for t in read_at(doc))]

    findings = []
    for x in doc.get("findings", []):
        item = {"severity": x.get("severity"), "type": x.get("type"), "title": x.get("title")}
        if start_ms is not None and x.get("start_offset_s") is not None:
            item["from"] = both(start_ms + x["start_offset_s"] * 1000)
            if x.get("end_offset_s") is not None:
                item["to"] = both(start_ms + x["end_offset_s"] * 1000)
        findings.append(item)

    return {
        "window": window,
        "test_type": f["test_type"],
        "peak_users": f["peak_users"],
        "load_profile": f["load_profile"],
        "steady_state": {"from": both(steady.get("start_ms")), "to": both(steady.get("end_ms")),
                         "users": steady.get("plateau_users")} if steady.get("start_ms") else None,
        "ramp_up_steps": [{"users": lv.get("users"), "from": both(lv.get("start_ms"))}
                          for lv in (steady.get("levels") or [])],
        "totals": f["totals"],
        "response_times": f["response_times"],
        "timeline": {"bucket_s": ((doc.get("timeline") or {}).get("bucket_s") or 60) * step,
                     "note": "one row per bucket, times are the bucket start", "columns": columns, "rows": rows},
        "read_at": {"note": "read every line on each picture at these times; values[i] is the value at row i",
                    "columns": ["i", "utc", *(["local"] if zone else [])], "rows": read_rows},
        "findings": findings,
        "cloud_monitoring": doc.get("server"),
    }


def read_at(doc: dict[str, Any]) -> list[float]:
    """The times (seconds from the run's start, on whole minutes) at which the model reads each line:
    every bucket of the test's timeline, thinned to at most MAX_READ_AT."""
    tl = doc.get("timeline") or {}
    duration = float(doc["run"].get("duration_s") or 0)
    times = [float(p["t_offset_s"]) for p in tl.get("points") or [] if p.get("samples")
             and -float(tl.get("bucket_s") or 60) < p["t_offset_s"] <= duration]
    step = max(1, -(-len(times) // MAX_READ_AT))
    return times[::step]


# ---------------------------------------------------------------------------
# The AI reading
# ---------------------------------------------------------------------------

def sha(doc: dict[str, Any], ctx: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    """What a reading depends on: the screenshots and their captions, the results and the test context."""
    payload = {"findings": doc, "context": hl.clean_context(ctx),
               "shots": [[i["sha256"], i.get("caption", "")] for i in items]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _strs(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [" ".join(str(v).split()) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return out[:limit]


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        x = float(str(v).replace(",", "").rstrip("%").strip())
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


def _series(raw: Any, n: int) -> list[dict[str, Any]]:
    """Lines read off one picture: name, unit and one value (or None) per read-at time."""
    out = []
    for entry in (raw if isinstance(raw, list) else [])[:MAX_SERIES]:
        if not isinstance(entry, dict):
            continue
        values = [_number(v) for v in (entry.get("values") if isinstance(entry.get("values"), list) else [])][:n]
        values += [None] * (n - len(values))
        if sum(v is not None for v in values) >= 3:
            out.append({"name": " ".join(str(entry.get("name") or "").split())[:80] or "value",
                        "unit": " ".join(str(entry.get("unit") or "").split())[:16], "values": values})
    return out


def relation(content: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any] | None:
    """How the lines read off the screenshots followed the load test (see server_load)."""
    from projectstack.results import server_load
    times = content.get("read_at_s") or []
    series = []
    for shot in content.get("screenshots", []):
        if shot.get("covers_test") == "no" or not shot.get("readable", True):
            continue
        for k, line in enumerate(shot.get("series") or []):
            series.append({"key": f"{shot['id']}|{k}", "name": _line_name(shot, line["name"]), "unit": _unit(line["unit"]),
                           "shot": shot["id"],
                           "points": [(t + 1, v) for t, v in zip(times, line["values"]) if v is not None]})
    rel = server_load.relate(series, doc, source="screenshots")
    if rel:
        by_key = {s["key"]: s for s in series}
        for row in rel["rows"]:
            row["shot"] = by_key[row["key"]]["shot"]
    return rel


def _line_name(shot: dict[str, Any], name: str) -> str:
    """ "metric · resource": the model sometimes names a line by its server only ("orders-db"), which is
    ambiguous across charts, so the metric comes from the chart's title then ("ReadLatency · orders-db")."""
    if " · " in name:
        return name
    metric = re.split(r"\s*(?:[,·(]| - | – )\s*", shot.get("title") or "", maxsplit=1)[0].strip()
    if not metric or metric.lower() in name.lower():
        return name
    return f"{metric} · {name}"


def _unit(unit: str) -> str:
    u = unit.strip().lower()
    return "%" if u in ("%", "percent", "pct") else "ms" if u in ("ms", "milliseconds") else unit.strip()


def coerce(data: dict[str, Any], items: list[dict[str, Any]], times: list[float] | None = None) -> list[dict[str, Any]]:
    """One batch's answer as an entry per screenshot of the batch, in its order (Image 1 is items[0])."""
    times = times or []
    by_image: dict[int, dict[str, Any]] = {}
    for entry in data.get("screenshots") or []:
        if not isinstance(entry, dict):
            continue
        try:
            n = int(entry.get("image"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(items) and n not in by_image:
            by_image[n] = entry
    shots = []
    for n, item in enumerate(items, 1):
        entry = by_image.get(n) or {}
        covers = str(entry.get("covers_test") or "unclear").strip().lower()
        clock = str(entry.get("clock") or "unclear").strip().lower()
        shot = {"id": item["id"], "title": " ".join(str(entry.get("title") or "").split())[:160],
                "readable": bool(entry.get("readable", bool(entry))),
                "time_axis": " ".join(str(entry.get("time_axis") or "").split())[:120],
                "test_on_chart": " ".join(str(entry.get("test_on_chart") or "").split())[:160],
                "covers_test": covers if covers in COVERAGE else "unclear",
                "clock": clock if clock in CLOCKS else "unclear",
                "series": _series(entry.get("series"), len(times)) if times else [],
                "observations": _strs(entry.get("observations"), MAX_PER_SHOT),
                "caveat": " ".join(str(entry.get("caveat") or "").split())[:300]}
        if not entry:
            shot["error"] = "The AI returned nothing for this screenshot"
        shots.append(shot)
    return shots


def _failed(item: dict[str, Any], error: str) -> dict[str, Any]:
    return {"id": item["id"], "title": "", "readable": False, "time_axis": "", "test_on_chart": "",
            "covers_test": "unclear", "clock": "unclear", "series": [], "observations": [], "caveat": "",
            "error": error[:300]}


def _sig(doc: dict[str, Any], ctx: dict[str, Any] | None, item: dict[str, Any], prompt_ref: str) -> str:
    """What one screenshot's reading depends on, so an unchanged one is not read again."""
    return sha(doc, ctx, [item]) + ":" + prompt_ref


def _saved(run_folder: Path) -> dict[str, Any] | None:
    try:
        saved = json.loads((run_folder / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and isinstance(saved.get("content"), dict) else None


def _public(saved: dict[str, Any], items: list[dict[str, Any]], current: str, doc: dict[str, Any]) -> dict[str, Any]:
    ids = {i["id"] for i in items}
    content = {**saved["content"],
               "screenshots": [s for s in saved["content"].get("screenshots", []) if s.get("id") in ids]}
    # Worked out each time from the values read (cheap, and rule changes apply without asking the AI again)
    content["relation"] = relation(content, doc)
    return {"content": content,
            "provider": saved.get("provider"), "local": saved.get("local", False), "prompt": saved.get("prompt"),
            "generated_at": saved.get("generated_at"), "stale": saved.get("sha256") != current,
            "failed": sum(1 for x in content["screenshots"] if x.get("error"))}


def current(run_folder: Path, doc: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any] | None:
    """The saved reading, marked stale when the screenshots, captions, results or notes changed since."""
    from projectstack.llm.prompts import load_prompt
    saved = _saved(run_folder)
    if saved is None:
        return None
    items = list_items(run_folder)
    key = sha(doc, ctx, items) if saved.get("prompt") == load_prompt(PROMPT).ref else ""
    return _public(saved, items, key, doc)


def state(run_folder: Path, doc: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {"items": list_items(run_folder), "analysis": current(run_folder, doc, ctx),
            "limits": {"files": MAX_FILES, "bytes": MAX_BYTES, "caption": MAX_CAPTION}}


def analyse(doc: dict[str, Any], run_folder: Path, ctx: dict[str, Any] | None, *, settings: dict[str, str],
            provider_spec: str | None = None, fresh: bool = False, tz_name: str = "",
            progress: Callable[[int, int, str], None] = lambda done, total, message: None) -> dict[str, Any]:
    """Have the vision model read the screenshots, a few per request, then write the overall observations.

    A screenshot already read with the same provider, prompt, caption, results and notes is not read
    again, so after a partial failure only the failed ones are retried (fresh reads all of them).
    progress(done, total, message) reports the screenshots read so far.

    Raises ScreenshotError (nothing attached) and LLMConfigError (provider not set up or cannot read
    images). A batch that fails is recorded as failed screenshots; the others are kept.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from projectstack.llm.base import LLMConfigError, LLMError
    from projectstack.llm.prompts import load_prompt
    from projectstack.llm.registry import provider_for_task

    items = list_items(run_folder)
    if not items:
        raise ScreenshotError("Attach at least one screenshot first")
    ctx = hl.clean_context(ctx)
    provider = provider_for_task("screenshots", settings, provider_spec)
    prompt, summary_prompt = load_prompt(PROMPT), load_prompt(SUMMARY_PROMPT)
    key = sha(doc, ctx, items)
    saved = _saved(run_folder)
    same = bool(saved and saved.get("prompt") == prompt.ref and saved.get("provider_requested") == provider.label)
    if (not fresh and same and saved.get("sha256") == key
            and not any(x.get("error") for x in saved["content"].get("screenshots", []))):
        return {**_public(saved, items, key, doc), "reused": True}

    sigs = {i["id"]: _sig(doc, ctx, i, prompt.ref) for i in items}
    prior = {} if fresh or not same else {
        x["id"]: x for x in saved["content"].get("screenshots", [])
        if not x.get("error") and x.get("id") in sigs and x.get("sig") == sigs[x["id"]]}
    todo = [i for i in items if i["id"] not in prior]
    local = provider.name == "ollama"
    size = LOCAL_BATCH if local else BATCH
    batches = [todo[k:k + size] for k in range(0, len(todo), size)]
    times = read_at(doc)
    facts_json = json.dumps(test_facts(doc, ctx, tz_name), ensure_ascii=False, separators=(",", ":"))
    usage = {"input_tokens": 0, "output_tokens": 0}
    models: list[str] = []
    done = 0
    total = len(todo)

    if batches:
        progress(0, total, f"Checking which {provider.label} models are answering…")
        provider.warm_up()

    def read(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def line(n: int, item: dict[str, Any]) -> str:
            size_px = f", {item['width']}×{item['height']} px" if item.get("width") else ""
            caption = f'caption "{item["caption"]}"' if item.get("caption") else "no caption"
            return f"Image {n}: {caption} (file {item['name']}{size_px})"
        system, user = prompt.render(
            run_id=doc["run"]["run_id"], scenario=doc["run"].get("scenario", ""),
            region=ctx["region"] or "(not given)", notes=ctx["notes"] or "(none)",
            images="\n".join(line(n, item) for n, item in enumerate(batch, 1)), test_json=facts_json)
        comp = provider.complete(system, user, max_tokens=2000 + 2000 * len(batch), json_schema=SCHEMA,
                                 effort="medium", images=_for_sending(run_folder, batch, provider.name))
        usage["input_tokens"] += comp.input_tokens or 0
        usage["output_tokens"] += comp.output_tokens or 0
        if comp.model and comp.model not in models:
            models.append(comp.model)
        from projectstack.results.narrative import parse_json
        return coerce(parse_json(comp.text), batch, times)

    def read_or_split(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A batch whose answer fails is read again one screenshot at a time, so one bad answer costs one screenshot."""
        try:
            return read(batch)
        except LLMConfigError:
            raise
        except (LLMError, ValueError):
            if len(batch) == 1:
                raise
        out = []
        for item in batch:
            try:
                out += read([item])
            except LLMConfigError:
                raise
            except (LLMError, ValueError) as exc:
                out.append(_failed(item, str(exc)))
        return out

    results: dict[str, dict[str, Any]] = {}
    workers = PARALLEL_BY.get(provider.name, PARALLEL)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for k, batch in enumerate(batches):
            if k < workers:
                progress(done, total, _reading(batch, items, provider.label))
        futures = {pool.submit(read_or_split, batch): batch for batch in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                shots = fut.result()
            except LLMConfigError:
                for f in futures:
                    f.cancel()
                raise
            except (LLMError, ValueError) as exc:
                shots = [_failed(item, str(exc)) for item in batch]
            results.update({x["id"]: x for x in shots})
            done += len(batch)
            progress(done, total, f"Read {done} of {total} screenshot{'s' if total != 1 else ''}")

    shots = []
    for item in items:
        shot = prior.get(item["id"]) or results.get(item["id"]) or _failed(item, "Not read")
        shots.append({**shot, "sig": sigs[item["id"]]})
    content: dict[str, Any] = {"screenshots": shots, "read_at_s": times, "observations": [], "caveats": []}
    failed = [x for x in shots if x.get("error")]

    if len(failed) < len(shots):
        progress(total, total, "Writing the overall observations…")
        readings = [{"screenshot": n, "caption": item.get("caption") or item["name"],
                     **{k: x[k] for k in ("title", "readable", "time_axis", "test_on_chart", "covers_test",
                                          "observations", "caveat")}}
                    for n, (item, x) in enumerate(zip(items, shots), 1) if not x.get("error")]
        rel = relation(content, doc)
        system, user = summary_prompt.render(
            run_id=doc["run"]["run_id"], scenario=doc["run"].get("scenario", ""),
            region=ctx["region"] or "(not given)", notes=ctx["notes"] or "(none)",
            readings_json=json.dumps(readings, ensure_ascii=False, separators=(",", ":")),
            relation_json=json.dumps({"observations": rel["observations"], "rows": [
                {k: r[k] for k in ("name", "unit", "r_users", "load", "r_p95", "with_p95", "r_errors", "with_errors",
                                   "steady_avg", "peak", "trend")} for r in rel["rows"]]} if rel else None,
                ensure_ascii=False, separators=(",", ":")),
            test_json=json.dumps({k: v for k, v in json.loads(facts_json).items() if k not in ("timeline", "read_at")},
                                 ensure_ascii=False, separators=(",", ":")))
        try:
            from projectstack.results.narrative import parse_json
            comp = provider.complete(system, user, max_tokens=3000, json_schema=SUMMARY_SCHEMA, effort="medium")
            usage["input_tokens"] += comp.input_tokens or 0
            usage["output_tokens"] += comp.output_tokens or 0
            data = parse_json(comp.text)
            content["observations"] = _strs(data.get("observations"), MAX_OVERALL)
            content["caveats"] = _strs(data.get("caveats"), MAX_CAVEATS)
        except LLMError as exc:
            content["caveats"] = [f"The overall observations could not be written ({exc}); the readings of each "
                                  "screenshot and the comparison with the load test are below."]

    record = {"content": content,
              "provider": f"{provider.name}/{', '.join(models)}" if models else provider.label,
              "provider_requested": provider.label, "local": local, "prompt": prompt.ref, "sha256": key,
              "time_zone": tz_name, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "usage": usage, "failed": len(failed)}
    (run_folder / CACHE_FILE).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**_public(record, items, key, doc), "reused": False}


def _reading(batch: list[dict[str, Any]], items: list[dict[str, Any]], label: str) -> str:
    order = [i["id"] for i in items]
    first, last = order.index(batch[0]["id"]) + 1, order.index(batch[-1]["id"]) + 1
    return f"Reading screenshot{'s' if last > first else ''} {first}{f'–{last}' if last > first else ''} of {len(items)} with {label}…"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_section(run_folder: Path, doc: dict[str, Any], ctx: dict[str, Any] | None, *, embed: bool,
                   link_from: Path | None = None) -> dict[str, Any] | None:
    """The screenshots and their reading for a report; None when the run has none.

    embed: pictures as data: URIs (a self-contained HTML report), scaled down for the page.
    link_from: the folder a Markdown report is written to, for relative links to the files.
    The reading is left out when it is out of date.
    """
    import os
    items = list_items(run_folder)
    if not items:
        return None
    reading = current(run_folder, doc, ctx)
    fresh = reading if reading and not reading["stale"] else None
    by_id = {s["id"]: s for s in (fresh["content"]["screenshots"] if fresh else [])}
    shots = []
    for item in items:
        path = _folder(run_folder) / item["file"]
        shot = {"caption": item.get("caption") or "", "name": item["name"], "title": "", "observations": [],
                "readable": True, "time_axis": "", "test_on_chart": "", "covers_test": "", "caveat": "", "error": "",
                **{k: v for k, v in by_id.get(item["id"], {}).items() if k != "id"}}
        shot["heading"] = shot["title"] or shot["caption"] or shot["name"]
        shot["src"] = ""
        if embed:
            data, mt = shrink(path.read_bytes(), item["media_type"], REPORT_EDGE, 2 * 1024 * 1024)
            shot["src"] = f"data:{mt};base64,{base64.b64encode(data).decode('ascii')}"
        shot["link"] = Path(os.path.relpath(path, link_from)).as_posix() if link_from else f"{DIR}/{item['file']}"
        shots.append(shot)
    return {"pictures": shots, "analysed": fresh is not None,
            "observations": fresh["content"]["observations"] if fresh else [],
            "caveats": fresh["content"]["caveats"] if fresh else [],
            "relation": fresh["content"].get("relation") if fresh else None,
            "provider": fresh["provider"] if fresh else "", "local": fresh["local"] if fresh else False,
            "stale": bool(reading and reading["stale"])}
