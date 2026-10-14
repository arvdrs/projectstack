"""Live run statistics: incremental tailing of a JMeter CSV results file."""

from projectstack.runner.live_stats import LiveRun

HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,sentBytes,grpThreads,allThreads\n"


def _row(ts, ms, label, ok=True, users=2):
    return f'{ts},{ms},{label},{200 if ok else 500},"OK, fine",T 1-1,text,{str(ok).lower()},,10,10,{users},{users}\n'


def test_tails_file_incrementally_including_partial_rows(tmp_path):
    jtl = tmp_path / "results.jtl"
    live = LiveRun(jtl, {"threads": 2, "rampup": 1, "loops": -1, "duration_seconds": 10})
    assert live.snapshot()["status"] == "starting"          # file not created yet

    jtl.write_text(HEADER + _row(1000, 20, "GET /a") + _row(1500, 40, "GET /b", ok=False))
    snap = live.snapshot()
    assert snap["status"] == "running"
    assert snap["totals"]["requests"] == 2 and snap["totals"]["errors"] == 1
    assert snap["totals"]["active_users"] == 2

    # A row written in two pieces is only counted once it is complete
    row = _row(2100, 60, "GET /a")
    with open(jtl, "a") as f:
        f.write(row[:15])
    assert live.snapshot()["totals"]["requests"] == 2
    with open(jtl, "a") as f:
        f.write(row[15:])
    snap = live.snapshot()
    assert snap["totals"]["requests"] == 3
    assert [p["rps"] for p in snap["timeline"]] == [2, 1]
    labels = {l["label"]: l for l in snap["labels"]}
    assert labels["GET /a"]["requests"] == 2 and labels["GET /a"]["avg_ms"] == 40.0
    assert snap["recent"][0]["label"] == "GET /a"


def test_recreated_file_restarts_reading(tmp_path):
    jtl = tmp_path / "results.jtl"
    jtl.write_text(HEADER + _row(1000, 20, "GET /a") * 5)
    live = LiveRun(jtl, {"threads": 1, "rampup": 1, "loops": 1, "duration_seconds": 0}, expected_requests=10)
    assert live.snapshot()["totals"]["requests"] == 5
    jtl.write_text(HEADER + _row(1000, 20, "GET /a"))       # shorter → new file
    assert live.snapshot()["totals"]["requests"] == 6
