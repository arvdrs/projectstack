"""
Proxy recorder with live transaction naming: requests sent after "start
transaction Login" land in the Login transaction, like JMeter's recorder.
"""

import shutil
import socket
import time
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from projectstack.api import main as api
from projectstack.proxy.har_capture import transaction_at

from tests.test_e2e_replay import server_url  # noqa: F401  (fixture)

MITMDUMP = Path(api.sys.executable).parent / "mitmdump"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


needs_proxy = pytest.mark.skipif(
    not (MITMDUMP.exists() or shutil.which("mitmdump")) or not _port_free(api._PROXY_PORT),
    reason="mitmproxy not installed or the recorder port is busy",
)


def test_transaction_at_uses_request_start_time():
    markers = [{"ts": 10.0, "name": "Open"}, {"ts": 20.0, "name": "Login"}]
    assert transaction_at([], 15) == ""
    assert transaction_at(markers, 5) == ""
    assert transaction_at(markers, 10) == "Open"
    assert transaction_at(markers, 19.9) == "Open"      # started before Login, even if it finishes later
    assert transaction_at(markers, 25) == "Login"


def test_cert_install_never_recreates_an_existing_nss_db(tmp_path, monkeypatch):
    """`certutil -N` on an existing database prompts for its password forever and hung the server."""
    home = tmp_path / "home"
    (home / ".mitmproxy").mkdir(parents=True)
    (home / ".mitmproxy" / "mitmproxy-ca-cert.pem").write_text("cert")
    (home / ".pki" / "nssdb").mkdir(parents=True)
    (home / ".pki" / "nssdb" / "cert9.db").write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "certutil"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$*" in *-N*) while true; do echo "Enter Password:"; done;; esac\n'
        'case "$*" in *-L*) exit 255;; esac\n'
        "exit 0\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")

    started = time.time()
    assert api._install_mitmproxy_cert_to_nss() is True
    assert time.time() - started < 5
    calls = log.read_text()
    assert "-N" not in calls and "-A" in calls


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(api, "REPORTS_BASE_DIR", tmp_path / "reports")
    monkeypatch.setattr(api, "_install_mitmproxy_cert_to_nss", lambda: False)   # leave ~/.pki alone
    c = TestClient(api.app)
    yield c
    if api._proxy_proc and api._proxy_proc.poll() is None:
        c.post("/api/proxy/stop")


def _wait_for(client, kept: int) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        status = client.get("/api/proxy/status").json()
        if status["kept_count"] >= kept:
            return status
        time.sleep(0.1)
    raise AssertionError(f"recorder saw {status}")


@needs_proxy
def test_record_with_transactions(client, server_url):  # noqa: F811
    assert client.post("/api/proxy/transaction", json={"name": "Too early"}).status_code == 400
    res = client.post("/api/proxy/start", json={"transaction": "Open login page", "launch_browser": False})
    assert res.status_code == 200 and res.json()["ok"], res.text
    assert res.json()["chromium_launched"] is False
    assert client.get("/api/proxy/status").json()["current_transaction"] == "Open login page"

    browser = httpx.Client(proxy=f"http://127.0.0.1:{api._PROXY_PORT}", timeout=10)
    browser.get(f"{server_url}/login")
    browser.get(f"{server_url}/static/app.css")          # static file: captured, but not imported
    _wait_for(client, 1)

    status = client.post("/api/proxy/transaction", json={"name": "Load orders"}).json()
    assert status["current_transaction"] == "Load orders"
    browser.get(f"{server_url}/api/orders")
    browser.get(f"{server_url}/api/orders")
    status = _wait_for(client, 3)
    assert status["request_count"] == 4
    assert status["transactions"] == [
        {"name": "Open login page", "request_count": 1},
        {"name": "Load orders", "request_count": 2},
    ]
    assert client.post("/api/proxy/transaction", json={"name": "  "}).status_code == 400
    client.post("/api/proxy/transaction", json={"name": "Checkout"})   # started, nothing sent yet
    assert client.get("/api/proxy/status").json()["transactions"][-1] == {"name": "Checkout", "request_count": 0}
    browser.close()

    stop = client.post("/api/proxy/stop").json()
    assert stop["ok"], stop
    assert stop["request_count"] == 3
    assert stop["transaction_names"] == {"0": "Open login page", "1": "Load orders", "2": "Load orders"}
    sid = stop["session_id"]

    # The grouping becomes Transaction Controllers in the script
    gen = client.post(f"/api/generate/{sid}", json={
        "no_transactions": False, "custom_transaction_names": stop["transaction_names"],
    }).json()
    assert [t["name"] for t in gen["transactions"]] == ["TC_01_Open_login_page", "TC_02_Load_orders"]

    # The names live in the recording, so filtering and re-uploading keep them
    host = server_url.split("//")[1]
    flt = client.post(f"/api/filter/{sid}", params={"base_url": host}).json()
    assert flt["transaction_names"] == stop["transaction_names"]
    har = client.get(f"/api/har/{sid}")
    assert har.status_code == 200 and '"_transaction": "Load orders"' in har.text.replace('":"', '": "')
    again = client.post("/api/upload", files={"file": ("rec.har", har.content, "application/json")}).json()
    assert again["transaction_names"] == stop["transaction_names"]


@needs_proxy
def test_recorder_rejects_bad_start_url(client):
    res = client.post("/api/proxy/start", json={"start_url": "javascript:alert(1)", "launch_browser": False})
    assert res.status_code == 400
    assert not (api._proxy_proc and api._proxy_proc.poll() is None)
