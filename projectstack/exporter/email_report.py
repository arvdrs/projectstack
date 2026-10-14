"""
Insights report rendering and Gmail delivery.

The same HTML is used for the email body and the downloadable insights report.
Email clients ignore <style> blocks and modern CSS, so everything is table-based
with inline styles on a light background.
"""

from __future__ import annotations

import html
import io
import re
import smtplib
import ssl
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Gmail caps messages at 25 MB including encoding overhead

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

_VERDICT = {
    "pass": ("PASS", "#1e8e4e", "#e8f6ee"),
    "at_risk": ("AT RISK", "#b7791f", "#fdf4e3"),
    "fail": ("FAIL", "#c62828", "#fdecec"),
    "no_sla": ("NO SLA", "#555555", "#f1f1f1"),
}
_SEVERITY = {
    "critical": ("Critical", "#c62828", "#fdecec"),
    "warning": ("Warning", "#b7791f", "#fdf4e3"),
    "info": ("Note", "#3a6fd8", "#eaf1fd"),
    "good": ("Good", "#1e8e4e", "#e8f6ee"),
}
_STATUS = {"ok": ("#1e8e4e", "Met SLA"), "fail": ("#c62828", "Missed SLA"), "none": ("#888888", "—"),
           "warn": ("#b7791f", "Watch")}


def parse_recipients(raw: str | list[str]) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", raw or "")
    out = [i.strip() for i in items if i and i.strip()]
    bad = [i for i in out if not _EMAIL_RE.match(i)]
    if bad:
        raise ValueError(f"Invalid email address: {', '.join(bad)}")
    return out


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _ms(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v / 1000:.2f} s" if v >= 1000 else f"{round(v)} ms"


def _num(v: Any, suffix: str = "", digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{round(float(v), digits):,}{suffix}"


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    return "".join(
        f'<p style="margin:0 0 12px;font-size:14px;line-height:1.65;color:#333">{_e(b).replace(chr(10), "<br>")}</p>'
        for b in blocks
    )


def render_report_html(analysis: dict[str, Any], meta: dict[str, Any]) -> str:
    """Render the insights report. `meta`: test_name, filename, note, ai_summary, report_url, generated_at."""
    k = analysis["kpis"]
    label, v_fg, v_bg = _VERDICT.get(analysis["verdict"], _VERDICT["no_sla"])
    sla = analysis.get("sla") or {}
    score_line = f"SLA checks passed {sla.get('passed_count', 0)}/{len(sla.get('checks') or [])}" if sla.get("defined") else "No SLA defined"
    generated = meta.get("generated_at") or datetime.now().strftime("%d %b %Y, %H:%M")
    window = analysis.get("window") or {}
    run_window = f"{window.get('start')} – {window.get('end')}" if window.get("start") else "—"

    kpi_cells = [
        ("Samples", f"{k['samples']:,}"),
        ("Error rate", _num(k["error_pct"], "%")),
        ("Throughput", _num(k["throughput"], " req/s")),
        ("Mean", _ms(k["mean_ms"])),
        ("p90", _ms(k["p90_ms"])),
        ("p95", _ms(k["p95_ms"])),
        ("p99", _ms(k["p99_ms"])),
        ("Apdex", f"{k['apdex']:.2f} · {k['apdex_rating']}" if k.get("apdex") is not None else "—"),
    ]
    kpi_rows = ""
    for i in range(0, len(kpi_cells), 4):
        kpi_rows += "<tr>" + "".join(
            f'<td style="width:25%;padding:14px 16px;border:1px solid #ececec;background:#fafafa">'
            f'<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#888">{_e(n)}</div>'
            f'<div style="font-size:20px;font-weight:600;color:#111;margin-top:4px">{_e(v)}</div></td>'
            for n, v in kpi_cells[i:i + 4]
        ) + "</tr>"

    finding_rows = ""
    for f in analysis["findings"]:
        sev, fg, bg = _SEVERITY.get(f["severity"], _SEVERITY["info"])
        finding_rows += (
            '<tr><td style="padding:12px 0;border-bottom:1px solid #eee;vertical-align:top;width:90px">'
            f'<span style="display:inline-block;font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;'
            f'color:{fg};background:{bg}">{sev}</span></td>'
            '<td style="padding:12px 0 12px 12px;border-bottom:1px solid #eee">'
            f'<div style="font-size:14px;font-weight:600;color:#111">{_e(f["title"])}</div>'
            f'<div style="font-size:13px;line-height:1.6;color:#555;margin-top:3px">{_e(f["detail"])}</div></td></tr>'
        )

    th = 'style="text-align:{a};padding:9px 10px;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:#888;border-bottom:2px solid #e5e5e5"'
    txn_rows = ""
    for t in analysis["transactions"]:
        color, word = _STATUS.get(t["status"], _STATUS["none"])
        td = 'style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#333;text-align:right"'
        txn_rows += (
            f'<tr><td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#111;font-family:Menlo,Consolas,monospace">{_e(t["name"])}</td>'
            f'<td {td}>{t["samples"]:,}</td><td {td}>{_num(t["error_pct"], "%")}</td>'
            f'<td {td}>{_ms(t["mean_ms"])}</td><td {td}>{_ms(t["p95_ms"])}</td><td {td}>{_ms(t["p99_ms"])}</td>'
            f'<td {td}><span style="color:{color};font-weight:600">{word}</span></td></tr>'
        )

    errors_html = ""
    if analysis["errors"]:
        rows = "".join(
            f'<tr><td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px">{_e(e["type"])}</td>'
            f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right">{e["count"]:,}</td>'
            f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right">{e["pct_of_errors"]}%</td>'
            f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right">{e["pct_of_samples"]}%</td></tr>'
            for e in analysis["errors"]
        )
        errors_html = _section("Errors", (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<tr><th {th.format(a="left")}>Type</th><th {th.format(a="right")}>Count</th>'
            f'<th {th.format(a="right")}>% of errors</th><th {th.format(a="right")}>% of samples</th></tr>{rows}</table>'
        ))

    sla_html = ""
    if sla.get("defined"):
        rows = ""
        for c in sla.get("checks") or []:
            ok = c["passed"]
            what = c["label"] or ("Error rate" if c["kind"] == "error_rate" else "Throughput")
            unit = c["unit"]
            fmt = (lambda v: _ms(v)) if unit == "ms" else (lambda v: "—" if v is None else f"{v:g} {unit}".replace(" %", "%"))
            cmp = "≥" if c["kind"] == "throughput" else "≤"
            rows += (
                f'<tr><td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;font-family:Menlo,Consolas,monospace">{_e(what)}</td>'
                f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right">{_e(c["metric"])} {cmp} {_e(fmt(c["target"]))}</td>'
                f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right">{_e(fmt(c.get("actual")))}</td>'
                f'<td style="padding:9px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;text-align:right;font-weight:600;color:{"#1e8e4e" if ok else "#c62828"}">{"Met" if ok else "Missed"}</td></tr>'
            )
        sla_html = _section("SLA", (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            f'<tr><th {th.format(a="left")}>Scope</th><th {th.format(a="right")}>Target</th>'
            f'<th {th.format(a="right")}>Actual</th><th {th.format(a="right")}>Result</th></tr>{rows}</table>'
        ))
    note_html = _section("Note from the sender", _paragraphs(meta["note"])) if meta.get("note") else ""
    ai_html = _section("AI analysis", _paragraphs(meta["ai_summary"])) if meta.get("ai_summary") else ""
    link_html = (
        f'<p style="margin:16px 0 0;font-size:13px;color:#555">Full JMeter dashboard: '
        f'<a href="{_e(meta["report_url"])}" style="color:#d62828">{_e(meta["report_url"])}</a></p>'
        if meta.get("report_url") else ""
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(meta.get('test_name') or 'Performance test')} — insights</title></head>
<body style="margin:0;padding:0;background:#f3f3f3;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#111">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f3f3"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e6e6e6">
  <tr><td style="background:#100f0f;padding:28px 32px">
    <div style="font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#de5353;font-weight:600">ProjectStack · Performance report</div>
    <div style="font-size:26px;font-weight:600;color:#ffffff;margin-top:8px">{_e(meta.get('test_name') or 'Performance test')}</div>
    <div style="font-size:13px;color:#a3a3a3;margin-top:6px">Run: {_e(run_window)} · Generated {_e(generated)}{(' · ' + _e(meta['filename'])) if meta.get('filename') else ''}</div>
  </td></tr>
  <tr><td style="padding:28px 32px 8px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{v_bg};border-radius:12px"><tr>
      <td style="padding:18px 20px;width:120px;vertical-align:middle">
        <div style="font-size:12px;color:#666">Verdict</div>
        <div style="font-size:22px;font-weight:700;color:{v_fg}">{label}</div></td>
      <td style="padding:18px 20px;vertical-align:middle;border-left:1px solid rgba(0,0,0,.08)">
        <div style="font-size:12px;color:#666">{_e(score_line)}</div>
        <div style="font-size:15px;color:#222;margin-top:2px">{_e(analysis['headline'])}</div></td>
    </tr></table>
  </td></tr>
  {note_html}
  {sla_html}
  {_section("Key metrics", f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{kpi_rows}</table>')}
  {_section("High-level insights", f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{finding_rows}</table>')}
  {ai_html}
  {_section("Transactions (slowest p95 first)", (
      '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
      f'<tr><th {th.format(a="left")}>Transaction</th><th {th.format(a="right")}>Samples</th><th {th.format(a="right")}>Errors</th>'
      f'<th {th.format(a="right")}>Mean</th><th {th.format(a="right")}>p95</th><th {th.format(a="right")}>p99</th><th {th.format(a="right")}>Status</th></tr>'
      f'{txn_rows}</table>'))}
  {errors_html}
  <tr><td style="padding:8px 32px 28px">
    {link_html}
    <p style="margin:16px 0 0;font-size:12px;color:#999">{_e("SLA: " + "; ".join(sla.get("targets") or []) + "." if sla.get("defined") else "No SLA was defined, so this run is not marked pass or fail.")}
    Insights are rule-based from the JMeter results{'; the AI analysis is model-generated and should be reviewed' if meta.get('ai_summary') else ''}.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _section(title: str, body: str) -> str:
    return (
        f'<tr><td style="padding:20px 32px 8px">'
        f'<div style="font-size:16px;font-weight:600;color:#111;margin-bottom:12px;padding-left:10px;border-left:3px solid #d62828">{_e(title)}</div>'
        f'{body}</td></tr>'
    )


def render_report_text(analysis: dict[str, Any], meta: dict[str, Any]) -> str:
    k = analysis["kpis"]
    label = _VERDICT.get(analysis["verdict"], _VERDICT["no_sla"])[0]
    sla = analysis.get("sla") or {}
    lines = [
        f"{meta.get('test_name') or 'Performance test'} — performance report",
        f"Verdict: {label}" + (f" ({sla.get('passed_count', 0)}/{len(sla.get('checks') or [])} SLA checks passed)" if sla.get("defined") else ""),
        analysis["headline"], "",
    ]
    if sla.get("defined"):
        lines += ["SLA: " + "; ".join(sla.get("targets") or []), ""]
    if meta.get("note"):
        lines += ["Note from the sender:", meta["note"], ""]
    lines += [
        "KEY METRICS",
        f"  Samples {k['samples']:,} · errors {_num(k['error_pct'], '%')} · throughput {_num(k['throughput'], ' req/s')}",
        f"  Mean {_ms(k['mean_ms'])} · p90 {_ms(k['p90_ms'])} · p95 {_ms(k['p95_ms'])} · p99 {_ms(k['p99_ms'])}",
        "", "HIGH-LEVEL INSIGHTS",
    ]
    lines += [f"  [{_SEVERITY[f['severity']][0]}] {f['title']} — {f['detail']}" for f in analysis["findings"]]
    if meta.get("ai_summary"):
        lines += ["", "AI ANALYSIS", meta["ai_summary"]]
    lines += ["", "TRANSACTIONS (slowest p95 first)"]
    lines += [f"  {t['name']}: {t['samples']} samples, {t['error_pct']}% errors, p95 {_ms(t['p95_ms'])}"
              for t in analysis["transactions"]]
    return "\n".join(lines)


def zip_directory(directory: str | Path) -> bytes:
    root = Path(directory)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(root.parent))
    return buf.getvalue()


def build_message(sender: str, to: list[str], cc: list[str], subject: str, html_body: str, text_body: str,
                  attachments: list[tuple[str, bytes, str, str]]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for filename, data, maintype, subtype in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def send_via_gmail(sender: str, app_password: str, msg: EmailMessage,
                   host: str = GMAIL_SMTP_HOST, port: int = GMAIL_SMTP_PORT, use_ssl: bool = True) -> None:
    """Send through Gmail SMTP with an app password. Raises RuntimeError with a readable message."""
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(sender, app_password)
                smtp.send_message(msg)
        else:  # plain SMTP, used only for local testing
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(_auth_hint(exc)) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise RuntimeError(f"Gmail refused the recipients: {', '.join(exc.recipients)}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Could not send via Gmail: {exc}") from exc


def verify_gmail(sender: str, app_password: str) -> None:
    """Log in and disconnect without sending anything."""
    try:
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(sender, app_password)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(_auth_hint(exc)) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Could not reach Gmail: {exc}") from exc


def _auth_hint(exc: smtplib.SMTPAuthenticationError) -> str:
    return (
        "Gmail rejected the login. Use a 16-character App Password (Google Account → Security → "
        "2-Step Verification → App passwords), not your normal Gmail password."
        f" ({exc.smtp_code})"
    )
