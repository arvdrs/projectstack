"""
mitmproxy addon: captures all HTTP/HTTPS flows and writes them to a HAR file.

Started by ProjectStack backend via:
  PS_HAR_OUTPUT=/tmp/ps_capture.har PS_TXN_FILE=… PS_STATUS_FILE=… \
    mitmdump -s har_capture.py --listen-port 8082 --ssl-insecure

Transactions work like JMeter's recorder: the backend appends a marker
{"ts": <epoch seconds>, "name": "Login"} to PS_TXN_FILE when the user starts a
transaction, and every request that starts after it is tagged with that name
in the HAR entry's custom "_transaction" field (HAR allows "_"-prefixed fields).

PS_STATUS_FILE gets a small summary after each response — request count and
requests per transaction — so the UI can poll it without reading the whole HAR.
"""

import base64
import bisect
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path(os.environ.get("PS_HAR_OUTPUT", "/tmp/ps_capture.har"))
TXN_PATH = Path(os.environ["PS_TXN_FILE"]) if os.environ.get("PS_TXN_FILE") else None
STATUS_PATH = Path(os.environ["PS_STATUS_FILE"]) if os.environ.get("PS_STATUS_FILE") else None

# Background traffic of the browser itself (updates, safe browsing, sync …) —
# never part of the user's flow
BROWSER_INTERNAL_HOSTS = {
    "update.googleapis.com", "optimizationguide-pa.googleapis.com", "content-autofill.googleapis.com",
    "safebrowsing.googleapis.com", "clientservices.googleapis.com", "clients1.google.com",
    "clients2.google.com", "clients2.googleusercontent.com", "redirector.gvt1.com", "edgedl.me.gvt1.com",
    "android.clients.google.com", "chromewebstore.googleapis.com",
    "content-signature-2.cdn.mozilla.net", "firefox.settings.services.mozilla.com",
}

# Static files and analytics are counted separately, as they are dropped when the recording is imported
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from projectstack.parser.har_parser import _is_noise
except Exception:  # pragma: no cover — status counts then include static files
    def _is_noise(url: str, headers: dict) -> bool:
        return False


def transaction_at(markers: list[dict], ts: float) -> str:
    """Name of the transaction active at `ts`: the last marker started at or before it ('' if none)."""
    times = [m["ts"] for m in markers]
    i = bisect.bisect_right(times, ts)
    return markers[i - 1]["name"] if i else ""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class HarCapture:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self._starts: list[float] = []      # request start times, parallel to entries
        self._markers: list[dict] = []
        self._markers_mtime = 0.0
        self._flush()  # write empty HAR so the file exists immediately

    # ------------------------------------------------------------------
    # mitmproxy hook
    # ------------------------------------------------------------------

    def response(self, flow) -> None:  # type: ignore[override]
        """Called after a complete request/response cycle."""
        if flow.response is None:
            return

        req = flow.request
        res = flow.response
        if req.pretty_host in BROWSER_INTERNAL_HOSTS:
            return

        # ----- request body -----
        post_data = None
        if req.content:
            mime = req.headers.get("content-type", "")
            try:
                text = req.content.decode("utf-8", errors="replace")
            except Exception:
                text = base64.b64encode(req.content).decode()
            post_data = {"mimeType": mime, "text": text}

        # ----- response body -----
        res_content: dict = {
            "size": len(res.content or b""),
            "mimeType": res.headers.get("content-type", ""),
        }
        if res.content:
            try:
                res_content["text"] = res.content.decode("utf-8", errors="replace")
            except Exception:
                res_content["text"] = base64.b64encode(res.content).decode()
                res_content["encoding"] = "base64"

        started_dt = datetime.fromtimestamp(req.timestamp_start, tz=timezone.utc).isoformat()
        elapsed_ms = max(
            0,
            ((res.timestamp_end or req.timestamp_start) - req.timestamp_start) * 1000,
        )
        wait_ms = max(
            0,
            ((res.timestamp_start or req.timestamp_start) - req.timestamp_start) * 1000,
        )

        entry = {
            "startedDateTime": started_dt,
            "time": elapsed_ms,
            "request": {
                "method": req.method,
                "url": req.pretty_url,
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in req.headers.items()],
                "queryString": [{"name": k, "value": v} for k, v in req.query.items()],
                "postData": post_data,
                "cookies": [],
                "headersSize": -1,
                "bodySize": len(req.content or b""),
            },
            "response": {
                "status": res.status_code,
                "statusText": res.reason or "",
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in res.headers.items()],
                "content": res_content,
                "cookies": [],
                "redirectURL": res.headers.get("location", ""),
                "headersSize": -1,
                "bodySize": len(res.content or b""),
            },
            "cache": {},
            "timings": {"send": 0, "wait": wait_ms, "receive": 0},
        }
        # A request belongs to the transaction that was active when it started,
        # even if its response arrives after the next one was started
        name = transaction_at(self._load_markers(), req.timestamp_start)
        if name:
            entry["_transaction"] = name
        # Keep entries in the order the requests were sent (responses can finish out of order)
        pos = bisect.bisect_right(self._starts, req.timestamp_start)
        self._starts.insert(pos, req.timestamp_start)
        self.entries.insert(pos, entry)
        self._flush()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_markers(self) -> list[dict]:
        if TXN_PATH is None:
            return []
        try:
            mtime = TXN_PATH.stat().st_mtime
            if mtime != self._markers_mtime:
                self._markers = sorted(json.loads(TXN_PATH.read_text(encoding="utf-8")), key=lambda m: m["ts"])
                self._markers_mtime = mtime
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return self._markers

    def _flush(self) -> None:
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "ProjectStack Proxy", "version": "1.0"},
                "entries": self.entries,
            }
        }
        _atomic_write(OUTPUT_PATH, json.dumps(har))
        if STATUS_PATH is not None:
            _atomic_write(STATUS_PATH, json.dumps(self._status()))

    def _status(self) -> dict:
        """Captured requests, and per transaction (in recording order) the ones that will be kept."""
        kept = 0
        per_txn: list[list] = []
        for e in self.entries:
            r = e["request"]
            if _is_noise(r["url"], {h["name"]: h["value"] for h in r["headers"]}):
                continue
            kept += 1
            name = e.get("_transaction", "")
            if per_txn and per_txn[-1][0] == name:
                per_txn[-1][1] += 1
            else:
                per_txn.append([name, 1])
        return {"captured": len(self.entries), "kept": kept, "transactions": per_txn}


addons = [HarCapture()]
