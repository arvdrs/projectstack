"""
ProjectStack Web UI — Streamlit app.

Run with:
    streamlit run projectstack/ui/app.py
or via the CLI:
    pstack ui
"""

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

# Ensure the package root is importable when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from projectstack.correlator.confidence import annotate as confidence_annotate
from projectstack.correlator.engine import run_correlation
from projectstack.exporters.grafana_exporter import export_grafana_dashboard, export_influxdb
from projectstack.exporters.postman_exporter import export_postman
from projectstack.generator.jmx_generator import generate_jmx
from projectstack.generator.transaction_grouper import group_transactions
from projectstack.generator.validator import validate_jmx
from projectstack.parser.har_parser import parse_har
from projectstack.runner.jmeter_runner import find_jmeter, run_jmeter
from projectstack.runner.metrics_parser import parse_jtl

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ProjectStack",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* ── Base ── */
    .stApp { background: #0a0c0f; }
    .main .block-container {
        padding: 2.5rem 2.5rem 4rem;
        max-width: 1200px;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: #0d1117;
        border-right: 1px solid #21262d;
    }
    [data-testid="stSidebar"] * { color: #c9d1d9 !important; }
    [data-testid="stSidebar"] .stMarkdown h2 {
        color: #ffffff !important;
        font-size: 1.1rem;
        font-weight: 700;
        letter-spacing: -0.01em;
    }

    /* ── Typography ── */
    h1 { color: #ffffff !important; font-weight: 700; letter-spacing: -0.03em; font-size: 2rem !important; }
    h2 { color: #e6edf3 !important; font-weight: 600; letter-spacing: -0.02em; }
    h3 { color: #e6edf3 !important; font-weight: 600; }
    p, span, li, label { color: #8b949e; }
    .stMarkdown p { color: #8b949e; font-size: 0.9rem; line-height: 1.6; }

    /* ── Inputs ── */
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input {
        background: #161b22 !important;
        border: 1px solid #30363d !important;
        color: #c9d1d9 !important;
        border-radius: 6px;
        font-size: 0.875rem;
    }
    .stTextInput > div > div > input:focus,
    .stNumberInput > div > div > input:focus {
        border-color: #388bfd !important;
        box-shadow: 0 0 0 3px rgba(56,139,253,0.12) !important;
    }
    .stTextInput > label, .stNumberInput > label,
    .stSlider > label, .stSelectbox > label,
    .stFileUploader > label { color: #8b949e !important; font-size: 0.8rem; font-weight: 500; }

    /* ── Buttons ── */
    .stButton > button {
        background: #21262d;
        border: 1px solid #30363d;
        color: #c9d1d9;
        border-radius: 6px;
        font-size: 0.875rem;
        font-weight: 500;
        transition: all 0.15s ease;
    }
    .stButton > button:hover {
        background: #30363d;
        border-color: #8b949e;
        color: #ffffff;
    }
    .stButton > button[kind="primary"] {
        background: #238636;
        border-color: #2ea043;
        color: #ffffff;
        font-weight: 600;
    }
    .stButton > button[kind="primary"]:hover {
        background: #2ea043;
        border-color: #3fb950;
    }
    .stDownloadButton > button {
        background: #21262d;
        border: 1px solid #30363d;
        color: #c9d1d9;
        border-radius: 6px;
        font-size: 0.875rem;
        font-weight: 500;
    }
    .stDownloadButton > button[kind="primary"] {
        background: #0d419d;
        border-color: #1158c7;
        color: #ffffff;
    }
    .stDownloadButton > button[kind="primary"]:hover {
        background: #1158c7;
    }
    .stFormSubmitButton > button {
        background: #238636 !important;
        border: 1px solid #2ea043 !important;
        color: #ffffff !important;
        font-weight: 600;
        border-radius: 6px;
    }
    .stFormSubmitButton > button:hover {
        background: #2ea043 !important;
    }

    /* ── Toggle ── */
    .stToggle > label { color: #8b949e !important; font-size: 0.875rem; }

    /* ── Slider ── */
    .stSlider { color: #8b949e; }

    /* ── File uploader ── */
    [data-testid="stFileUploader"] {
        background: #161b22;
        border: 1px dashed #30363d;
        border-radius: 8px;
        padding: 1rem;
    }
    [data-testid="stFileUploader"]:hover { border-color: #388bfd; }
    [data-testid="stFileUploader"] * { color: #8b949e !important; }

    /* ── Metrics ── */
    div[data-testid="metric-container"] {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 8px;
        padding: 1rem 1.25rem;
    }
    div[data-testid="metric-container"] [data-testid="stMetricLabel"] {
        color: #8b949e !important;
        font-size: 0.75rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    div[data-testid="metric-container"] [data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-size: 1.5rem;
        font-weight: 700;
    }

    /* ── DataFrames ── */
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    .stDataFrame [data-testid="stDataFrameResizable"] {
        background: #161b22;
        border: 1px solid #21262d;
    }

    /* ── Expander ── */
    .stExpander {
        background: #161b22;
        border: 1px solid #21262d !important;
        border-radius: 8px !important;
    }
    .stExpander summary { color: #c9d1d9 !important; font-weight: 500; }
    .stExpander summary:hover { color: #ffffff !important; }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background: transparent;
        border-bottom: 1px solid #21262d;
        gap: 0;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        color: #8b949e;
        font-size: 0.875rem;
        font-weight: 500;
        padding: 0.5rem 1rem;
        border-bottom: 2px solid transparent;
    }
    .stTabs [aria-selected="true"] {
        color: #e6edf3 !important;
        border-bottom: 2px solid #3fb950 !important;
    }

    /* ── Alerts ── */
    .stSuccess {
        background: #0d2119;
        border: 1px solid #1a4731;
        border-radius: 6px;
        color: #56d364 !important;
    }
    .stWarning {
        background: #161205;
        border: 1px solid #3d2700;
        border-radius: 6px;
        color: #d29922 !important;
    }
    .stError {
        background: #160b0b;
        border: 1px solid #490202;
        border-radius: 6px;
        color: #f85149 !important;
    }
    .stInfo {
        background: #0d1a2e;
        border: 1px solid #1158c7;
        border-radius: 6px;
        color: #79c0ff !important;
    }
    .stSuccess *, .stWarning *, .stError *, .stInfo * { color: inherit !important; }

    /* ── Radio (nav) ── */
    .stRadio [role="radiogroup"] { gap: 4px; }
    .stRadio [role="radiogroup"] label {
        color: #8b949e !important;
        font-size: 0.875rem;
        padding: 6px 8px;
        border-radius: 6px;
        width: 100%;
        transition: background 0.1s ease;
    }
    .stRadio [role="radiogroup"] label:hover {
        background: #161b22;
        color: #c9d1d9 !important;
    }
    .stRadio [data-checked="true"] label,
    .stRadio input:checked + div label {
        color: #ffffff !important;
    }

    /* ── Code ── */
    .stCodeBlock { border-radius: 8px; }
    pre { background: #161b22 !important; border: 1px solid #21262d !important; }

    /* ── Divider ── */
    hr { border: none; border-top: 1px solid #21262d; margin: 1.25rem 0; }

    /* ── Caption ── */
    .stCaption { color: #6e7681 !important; font-size: 0.8rem !important; }

    /* ── Form ── */
    [data-testid="stForm"] {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 1.5rem;
    }

    /* ── Spinner ── */
    .stSpinner > div { border-top-color: #3fb950 !important; }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0d1117; }
    ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #484f58; }

    /* ── Pipeline status badges ── */
    .step-done   { color: #3fb950 !important; font-size: 0.85rem; }
    .step-pending { color: #6e7681 !important; font-size: 0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "har_path": None,
    "requests": None,
    "correlated": None,
    "annotated": None,
    "jmx_content": None,
    "jmx_path": None,
    "jtl_path": None,
    "metrics": None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## ⚡ ProjectStack")
    st.caption("HAR → jMeter automation")
    st.divider()

    st.markdown('<span style="color:#6e7681;font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em">Claude API Key</span>', unsafe_allow_html=True)
    api_key_input = st.text_input(
        "ANTHROPIC_API_KEY",
        value=os.environ.get("ANTHROPIC_API_KEY", ""),
        type="password",
        placeholder="sk-ant-...",
        label_visibility="collapsed",
    )
    if api_key_input:
        os.environ["ANTHROPIC_API_KEY"] = api_key_input

    st.divider()

    st.markdown('<span style="color:#6e7681;font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em">Navigation</span>', unsafe_allow_html=True)
    page = st.radio(
        "Navigation",
        ["Parse & Correlate", "Generate JMX", "Run Tests", "Export"],
        label_visibility="collapsed",
    )

    st.divider()

    st.markdown('<span style="color:#6e7681;font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em">Pipeline</span>', unsafe_allow_html=True)

    def _step(label, done):
        icon = "●" if done else "○"
        cls = "step-done" if done else "step-pending"
        st.markdown(f'<span class="{cls}">{icon} {label}</span>', unsafe_allow_html=True)

    _step("HAR loaded", st.session_state.requests is not None)
    _step("Values correlated", st.session_state.correlated is not None)
    _step("JMX generated", st.session_state.jmx_content is not None)
    _step("Test run complete", st.session_state.metrics is not None)

    if st.session_state.requests:
        st.caption(f"{len(st.session_state.requests)} requests captured")
    if st.session_state.correlated is not None:
        st.caption(f"{len(st.session_state.correlated)} correlations found")

    st.divider()
    if st.button("Reset session", use_container_width=True):
        for k in _DEFAULTS:
            st.session_state[k] = _DEFAULTS[k]
        st.rerun()


# ===========================================================================
# PAGE 1 — Parse & Correlate
# ===========================================================================

if page == "Parse & Correlate":
    st.markdown("# Parse & Correlate")
    st.markdown("Upload a HAR file captured from your browser DevTools → Network → Export HAR.")
    st.divider()

    col_upload, col_opts = st.columns([3, 2], gap="large")

    with col_upload:
        uploaded_har = st.file_uploader(
            "HAR file", type=["har", "json"], key="har_uploader"
        )

    with col_opts:
        st.markdown('<span style="color:#6e7681;font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em">Options</span>', unsafe_allow_html=True)
        use_claude = st.toggle(
            "Use Claude AI (Pass 6)",
            value=False,
            help="Sends request/response pairs to Claude Sonnet 4.6 to catch missed values. Requires ANTHROPIC_API_KEY.",
        )
        min_conf_parse = st.slider(
            "Min confidence to display",
            0, 99, 0,
            help="Filter correlated values by minimum confidence score.",
        )

    if uploaded_har:
        with tempfile.NamedTemporaryFile(suffix=".har", delete=False) as f:
            f.write(uploaded_har.read())
            st.session_state.har_path = f.name

        if st.button("Run correlation", type="primary"):
            with st.spinner("Parsing HAR file…"):
                try:
                    reqs = parse_har(st.session_state.har_path)
                    st.session_state.requests = reqs
                    st.session_state.correlated = None
                    st.session_state.annotated = None
                    st.session_state.jmx_content = None
                    st.session_state.jmx_path = None
                    st.session_state.metrics = None
                except Exception as exc:
                    st.error(f"Parse error: {exc}")
                    st.stop()

            with st.spinner("Running 6-pass correlation engine…"):
                try:
                    corr = run_correlation(reqs, use_ai=use_claude)
                    annotated = confidence_annotate(corr)
                    st.session_state.correlated = corr
                    st.session_state.annotated = annotated
                except Exception as exc:
                    st.error(f"Correlation error: {exc}")
                    st.stop()

            st.success(
                f"Parsed **{len(reqs)}** requests — found **{len(corr)}** correlated values."
            )

    if st.session_state.requests:
        with st.expander(f"Captured requests  ·  {len(st.session_state.requests)} total", expanded=True):
            rows = []
            for i, r in enumerate(st.session_state.requests):
                p = urlparse(r.url)
                path: str = str(p.path)
                rows.append(
                    {
                        "#": i + 1,
                        "Method": r.method,
                        "Host": str(p.netloc),
                        "Path": (path[:70] + "…") if len(path) > 70 else path,  # type: ignore[index]
                        "Status": r.status_code,
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if st.session_state.annotated:
        st.divider()
        st.markdown("## Correlated values")

        filtered = [
            a for a in st.session_state.annotated
            if a["confidence_score"] >= min_conf_parse
        ]

        if not filtered:
            st.warning(f"No values with confidence ≥ {min_conf_parse}.")
        else:
            high = sum(1 for a in filtered if a["confidence_label"] == "High")
            med  = sum(1 for a in filtered if a["confidence_label"] == "Medium")
            low  = sum(1 for a in filtered if a["confidence_label"] == "Low")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(filtered))
            c2.metric("High", high)
            c3.metric("Medium", med)
            c4.metric("Low", low)

            st.markdown("")
            rows = []
            for a in filtered:
                cv    = a["value"]
                label = a["confidence_label"]
                score = a["confidence_score"]
                preview = cv.extract_value
                if len(preview) > 45:
                    preview = preview[:45] + "…"
                rows.append(
                    {
                        "Variable": cv.var_name,
                        "Score": score,
                        "Confidence": label,
                        "Source": f"Req #{cv.source_request_index + 1} ({cv.extract_from})",
                        "Key": cv.extract_key,
                        "Value preview": preview,
                        "Used in": str([i + 1 for i in cv.used_in_requests]),
                        "Pass": cv.detection_pass,
                        "Reason": cv.reason,
                    }
                )

            def _color_confidence(val):
                colors = {"High": "#3fb950", "Medium": "#d29922", "Low": "#f85149"}
                c = colors.get(val, "")
                return f"color: {c}; font-weight: 600" if c else ""

            styled = pd.DataFrame(rows).style.map(_color_confidence)
            st.dataframe(styled, use_container_width=True, hide_index=True)


# ===========================================================================
# PAGE 2 — Generate JMX
# ===========================================================================

elif page == "Generate JMX":
    st.markdown("# Generate JMX")
    st.markdown("Configure thread-group parameters and generate a jMeter test plan.")
    st.divider()

    if not st.session_state.requests:
        st.warning("No HAR loaded. Go to **Parse & Correlate** first.")
        st.stop()

    st.caption(
        f"{len(st.session_state.requests)} requests  ·  "
        f"{len(st.session_state.correlated or [])} correlated values"
    )

    st.markdown("")

    with st.form("jmx_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            threads    = st.number_input("Virtual users", 1, 1000, 1)
            ramp_up    = st.number_input("Ramp-up (seconds)", 0, 3600, 1)
        with col2:
            loops      = st.number_input("Loop iterations", 1, 1000, 1)
            min_conf_gen = st.slider(
                "Min correlation confidence",
                0, 99, 0,
                help="Drop correlations below this score.",
            )
        with col3:
            test_name  = st.text_input("Test plan name", "ProjectStack Test")
            st.markdown("")

        submitted = st.form_submit_button("Generate JMX", type="primary", use_container_width=True)

    if submitted:
        corr = st.session_state.correlated or []
        if min_conf_gen > 0 and st.session_state.annotated:
            corr = [
                a["value"] for a in st.session_state.annotated
                if a["confidence_score"] >= min_conf_gen
            ]

        with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False) as f:
            jmx_path = f.name

        with st.spinner("Grouping transactions and generating JMX…"):
            try:
                transactions = group_transactions(st.session_state.requests)
                generate_jmx(
                    requests=st.session_state.requests,
                    correlated_values=corr,
                    output_path=jmx_path,
                    test_name=test_name,
                    threads=threads,
                    ramp_up=ramp_up,
                    loops=loops,
                    transactions=transactions,
                )
                jmx_content = Path(jmx_path).read_text()
                st.session_state.jmx_content = jmx_content
                st.session_state.jmx_path    = jmx_path
                st.success(
                    f"JMX ready — **{len(st.session_state.requests)}** samplers, "
                    f"**{len(transactions)}** transactions, "
                    f"**{len(corr)}** correlations."
                )
                # Surface any structural warnings
                try:
                    errs = validate_jmx(jmx_path)
                    if errs:
                        for e in errs:
                            st.warning(f"[{e.code}] {e.message}")
                except Exception:
                    pass
            except Exception as exc:
                st.error(f"Generation failed: {exc}")

    if st.session_state.jmx_content:
        st.divider()
        col_dl, col_info = st.columns([1, 3])
        with col_dl:
            st.download_button(
                "Download JMX",
                data=st.session_state.jmx_content,
                file_name="test_plan.jmx",
                mime="application/xml",
                type="primary",
                use_container_width=True,
            )
        with col_info:
            lines = st.session_state.jmx_content.count("\n")
            st.info(
                f"{len(st.session_state.jmx_content):,} bytes · {lines:,} lines"
            )

        with st.expander("JMX preview  ·  first 200 lines"):
            preview_lines = st.session_state.jmx_content.splitlines()[:200]
            st.code("\n".join(preview_lines), language="xml")


# ===========================================================================
# PAGE 3 — Run Tests
# ===========================================================================

elif page == "Run Tests":
    st.markdown("# Run Tests")
    st.markdown("Execute a jMeter test plan and view results.")
    st.divider()

    jmeter_bin = find_jmeter()
    if not jmeter_bin:
        st.error(
            "jMeter not found. "
            "Install Apache jMeter and set `$JMETER_BIN` or `$JMETER_HOME`."
        )
        st.stop()

    st.caption(f"jMeter binary: `{jmeter_bin}`")

    jmx_source = st.radio(
        "JMX source",
        ["Use generated JMX", "Upload a JMX file"],
        horizontal=True,
    )

    jmx_path = None
    if jmx_source == "Use generated JMX":
        if not st.session_state.jmx_path:
            st.warning("No JMX generated yet. Go to **Generate JMX** first.")
            st.stop()
        jmx_path = st.session_state.jmx_path
        st.caption(f"Plan: `{Path(jmx_path).name}`")
    else:
        uploaded_jmx = st.file_uploader("Upload JMX file", type=["jmx", "xml"])
        if uploaded_jmx:
            with tempfile.NamedTemporaryFile(suffix=".jmx", delete=False) as f:
                f.write(uploaded_jmx.read())
                jmx_path = f.name

    if not jmx_path:
        st.stop()

    st.markdown("")

    with st.form("run_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            run_threads = st.number_input("Threads", 1, 1000, 1)
        with col2:
            run_ramp    = st.number_input("Ramp-up (s)", 0, 3600, 1)
        with col3:
            run_loops   = st.number_input("Loops", 1, 1000, 1)
        with col4:
            run_timeout = st.number_input("Timeout (s)", 10, 7200, 3600)

        run_submitted = st.form_submit_button("Run Test", type="primary", use_container_width=True)

    if run_submitted:
        with st.spinner("Running jMeter…"):
            try:
                result = run_jmeter(
                    jmx_path=jmx_path,
                    threads=run_threads,
                    ramp_up=run_ramp,
                    loops=run_loops,
                    timeout=run_timeout,
                )
            except Exception as exc:
                st.error(f"Run failed: {exc}")
                st.stop()

        if result["returncode"] != 0:
            st.error("jMeter exited with errors.")
            with st.expander("Last 20 log lines"):
                st.code(
                    "\n".join(result.get("stderr", "").splitlines()[-20:]),
                    language="text",
                )
        else:
            st.session_state.jtl_path = result["jtl_path"]
            try:
                st.session_state.metrics = parse_jtl(result["jtl_path"])
                st.success("Test complete.")
            except Exception as exc:
                st.warning(f"Test ran but could not parse results: {exc}")

    if st.session_state.metrics:
        m = st.session_state.metrics
        st.divider()
        st.markdown("## Results")

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Requests",   m.total_requests)
        c2.metric("Passed",     m.passed)
        c3.metric("Failed",     m.failed)
        c4.metric("Error rate", f"{m.error_rate:.1f}%")
        c5.metric("Throughput", f"{m.throughput:.1f}/s")
        c6.metric("Duration",   f"{m.duration_sec:.1f}s")

        st.markdown("")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg",  f"{m.avg_ms:.0f} ms")
        c2.metric("Min",  f"{m.min_ms} ms")
        c3.metric("Max",  f"{m.max_ms} ms")
        c4.metric("p95",  f"{m.p95_ms} ms")

        if m.samplers:
            st.divider()
            st.markdown("## Per-sampler breakdown")
            rows = []
            for name, s in m.samplers.items():
                rows.append(
                    {
                        "Sampler":    name,
                        "Requests":   s.total,
                        "Passed":     s.passed,
                        "Failed":     s.failed,
                        "Avg (ms)":   round(s.avg_ms),
                        "Min (ms)":   s.min_ms,
                        "Max (ms)":   s.max_ms,
                        "p90 (ms)":   s.p90_ms,
                        "p95 (ms)":   s.p95_ms,
                        "Error %":    f"{s.error_rate:.1f}%",
                    }
                )

            def _highlight_errors(val):
                try:
                    pct = float(str(val).replace("%", ""))
                    if pct > 10:
                        return "color: #f85149; font-weight: 600"
                    if pct > 0:
                        return "color: #d29922"
                except ValueError:
                    pass
                return ""

            styled = pd.DataFrame(rows).style.map(_highlight_errors)
            st.dataframe(styled, use_container_width=True, hide_index=True)

        if st.session_state.jtl_path:
            st.markdown("")
            jtl_bytes = Path(st.session_state.jtl_path).read_bytes()
            st.download_button(
                "Download JTL results",
                data=jtl_bytes,
                file_name="results.jtl",
                mime="text/csv",
            )


# ===========================================================================
# PAGE 4 — Export
# ===========================================================================

elif page == "Export":
    st.markdown("# Export")
    st.markdown("Convert test artifacts to Postman collections or Grafana dashboards.")
    st.divider()

    tab_postman, tab_grafana = st.tabs(["Postman Collection", "Grafana / InfluxDB"])

    with tab_postman:
        st.markdown("### Postman Collection v2.1")
        if not st.session_state.requests:
            st.warning("Parse a HAR file first in **Parse & Correlate**.")
        else:
            st.caption(f"{len(st.session_state.requests)} requests ready for export.")
            collection_name = st.text_input("Collection name", "ProjectStack Collection")

            if st.button("Generate collection", type="primary"):
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                    out_path = f.name
                try:
                    export_postman(
                        st.session_state.requests,
                        out_path,
                        collection_name=collection_name,
                    )
                    postman_json = Path(out_path).read_text()
                    st.success(
                        f"Collection ready — {len(st.session_state.requests)} requests."
                    )
                    st.download_button(
                        "Download collection.json",
                        data=postman_json,
                        file_name="collection.json",
                        mime="application/json",
                        type="primary",
                    )
                    st.info("Import via Postman → File → Import → Upload Files.")
                except Exception as exc:
                    st.error(f"Export failed: {exc}")

    with tab_grafana:
        st.markdown("### InfluxDB + Grafana export")

        if not st.session_state.metrics:
            st.info("No test results in session. Upload an existing JTL file to export.")
            jtl_upload = st.file_uploader("Upload JTL (CSV) file", type=["jtl", "csv"])
            if jtl_upload:
                with tempfile.NamedTemporaryFile(suffix=".jtl", delete=False) as f:
                    f.write(jtl_upload.read())
                    jtl_tmp = f.name
                try:
                    st.session_state.metrics  = parse_jtl(jtl_tmp)
                    st.session_state.jtl_path = jtl_tmp
                    st.success("JTL parsed — proceed with export below.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Parse error: {exc}")

        if st.session_state.metrics:
            m = st.session_state.metrics
            st.caption(
                f"{m.total_requests} requests across {len(m.samplers)} samplers."
            )
            test_tag = st.text_input(
                "InfluxDB measurement tag",
                "pstack_test",
                help="Tag added to every InfluxDB data point.",
            )

            if st.button("Generate Grafana export", type="primary"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    lp_path   = Path(tmpdir) / "metrics.lp"
                    dash_path = Path(tmpdir) / "grafana_dashboard.json"
                    try:
                        export_influxdb(m, lp_path, test_name=test_tag)
                        export_grafana_dashboard(m, dash_path)
                        lp_content   = lp_path.read_text()
                        dash_content = dash_path.read_text()

                        st.success("Export files ready.")
                        col1, col2 = st.columns(2)
                        with col1:
                            st.download_button(
                                "Download metrics.lp",
                                data=lp_content,
                                file_name="metrics.lp",
                                mime="text/plain",
                                type="primary",
                                use_container_width=True,
                            )
                            st.caption("Push with: `influx write -b <bucket> -f metrics.lp`")
                        with col2:
                            st.download_button(
                                "Download grafana_dashboard.json",
                                data=dash_content,
                                file_name="grafana_dashboard.json",
                                mime="application/json",
                                type="primary",
                                use_container_width=True,
                            )
                            st.caption("Import via Grafana → Dashboards → Import → Upload JSON.")
                    except Exception as exc:
                        st.error(f"Export failed: {exc}")
