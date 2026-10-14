"""
`pstack ask`: answer a question about one run with an AI agent that can query it.

The model gets findings.json, the table schema and one tool, run_sql, which runs
a read-only query over this run's samples (sandbox.py). It drills down as
needed, then answers citing the queries it ran. Every number in the answer is
checked against findings.json and the query results, and the whole exchange
(question, queries, results, answer) is saved in the run's folder under ask/.

Raw samples never go to the model wholesale: it sees only what its own
aggregate queries return, at most 200 rows each.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from projectstack.llm.base import LLMError, Provider, ToolSpec
from projectstack.llm.prompts import load_prompt
from projectstack.results import numbers
from projectstack.results.sandbox import MAX_ROWS, QueryResult

MAX_QUERIES = 8

RUN_SQL = ToolSpec(
    name="run_sql",
    description=("Run one read-only ClickHouse SELECT over this run's data (tables samples and run_labels, already "
                 f"limited to this run). Returns at most {MAX_ROWS} rows as tab-separated text, or the reason the "
                 "query was rejected. Aggregate in SQL; use quantileExact for percentiles."),
    parameters={"type": "object", "additionalProperties": False, "required": ["sql"],
                "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}}},
)

SMALL_MODEL_WARNING = ("Small local models are unreliable at multi-step tool use; for ask, a capable model "
                       "(Claude, or Gemini) gives much better answers.")


@dataclass
class AskResult:
    run_id: str
    question: str
    answer: str
    queries: list[QueryResult] = field(default_factory=list)
    provider: str = ""
    prompt: str = ""
    numbers: numbers.NumberCheck | None = None
    usage: dict[str, int] = field(default_factory=dict)
    turns: int = 0
    stopped: str = "answered"            # answered | query_limit
    saved: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "question": self.question, "answer": self.answer,
                "queries": [q.as_dict() for q in self.queries], "provider": self.provider, "prompt": self.prompt,
                "numbers": self.numbers.as_dict() if self.numbers else None, "usage": self.usage,
                "turns": self.turns, "stopped": self.stopped,
                "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _ts(ms: int | None) -> str:
    if ms is None:
        return "toDateTime64(0, 3, 'UTC')"
    text = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"toDateTime64('{text}', 3, 'UTC')"


def ask(doc: dict[str, Any], question: str, *, provider: Provider, sandbox, max_queries: int = MAX_QUERIES,
        progress: Callable[[str], None] = lambda _: None) -> AskResult:
    question = " ".join((question or "").split())
    if not question:
        raise ValueError("Ask a question, e.g. pstack ask <run> \"Which request got slow first?\"")
    prompt = load_prompt("ask")
    steady = doc.get("steady") or {}
    system, user = prompt.render(
        run_id=doc["run"]["run_id"], scenario=doc["run"].get("scenario", ""), question=question,
        findings_json=json.dumps(doc, separators=(",", ":"), ensure_ascii=False),
        max_rows=MAX_ROWS, max_queries=max_queries,
        steady_start=_ts(steady.get("start_ms")), steady_end=_ts(steady.get("end_ms")),
    )
    result = AskResult(doc["run"]["run_id"], question, "", provider=provider.label, prompt=prompt.ref,
                       usage={"input_tokens": 0, "output_tokens": 0})
    messages: list[Any] = [provider.user_message(user)]
    answer = ""
    while True:
        result.turns += 1
        turn = provider.complete_with_tools(system, messages, [RUN_SQL], max_tokens=16000, effort="medium")
        for k in ("input_tokens", "output_tokens"):
            result.usage[k] += int((turn.usage or {}).get(k) or 0)
        if turn.model:
            result.provider = f"{provider.name}/{turn.model}"
        messages += turn.messages
        if not turn.tool_calls:
            answer = turn.text
            break
        if len(result.queries) >= max_queries:
            # Over budget: answer with what it has; the pending calls get no results
            result.stopped = "query_limit"
            answer = turn.text
            break
        outputs = []
        for call in turn.tool_calls:
            if call.name != RUN_SQL.name:
                outputs.append((call, f"Unknown tool {call.name!r}; the only tool is run_sql.", True))
                continue
            if len(result.queries) >= max_queries:
                outputs.append((call, f"Query budget of {max_queries} used up. Answer with what you have.", True))
                continue
            sql = str(call.arguments.get("sql") or "")
            progress(f"Q{len(result.queries) + 1}: {' '.join(sql.split())[:160]}")
            q = sandbox.run(sql)
            q.n = len(result.queries) + 1
            result.queries.append(q)
            outputs.append((call, q.as_text(), not q.ok))
        messages += provider.tool_results(outputs)
        if len(result.queries) >= max_queries:
            messages.append(provider.user_message(
                f"That was the last of your {max_queries} queries. Answer the question now from what you have."))
        if result.turns > max_queries + 3:
            raise LLMError("The model kept calling tools without answering")
    result.answer = (answer or "").strip() or "(The model did not give an answer.)"
    allowed = numbers.Allowed(doc).add({"question": question,
                                        "queries": [q.as_dict()["rows"] for q in result.queries if q.ok]})
    result.numbers = numbers.check({"answer": result.answer}, doc, allowed)
    return result


def save(result: AskResult, folder: Path) -> Path:
    out = folder / "ask"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    n = 1
    while path.exists():
        n += 1
        path = out / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{n}.json"
    path.write_text(json.dumps(result.as_dict(), indent=2, ensure_ascii=False, default=str))
    result.saved = path
    return path
