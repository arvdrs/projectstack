# ProjectStack

Turns browser recordings (HAR files, or the built-in proxy recorder) into JMeter
test plans with automatic correlation, runs them, and analyses the results.

- Web app: `cd projectstack/ui-web && ./start.sh`, then open http://localhost:6292
- CLI: `pip install -e .`, then `pstack --help`

## Results analysis

`pstack analyze` loads a JMeter result file (JTL) into ClickHouse, finds the
steady state, computes per-transaction statistics, runs deterministic detectors
(SLA breaches, latency and error-rate change points, saturation, error bursts,
load-generator problems) and writes a compact `findings.json`. It is built for
large files (hundreds of MB to several GB). Every number is computed by code from
the raw samples; nothing is estimated by a model.

### Setup

ProjectStack uses a local ClickHouse by default:

```bash
docker compose -f deploy/clickhouse/docker-compose.yml up -d
pstack db status
```

```
Connected to ClickHouse 25.8.33.6 at http://localhost:8123/projectstack as projectstack
  run_labels                       20 rows      1.05 KiB on disk
  runs                              1 rows      2.79 KiB on disk
  samples                   1,000,000 rows     50.98 MiB on disk
  schema_migrations                 1 rows      427.00 B on disk
  1 analysed run(s); raw samples kept 180 days
```

The schema is created automatically on first use, from the versioned SQL files in
`projectstack/results/schema/`.

### Analyse a run

```bash
pstack analyze results.jtl --scenario checkout --tag build-119 --sla p95=1000,err=1
```

```
Run 20261013-084506-build-119   scenario: checkout   tag: build-119
346,975 samples · 41m 59s (2025-10-09 14:30:01 → 15:11:59) · peak 50 users (allThreads)
Steady state (auto): 14:35:42 → 15:09:08 (33m 26s, 50 users from allThreads) — stats below cover this window only

Requests (ms)                         Samples      Avg      p50      p90      p95      p99      Max     Err%      TPS
─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Step 6 /app/page/6                     33,421      514      510      876      990    1,239    1,916    1.89%    16.66
…
All requests                          200,986      515      511      879      992    1,238    2,493    1.86%   100.19

Transactions (ms)                     Samples      Avg      p50      p90      p95      p99      Max     Err%      TPS
─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
TC Checkout                           100,001    1,029    1,116    1,649    1,801    2,108    3,242    1.83%    49.85

Top errors (full run, requests)
      676×  Step 3 /app/page/3                503   Service Unavailable   14:31:09–15:11:06
…
Verdict: FAIL — SLA breached
Findings (4)
  ✖ critical  SLA breach: TC Checkout p95 1,801 ms > 1,000 ms
               Measured over the steady state (auto window).
  ✖ critical  SLA breach: error rate 1.86% > 1%
               Measured over the steady state (auto window).
  ✖ critical  p95 rose from 362 ms to 1,066 ms at t+17m59s
               Response times stepped up 195% while the load stayed at about 50 users; most affected: Step 1 /app/page/1, …
  ✖ critical  Error burst at t+29m59s for 2 min: up to 25.4% failed, mostly 503 Service Unavailable
               2,982 of 11,985 requests failed in this window, against a typical error rate of 0.40%; the rate returned to normal afterwards.

Loaded 346,975 rows in 0.7s (482,580 rows/s) · metrics 0.3s · ClickHouse http://localhost:8123/projectstack
Artifacts: /home/you/.projectstack/runs/20261013-084506-build-119
```

| Option | Meaning |
| --- | --- |
| `--steady START:END` | Window for the statistics instead of the detected steady state, e.g. `5m:30m` (offsets from the first sample; `s`, `m`, `h`, plain numbers are seconds), `5m:`, `:30m`, `0:` for the whole run, or epoch milliseconds. |
| `--sla SPEC` | `p95=3000,err=1`, `p95[Login]=2000`, `tps>=50`. Judged over the steady state. Same SLA format as the web app. |
| `--tag`, `--scenario` | Build/label and scenario name, stored with the run (the scenario defaults to the file name). |
| `--stats both\|parent\|child` | Transaction Controller parents, their child requests, or both (default, shown separately). |
| `--props user.properties` | Column layout for CSV files written without a header row. |
| `--max-bad-rows N` | Skip up to N malformed rows instead of failing. |
| `--json`, `--all` | Machine-readable output; list every label instead of the first 25. |
| `--clickhouse URL` | Use another server for this command (see below). |

### Steady state

Unless `--steady` is given, statistics and SLA checks cover the steady state:
the longest stretch where active threads (`allThreads`) stay within 5% of their
peak, trimmed by 10% of its length (at most 60 s) at each end. Stepping profiles
get their top step. Without an `allThreads` column, distinct thread names per
10 s are used. If the load never held near its peak for at least a minute and 20%
of the run, the full run is used and the output says so. The window used is always
printed and stored in `meta.json` and `findings.json`.

### Findings and verdict

| Detector | Reports |
| --- | --- |
| SLA breach | Each `--sla` target missed in the steady state (response time per transaction — or per request when the script has no Transaction Controllers — error rate, throughput). |
| Latency change | The bucket where p95 steps up and stays up (≥ +25% and ≥ +50 ms), the levels before/after, the load at the time and the labels that changed most. |
| Error-rate change | A lasting rise in the error rate (≥ +2 points and doubled). |
| Saturation | The user count at the knee where throughput stopped growing with the load (or fell) while p95 climbed (needs a ramp or steps through ≥ 4 load levels). |
| Error burst | Windows where the error rate spiked (≥ 3× typical and ≥ +5 points) and then recovered, with the dominant response codes, messages and labels. |
| Injector: Connect time | Connect time doubling (and ≥ +20 ms): sockets/ports or network on the load generator, or the server's backlog. |
| Injector: throughput | Throughput ≥ 20% below normal with users constant and latency flat — the load generator stopped sending. |
| Regression | With `--baseline`: labels whose p95 or error rate worsened against the baseline run at comparable load (see [Compare runs](#compare-runs)). |
| Correlation | Script problems: requests sent with an extractor's `NOT_FOUND` default, 401/403 responses that start mid-run or from the start, redirects to a login page, authentication-error messages (see [Correlation health check](#correlation-health-check)). |

Change points use binary segmentation on the timeline (one value per bucket).
A detector that lacks the data it needs (for example no `Connect` column) says
why it was skipped. Thresholds can be tuned with the `results_detectors` setting,
a JSON object such as `{"latency_rel_increase": 0.5, "burst_factor": 4}` (names in
`projectstack/results/detectors/__init__.py`).

**Verdict**: `fail` when an SLA target is missed; `at_risk` when the SLA is met
but other problems were found (a regression against the baseline included);
`pass` otherwise; `no_sla` when no SLA was given — the run is then described, not
judged.

### findings.json

Each run's folder gets a `findings.json`: run facts, the steady-state window, the
verdict and SLA checks, overall and per-label statistics, a timeline, top errors,
every finding with its evidence values, and the detectors that were skipped. It
is what reports and the AI narrative work from, and it is kept under 30 KB:
with many labels or a long timeline it keeps the labels named in findings plus
the busiest ones and merges timeline buckets (highest p95 of each group, error
rate recomputed from counts — percentiles are never averaged), listing what was
cut under `truncated`.

### Reports

```bash
pstack report 20261013-0845                       # technical report, HTML, in the run's folder
pstack report 20261013-0845 --audience exec       # one-page summary for decision makers
pstack report 20261013-0845 --format both         # report_tech.html and report_tech.md
pstack report 20261013-0845 --no-llm              # rule-based text; nothing leaves the machine
pstack report runs/…/findings.json --out -        # from a published artifact, Markdown to stdout
```

A report is built from the run's `findings.json` alone, so it needs no database
and CI can render it from published artifacts. The **technical** report has the
verdict and SLA checks, a timeline chart (active users, throughput, p95 and error
rate as four panels on one time axis, with finding windows and the steady state
shaded; hover for values, or open the table under it), per-transaction and
per-request tables, every finding with its evidence, likely root causes ranked by
confidence, recommended next tests and the top errors. The **executive** summary
is one page: verdict, headline, four figures, key points, business impact and the
recommended action. HTML reports are single self-contained files (no external
assets) and follow the system's light or dark mode.

The detectors decide; the AI only explains. The narrative text (summary,
timeline observations, what each finding means, root causes, next tests — or the
headline and key points for the executive summary) is written by the AI provider
configured for reports, which receives `findings.json` and nothing else (no raw
samples, request or response data). It answers in JSON that fills
fixed slots in the template, so it cannot change the findings, the verdict or the
tables.

**Number check.** The prompts tell the model to cite only numbers that appear in
`findings.json`, and every number in its answer is then checked against the
document (allowing rounding, ms→s and s→min conversions, and differences or
ratios of two values it contains; label names, the run id and tag are ignored, and
clock times and `t+…` offsets are checked against the run's timestamps). Numbers
that do not match are listed at the top of the report under "Check these figures".
With `--strict-numbers` the model is asked once to correct them, and if any remain
the command fails with exit code 3 and writes nothing.

Without an AI provider, or when the provider fails, the report is written with
the rule-based narrative and says so (and `pstack report` prints a warning); with
`--provider` or `--strict-numbers` a provider failure is an error instead. An AI
narrative is saved as `narrative_<audience>.json` in the run's folder, together
with the provider, model, prompt version, token usage and the number-check result,
and reused while `findings.json` and the prompt are unchanged (`--fresh` asks
again), so rendering the other format costs nothing.

| Exit code | Meaning |
|---|---|
| 0 | Report written (whatever the run's verdict) |
| 2 | Run not found, unreadable findings.json, or the chosen AI provider failed |
| 3 | `--strict-numbers`: the AI narrative cited numbers that are not in findings.json |

### AI providers

Correlation, reports and the `ask` command share one AI layer
(`projectstack/llm/`) with Claude, OpenAI, Groq, Gemini and Ollama behind one
interface (`complete(system, prompt)` and `complete_with_tools(system, messages,
tools)`). Each task picks its provider separately:

| Task | Default |
|---|---|
| `correlation` | the provider chosen in the web app (`ai_provider`) |
| `report`, `ask` | Claude (`claude-opus-5-5`) when an Anthropic key is set (`anthropic_api_key` or `ANTHROPIC_API_KEY`), otherwise Gemini when a Gemini key is set, otherwise `ai_provider` |

Override per task or per provider model in `~/.projectstack_settings.json`:

```json
{
  "llm_tasks":  "{\"report\": \"claude\", \"ask\": \"claude\", \"correlation\": \"ollama/mistral\"}",
  "llm_models": "{\"claude\": \"claude-opus-5-5\", \"groq\": \"llama-3.3-70b-versatile\"}"
}
```

or for one report: `pstack report <run> --provider groq/llama-3.3-70b-versatile`.
Tool calling (used by `ask`) works with all five providers.
Claude requests use structured outputs (the JSON schema is enforced) with medium
effort; the other providers are asked for JSON and their answers are validated and
reshaped. Prompts are versioned files in `projectstack/llm/prompts/`
(`report_tech.v1.md`, `report_exec.v1.md`, `correlation_detect.v1.md`); a change
to a prompt is a new version file, and each report records the version it used.

### Compare runs

```bash
pstack compare 20261013-1019 20261013-1020         # baseline first, then the run to judge
pstack compare last-green 20261013-1020            # the latest passing run of the same scenario
pstack compare <a> <b> --format md --out cmp.md    # Markdown (or --format json)
pstack analyze results.jtl --scenario search --baseline last-green   # compare while analysing
```

```
Baseline  20261013-101922-build-120  (build-120)  verdict pass
Current   20261013-101934-build-121  (build-121)
Load      40 → 40 users, 39.9 → 39.6 transactions/s — comparable

Transactions                                   Samples          p95 (ms)    Δ p95           Err %    Δ err  Status
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
TC Checkout                            43,823→43,508        584→873        +49.5%    0.52→0.46    -0.06pt  ✖ p95
…
7 regressed, 0 improved, 7 compared (regression: p95 +10% and +50 ms, or errors +1 pt)
```

Both runs are compared on their steady-state statistics, label by label (exact
percentiles, never averaged). A label has **regressed** when

- its p95 rose by at least 10% **and** at least 50 ms (the floor keeps fast
  requests from flagging on noise), or
- its error rate rose by at least 1 percentage point,

and both runs have at least 100 samples of it. It is critical from +50% p95 or +5
points of errors. Regressions only count when the **load was comparable**: the
steady-state users of the two runs within 10% (throughput when user counts are
unknown). With different loads the differences are still listed, marked as not
comparable, and do not fail CI. Labels that exist in only one run are listed
separately.

Change the thresholds per run with `--regression p95=15%,p95ms=100,err=2,samples=50,load=20`,
or for every run with the `results_regression` setting
(`{"p95_pct": 15, "p95_ms": 100, "err_pp": 2, "min_samples": 50, "load_pct": 20}`).

**last-green** is the most recent run of the same `--scenario` whose verdict was
`pass` (created before the run being judged). A run that regressed is `at_risk`,
so it never becomes the next baseline and regressions cannot creep in one build
at a time. Runs without an SLA are never green; compare those with an explicit
run id.

When the baseline's folder is not on the machine (a fresh CI runner using a
shared ClickHouse), its statistics are recomputed from its samples over the
window it was analysed with, as long as they are within the retention period.
`pstack analyze --baseline` writes `comparison.json` (every label) next to
`findings.json`, which keeps a compact `baseline` block and the regression
findings that reports explain.

### CI mode

```bash
pstack analyze results.jtl --scenario checkout --tag "$CI_COMMIT_SHORT_SHA" \
    --sla p95=1000,err=1 --baseline last-green --ci --artifacts perf
```

`--ci` analyses the run, compares it with the baseline, writes `report.html`
(the technical report) and `summary.md`, prints the summary to standard output
(tables and progress go to standard error) and exits with:

| Exit code | Meaning |
|---|---|
| 0 | Passed, or nothing to gate on (no SLA and no comparable baseline). Runs that are `at_risk` for other findings pass, with a warning in the summary. |
| 1 | SLA breach |
| 2 | Regression against the baseline at comparable load |
| 3 | SLA breach and regression |
| 4 | The analysis failed: unreadable file, ClickHouse unreachable, invalid options |

Command-line mistakes exit with 4 under `--ci` (not click's usual 2, which would
read as a regression). A missing baseline (no green run yet) or one whose data is
gone is reported in the summary and does not fail the job. `--artifacts DIR`
copies `report.html`, `summary.md`, `findings.json` and `comparison.json` there;
`--no-llm` writes the report narrative without AI (otherwise the `report` task's
provider is used when configured, falling back to the rule-based text).

`summary.md` is sized for a merge-request comment:

```markdown
### ❌ Regression against the baseline · performance test: search

`20261013-101934-build-121` · build-121 · 40 users · 24m 00s run · steady state 18m 19s · verdict `at_risk` · exit code 2
Baseline `20261013-101922-build-120` (build-120) — load comparable; 7 regressed, 0 improved

| SLA | Target | Measured | Result |
|---|---:|---:|---|
| TC Checkout p95 | 3,000 ms | 873 ms | ✅ |
| Error rate | 1% | 0.49% | ✅ |

**Regressions against the baseline** (p95 +10% and +50 ms, or errors +1 pt)

| Label | p95 baseline → now | Δ p95 | Errors baseline → now |
|---|---:|---:|---:|
| TC Checkout | 584 → 873 ms ❌ | +50% | 0.52% → 0.46% |
…

📄 [Full report](https://gitlab.example.com/team/app/-/jobs/123/artifacts/file/perf/report.html)
```

In GitLab CI the report link points at the job artifact (from `CI_JOB_URL` and
`CI_PROJECT_DIR`). An example job, posting the summary as a merge-request comment
with a project access token stored in `PERF_BOT_TOKEN`:

```yaml
performance:
  stage: test
  script:
    - jmeter -n -t plan.jmx -l results.jtl
    - pstack analyze results.jtl --scenario checkout --tag "$CI_COMMIT_SHORT_SHA"
        --sla p95=1000,err=1 --baseline last-green --ci --artifacts perf || status=$?
    - |
      if [ -n "$CI_MERGE_REQUEST_IID" ] && [ -f perf/summary.md ]; then
        curl -sf -X POST -H "PRIVATE-TOKEN: $PERF_BOT_TOKEN" \
          --data-urlencode "body@perf/summary.md" \
          "$CI_API_V4_URL/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes"
      fi
    - exit ${status:-0}
  artifacts:
    when: always
    paths: [perf/]
  variables:
    PSTACK_CLICKHOUSE_URL: "clickhouse://perf_ci:$CH_PASSWORD@clickhouse.internal:8443/projectstack?secure=1"
```

### Ask questions about a run

```bash
pstack ask 20261013-0845 "During the error burst, were all requests affected equally?"
pstack ask 20261013-0845 "Which request slowed down first, and by how much?" --show-queries
pstack ask <run> "…" --provider claude --max-queries 6 --json
```

```
- Were requests affected equally? Yes. During the error burst (t+29m59s to t+31m59s) all 6 request steps
  failed at between 23.76% and 24.94% (findings, [Q2]).
- …

Q1 — 6 row(s), 0.01s
    SELECT label, count() AS samples, countIf(NOT success) AS errors, … FROM samples WHERE kind = 'request' AND ts >= …
Q2 — 6 row(s), 0.02s
    …
Answered by gemini/gemini-flash-lite-latest with prompt ask.v1 · 4 queries · numbers: all 14 numbers found in findings.json or the query results
```

The AI starts from the run's `findings.json` and has one tool, `run_sql`, for
read-only queries over the run's samples. It answers with the evidence, citing
its queries as [Q1], [Q2] … or (findings); every number in the answer is checked
against `findings.json` and the query results. The whole exchange (question,
queries, results, answer, provider and token use) is saved in the run's folder
under `ask/`.

Each query is checked before it runs, by ClickHouse's own parser: it must be a
single `SELECT` that reads only `samples` and `run_labels`, with no table
functions (`url`, `file`, `remote`, `s3` …), `FORMAT`, `INTO OUTFILE` or
`SETTINGS`. The server then runs it read-only (`readonly = 1`), with both tables
filtered to this run, a 10-second time limit, a memory cap and at most 200 rows
returned. Raw samples reach the model only as the aggregates its own queries
return. On a team server, set `clickhouse_ask_user` / `clickhouse_ask_password`
to a SELECT-only user whose profile has `readonly = 2` (read-only, but query
limits can still be set per query).

`ask` uses the provider of the `ask` task (Claude when an Anthropic key is set,
otherwise Gemini). Small local models are unreliable at multi-step tool use, so
`ask` warns when it runs on Ollama.

### Correlation health check

```bash
pstack analyze results.jtl --scenario checkout --jmx plan.jmx    # check while analysing
pstack health 20261013-1048                                       # or later, for any run
```

```
✖ 401/403 responses began at t+9m57s for 1 request after they had been succeeding
  Requests were accepted at first and rejected later — the pattern of a token or session that expires
  (here after about 10 minutes). C_BEARER_TOKEN is set by JSON Extractor “Extract C_BEARER_TOKEN”
  `$.access_token` on “POST /v1/login”: if that runs once per user (login outside the loop), extract it
  again each iteration or add a token refresh before it expires.
    GET /v1/orders: 300 samples
    C_BEARER_TOKEN ← JSON Extractor “Extract C_BEARER_TOKEN” `$.access_token` on “POST /v1/login”
✖ Correlation: C_ORDER_NO was missing in 1 request (300 samples sent NOT_FOUND)
  … C_ORDER_NO comes from “GET /v1/orders”, which itself failed 50% of the time — fix that request first.
```

Every analysis looks for signs that the script, not the system, is failing:

| Pattern | How it is found |
|---|---|
| Missing value | A request was sent with an extractor's default `NOT_FOUND` — from the marker below, or `NOT_FOUND` in the request URL |
| 401/403 onset | A label succeeded, then at least half its samples per minute were rejected with 401/403 (an expiring token or session) |
| 401/403 from the start | A label was rejected from its first minute (the credential was never valid) |
| Login redirect | A request, or one of its redirect hops, landed on a login page (`/login`, `/signin`, `/sso`, `/oauth` …) |
| Authentication message | Failures that read like "session expired", "invalid token", "CSRF" … without a 401/403 |

With the test plan (`--jmx` on `analyze`, which also saves a copy as
`plan.jmx` in the run's folder, or `--jmx` on `health`), each finding names the
correlated variables the failing requests send, the extractor that sets each one
and the sampler it sits on, whether that sampler itself was failing, and a fix.
Any JMX works: Regular Expression, JSON, Boundary, CSS and XPath extractors and
JSR223 scripts that call `vars.put(…)` are recognised. Correlation findings go
into `findings.json`, so reports and `ask` explain them too.

**Markers in generated scripts.** A standard JTL does not record what was sent,
so scripts generated by ProjectStack add a small JSR223 assertion ("Correlation
check") to every request that sends a correlated variable: if the request went
out containing `NOT_FOUND`, the sample fails with
`PS_CORRELATION_MISSING: <variable>`. It costs one string search per sample. Turn
it off with `pstack generate --no-correlation-markers` (or
`"correlation_markers": false` in the web API's generate request), or delete the
assertion in JMeter. Replay verification leaves it out, as it checks extracted
values itself.

### Runs

```bash
pstack runs list                 # newest first, --scenario to filter
pstack runs show 20261013-0824   # an id or a unique prefix of one
pstack runs delete <run> --yes   # samples, registry entry and artifacts
```

```
Run                                Scenario           Tag              Samples  Duration  Users     p95    Err%      TPS
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
20261013-082450-build-118          checkout           build-118      1,000,000   30m 01s     50     547   0.99%   555.50
```

Each run's samples live in ClickHouse (`samples`, with the registry in `runs`);
its artifacts (`meta.json`, `metrics.json`, `findings.json`, reports) are written
to `~/.projectstack/runs/<run_id>/`.

### Supported result files

- **CSV with a header row** (JMeter's default), any delimiter (`,` `;` `|` tab), any
  column set: missing optional columns are tolerated, custom `sample_variables`
  columns are ignored.
- **CSV without a header row**: pass the `user.properties` the test ran with
  (`--props`); otherwise JMeter's default column order is assumed.
- **XML JTL**, streamed (memory stays flat on multi-GB files). Nested samples are
  understood: children of a parent-sample Transaction Controller count as
  requests, redirect/embedded children as sub-results.
- **gzip-compressed** versions of any of these (`results.jtl.gz`).
- `timeStamp` must be epoch milliseconds (JMeter's default).

### How the numbers are computed

- **Percentiles are exact**, never averaged across labels or time buckets. They use
  JMeter's Aggregate Report definition: the value at position ⌊N·p⌋ of the sorted
  samples (ClickHouse `quantileExact`). JMeter's HTML dashboard uses a different
  estimator, so small differences from the dashboard are expected.
- **No double counting**: Transaction Controller parent samples ("Number of samples
  in transaction …") are reported separately from requests, and redirect
  sub-results (`Login-0` inside `Login`) are excluded. A label that only *looks*
  like a sub-result (`Step-1`) is kept when its samples do not fall inside the
  base label's samples.
- **Throughput** over the full run follows JMeter: samples ÷ (last sample end −
  first sample start); with `--steady`, samples ÷ window length.
- **Timeline**: 1-minute buckets (10 s for runs shorter than 10 minutes) with users
  (`allThreads`, or distinct threads when that column is missing), throughput,
  p95, errors and connect time.

### Performance

Measured with `scripts/bench_results.py` on generated files (8 cores, local ClickHouse in Docker):

| File | Rows | Total time (ingest + metrics + detectors) | Python peak memory | ClickHouse peak memory |
| --- | --- | --- | --- | --- |
| 142 MB | 1,000,000 | 3.2 s | 122 MB | 355 MB |
| 1.4 GB | 10,000,000 | 25.4 s | 121 MB | 768 MB |

The file is streamed to ClickHouse and parsed there, so Python's memory does not
grow with the file size.

### Using another ClickHouse server

Settings live in `~/.projectstack_settings.json` (`clickhouse_host`, `clickhouse_port`,
`clickhouse_user`, `clickhouse_password`, `clickhouse_database`, `clickhouse_secure`,
`results_retention_days`, `runs_dir`). For CI, environment variables override them:

```bash
export PSTACK_CLICKHOUSE_URL='clickhouse://perf_user:secret@clickhouse.internal:8443/projectstack?secure=1'
export PSTACK_RUNS_DIR="$CI_PROJECT_DIR/perf-runs"
pstack analyze results.jtl --tag "$CI_COMMIT_SHORT_SHA"
```

The user needs to be able to create tables in that database (or an admin applies
`projectstack/results/schema/*.sql`, replacing `{db}` and `{retention_days}`). Uploads
to non-local servers are gzip-compressed on the fly.
