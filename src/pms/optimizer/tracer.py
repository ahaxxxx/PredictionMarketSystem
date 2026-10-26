"""Self-hosted call tracing for the optimizer loop (LangSmith replacement).

Every scored step of the optimization loop (fast evaluation, slow validation,
promotion decision) is recorded as a span row in a local SQLite database.
Parent/child nesting is expressed with ``parent_id`` and recovered on read,
so a whole trace renders as a nested tree without any external service.

Usage::

    tracer = Tracer.open("optimizer-traces.sqlite3")

    with tracer.trace("fast_evaluate", candidate_id="c-1") as span:
        score = evaluate(candidate)
        span.set_outputs({"brier": score.brier})
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

__all__ = ("Span", "SpanNode", "Tracer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id     TEXT PRIMARY KEY,
    parent_id   TEXT,
    name        TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    status      TEXT NOT NULL DEFAULT 'ok',
    inputs_json TEXT,
    outputs_json TEXT,
    meta_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_id);
CREATE INDEX IF NOT EXISTS idx_spans_name ON spans(name);
"""

_current_span: ContextVar["Span | None"] = ContextVar("optimizer_tracer_span", default=None)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@dataclass(slots=True)
class Span:
    """A single in-flight trace span."""

    span_id: str
    parent_id: str | None
    name: str
    started_at: float
    inputs: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)
    _tracer: Tracer | None = field(default=None, repr=False)
    ended_at: float | None = field(default=None, init=False)
    status: str = field(default="ok", init=False)
    outputs: dict[str, Any] = field(default_factory=dict, init=False)

    def set_outputs(self, **outputs: Any) -> None:
        self.outputs.update(outputs)

    def set_meta(self, **meta: Any) -> None:
        merged = dict(self.meta)
        merged.update(meta)
        object.__setattr__(self, "meta", merged)

    def finish(self, status: str = "ok") -> None:
        if self.ended_at is not None:
            return
        self.ended_at = time.time()
        self.status = status
        if self._tracer is not None:
            self._tracer._flush(self)

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass(frozen=True, slots=True)
class SpanNode:
    """A span plus its children, for nested-tree reads."""

    span_id: str
    parent_id: str | None
    name: str
    status: str
    started_at: float
    ended_at: float | None
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    meta: Mapping[str, Any]
    children: tuple["SpanNode", ...]

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "status": self.status,
            "duration_s": self.duration_s,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "meta": dict(self.meta),
            "children": [child.to_dict() for child in self.children],
        }


class Tracer:
    """SQLite-backed span recorder with context-manager nesting."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    @classmethod
    def open(cls, path: str | Path) -> Self:
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        return cls(connection)

    @classmethod
    def in_memory(cls) -> Self:
        return cls(sqlite3.connect(":memory:"))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def trace(
        self,
        name: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        **meta: Any,
    ) -> Iterator[Span]:
        """Open a span; auto-nests under the span active in this context."""
        parent = _current_span.get()
        span = Span(
            span_id=uuid.uuid4().hex,
            parent_id=parent.span_id if parent is not None else None,
            name=name,
            started_at=time.time(),
            inputs=dict(inputs or {}),
            meta=meta,
            _tracer=self,
        )
        token = _current_span.set(span)
        try:
            yield span
        except Exception as exc:
            span.set_outputs(error=repr(exc))
            span.finish(status="error")
            raise
        else:
            span.finish()
        finally:
            _current_span.reset(token)

    def _flush(self, span: Span) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO spans (
                span_id, parent_id, name, started_at, ended_at, status,
                inputs_json, outputs_json, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span.span_id,
                span.parent_id,
                span.name,
                span.started_at,
                span.ended_at,
                span.status,
                _json_dumps(dict(span.inputs)),
                _json_dumps(span.outputs),
                _json_dumps(dict(span.meta)),
            ),
        )
        self._connection.commit()

    # ---- reads -----------------------------------------------------------

    def get_span(self, span_id: str) -> SpanNode | None:
        row = self._connection.execute(
            "SELECT * FROM spans WHERE span_id = ?", (span_id,)
        ).fetchone()
        if row is None:
            return None
        return self._to_node(row)

    def get_trace(self, root_span_id: str) -> SpanNode | None:
        """Return the nested tree rooted at ``root_span_id``."""
        return self.get_span(root_span_id)

    def roots(self, *, name: str | None = None, limit: int = 100) -> list[SpanNode]:
        """Recent root spans (no parent), newest first."""
        query = "SELECT * FROM spans WHERE parent_id IS NULL"
        params: list[Any] = []
        if name is not None:
            query += " AND name = ?"
            params.append(name)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = self._connection.execute(query, params).fetchall()
        return [self._to_node(row) for row in rows]

    def _to_node(self, row: sqlite3.Row) -> SpanNode:
        child_rows = self._connection.execute(
            "SELECT * FROM spans WHERE parent_id = ? ORDER BY started_at ASC",
            (row["span_id"],),
        ).fetchall()
        return SpanNode(
            span_id=row["span_id"],
            parent_id=row["parent_id"],
            name=row["name"],
            status=row["status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            inputs=json.loads(row["inputs_json"] or "{}"),
            outputs=json.loads(row["outputs_json"] or "{}"),
            meta=json.loads(row["meta_json"] or "{}"),
            children=tuple(self._to_node(child) for child in child_rows),
        )

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM spans").fetchone()
        return int(row["n"])
