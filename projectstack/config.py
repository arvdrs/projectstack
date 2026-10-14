"""
ProjectStack settings, shared by the web API and the CLI.

Settings live in ~/.projectstack_settings.json (PROJECTSTACK_SETTINGS overrides
the path). Keys the running code does not know are preserved, so a newer CLI and
an older web app can share one file without losing each other's settings.

Environment variables override the file for the results store, so CI can be
configured without a settings file:

  PSTACK_CLICKHOUSE_URL       clickhouse://user:password@host:8123/database?secure=1
  PSTACK_CLICKHOUSE_HOST / _PORT / _USER / _PASSWORD / _DATABASE / _SECURE
  PSTACK_RUNS_DIR             folder for per-run artifacts (reports, findings)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_SETTINGS: dict[str, str] = {
    "ai_provider": "ollama",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "groq_api_key": "",
    "gemini_api_key": "",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "gemma4",
    "jmeter_path": "jmeter",
    "default_threads": "1",
    "default_rampup": "1",
    "default_loops": "1",
    "grafana_url": "",
    "gmail_address": "",
    "gmail_app_password": "",
    # Results analysis — a local ClickHouse by default (deploy/clickhouse/docker-compose.yml)
    "clickhouse_host": "localhost",
    "clickhouse_port": "8123",
    "clickhouse_user": "projectstack",
    "clickhouse_password": "projectstack",
    "clickhouse_database": "projectstack",
    "clickhouse_secure": "false",
    "results_retention_days": "180",
    # Optional SELECT-only user for `pstack ask` (profile readonly = 2); empty: the user above, read-only per query
    "clickhouse_ask_user": "",
    "clickhouse_ask_password": "",
    # AI provider per task, JSON: {"report": "claude", "ask": "gemini", "correlation": "ollama/mistral"}.
    # Empty: correlation follows ai_provider; report and ask use Claude when an Anthropic key is set, else Gemini.
    "llm_tasks": "",
    "llm_models": "",                     # JSON: default model per provider, e.g. {"claude": "claude-opus-5-5"}
    "runs_dir": str(Path.home() / ".projectstack" / "runs"),
}

SECRET_FIELDS = (
    "anthropic_api_key", "openai_api_key", "groq_api_key", "gemini_api_key", "gmail_app_password",
    "clickhouse_password", "clickhouse_ask_password",
)

_ENV_OVERRIDES = {
    "clickhouse_host": "PSTACK_CLICKHOUSE_HOST",
    "clickhouse_port": "PSTACK_CLICKHOUSE_PORT",
    "clickhouse_user": "PSTACK_CLICKHOUSE_USER",
    "clickhouse_password": "PSTACK_CLICKHOUSE_PASSWORD",
    "clickhouse_database": "PSTACK_CLICKHOUSE_DATABASE",
    "clickhouse_secure": "PSTACK_CLICKHOUSE_SECURE",
    "runs_dir": "PSTACK_RUNS_DIR",
}


def settings_path() -> Path:
    return Path(os.environ.get("PROJECTSTACK_SETTINGS") or Path.home() / ".projectstack_settings.json")


def _read_file() -> dict[str, str]:
    try:
        saved = json.loads(settings_path().read_text())
        return {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings(apply_env: bool = True) -> dict[str, str]:
    """Defaults, then the settings file (all keys), then environment overrides."""
    settings = {**DEFAULT_SETTINGS, **_read_file()}
    if apply_env:
        for key, var in _ENV_OVERRIDES.items():
            if os.environ.get(var):
                settings[key] = os.environ[var]
    return settings


def save_settings(values: dict[str, str]) -> None:
    """Write settings, keeping keys already in the file that `values` does not mention."""
    merged = {**_read_file(), **{str(k): str(v) for k, v in values.items()}}
    path = settings_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, path)
