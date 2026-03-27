"""
Grafana / InfluxDB exporter.

Converts a MetricsSummary (from a parsed JTL file) into two artefacts:

1. InfluxDB line protocol file  — push with `influx write -f metrics.lp`
2. Grafana dashboard JSON       — import via Grafana UI → Dashboards → Import

The dashboard JSON is self-contained and uses Grafana's built-in
TestData datasource so it works without any external data source.
For real use, replace the datasource uid with your InfluxDB datasource.
"""

import json
import time
from pathlib import Path

from projectstack.runner.metrics_parser import MetricsSummary


# ---------------------------------------------------------------------------
# InfluxDB line protocol
# ---------------------------------------------------------------------------

def _escape_tag(value: str) -> str:
    """Escape spaces and commas in InfluxDB tag values."""
    return value.replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


def export_influxdb(
    summary: MetricsSummary,
    output_path: str | Path,
    measurement: str = "jmeter_results",
    test_name: str = "pstack_test",
) -> None:
    """
    Write InfluxDB line protocol to output_path.

    Each sampler gets one line. An aggregate summary line is also written.

    Args:
        summary:     MetricsSummary from parse_jtl().
        output_path: Path to write the .lp file.
        measurement: InfluxDB measurement name.
        test_name:   Tag added to every line for filtering in Grafana.
    """
    lines = []
    ts_ns = int(summary.end_epoch_ms * 1_000_000) if summary.end_epoch_ms else int(time.time() * 1e9)

    def _line(label: str, sm) -> str:
        tag = f"label={_escape_tag(label)},test={_escape_tag(test_name)}"
        fields = (
            f"avg_ms={sm.avg_ms:.2f},"
            f"min_ms={sm.min_ms}i,"
            f"max_ms={sm.max_ms}i,"
            f"p90_ms={sm.p90_ms}i,"
            f"p95_ms={sm.p95_ms}i,"
            f"total={sm.total if hasattr(sm, 'total') else summary.total_requests}i,"
            f"passed={sm.passed}i,"
            f"failed={sm.failed}i,"
            f"error_rate={sm.error_rate:.2f}"
        )
        return f"{measurement},{tag} {fields} {ts_ns}"

    # Per-sampler lines
    for sm in summary.samplers.values():
        lines.append(_line(sm.label, sm))

    # Aggregate summary line
    class _Agg:
        avg_ms = summary.avg_ms
        min_ms = summary.min_ms
        max_ms = summary.max_ms
        p90_ms = summary.p90_ms
        p95_ms = summary.p95_ms
        total = summary.total_requests
        passed = summary.passed
        failed = summary.failed
        error_rate = summary.error_rate

    lines.append(_line("__aggregate__", _Agg()))

    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Grafana dashboard JSON
# ---------------------------------------------------------------------------

def export_grafana_dashboard(
    summary: MetricsSummary,
    output_path: str | Path,
    dashboard_title: str = "ProjectStack Results",
) -> None:
    """
    Write a Grafana dashboard JSON that visualises the MetricsSummary.

    The dashboard contains:
      - Stat panels: Total Requests, Error Rate, Throughput, Avg Response
      - Bar chart: Per-sampler average response times
      - Table: Full per-sampler breakdown

    Import via Grafana UI → Dashboards → Import → Upload JSON file.
    """
    panels = []
    panel_id = 1

    def _stat(title: str, value: str, unit: str, x: int, y: int, color: str = "blue") -> dict:
        nonlocal panel_id
        p = {
            "id": panel_id,
            "type": "stat",
            "title": title,
            "gridPos": {"x": x, "y": y, "w": 6, "h": 4},
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "orientation": "auto",
                "textMode": "auto",
                "colorMode": "background",
            },
            "fieldConfig": {
                "defaults": {
                    "unit": unit,
                    "color": {"mode": "fixed", "fixedColor": color},
                }
            },
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "targets": [
                {
                    "refId": "A",
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "queryType": "randomWalk",
                    "scenarioId": "manual_entry",
                    "stringInput": value,
                }
            ],
        }
        panel_id += 1
        return p

    error_color = "red" if summary.error_rate > 5 else "green"

    panels.append(_stat("Total Requests", str(summary.total_requests), "short", 0, 0))
    panels.append(_stat("Error Rate", f"{summary.error_rate:.1f}%", "percent", 6, 0, error_color))
    panels.append(_stat("Throughput", f"{summary.throughput:.2f}", "reqps", 12, 0))
    panels.append(_stat("Avg Response", f"{summary.avg_ms:.0f}", "ms", 18, 0))

    # Table panel with per-sampler data
    table_rows = [
        {
            "label": sm.label,
            "total": sm.total,
            "passed": sm.passed,
            "failed": sm.failed,
            "avg_ms": round(sm.avg_ms),
            "p90_ms": sm.p90_ms,
            "p95_ms": sm.p95_ms,
            "error_rate": f"{sm.error_rate:.1f}%",
        }
        for sm in summary.samplers.values()
    ]

    table_panel = {
        "id": panel_id,
        "type": "table",
        "title": "Per-Sampler Results",
        "gridPos": {"x": 0, "y": 4, "w": 24, "h": 8},
        "options": {"showHeader": True},
        "fieldConfig": {"defaults": {}, "overrides": []},
        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
        "targets": [
            {
                "refId": "A",
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "queryType": "manual_entry",
                "scenarioId": "manual_entry",
                "stringInput": json.dumps(table_rows),
            }
        ],
        "_staticData": table_rows,
    }
    panels.append(table_panel)

    dashboard = {
        "title": dashboard_title,
        "uid": "pstack_results",
        "schemaVersion": 38,
        "version": 1,
        "refresh": "",
        "time": {"from": "now-1h", "to": "now"},
        "panels": panels,
        "_meta": {
            "generated_by": "ProjectStack",
            "summary": {
                "total": summary.total_requests,
                "passed": summary.passed,
                "failed": summary.failed,
                "error_rate_pct": round(summary.error_rate, 2),
                "avg_ms": round(summary.avg_ms),
                "p90_ms": summary.p90_ms,
                "p95_ms": summary.p95_ms,
                "throughput_rps": round(summary.throughput, 2),
                "duration_sec": round(summary.duration_sec, 1),
            },
        },
    }

    Path(output_path).write_text(
        json.dumps({"dashboard": dashboard}, indent=2), encoding="utf-8"
    )
