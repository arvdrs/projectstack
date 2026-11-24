# ProjectStack

Turns browser recordings into correlated JMeter scripts, runs them, and reports on the results.
It runs on your own computer. Data leaves it only when you use an AI feature, read cloud
server metrics or send a report by Gmail.

The web app has three parts, in its top navigation:

- **HAR → JMX**: record a user journey or upload HAR files, correlate the dynamic values, and
  generate a JMeter script that replays under load. Then run it and see its report. See
  [HAR → JMX](#har--jmx).
- **Analyse Results**: upload a JTL from any test and get a verdict against your SLA, findings,
  per-transaction tables, run comparisons, server metrics, and Word and HTML reports. See
  [Results analysis](#results-analysis).
- **Reports**: JMeter's own reports for a JTL you already have. These are the HTML dashboard,
  with its graphs as JPEGs, and the Aggregate Report for the APIs and the transactions, as CSV
  or Excel. See [Reports from a JTL](#reports-from-a-jtl).

**Settings** holds the AI provider, correlation rule packs, the JMeter path, default test
parameters, the Word report template, cloud monitoring keys, Grafana and Gmail.

To start it:

- Docker, with everything included: `docker compose up -d --build`, then open http://localhost:6292.
  See [Run with Docker](#run-with-docker).
- Web app: `source .venv/bin/activate && cd projectstack/ui-web && ./start.sh`, then open
  http://localhost:6292. See [Run it directly](#run-it-directly) for creating `.venv` once.
- CLI: in the same virtual environment, `pstack --help`

The web app has a light and a dark mode: the sun/moon button in the top bar switches between them,
and **Settings → Appearance** also offers *Match system*. The choice is kept per browser.

## Run with Docker

The quickest way to run ProjectStack on your own computer. One command starts the web app
with everything it uses, so you don't install Python, Node, Java, JMeter or ClickHouse
yourself, and you get the same versions as the machine ProjectStack is developed on.

### What you need

- **Docker**:
  - macOS or Windows: Docker Desktop (on Windows, with the WSL 2 backend).
  - Linux: Docker Engine with the Compose plugin.
  - `docker compose version` should print v2 or later.
  - Intel and Apple Silicon both work.
- **Memory:** give Docker at least 4 GB; 8 GB is better, since JMeter can use up to 8 GB in a
  large test. In Docker Desktop, set it under Settings → Resources.
- **Disk:** about 5 GB for the images, plus your results.

### Start it

```bash
git clone https://github.com/arvdrs/projectstack.git
cd projectstack
docker compose up -d --build
```

The first build takes 5–10 minutes, because it downloads JMeter, the Python packages and the
base images. After that, starting takes a few seconds. Once `docker compose ps` shows both
services as `healthy`, open **http://localhost:6292**.

### What's inside

| Part | What you get |
| --- | --- |
| Web app and API | The full app at http://localhost:6292, with the API under `/api` |
| Python | 3.10, with the exact package versions in `requirements.lock` |
| JMeter | 5.6 on Java 11, with the same plugins and libraries as the reference install (listed in `deploy/docker/jmeter-libs.txt`), the same 8 GB heap limit (`-Xmx8g`) and a 30-minute HTML report timeout |
| JMeter plugins | Custom Thread Groups (Stepping, Ultimate, Concurrency, Arrivals), Dummy Sampler, Plugins Manager, WebSocket Samplers (Peter Doornbosch), WebSocket Sampler (Maciej Zaleski), Apache POI 5.2.3 for Excel files |
| ClickHouse | 25.8, for results analysis, already connected |
| Chromium | Captures the report graphs (JPEG) from JMeter's HTML reports. Liberation fonts give it the Arial and Times metrics the dashboard is laid out with |
| LibreOffice Writer | Converts Word reports to Word 97–2003 (.doc) |
| `pstack` CLI | Inside the app container |

### Everyday commands

Run these in the `projectstack` folder.

| To | Run |
| --- | --- |
| Stop | `docker compose stop` |
| Start again | `docker compose up -d` |
| Update after `git pull` | `docker compose up -d --build` |
| Check that it is running | `docker compose ps` |
| See the app's log | `docker compose logs -f app` |
| Use the CLI | `docker compose run --rm app pstack --help` |
| Open a shell in the container | `docker compose run --rm app bash` |

Docker restarts ProjectStack with Docker itself, for example after a reboot, unless you
stopped it.

The `workspace/` folder in your clone is the CLI's working folder (`/workspace` in the
container). Put files there to use them from the CLI, and find the CLI's output files there.
On Linux, files the container writes there belong to you, like files you create yourself:

```bash
cp ~/Downloads/results.jtl workspace/
docker compose run --rm app pstack analyze results.jtl --scenario checkout --tag build-42
```

The web app doesn't need this folder: you upload and download files in the browser as usual.

### Where your data is kept

- **Settings** (including API keys), sessions, the reports made under **Reports** (HTML and
  Aggregate), JMeter reports of test runs, uploaded files and the recorder's certificate are in
  the Docker volume `projectstack_data`.
- **Analysed runs** are in the volume `projectstack_clickhouse-data`.

Both survive stopping, restarting, rebuilding and updating. `docker compose down` removes the
containers and keeps the volumes. `docker compose down -v` also deletes the volumes, which
means all your settings and results.

### Differences from running it directly

- **Recording a journey.** ProjectStack can't open a browser window from inside Docker. After
  **Start recording**, it shows a command for your system that opens a separate Chrome window
  whose traffic goes through the recorder (port 8082). Copy it, run it, and record in that
  window.
  - Chromium on Linux: replace `google-chrome` with `chromium`.
  - Any other browser: set its HTTP and HTTPS proxy to `127.0.0.1:8082` and install the
    certificate from http://mitm.it.
- **`localhost` means the container itself.** To reach something running on your own computer
  (the application under test, Ollama, InfluxDB for Grafana), use `host.docker.internal`, for
  example `http://host.docker.internal:3000`. This applies to scripts you run from ProjectStack
  and to addresses in Settings.
- **Ollama.** Settings already points at `http://host.docker.internal:11434`. On Linux, Ollama
  only accepts connections from the computer itself by default, so make it listen on all
  addresses:
  1. Run `sudo systemctl edit ollama`.
  2. Add these two lines:
     ```
     [Service]
     Environment="OLLAMA_HOST=0.0.0.0"
     ```
  3. Run `sudo systemctl restart ollama`.

  Your firewall then decides whether other computers can reach Ollama.
- **Server metrics sign-in.** Read-only keys in **Settings → Cloud Monitoring** work as usual.
  - AWS CLI sign-in: to use your own (`aws configure` or `aws sso login`) instead of keys,
    start ProjectStack with the extra file, and use the same command every time you start it:
    ```bash
    docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d
    ```
  - Azure: `az login` isn't available in the container, so use a service principal in
    Settings.
- **Time zone.** Run summaries and JMeter's dashboard show times in Asia/Kolkata. To use
  another time zone, set `TZ` in `.env` (below).

### Settings for your computer (`.env`)

To change any of these, create a file named `.env` next to `docker-compose.yml` with the
lines you need, then run `docker compose up -d`.

```bash
# Use another port if 6292 is taken
PSTACK_PORT=6300
# Your time zone
TZ=Europe/London
# A smaller JMeter heap when Docker has less than 8 GB of memory
JMETER_HEAP="-Xms1g -Xmx4g -XX:MaxMetaspaceSize=512m"
# Store results in a shared team ClickHouse instead of the one in Docker
PSTACK_CLICKHOUSE_URL=clickhouse://user:password@clickhouse.example.internal:8123/projectstack
```

`.env` is ignored by git, so it stays on your computer.

### If something goes wrong

- **"port is already allocated"**: another program is using 6292 or 8082, such as ProjectStack
  started with `start.sh`. Stop that program, or set `PSTACK_PORT` in `.env` (the recorder
  needs 8082 to be free).
- **The page doesn't load right after starting**: wait until `docker compose ps` shows
  `healthy`. ClickHouse starts first.
- **"Cannot reach ClickHouse"**: run `docker compose up -d clickhouse`. If that doesn't fix
  it, `docker compose logs clickhouse` shows why it stopped.
- **The build fails while downloading** (company proxy or VPN): set the proxy in Docker
  Desktop under Settings → Resources → Proxies. On Linux, set it under `"proxies"` in
  `~/.docker/config.json`. Then run `docker compose up -d --build` again.
- **A test stops with exit code 137**: Docker ran out of memory. Give Docker more memory, or
  lower `JMETER_HEAP`.
- **Docker installed as a snap (Ubuntu)**: clone ProjectStack inside your home folder,
  because the snap can't use folders outside it.

### For maintainers

- **Python packages:** after upgrading one, regenerate the pinned versions with
  `.venv/bin/python scripts/lock_requirements.py > requirements.lock`, then rebuild.
- **JMeter plugins:** to add one for everyone, add its jar's folder, SHA-256 and URL to
  `deploy/docker/jmeter-libs.txt`, then rebuild.
- **Tests in the container:** `docker compose run --rm app pytest /app/tests -q -p no:cacheprovider`.
- **CLI commands run with `docker compose run --rm app …`**, not `exec`: `run` goes through the
  container's start-up step, which runs commands as your user. `exec` runs them as root, and
  files root creates in the data volume are then read-only for the app.

## Run it directly

Without Docker you need Python 3.10 or later, Node 18, Java 11 with JMeter 5.6 on the `PATH`, and,
for results analysis, the local ClickHouse (see [Setup](#setup)). Two more are optional: Google
Chrome or Chromium to capture the report graphs, and LibreOffice Writer to save Word reports as
.doc.

```bash
# Once: a virtual environment with the exact package versions
python3 -m venv .venv
.venv/bin/pip install --no-deps -r requirements.lock
.venv/bin/pip install --no-deps -e .

# Every time: activate it, then start the API (port 8001) and the web app
source .venv/bin/activate
cd projectstack/ui-web && ./start.sh          # open http://localhost:6292
```

`start.sh` runs whichever `python3` comes first on the `PATH`, so activate the virtual environment
first; with the system Python the API is missing packages and Analyse Results cannot reach
ClickHouse. On Debian and Ubuntu, `python3 -m venv` needs the `python3-venv` package
(`sudo apt install python3-venv`). When an OS upgrade changes the Python version (Ubuntu 24.04 moves
from 3.10 to 3.12), delete `.venv` and create it again.

## HAR → JMX

**HAR → JMX** turns a user journey into a JMeter script in five steps: **Upload**, **Correlate
Values**, **Generate Script**, **Run Test** and **View Report**.

### 1. Upload

There are three ways to start:

- **Upload HAR files.** Save the recording in Chrome DevTools: **Network**, then *Save all as HAR
  with content*. Add a **second recording** of the same journey to flag the values that differ
  between the two as dynamic. Those are correlated with certainty.
- **Record in browser.** ProjectStack's recorder (port 8082) opens a separate Chrome window whose
  traffic goes through it. Type a transaction name before each step of the journey: the requests
  that follow are grouped under it, as with Transaction Controllers in JMeter's recorder. Static
  files and the browser's background traffic are left out, and **Save recording (.har)** keeps a
  copy. To record from Docker, see
  [Differences from running it directly](#differences-from-running-it-directly).
- **Quick Convert.** This turns a HAR straight into a JMX, with every request exactly as recorded
  and no correlation, parameters, transactions or SLA. It does not start the five steps.

Then choose the base URLs to keep; each domain gets its own `BASE_URL` variable. You can untick
requests to leave them out and change their order. **Analyse HAR** shows the protocol, methods,
authentication flow, response times, errors, duplicates and rate limits. **Scan for Sensitive
Data** looks for personal data and tokens in URLs and bodies before you share the script.

### 2. Correlate Values

- **Auto-correlation** finds the dynamic values and shows a confidence for each. It looks at tokens
  and JWTs, values from responses, headers, redirects and cookies, and text, forms and framework
  rules, and it searches backwards from where each value is used. Your own rule packs
  (**Settings → Correlation Rule Packs**) add patterns.
- **AI auto-correlation** uses the provider chosen in Settings (Groq, Gemini, Ollama, Claude or
  OpenAI) to find what the rules missed.
- Review, rename or switch off each correlation. Click a request to see its correlated variables
  highlighted. **Define Correlation Rule** adds your own extractor (JSON Path, a regular expression,
  or the text before and after the value, from the body or a header). **Test Rule** checks it
  against the recording.
- **Replay & auto-fix** runs the script once with JMeter (1 user, 1 iteration) and compares every
  response with the recording. It repairs extractors and adds missed correlations, and repeats
  until the script replays cleanly. This sends real requests to the recorded application.
- **Test Data:** `P_` variables become JMeter User Defined Variables. CSV and TXT files become CSV
  Data Set Configs, either unique per thread or shared across threads.

### 3. Generate Script

- Test name and thread group: Thread Group, Stepping, Concurrency or Ultimate Thread Group, with
  the users, ramp-up and duration, or stages.
- Options:
  - group the requests into Transaction Controllers;
  - add think times from the HAR's timestamps (Constant Timers);
  - add a View Results Tree listener for checking the script in JMeter;
  - send metrics to Grafana through InfluxDB (Backend Listener).
- **SLA (optional):** a response-time target, a maximum error rate and a minimum throughput, with
  per-transaction response times when needed. The run is judged against it.
- **Export Script:** the JMX, a k6 script, a GitHub Actions or Jenkins pipeline, or a ZIP for the
  team (the JMX, its correlations as JSON, the k6 script, a GitHub Actions workflow and a short
  README).

### 4. Run Test

ProjectStack runs the script with your local JMeter. The users, ramp-up, loops and duration come
from the script, and you can change them here. A live monitor shows requests, errors and response
times while the test runs, and **Open Grafana UI** appears when metrics go to Grafana. When JMeter
finishes, ProjectStack offers to analyse the run (see [In the web app](#in-the-web-app)).

### 5. View Report

- The run's **Test Execution Summary** and **Per-Transaction / Endpoint Detail**, judged against
  the SLA when one was set.
- **Open JMeter Report** (JMeter's HTML dashboard), **Download Summary Report** and **Download
  JMX**.
- **Report Analyzer:** high-level insights from the JMeter report, with an optional AI narrative.
  **Share report via Gmail** sends them; set up the account in **Settings → Email (Gmail)**.

### From the CLI

```bash
pstack parse journey.har                         # what the recording contains
pstack correlate journey.har                     # the dynamic values found, with their confidence
pstack generate journey.har -t 50 -r 60 -o checkout.jmx
pstack validate checkout.jmx
pstack run checkout.jmx -t 50 -r 60 -l 10
```

`pstack <command> --help` lists each command's options.

## Reports from a JTL

**Reports** in the top navigation (or *Have a JTL already?* on the home page) makes JMeter's own
reports for a result file you already have. It is separate from HAR → JMX and from results
analysis, and it does not need ClickHouse. It has two tabs, and a file chosen on one tab stays
chosen on the other:

- **HTML Report** (`/reports/html`): [JMeter's HTML dashboard](#jmeter-html-report), and its
  [graphs as JPEGs](#graphs-to-download).
- **Aggregate Report** (`/reports/aggregate`): [JMeter's Aggregate Report](#aggregate-report) in
  two tables, one for the APIs and one for the transactions, as CSV or Excel.

Both read the same files: CSV with or without a header row, with any delimiter, or XML results,
optionally gzipped. That is the same set results analysis accepts.

### JMeter HTML report

The **HTML Report** tab turns a JMeter result file into JMeter's standard HTML dashboard: Apdex,
statistics per label, errors, and the graphs over time. It is the report
`jmeter -g results.jtl -o report/` makes, built by your own JMeter, so every number is JMeter's.

1. Drop a JTL on the tab.
2. Optionally set:
   - a title for the dashboard;
   - the graph granularity (JMeter's own 1 minute by default; *Automatic* picks one from the test's
     length, about 200 points per graph);
   - the Apdex thresholds (with only T set, F is 4 × T);
   - under *Advanced*, a regular expression for the labels to include.
3. Click **Generate report**. The page shows the headline numbers, **Open report** (in a new tab)
   and **Download ZIP**.

Earlier reports are listed under the form. Each one can be opened, downloaded or deleted.

The result, and **Graphs** on each listed report, also offer [the report graphs](#graphs-to-download)
as JPEGs, one at a time or all five in a ZIP.

- **Files it accepts:** more than JMeter's report generator reads on its own. That generator only
  accepts CSV with a header row. ProjectStack first converts XML results, gzipped files, and CSV
  without a header row or with another delimiter into that form. The dashboard numbers do not change: an XML file and the same samples as CSV give
  identical statistics.
- **Where reports are kept:** `~/.projectstack/html-reports/<id>/`, with JMeter's log of the
  generation. In Docker that is `/data/.projectstack/html-reports`, in the `projectstack_data`
  volume. The uploaded file is deleted once its report has been generated.
- **Large files:** one report is generated at a time, and the next one waits for it. JMeter reads
  the whole file, so a JTL of several GB takes a few minutes.

### Graphs to download

The five graphs of a performance report, as JPEGs for a document. They are JMeter's own charts,
captured from its HTML report, so they are exactly what the report shows: each panel's title bar,
the graph and its legend, cut just right of the graph (without the *Zoom* overview and the
panel's buttons), at twice screen resolution.

| Graph | JMeter HTML report chart |
| --- | --- |
| Running Vusers Graph | Active Threads Over Time |
| Hits Per Second Graph | Hits Per Second |
| Throughput Graph | Bytes Throughput Over Time |
| Response Time Graph | Response Times Over Time |
| Error Graph | Codes Per Second (one line per response code, errors included) |

Each one downloads on its own (**Download JPEG**), or all five at once (**Download all (ZIP)**).

- **The same JTL gives the same pictures on both pages.** An analysed run's graphs come from
  JMeter's HTML report for the run, built from the uploaded file while it is analysed, with the
  HTML Report tab's defaults. The JPEGs are byte-for-byte the ones the HTML Report tab gives for
  the same file. That report is kept in the run's folder (`jmeter-report/`).
- **Runs analysed before this:** their original file is gone, so **Create the graphs** builds
  JMeter's report from the run's stored samples. The counts and rates are identical. Average
  response times can differ in the 15th digit, because JMeter keeps running averages that depend on
  the samples' order. With many labels, the line colours of the Response Time graph can also
  differ from a report of the original file: JMeter orders those lines partly by where each label
  first appears in the file, and that order is not stored.
- **Graphs captured before the Error Graph became Codes Per Second** (it was Total Transactions
  Per Second at first) are captured again from the same JMeter report the next time they are
  shown, on the HTML Report tab and on analysed runs alike. Nothing needs to be generated again.
- **Needs Google Chrome or Chromium** to capture the charts (in the Docker image already;
  `PSTACK_CHROME` points at another binary). Without it, the reports and analyses still work, and
  the page says why the graphs are missing.

### Aggregate report

The **Aggregate Report** tab gives JMeter's Aggregate Report for a result file in two tables:

- **APIs:** every sampler (HTTP requests and any other sampler), one row per label.
- **Transactions:** the Transaction Controllers, one row per controller. A file without
  Transaction Controllers has no transactions table.

Each table has JMeter's columns (Label, # Samples, Average, Median, 90%, 95% and 99% Line, Min,
Max, Error %, Throughput, Received KB/sec, Sent KB/sec), with a TOTAL row for that table's
samples. Rows are in the order their label first appears in the file, as in JMeter. Click a
column heading to sort, and type in the filter box to narrow the labels; TOTAL stays last and
always covers every label.

1. Drop a JTL on the tab, and optionally give a title (used in the downloaded file names).
2. Choose what to include (both are off by default):
   - **List sub-results as APIs.** JMeter writes each redirect and embedded resource of a request
     as a row of its own (`Login-0`, `Login-1` after `Login`), and their time is already part of
     the request.
   - **Count empty transactions.** These are Transaction Controller samples where none of the
     controller's samplers ran (message `Number of samples in transaction : 0`, recorded as 0 ms).
     JMeter's Aggregate Report counts them; its HTML report leaves them out. Counting them pulls
     the controller's average and percentiles down.
3. Click **Create aggregate report**. The tables appear on the page. The page also says what was
   left out, with an example label.

Downloads:

- **Download CSV**, on each table: the table exactly as JMeter's Aggregate Report saves it with
  *Save Table Data* and *Save Table Header*. That is the same header, the same number formats
  (Error % as `0.210%`, Throughput per second as `12.34567` or `.03118`, KB/sec with two
  decimals), and the same quoting.
- **Download both (Excel):** one workbook with an *APIs* sheet and a *Transactions* sheet. The
  numbers stay numbers, Error % is a percentage, and the header and TOTAL rows are bold.

On the page, Error % and Throughput look as they do in JMeter's table (`0.17%`, `31.6/sec`,
`52.1/hour`).

**How the numbers are computed.** As JMeter 5.6's Aggregate Report listener computes them. The
tests check this byte for byte against JMeter's own classes (`tests/jmeter_aggregate.groovy`):

- Average is the mean, cut to a whole millisecond (not rounded).
- Median and the 90/95/99% Lines are the smallest elapsed time that at least round(n × p) of the
  row's n samples do not exceed. They cover all samples, with no interpolation and no window. p is
  JMeter's single-precision 0.9, 0.95 and 0.99, which changes the result for a few sample counts.
  These are not the HTML dashboard's percentiles, which interpolate over each label's last 20,000
  samples.
- Throughput is samples per second from the first sample's start to the last sample's end.
  Received and Sent KB/sec divide the bytes by the same time, with 1 KB = 1024 bytes.

**How the samples are split:**

- Transactions are the labels with Transaction Controller samples (message
  `Number of samples in transaction …`).
- Every other label is an API, except sub-results. In a CSV file, a label is a sub-result when it
  is `<label>-<n>`, `<label>` is a request in the file, and at least 90% of its samples start
  inside a sample of `<label>` on the same thread. That is the rule results analysis uses, and
  the order the rows were written in does not matter. XML files nest sub-results, so there is
  nothing to guess.
- Samples with a negative elapsed time (JMeter writes one when a test stops in the middle of a
  Transaction Controller) are left out, and so is a line cut off at the end of the file. The
  page says how many there were.

**Where reports are kept:** `~/.projectstack/aggregate-reports/<id>/` (`meta.json` and the
tables). In Docker that is `/data/.projectstack/aggregate-reports`. The uploaded file is deleted
once the tables are built. Earlier reports are listed under the form, with **View**, **Excel** and
delete.

**Speed:** about 250,000 samples a second, so a JTL of 2 GB (around 14 million samples) takes about
a minute. It does not need JMeter.

## Results analysis

`pstack analyze` loads a JMeter result file (JTL) into ClickHouse, finds the
steady state, computes per-transaction statistics, runs deterministic detectors
(SLA breaches, latency and error-rate change points, saturation, error bursts,
load-generator problems) and writes a compact `findings.json`. It is built for
large files (hundreds of MB to several GB). Every number is computed by code from
the raw samples; nothing is estimated by a model.

### In the web app

- **Analyse Results** (top navigation, or "Analyse test results" on the home page): upload a JTL from
  any test, optionally with an SLA, a scenario name, a build tag and a baseline to compare with. You
  get the verdict, plain-language observations, the timeline, findings with evidence, per-transaction
  tables, the comparison with the baseline, the technical and executive reports, and a box to ask
  questions about the run. Earlier analyses are listed on the same page.
- **Graphs** (a card under a run's timeline): [the report graphs](#graphs-to-download) as JPEGs,
  one at a time or all five in a ZIP.
- **After a test run** (step 4, Run Test): when JMeter finishes, ProjectStack offers to analyse the
  run. It uses the run's results, the SLA you set in step 3 and the generated script (so correlation
  problems point at the extractor responsible), compares the run with the last passing run of the
  same script, and shows the high-level observations with a link to the full analysis.
- **Performance Highlights & Observations** (top of a run's page, and in both reports): when the test
  ran, the test type, users, region and load profile ("an 18-minute stepped ramp-up followed by 58
  minutes of steady-state execution"), the headline statistics (maximum users, total requests, average
  throughput and total errors over the whole run), and observations: errors and throughput, how
  consistent response times were, the slowest endpoint, gains or regressions against the baseline, and
  serious findings. **Test context** under it holds the region, an optional test type and notes (what
  endpoints do, what changed since the last run, quotas and limits); runs of the same scenario without
  their own context use the latest one. The section has two options: **Without AI** (the default),
  written by rules with nothing sent anywhere, and **With AI**, which rewrites it in report style using
  the notes. With AI, your AI provider gets findings.json, the computed figures and the notes, and every
  number it writes is checked against them; the AI version is saved, so switching back to it is instant
  and **Rewrite** asks for a new one. **Copy** puts the version on screen on the clipboard as plain text.

- **Download**: the **Download** menu on a run's page saves the analysis as a file: the
  **performance report in Word** (.doc, or .docx, in your company template — see
  [Word reports in your company template](#word-reports-in-your-company-template)), the **technical
  report** as one HTML file with everything in it (highlights, timeline, findings, tables, server
  metrics, the screenshots themselves and their comparison with the load test, as a chart and a
  table), the **executive summary** or the **requests & transactions** report as HTML, or the
  technical report as Markdown. The HTML files load nothing from elsewhere, so they open offline and
  can be emailed or attached as they are; they follow the reader's light or dark mode and print
  cleanly. Files are named after the scenario and run (`Checkout_20261117-0441_technical-report.html`).
  With **AI narrative** ticked, the AI-written text is used.
- **Per request and transaction**: click any label on a run's page (or search for it among all the
  run's labels) for its own page: a plain-language summary and a status (needs action / worth a look /
  healthy), its timeline with p50/p95/p99, the response-time distribution, percentiles, where the time
  goes (connecting, waiting for the server, downloading), response codes, errors, the slowest samples,
  the findings that name it and its change from the baseline. **Requests & transactions** opens the
  same as one report with a section per transaction and main request (HTML or Markdown).
- **Compare runs**: **Compare** on a run's page (against the previous run of the scenario, the last
  passing one, or any run), or tick two runs in the list. Headline figures side by side, both
  timelines overlaid, every label's change with regressions and improvements, which findings are new,
  resolved or still present, and a Markdown export.
- **Server metrics**: **Choose resources** in a run's Server metrics card reads CPU, memory,
  connections, latency, errors and capacity from AWS CloudWatch or Azure Monitor for the run's
  window and charts them under the test's p95. Server problems join the findings, linked to the
  slowdowns they coincide with. See [Server metrics from AWS and Azure](#server-metrics-from-aws-and-azure).
- **Server metrics screenshots**: attach screenshots of CPU, memory, connections and so on from any
  monitoring tool (Grafana, CloudWatch, New Relic, Datadog …) to a run — drop them, choose them, or
  paste one with Ctrl+V — and **Read with AI** has a vision model write observations per screenshot
  and for the servers overall, lined up with the test's load steps. See
  [Server metrics from screenshots](#server-metrics-from-screenshots).
- **Server metrics vs the load test**: with server metrics from either source, the run's page draws
  the load test's graph (active users, throughput, p95, errors) with the server metrics under it on
  one clock, and says for each server metric whether it followed the load, moved with the response
  times or with the errors, and whether it kept climbing under flat load. See
  [Server metrics vs the load test](#server-metrics-vs-the-load-test).

Both need the local ClickHouse (below); the page shows the command to start it when it is not
running. Nothing is sent to an AI service unless you choose **With AI** in Performance Highlights &
Observations, tick **AI narrative** for a report, **Read with AI** in Server metrics screenshots, or
ask a question.

### Setup

With [Docker](#run-with-docker), ClickHouse is already running and connected; skip this.
Otherwise ProjectStack uses a local ClickHouse by default:

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

Statistics cover the steady state, except where named otherwise: the timeline and top errors cover the
whole run, and so do `run_samples` and `run_errors` on each label (for quotas and daily limits). The
`steady` block keeps the untrimmed plateau too (`plateau_start_ms`, `plateau_end_ms`), and the
`baseline` block lists the labels that improved (`improvements`) next to the counts. Runs analysed
before these were added lack them: their load profile is worked out from the trimmed window, and
gains against a baseline are not reported until the run is analysed again. Next to `findings.json`,
`context.json` holds the run's test context (region, test type and notes) when one was saved.

### Reports

```bash
pstack report 20261013-0845                       # technical report, HTML, in the run's folder
pstack report 20261013-0845 --audience exec       # one-page summary for decision makers
pstack report 20261013-0845 --format both         # report_tech.html and report_tech.md
pstack report 20261013-0845 --no-llm              # rule-based text; nothing leaves the machine
pstack report runs/…/findings.json --out -        # from a published artifact, Markdown to stdout
pstack report 20261013-0845 --format doc          # the performance report in Word 97–2003, in the company template
pstack report 20261013-0845 --format docx         # the same in the current Word format
```

A report is built from the run's `findings.json` alone, so it needs no database
and CI can render it from published artifacts. The **technical** report has the
verdict and SLA checks, the Performance Highlights & Observations, a timeline chart (active users, throughput, p95 and error
rate as four panels on one time axis, with finding windows and the steady state
shaded; hover for values, or open the table under it), per-transaction and
per-request tables, every finding with its evidence, likely root causes ranked by
confidence, recommended next tests and the top errors, and the run's server
metrics screenshots with what the AI read from them. The **executive** summary
is one page: verdict, headline, four figures, the Performance Highlights &
Observations, the AI's overall reading of the server screenshots (when there is
one), business impact and the recommended action. HTML reports are single
self-contained files (no external assets) and follow the system's light or dark mode.

The detectors decide; the AI only explains. The narrative text (summary,
timeline observations, what each finding means, root causes, next tests — or the
headline, business impact and recommended action for the executive summary) is written by the AI provider
configured for reports, which receives `findings.json` and nothing else (no raw
samples, request or response data). It answers in JSON that fills
fixed slots in the template, so it cannot change the findings, the verdict or the
tables.

**Number check.** The prompts tell the model to cite only numbers that appear in
`findings.json`, and every number in its answer is then checked against the
document (allowing rounding, ms→s and s→min conversions, and differences or
ratios of two values it contains; label names, the run id and tag are ignored, and
dates (2026-10-28 or October 28, 2026), clock times (09:17 or 08:09 - 09:28 AM) and
`t+…` offsets are checked against the run's timestamps). Numbers
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

**Performance Highlights & Observations** in a report follow the same rules: with
`--no-llm`, or without an AI provider, rules write them; otherwise the AI provider
rewrites them in report style using the run's test context. That is `context.json`
in the run's folder (the web app writes it), or else the latest one saved for a run
of the same scenario. To give a context without the web app, as in CI, write the
file yourself:

```json
{
  "region": "us-east-1",
  "test_type": "",
  "notes": "/api/orders/sync is the CRM order-sync integration; optimised since the last run.\nThe lead API has a quota of 1,000 leads per day."
}
```

An empty `test_type` is worked out from the load profile (incremental load test,
smoke test and so on). The AI gets `findings.json`, the computed figures and the
notes, and numbers it takes from the notes pass the number check. The statistics
list is always computed, never AI-written. The AI version is saved as
`narrative_highlights.json` (prompt `report_highlights`) and reused while
`findings.json`, the context and the prompt are unchanged.

### Word reports in your company template

**Download → Performance report · Word (.doc)** on a run's page (or `pstack report <run> --format doc`)
writes the full report as a Word 97–2003 document; **Word (.docx)** (`--format docx`) writes the same
report in the current Word format, for Google Docs and newer Word. Either way it is a Word document in numbered sections that follow the client report it was
modelled on: 1. Executive Summary, 2. Test Scope, 3. Load Profile & Test Approach, 4. Performance
Highlights & Observations, 5. Response Times (SLA, transactions, requests, baseline), 6. Server Metrics
(cloud metrics, screenshots with what the AI read, and the comparison with the load test), 7. Findings
(with likely root causes), 8. Recommendations, 9. Top Errors, and About this report. Charts are
pictures; the screenshots are embedded. With **AI narrative** ticked, the AI-written text is used.

**.doc needs LibreOffice.** The report is written as .docx and LibreOffice converts it to .doc,
headless and with a throwaway profile (it never touches a LibreOffice window you have open). Install
it once — it is already in the Docker image:

```bash
sudo apt install --no-install-recommends libreoffice-writer-nogui     # Debian / Ubuntu, about 160 MB
```

It is found on the `PATH` (`soffice`, `libreoffice`) or in the usual install folders; set
`libreoffice_path` in `~/.projectstack_settings.json` for another location. Settings shows whether it
was found; without it the .doc download says so and .docx still works.

To make it look like your company's reports, upload your Word template in **Settings → Report template
(Word)** (.doc, .docx or .dotx — a .doc template is converted with LibreOffice; **Starter template**
downloads a plain one to restyle). The report is built
inside it, keeping its fonts, colours, heading, list and table styles, page setup, cover page, header,
footer and logo:

- A paragraph holding only `[[REPORT]]` marks where the report goes. Without it, the report follows the
  template's own content on a new page.
- Placeholders are filled in anywhere — cover page, header, footer, tables: `{{scenario}}`, `{{run_id}}`,
  `{{tag}}`, `{{test_date}}`, `{{test_window}}`, `{{duration}}`, `{{peak_users}}`, `{{region}}`,
  `{{test_type}}`, `{{verdict}}`, `{{total_requests}}`, `{{error_rate}}`, `{{throughput}}`, `{{p95}}`,
  `{{report_date}}`, `{{generated_by}}`. Unknown ones are left as they are, and Settings lists them.
- Headings use the template's Heading 1–3 and are numbered in the text ("4. …") unless its Heading 1
  numbers itself; tables use its *Table Grid* style (plain borders otherwise); bullets *List Bullet*.

The template is kept next to the runs folder (`~/.projectstack/templates/report_template.docx`), so the
CLI and every run use it.

### A new report in the format of a past one (preview)

`pstack create-report` takes a finished report from an earlier test cycle (a .docx: the
*blueprint*) and analysed runs — one per region or test — and writes a new report in exactly the
blueprint's format:

```bash
pstack create-report "Performance Test Report Nov 2026.docx" \
    20261105-0530="Incremental Load Test - US region" \
    20261105-0707="Incremental Load Test - CA region" \
    --out "Performance Test Report Dec 2026.docx"          # or .doc / .pdf (LibreOffice)
```

| Part of the blueprint | In the new report |
|---|---|
| Cover, attention notice, authorisation, sections before *Performance Highlights & Observations*, header, footer, fonts, styles | kept |
| Month on the cover, date in the version table, users per journey in the workload table (from the runs' thread groups), table of contents | updated (Word refreshes the contents when the file is opened) |
| One *4.x* subsection per run: heading, highlight bullets (bold figures), Performance Metrics (JMeter's Aggregate Report columns, whole run), Running Vusers, Hits Per Second, Throughput and Response Time graphs, Server Metrics screenshots with the AI's reading | rebuilt from each run, by cloning the blueprint's first subsection |
| Tuning Recommendations, Conclusion | drafted from the findings and figures — review them |

`--ai` uses AI-written highlights (with the run's test notes). A run without server metrics
screenshots gets a highlighted placeholder in its Server Metrics block. A .doc keeps everything but
the blueprint's embedded fonts (readers need them installed); .docx and .pdf keep them.

| Exit code | Meaning |
|---|---|
| 0 | Report written (whatever the run's verdict) |
| 2 | Run not found, unreadable findings.json, or the chosen AI provider failed |
| 3 | `--strict-numbers`: the AI narrative cited numbers that are not in findings.json |

### Requests and transactions report

```bash
pstack labels 20261013-1019                         # report_labels.html in the run's folder
pstack labels 20261013-1019 --format md --limit 80  # every transaction, then up to 80 labels in all
pstack labels 20261013-1019 "TC Checkout"           # one label's summary in the terminal
```

```
TC Checkout  (transaction) — ✖ needs action
  • Ran 43,508 times in the steady state (39.59 per second), 100% of all transaction time.
  • Half the responses took up to 614 ms, 95% up to 873 ms, 99% up to 1,012 ms; the slowest took 1,514 ms.
  • SLA p95 ≤ 3,000 ms: met (873 ms).
  • Compared with build-120: p95 584 ms → 873 ms (50% slower); errors 0.52% → 0.46%.
```

A section per label: the summary, statistics for the steady state and the whole run (and the
baseline), the percentile ladder up to p99.9, the response-time distribution, where the time goes,
response codes, errors and the slowest samples. The report covers every transaction, then requests
named in findings or SLA checks, then the busiest requests. Statuses and summaries are rule-based:
“needs action” for a missed SLA, a critical finding or at least 5% errors; “worth a look” for 1%
errors or more, a long tail (p99 at least 3× p50), a slowdown during the steady state or a warning
finding. Nothing in it is written by AI.

### AI providers

Correlation, reports and the `ask` command share one AI layer
(`projectstack/llm/`) with Claude, OpenAI, Groq, Gemini and Ollama behind one
interface (`complete(system, prompt)` and `complete_with_tools(system, messages,
tools)`). Each task picks its provider separately:

| Task | Default |
|---|---|
| `correlation` | the provider chosen in the web app (`ai_provider`) |
| `report`, `ask` | Claude (`claude-opus-5-5`) when an Anthropic key is set (`anthropic_api_key` or `ANTHROPIC_API_KEY`), otherwise Gemini when a Gemini key is set, otherwise `ai_provider` |
| `screenshots` | the same as `report`; the web app lets you pick any provider here that can read images |

Override per task or per provider model in `~/.projectstack_settings.json`:

```json
{
  "llm_tasks":  "{\"report\": \"claude\", \"ask\": \"claude\", \"correlation\": \"ollama/mistral\", \"screenshots\": \"ollama/gemma4\"}",
  "llm_models": "{\"claude\": \"claude-opus-5-5\", \"groq\": \"openai/gpt-oss-120b\"}"
}
```

or for one report: `pstack report <run> --provider groq/openai/gpt-oss-120b`.
Tool calling (used by `ask`) works with all five providers. Groq's default is `openai/gpt-oss-120b`
(Groq retired Llama 3.3 70B in 2026). Reading screenshots needs a model that can see images
(`complete(system, prompt, images=[…])`): Claude, Gemini and OpenAI's default models can; on Groq,
`qwen/qwen3.8-27b` is offered when your key lists it; on Ollama only vision models (gemma4, qwen3-vl,
gemma3 …) are offered.
Claude requests use structured outputs (the JSON schema is enforced) with medium
effort; the other providers are asked for JSON and their answers are validated and
reshaped. Prompts are versioned files in `projectstack/llm/prompts/`
(`report_tech.v1.md`, `report_exec.v1.md`, `server_screenshots.v1.md`, `correlation_detect.v1.md`); a change
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

### Server metrics from AWS and Azure

A slow test only tells half the story. ProjectStack can read the platform metrics of the servers
behind the test from **AWS CloudWatch** (`GetMetricData`) and **Azure Monitor** for the run's time
window (plus 5 minutes either side), keep them in ClickHouse (`server_metrics`) and analyse them
together with the test. Access is read-only and only metric values are read.

**Sign-in** — Settings → Cloud Monitoring, or the settings file. Leave the keys empty to use this
machine's own sign-in:

| | Machine sign-in (keys empty) | Keys in Settings | Permissions |
| --- | --- | --- | --- |
| AWS | `aws_profile`, else the default chain (environment, `~/.aws`, SSO, instance role) | `aws_access_key_id`, `aws_secret_access_key`, optional `aws_session_token` | `CloudWatchReadOnlyAccess`; `ViewOnlyAccess` to find resources |
| Azure | `az login`, environment variables or a managed identity | service principal: `azure_tenant_id`, `azure_client_id`, `azure_client_secret` | Monitoring Reader; Reader to find resources (needs `azure_subscription_id`) |

Secrets are stored like the AI keys and only shown masked. **Save and test** in Settings
(or `pstack cloud check`) shows which identity is used.

**Choose the resources** once per scenario — on a run's page (**Choose resources**: find them by
type and region, or add one by its id, and give each a role such as *app* or *database*) or with the
CLI. They are saved for the scenario, and every later analysis of that scenario fetches their
metrics automatically (turn off with `cloud_auto_fetch`).

```bash
pstack cloud check                                        # who am I, on AWS and Azure
pstack cloud discover --provider aws --type rds --type alb --region ap-south-1
pstack cloud targets search --add aws:rds:orders-db::database \
                            --add aws:alb:app/web-alb/50dc6c495c0c9188::load-balancer
pstack server-metrics 20261013-1019                       # fetch for an existing run
```

A resource spec is `provider:type:id[:name[:role]]`: the instance id, DB identifier, the
`app/…`/`net/…` part of a load balancer ARN, `cluster/service` for ECS, or the full resource id on
Azure.

| Category | AWS | Azure |
| --- | --- | --- |
| Virtual machines | EC2 instance, Auto Scaling group | Virtual machine, VM scale set |
| Containers | ECS service, EKS cluster (Container Insights) | AKS cluster, Container App |
| Databases and caches | RDS instance, Aurora cluster, ElastiCache node | Azure SQL database, PostgreSQL / MySQL flexible server, Azure Cache for Redis |
| Load balancers and app platforms | Application / Network Load Balancer, API Gateway (REST), Lambda | App Service app and plan, Application Gateway, Load Balancer |

Each type has a preset of the metrics that matter under load (CPU, memory, CPU credits,
connections, latency, IOPS and queue depth, 5xx and throttles, evictions, instance counts, unhealthy
hosts). Memory on EC2 needs the CloudWatch agent, and EKS needs Container Insights; metrics that come
back empty are listed with the reason.

**Server findings** are made by the same kind of deterministic rules as the test findings:

- CPU at 85% or more for at least 3 minutes (critical from 95% or over half the steady state);
  CPU credits running out
- memory at 90% or more, or growing through the run (a leak or a cache filling up)
- server errors, throttling or cache evictions during the test
- database queue depth, and latency that steps up (at least 1.5× and 5 ms)
- connections held at their peak for 5 minutes or more (a pool or server limit)
- scaling events and unhealthy hosts

A server finding within 2 minutes of a test finding is **linked** to it, in both directions ("At
the same time on the servers: CPU at 91% on orders-db"), and raised one level (info → warning,
warning → critical) when the slowdown it coincides with is critical. Server findings change the
verdict like any other finding, and the reports and observations name the likely cause and the next
test to run. The AI narrative sees the
findings and a per-resource summary in `findings.json`, never the series.

**Retention**: CloudWatch keeps 1-minute values for 15 days, 5-minute values for 63 days and hourly
values for 15 months, so fetch soon after the test; ProjectStack uses the finest resolution still
available. Azure Monitor keeps platform metrics for 93 days.

### Server metrics from screenshots

When the servers are not in AWS or Azure monitoring that ProjectStack can query — or the dashboards
are already open in Grafana, New Relic, Datadog, Dynatrace or a cloud console — attach screenshots to
the run instead, in the **Server metrics screenshots** card of a run's page: drop them, choose them,
or paste one with Ctrl+V (PNG, JPEG, WebP or GIF; up to 20 per run, 10 MB each). A caption such as
"CPU – app servers" helps the AI and appears in the report.

**Read with AI** sends the screenshots to the provider you pick next to the button, together with the
run's window, ramp-up steps, steady state and a per-minute table of users, throughput, p95 and error
rate (in UTC and in your browser's time zone, since dashboards usually show local time), the
findings, and the test notes. For each screenshot it returns what it shows, the time range on its
axis and where the test falls on it (with a warning when a chart does not cover the test), and what
happened during the test; then overall observations on headroom, the resource closest to its limit,
and whether server metrics moved with the slowdowns or errors the test saw. Figures about the servers
are read from the images, so check them against the screenshots; the page and the reports say so.

Which provider reads them is your choice: a cloud model with a key in Settings (Gemini, Claude,
OpenAI, or Groq's open-weight vision model Qwen 3.8 — the screenshots leave the machine), or a vision
model in Ollama, which keeps everything on this machine but needs the model to fit in free memory
(gemma4 needs about 10 GB) and takes minutes on a CPU. The default is `llm_tasks.screenshots`, else
the provider reports use.

**How a reading runs.** It runs in the background (the page shows progress and picks it up again
after a reload): first, with Gemini, every Flash model gets a one-word request at once, and the ones
that are out of quota or not answering are skipped; then the screenshots are read three per request,
two requests at a time (one at a time on Groq, whose free tier counts tokens per minute, and one
screenshot at a time on Ollama); last, a text-only request writes the overall observations from the
readings and the comparison with the load test. Requests with pictures stream the answer, so a model
that sends nothing for 45 seconds is dropped for the next one instead of holding the reading up, and
Gemini is given the answer's JSON Schema, so its answers are always valid JSON. A batch that fails is
read again one screenshot at a time; a screenshot that still fails is marked, the others are kept, and
**Retry failed** reads only those.

Files are kept in the run's folder: `screenshots/` (the images as uploaded and `index.json` with
names and captions) and `narrative_screenshots.json` (the reading, its provider, prompt version and
token usage). The reading is reused until a screenshot, a caption, the test notes or `findings.json`
changes; then the page marks it out of date, reports leave it out, and **Read again** reads the
screenshots that changed (**Read all again** reads every one). The technical report shows the screenshots (scaled to 1,600 px and embedded, so the HTML stays a
single file) with the reading; the executive summary carries the overall observations only.

### Server metrics vs the load test

Server metrics only help when they are read against the test. For metrics fetched from AWS or Azure,
and for the lines the AI reads off screenshots, ProjectStack compares every server metric minute by
minute with the test's own timeline and shows the result under **Server metrics vs the load test**:
the load test's graph (active users, throughput, p95 and the error rate) with the server metrics
under it on the same clock (each server in one colour in every panel), a table, and observations such as

- *CPU · app-server-1 (r 0.97, about 60% at 50 users) and CPU · app-server-2 (r 0.97, about 51% at
  50 users) followed the load: they rose with the users through the ramp-up and held steady with them.*
- *During the steady state, while the load was flat, the p95 response time and CPU · orders-db rose
  and fell together (r 1.00): this server metric is the likeliest place to look for what slowed
  responses.*
- *The error rate rose together with CPU · orders-db (r 0.61).*
- *Memory used · app-server-1 rose with the load and kept rising during the steady state although the
  load was flat (about 48% to 52%): worth watching on a longer test.*

| Column | How it is worked out |
|---|---|
| With the load | Pearson r with the active users over the whole test, ramp-up included |
| With p95 response time | r with p95 over the **steady state**, where the load is flat, so the two simply growing with the users does not count (over the whole test, marked, when the steady state has too few points) |
| With errors | r with the error rate, when the test had errors |
| Under flat load | the steady state's last quarter against its first: rising or falling by 3 points or more for a percentage, 10% of the level otherwise |

Strong means |r| ≥ 0.7 and moderate ≥ 0.4; a metric that hardly moves is *flat* and gets no r.
Server values that cover a longer period (5-minute CloudWatch values) are compared with the test's
average over the same minutes. The observations are written from these numbers by rules, so they are
the same every time and nothing extra is sent to an AI. Correlation shows what moved together, not
what caused it.

For screenshots, the AI reads every line's value at up to 30 times across the test (the times are
given to it in UTC and in your local time, and it says which clock the chart uses); charts that do not
cover the test are left out. Those values are approximate, which is why they are drawn over the test's
graph: compare the lines with your screenshots. The technical report has the observations and the
table, under a chart of the test's users and p95 with the server lines beneath, in the Server metrics
section (AWS/Azure) and in Server metrics from screenshots; the executive summary has the observations
for screenshots.

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
to servers on other machines (a host name with a dot, or an IP address) are gzip-compressed
on the fly; `localhost` and single-word names such as Docker's `clickhouse` get them as is.
