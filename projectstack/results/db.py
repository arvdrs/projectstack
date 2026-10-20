"""
ClickHouse access for results analysis: connection settings, schema migrations
and streaming inserts.

Queries go through clickhouse-connect (ClickHouse's official HTTP client). Large
JTL uploads go straight to the HTTP interface as a streamed POST, so ClickHouse's
own CSV parser does the work and the file is never loaded into Python memory.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import requests

SCHEMA_DIR = Path(__file__).parent / "schema"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ResultsDBError(RuntimeError):
    """A ClickHouse problem, explained for the user."""


class ResultsDBUnavailable(ResultsDBError):
    """ClickHouse could not be reached (not running, wrong host or port)."""


@dataclass
class ClickHouseConfig:
    host: str = "localhost"
    port: int = 8123
    user: str = "projectstack"
    password: str = "projectstack"
    database: str = "projectstack"
    secure: bool = False
    retention_days: int = 180

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "ClickHouseConfig":
        import os
        if os.environ.get("PSTACK_CLICKHOUSE_URL"):
            cfg = cls.from_url(os.environ["PSTACK_CLICKHOUSE_URL"])
        else:
            cfg = cls(
                host=settings.get("clickhouse_host", "localhost"),
                port=int(settings.get("clickhouse_port") or 8123),
                user=settings.get("clickhouse_user", "projectstack"),
                password=settings.get("clickhouse_password", ""),
                database=settings.get("clickhouse_database", "projectstack"),
                secure=str(settings.get("clickhouse_secure", "false")).lower() in ("1", "true", "yes"),
            )
        cfg.retention_days = int(settings.get("results_retention_days") or 180)
        return cfg

    @classmethod
    def from_url(cls, url: str) -> "ClickHouseConfig":
        """clickhouse://user:password@host:8123/database?secure=1 (https:// implies secure)."""
        p = urlparse(url)
        if p.scheme not in ("clickhouse", "http", "https"):
            raise ResultsDBError(f"Unsupported ClickHouse URL scheme {p.scheme!r} — use clickhouse://user:pass@host:8123/db")
        q = parse_qs(p.query)
        secure = p.scheme == "https" or q.get("secure", ["0"])[0].lower() in ("1", "true", "yes")
        return cls(
            host=p.hostname or "localhost",
            port=p.port or (8443 if secure else 8123),
            user=unquote(p.username or "default"),
            password=unquote(p.password or ""),
            database=(p.path or "/projectstack").strip("/") or "projectstack",
            secure=secure,
        )

    @property
    def http_url(self) -> str:
        return f"{'https' if self.secure else 'http'}://{self.host}:{self.port}/"

    @property
    def display(self) -> str:
        return f"{self.http_url}{self.database}"

    @property
    def is_local(self) -> bool:
        return self.host in ("localhost", "127.0.0.1", "::1")

    @property
    def is_nearby(self) -> bool:
        """This machine, or a single-label name such as a Docker Compose service: uploads go uncompressed."""
        return self.is_local or "." not in self.host


def quote_literal(value: str) -> str:
    """A ClickHouse string literal."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


class ResultsDB:
    """Connection to the results database, with the schema applied."""

    def __init__(self, cfg: ClickHouseConfig):
        if not _IDENT_RE.match(cfg.database):
            raise ResultsDBError(f"Invalid database name {cfg.database!r}")
        self.cfg = cfg
        self.db = cfg.database
        try:
            import clickhouse_connect
        except ImportError as exc:  # pragma: no cover
            raise ResultsDBError("clickhouse-connect is not installed — run: pip install clickhouse-connect") from exc
        try:
            self.client = clickhouse_connect.get_client(
                host=cfg.host, port=cfg.port, username=cfg.user, password=cfg.password,
                secure=cfg.secure, connect_timeout=5, send_receive_timeout=600,
            )
        except Exception as exc:
            raise ResultsDBUnavailable(_connect_hint(cfg, exc)) from exc

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> list[str]:
        """Create the database if needed and apply pending migrations. Returns those applied."""
        try:
            self.client.command(f"CREATE DATABASE IF NOT EXISTS {self.db}")
        except Exception as exc:
            raise ResultsDBError(
                f"Could not create database {self.db!r} on {self.cfg.http_url}: {_short(exc)}. "
                "Ask an admin to create it, or set clickhouse_database to one you can use."
            ) from exc
        self.client.command(
            f"CREATE TABLE IF NOT EXISTS {self.db}.schema_migrations "
            "(version UInt32, name String, applied_at DateTime DEFAULT now()) "
            "ENGINE = MergeTree ORDER BY version"
        )
        done = {r[0] for r in self.client.query(f"SELECT version FROM {self.db}.schema_migrations").result_rows}
        applied = []
        for path in sorted(SCHEMA_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:3])
            if version in done:
                continue
            sql = path.read_text().replace("{db}", self.db).replace("{retention_days}", str(self.cfg.retention_days))
            for stmt in _split_sql(sql):
                self.client.command(stmt)
            self.client.insert(f"{self.db}.schema_migrations", [[version, path.name]], column_names=["version", "name"])
            applied.append(path.name)
        return applied

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def rows(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple]:
        return self._call(lambda: self.client.query(sql, parameters=params or {}).result_rows)

    def dicts(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        res = self._call(lambda: self.client.query(sql, parameters=params or {}))
        return [dict(zip(res.column_names, row)) for row in res.result_rows]

    def command(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        return self._call(lambda: self.client.command(sql, parameters=params or {}))

    def insert(self, table: str, rows: list[list], columns: list[str]) -> None:
        self._call(lambda: self.client.insert(f"{self.db}.{table}", rows, column_names=columns))

    def _call(self, fn):
        from clickhouse_connect.driver.exceptions import ClickHouseError
        try:
            return fn()
        except ClickHouseError as exc:
            text = str(exc)
            if "Connection refused" in text or "Max retries exceeded" in text:
                raise ResultsDBUnavailable(_connect_hint(self.cfg, exc)) from exc
            raise ResultsDBError(_clean_server_error(text.split("server response:", 1)[-1])) from exc

    # ------------------------------------------------------------------
    # Streaming insert (large files)
    # ------------------------------------------------------------------

    def stream_insert(self, sql: str, chunks: Iterable[bytes], settings: dict[str, Any] | None = None,
                      gzip_body: bool = False) -> dict:
        """
        POST `chunks` as the data of an INSERT … FORMAT … statement. Set gzip_body
        when the chunks are gzip-compressed. Returns ClickHouse's query summary.
        """
        params = {"query": sql, "database": self.db, **{k: str(v) for k, v in (settings or {}).items()}}
        headers = {"Content-Type": "application/octet-stream"}
        if gzip_body:
            headers["Content-Encoding"] = "gzip"
            params["enable_http_compression"] = "1"
        try:
            resp = requests.post(self.cfg.http_url, params=params, data=iter(chunks), headers=headers,
                                 auth=(self.cfg.user, self.cfg.password), timeout=(10, None))
        except requests.ConnectionError as exc:
            raise ResultsDBUnavailable(_connect_hint(self.cfg, exc)) from exc
        if resp.status_code != 200:
            raise ResultsDBError(_clean_server_error(resp.text))
        try:
            return json.loads(resp.headers.get("X-ClickHouse-Summary", "{}"))
        except ValueError:
            return {}


def file_chunks(path: Path, size: int = 4 << 20, gzip_level: int | None = None) -> Iterator[bytes]:
    """Read a file in chunks, optionally gzip-compressing on the fly."""
    comp = zlib.compressobj(gzip_level, zlib.DEFLATED, 31) if gzip_level is not None else None
    with open(path, "rb") as f:
        while True:
            block = f.read(size)
            if not block:
                break
            yield comp.compress(block) if comp else block
    if comp:
        yield comp.flush()


def connect(settings: dict[str, str] | None = None, url: str | None = None) -> ResultsDB:
    """Open the results database from settings (or an explicit URL) and apply the schema."""
    from projectstack.config import load_settings
    settings = settings or load_settings()
    cfg = ClickHouseConfig.from_url(url) if url else ClickHouseConfig.from_settings(settings)
    if url:
        cfg.retention_days = int(settings.get("results_retention_days") or 180)
    db = ResultsDB(cfg)
    try:
        db.ensure_schema()
    except ResultsDBError:
        raise
    except Exception as exc:
        raise ResultsDBUnavailable(_connect_hint(cfg, exc)) from exc
    return db


def _split_sql(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


def _clean_server_error(text: str) -> str:
    """ClickHouse errors are long; keep the code and the first sentence."""
    msg = " ".join(text.split())
    m = re.search(r"Code: (\d+)\. DB::Exception: (.*?)(?:\(version [\d.]+.*)?$", msg)
    return f"ClickHouse error {m.group(1)}: {m.group(2)[:600]}" if m else msg[:600]


def _connect_hint(cfg: ClickHouseConfig, exc: Exception) -> str:
    msg = _short(exc)
    if "Authentication failed" in msg or "AUTHENTICATION_FAILED" in msg or "516" in msg:
        return f"ClickHouse at {cfg.http_url} rejected user {cfg.user!r} — check clickhouse_user / clickhouse_password."
    hint = (" Start the local server with: docker compose -f deploy/clickhouse/docker-compose.yml up -d"
            if cfg.is_local else "")
    return (f"Cannot reach ClickHouse at {cfg.http_url} ({msg}).{hint} "
            "Or point ProjectStack at another server with --clickhouse or PSTACK_CLICKHOUSE_URL.")
