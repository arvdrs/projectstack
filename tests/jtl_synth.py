"""
Synthetic JMeter result files with known contents, for tests and benchmarks.

flow_samples()  — a small, fully known user journey per thread: plain requests,
                  a Transaction Controller (parent sample after its children, as
                  JMeter writes it with "Generate parent sample" off), redirect
                  sub-results ("Search-0"), and planted errors.
write_csv / write_xml — write samples as a CSV JTL (any column set, header or
                  not, any delimiter, optionally gzipped) or an XML JTL.
bulk_csv()      — millions of rows quickly (numpy), for the benchmark.
"""

from __future__ import annotations

import csv
import gzip
import io
import random
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

JMETER_COLUMNS = ["timeStamp", "elapsed", "label", "responseCode", "responseMessage", "threadName", "dataType",
                  "success", "failureMessage", "bytes", "sentBytes", "grpThreads", "allThreads", "URL", "Latency",
                  "IdleTime", "Connect"]
START_MS = 1_760_000_000_000      # 2025-10-09T08:53:20Z
TC_MSG = "Number of samples in transaction : {n}, number of failing samples : {f}"


def sample(ts, elapsed, label, thread, users, ok=True, code="200", msg="OK", failure="", connect=5, url=""):
    return {
        "timeStamp": ts, "elapsed": elapsed, "label": label, "responseCode": code,
        "responseMessage": msg, "threadName": thread, "dataType": "text", "success": "true" if ok else "false",
        "failureMessage": failure, "bytes": 1200, "sentBytes": 300, "grpThreads": users, "allThreads": users,
        "URL": url or f"https://shop.test/{label.lower().replace(' ', '-')}", "Latency": max(1, elapsed - 3),
        "IdleTime": 0, "Connect": connect, "children": [],
    }


def flow_samples(users: int = 3, iterations: int = 4, seed: int = 1, error_every: int = 0) -> list[dict]:
    """
    Per thread and iteration: Home → [TC "Login": Login page, Login submit] → Search
    (with redirect child "Search-0") → Step-1 (a real request whose name only looks
    like a sub-result). Every `error_every`-th Login submit fails with a 500.
    """
    rng = random.Random(seed)
    rows: list[dict] = []
    n = 0
    for u in range(users):
        thread = f"Thread Group 1-{u + 1}"
        t = START_MS + u * 1000
        for _ in range(iterations):
            home = rng.randint(80, 200)
            rows.append(sample(t, home, "Home", thread, users))
            t += home + 100
            page = rng.randint(50, 150)
            child1 = sample(t, page, "Login page", thread, users)
            t += page + 50
            submit = rng.randint(100, 400)
            n += 1
            fail = bool(error_every) and n % error_every == 0
            child2 = sample(t, submit, "Login submit", thread, users, ok=not fail,
                            code="500" if fail else "200", msg="Internal Server Error" if fail else "OK")
            t += submit
            rows += [child1, child2]
            parent = sample(child1["timeStamp"], page + submit, "Login", thread, users, ok=not fail,
                            code="500" if fail else "200",
                            msg=TC_MSG.format(n=2, f=1 if fail else 0))
            parent["dataType"] = ""
            rows.append(parent)                      # JMeter writes the parent after its children
            t += 100
            search = rng.randint(120, 300)
            redirect = sample(t, search - 20, "Search-0", thread, users, code="302", msg="Found")
            s = sample(t, search, "Search", thread, users)
            s["children"] = [redirect]
            rows += [redirect, s]                    # CSV: sub-result rows sit next to the parent
            t += search + 100
            step = rng.randint(30, 90)
            rows.append(sample(t + 5000, step, "Step-1", thread, users))   # outside any Step sample
            rows.append(sample(t + 6000, step + 10, "Step", thread, users))
            t += 7000
    rows.sort(key=lambda r: r["timeStamp"])
    return rows


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None, header: bool = True,
              delimiter: str = ",", gz: bool = False, extra: dict[str, str] | None = None) -> Path:
    columns = columns or JMETER_COLUMNS
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    extra = extra or {}
    if header:
        w.writerow(columns + list(extra))
    for r in rows:
        w.writerow([r.get(c, "") for c in columns] + list(extra.values()))
    data = buf.getvalue().encode()
    path.write_bytes(gzip.compress(data) if gz else data)
    return path


def write_xml(path: Path, samples: list[dict]) -> Path:
    """XML JTL. Transaction parents nest their children (parent-sample mode); requests nest sub-results."""
    def el(s: dict, depth: int) -> str:
        tag = "sample" if s["responseMessage"].startswith("Number of samples") else "httpSample"
        attrs = {"t": s["elapsed"], "it": s["IdleTime"], "lt": s["Latency"], "ct": s["Connect"], "ts": s["timeStamp"],
                 "s": s["success"], "lb": s["label"], "rc": s["responseCode"], "rm": s["responseMessage"],
                 "tn": s["threadName"], "dt": s["dataType"], "by": s["bytes"], "sby": s["sentBytes"],
                 "ng": s["grpThreads"], "na": s["allThreads"]}
        inner = ""
        if s["success"] == "false":
            inner += ("<assertionResult><name>Status</name><failure>true</failure><error>false</error>"
                      f"<failureMessage>{escape('Test failed: code expected to equal /200/')}</failureMessage>"
                      "</assertionResult>")
        inner += "".join(el(c, depth + 1) for c in s.get("children", []))
        inner += f"<responseData class=\"java.lang.String\">{escape('<html>lots of body</html>')}</responseData>"
        inner += f"<java.net.URL>{escape(s['URL'])}</java.net.URL>"
        return f"<{tag} " + " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items()) + f">{inner}</{tag}>\n"

    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<testResults version="1.2">\n'
                    + "".join(el(s, 0) for s in samples) + "</testResults>\n")
    return path


def bulk_csv(path: Path, rows: int = 1_000_000, labels: int = 20, users: int = 50, minutes: int = 30,
             seed: int = 7) -> Path:
    """A large CSV JTL with JMeter's default columns: ramp-up, steady load, 1% errors."""
    import numpy as np
    rng = np.random.default_rng(seed)
    dur = minutes * 60_000
    ts = np.sort(rng.integers(0, dur, rows)) + START_MS
    ramp = dur // 6
    active = np.minimum(users, 1 + (ts - START_MS) * users // ramp).astype(int)
    label_idx = rng.integers(0, labels, rows)
    base = 80 + label_idx * 15
    elapsed = (rng.lognormal(0, 0.45, rows) * base).astype(int) + 1
    ok = rng.random(rows) >= 0.01
    thread = (rng.integers(0, 1 << 30, rows) % active) + 1
    names = [f"Transaction {i:02d} /api/endpoint/{i}" for i in range(labels)]
    with open(path, "w", newline="") as f:
        f.write(",".join(JMETER_COLUMNS) + "\n")
        lines = []
        for i in range(rows):
            e = int(elapsed[i])
            good = bool(ok[i])
            lines.append(
                f"{ts[i]},{e},{names[label_idx[i]]},{200 if good else 500},{'OK' if good else 'Internal Server Error'},"
                f"Thread Group 1-{thread[i]},text,{'true' if good else 'false'},,1534,412,{active[i]},{active[i]},"
                f"https://shop.test/api/endpoint/{label_idx[i]},{max(1, e - 4)},0,{3 if good else 9}\n"
            )
            if len(lines) >= 100_000:
                f.writelines(lines)
                lines.clear()
        f.writelines(lines)
    return path


# ---------------------------------------------------------------------------
# Scenarios with planted problems (detector tests)
# ---------------------------------------------------------------------------

SCENARIO_START_MS = 1_760_000_400_000      # on a minute boundary, so planted changes align with buckets


def ramp_steady(users: int = 50, ramp_s: int = 300, steady_s: int = 2100, down_s: int = 120):
    """Linear ramp-up, steady load, linear ramp-down."""
    def fn(s: int) -> int:
        if s < ramp_s:
            return max(1, -(-users * (s + 1) // ramp_s))
        if s < ramp_s + steady_s:
            return users
        left = ramp_s + steady_s + down_s - s
        return max(0, -(-users * left // down_s))
    fn.total_s = ramp_s + steady_s + down_s
    return fn


def stepping(start: int = 10, step: int = 10, steps: int = 10, hold_s: int = 180):
    def fn(s: int) -> int:
        return start + step * min(steps - 1, s // hold_s)
    fn.total_s = steps * hold_s
    return fn


def scenario_csv(path: Path, users_fn, *, rate_per_user: float = 2.0, capacity_users: int | None = None,
                 latency_ms=lambda s, u: 200.0, error_rate=lambda s: 0.005, error_code=("500", "Internal Server Error"),
                 connect_ms=lambda s: 5.0, tps_factor=lambda s: 1.0, labels: int = 6, seed: int = 3,
                 with_tc: bool = False, start_ms: int = SCENARIO_START_MS) -> Path:
    """
    A closed-workload run, second by second: `users_fn(s)` active users each sending
    `rate_per_user` requests/s, capped at `capacity_users` worth of throughput;
    response times log-normal around `latency_ms(s, users)`; failures with
    probability `error_rate(s)`. `with_tc` adds a Transaction Controller parent
    sample after every pair of requests.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    names = [f"Step {i + 1} /app/page/{i + 1}" for i in range(labels)]
    lines = [",".join(JMETER_COLUMNS) + "\n"]
    for s in range(users_fn.total_s):
        u = users_fn(s)
        if u <= 0:
            continue
        tps = min(u, capacity_users or u) * rate_per_user * tps_factor(s)
        n = int(rng.poisson(tps))
        if n == 0:
            continue
        ts = np.sort(rng.integers(0, 1000, n)) + start_ms + s * 1000
        median = latency_ms(s, u)
        el = (rng.lognormal(np.log(median), 0.3, n)).astype(int) + 1
        fail = rng.random(n) < error_rate(s)
        lab = rng.integers(0, labels, n)
        thr = rng.integers(1, u + 1, n)
        con = np.maximum(0, rng.normal(connect_ms(s), connect_ms(s) * 0.1 + 0.5, n)).astype(int)
        for i in range(n):
            ok = not fail[i]
            code, msg = ("200", "OK") if ok else error_code
            lines.append(f"{ts[i]},{el[i]},{names[lab[i]]},{code},{msg},Thread Group 1-{thr[i]},text,"
                         f"{'true' if ok else 'false'},,1500,400,{u},{u},https://shop.test/app/page/{lab[i] + 1},"
                         f"{max(1, el[i] - 2)},0,{con[i]}\n")
            if with_tc and i % 2 == 1:
                lines.append(f"{ts[i]},{el[i] + el[i - 1]},TC Checkout,{code},"
                             f"\"Number of samples in transaction : 2, number of failing samples : {0 if ok else 1}\","
                             f"Thread Group 1-{thr[i]},,{'true' if ok else 'false'},,3000,800,{u},{u},,0,0,0\n")
    path.write_text("".join(lines))
    return path
