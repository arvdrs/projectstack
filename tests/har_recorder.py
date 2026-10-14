"""Drive the mock app like a browser and record the traffic as a HAR file."""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

import httpx


class HarRecorder:
    def __init__(self, base_url: str):
        self.client = httpx.Client(base_url=base_url, follow_redirects=False, timeout=10)
        self.entries: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        started = datetime.now(timezone.utc)
        resp = self.client.request(method, url, **kwargs)
        req = resp.request
        body = req.content.decode() if req.content else None
        set_cookies = resp.headers.get_list("set-cookie")
        self.entries.append({
            "startedDateTime": started.isoformat().replace("+00:00", "Z"),
            "time": 5,
            "request": {
                "method": method,
                "url": str(req.url),
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in req.headers.items()],
                "queryString": [{"name": k, "value": v} for k, v in parse_qsl(urlparse(str(req.url)).query)],
                "cookies": [],
                **({"postData": {"mimeType": req.headers.get("content-type", ""), "text": body}} if body else {}),
            },
            "response": {
                "status": resp.status_code,
                "statusText": resp.reason_phrase,
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in resp.headers.multi_items()],
                "cookies": [
                    {"name": c.split("=", 1)[0], "value": c.split("=", 1)[1].split(";", 1)[0]}
                    for c in set_cookies
                ],
                "content": {"size": len(resp.content), "mimeType": resp.headers.get("content-type", ""),
                            "text": resp.text},
                "redirectURL": resp.headers.get("location", ""),
            },
        })
        return resp

    def save(self, path) -> None:
        har = {"log": {"version": "1.2", "creator": {"name": "test", "version": "1"}, "entries": self.entries}}
        with open(path, "w") as f:
            json.dump(har, f)


def record_checkout_flow(base_url: str, har_path) -> None:
    """Login (CSRF form) → OAuth-style redirect → API calls with bearer/XSRF/nonce."""
    rec = HarRecorder(base_url)
    page = rec.request("GET", "/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    nonce = re.search(r'nonce: "([^"]+)"', page.text).group(1)
    login = rec.request("POST", "/login", data={"csrf_token": csrf, "username": "alice", "password": "pw"})
    cb = rec.request("GET", login.headers["location"])
    token = cb.json()["access_token"]
    xsrf_cookie = rec.client.cookies.get("XSRF-TOKEN")
    from urllib.parse import unquote
    auth = {"Authorization": f"Bearer {token}"}
    orders = rec.request("GET", "/api/orders", headers=auth).json()["items"]
    silver = next(o["id"] for o in orders if o["name"] == "Silver")
    ts = int(time.time() * 1000)
    placed = rec.request(
        "POST", f"/api/orders/{silver}/checkout?_={ts}",
        headers={**auth, "X-XSRF-TOKEN": unquote(xsrf_cookie), "X-Nonce": nonce,
                 "Content-Type": "application/json"},
        content=json.dumps({"orderId": silver, "ts": ts}),
    ).json()
    rec.request("GET", f"/api/receipt/{placed['orderNo']}", headers=auth)
    rec.save(har_path)
