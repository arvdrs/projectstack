"""
Read-only SQL over one run, for the `ask` agent.

Every query the model writes is checked twice:

1. Before it runs, by ClickHouse's own parser (EXPLAIN AST): it must be a single
   SELECT that reads only the `samples` and `run_labels` tables of the results
   database, with no table functions (url, file, remote, s3 …), no FORMAT or
   INTO OUTFILE and no SETTINGS.
2. While it runs, by the server: readonly=1 (no writes, no setting changes, no
   temporary tables or external table functions), a row filter that limits both
   tables to this run (additional_table_filters), a time limit, a memory cap and
   results cut at 200 rows.

With clickhouse_ask_user / clickhouse_ask_password set, queries run as that user
(on a team server: a SELECT-only user whose profile has readonly = 2, so the
limits above can still be applied per query).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from projectstack.results.db import ClickHouseConfig

ALLOWED_TABLES = ("samples", "run_labels")
ALLOWED_TABLE_FUNCTIONS = ("numbers",)
MAX_ROWS = 200
MAX_SQL_CHARS = 10_000
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_OUTFILE_RE = re.compile(r"\bINTO\s+OUTFILE\b|\bFORMAT\s+[A-Za-z]+\s*$", re.I)


class SQLRejected(ValueError):
    """The query is not allowed; the message says why (it is shown to the model)."""


@dataclass
class QueryResult:
    n: int                                   # Q1, Q2 … in the order the model ran them
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_text(self, max_chars: int = 12_000) -> str:
        """What the model sees: a header line, then tab-separated rows."""
        if self.error:
            return f"Q{self.n} failed: {self.error}"
        lines = ["\t".join(self.columns)] + ["\t".join(_cell(v) for v in row) for row in self.rows]
        body = "\n".join(lines)
        if len(body) > max_chars:
            body = body[:max_chars].rsplit("\n", 1)[0] + "\n… (output cut)"
        more = f"; cut at {MAX_ROWS} rows — aggregate or add LIMIT" if self.truncated else ""
        return f"Q{self.n}: {len(self.rows)} row(s) in {self.seconds:.2f}s{more}\n{body}"

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "sql": self.sql, "columns": self.columns,
                "rows": [[_cell(v) for v in row] for row in self.rows], "truncated": self.truncated,
                "seconds": round(self.seconds, 3), "error": self.error}


def _cell(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e15 else f"{v:.0f}"
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="milliseconds").replace("+00:00", "")
    if isinstance(v, date):
        return v.isoformat()
    text = str(v).replace("\t", " ").replace("\n", " ")
    return text if len(text) <= 300 else text[:300] + "…"


def _short(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    m = re.search(r"DB::Exception: (.*?)(?: \(version [\d.]+|$)", text)
    return (m.group(1) if m else text).replace(" FORMAT TabSeparatedRaw", "")[:400]


class RunSandbox:
    def __init__(self, cfg: ClickHouseConfig, run_id: str, settings: dict[str, str] | None = None,
                 timeout_s: int = 10, max_rows: int = MAX_ROWS):
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(f"Invalid run id {run_id!r}")
        settings = settings or {}
        import clickhouse_connect
        user = settings.get("clickhouse_ask_user") or cfg.user
        password = settings.get("clickhouse_ask_password") if settings.get("clickhouse_ask_user") else cfg.password
        self.run_id, self.db, self.max_rows, self.timeout_s = run_id, cfg.database, max_rows, timeout_s
        self.client = clickhouse_connect.get_client(
            host=cfg.host, port=cfg.port, username=user, password=password or "", database=cfg.database,
            secure=cfg.secure, connect_timeout=5, send_receive_timeout=timeout_s + 20)
        rows = self.client.query("SELECT name FROM system.tables WHERE database = {d:String}",
                                 parameters={"d": cfg.database}).result_rows
        self.other_tables = {r[0] for r in rows} - set(ALLOWED_TABLES)
        self.count = 0
        quoted = run_id.replace("'", "''")
        filters = ", ".join(f"'{self.db}.{t}': 'run_id = ''{quoted}'''" for t in ALLOWED_TABLES)
        self.query_settings = {
            "readonly": 1,
            "max_execution_time": timeout_s,
            "max_result_rows": max_rows + 1,
            "result_overflow_mode": "break",
            "max_result_bytes": 50_000_000,
            "max_memory_usage": 4_000_000_000,
            "additional_table_filters": "{" + filters + "}",
        }

    # ------------------------------------------------------------------

    def check(self, sql: str) -> str:
        """The query, cleaned, if it may run; SQLRejected otherwise."""
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            raise SQLRejected("empty query")
        if len(sql) > MAX_SQL_CHARS:
            raise SQLRejected(f"query longer than {MAX_SQL_CHARS} characters")
        if _OUTFILE_RE.search(sql):
            raise SQLRejected("FORMAT and INTO OUTFILE clauses are not allowed; results come back as a table")
        try:
            raw = self.client.raw_query(f"EXPLAIN AST {sql}", settings={"readonly": 1}, fmt="TabSeparatedRaw")
        except Exception as exc:
            raise SQLRejected(_short(exc)) from exc
        lines = [ln for ln in raw.decode("utf-8", "replace").split("\n") if ln.strip()]
        if not lines or not lines[0].startswith("SelectWithUnionQuery"):
            kind = lines[0].split(" ")[0] if lines else "nothing"
            raise SQLRejected(f"only SELECT queries are allowed (got {kind})")
        parents: list[tuple[int, str]] = []
        for line in lines:
            depth = len(line) - len(line.lstrip(" "))
            node = line.strip()
            while parents and parents[-1][0] >= depth:
                parents.pop()
            parent = parents[-1][1] if parents else ""
            parents.append((depth, node))
            if depth == 1 and not node.startswith("ExpressionList") and node != "Identifier TabSeparatedRaw":
                raise SQLRejected("only a plain SELECT is allowed (no FORMAT, INTO OUTFILE or SETTINGS)")
            if node.startswith("Set ") or node == "Set":
                raise SQLRejected("SETTINGS clauses are not allowed")
            if node.startswith("TableIdentifier "):
                self._check_table(node.split(" ")[1])
            elif node.startswith("Function ") and parent.startswith("TableExpression"):
                name = node.split(" ")[1]
                if name not in ALLOWED_TABLE_FUNCTIONS:
                    raise SQLRejected(f"table function {name}() is not allowed; query the samples table")
        return sql

    def _check_table(self, name: str) -> None:
        if "." in name:
            database, table = name.split(".", 1)
            if database != self.db or table not in ALLOWED_TABLES:
                raise SQLRejected(f"table {name} is not available; use samples or run_labels")
        elif name in self.other_tables:
            raise SQLRejected(f"table {name} is not available; use samples or run_labels")

    def run(self, sql: str) -> QueryResult:
        self.count += 1
        result = QueryResult(self.count, (sql or "").strip())
        t0 = time.monotonic()
        try:
            result.sql = self.check(sql)
            res = self.client.query(result.sql, settings=self.query_settings)
            result.columns = list(res.column_names)
            rows = [list(r) for r in res.result_rows]
            result.truncated = len(rows) > self.max_rows
            result.rows = rows[: self.max_rows]
        except SQLRejected as exc:
            result.error = f"rejected: {exc}"
        except Exception as exc:
            result.error = _short(exc)
        result.seconds = time.monotonic() - t0
        return result
