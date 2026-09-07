"""Audit record + a thin SQLite store.

One row per processed request. A reader can answer two separate questions from
one row:

* *What did the AI think?*  → ``interpretation`` + ``validation_status`` + ``validation_error``
* *What rule caused the decision?*  → ``rules_triggered`` + ``rule_trace`` + ``reasons``

Never stored: API keys, environment values, or model chain-of-thought (the
schema has none). ``AuditRecord`` is built from already-sanitised objects; the
store adds nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    Disposition,
    EscalationLevel,
    KeywordSignals,
    Reason,
    Route,
    RuleTraceEntry,
    TriageInterpretation,
    Urgency,
    ValidationStatus,
)

_TABLE = "triage_audit"

# Every column, in the order the CREATE statement declares them (minus row_seq,
# which the store manages).
_ALL_COLUMNS: tuple[str, ...] = (
    "audit_id",
    "request_id",
    "received_at",
    "schema_version",
    "harness_version",
    "input_text",
    "channel",
    "provider_name",
    "provider_model",
    "provider_attempts",
    "validation_status",
    "validation_error",
    "interpretation",
    "keyword_signals",
    "rules_triggered",
    "rule_trace",
    "reasons",
    "final_route",
    "suggested_route",
    "final_urgency",
    "final_disposition",
    "escalation",
    "human_review",
    "human_review_reason",
    "missing_information",
    "decision_confidence",
    "processing_errors",
)

# Columns holding a JSON document as TEXT.
_JSON_COLUMNS = frozenset(
    {
        "interpretation",
        "keyword_signals",
        "rules_triggered",
        "rule_trace",
        "reasons",
        "missing_information",
        "processing_errors",
    }
)

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    audit_id            TEXT PRIMARY KEY,
    request_id          TEXT NOT NULL,
    received_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    harness_version     TEXT NOT NULL,
    input_text          TEXT NOT NULL,
    channel             TEXT,
    provider_name       TEXT NOT NULL,
    provider_model      TEXT,
    provider_attempts   INTEGER NOT NULL,
    validation_status   TEXT NOT NULL,
    validation_error    TEXT,
    interpretation      TEXT,
    keyword_signals     TEXT NOT NULL,
    rules_triggered     TEXT NOT NULL,
    rule_trace          TEXT NOT NULL,
    reasons             TEXT NOT NULL,
    final_route         TEXT NOT NULL,
    suggested_route     TEXT,
    final_urgency       TEXT NOT NULL,
    final_disposition   TEXT NOT NULL,
    escalation          TEXT NOT NULL,
    human_review        INTEGER NOT NULL,
    human_review_reason TEXT,
    missing_information TEXT NOT NULL,
    decision_confidence REAL NOT NULL,
    processing_errors   TEXT NOT NULL,
    row_seq             INTEGER
);
"""


class AuditRecord(BaseModel):
    """The canonical, strict record. ``audit_persisted`` is deliberately NOT a
    field — whether the write succeeded is reported on ``pipeline.ProcessResult``,
    not stored in the row it describes.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str
    request_id: str
    received_at: str
    schema_version: int
    harness_version: str

    input_text: str
    channel: str | None = None

    provider_name: str
    provider_model: str | None = None
    provider_attempts: int
    validation_status: ValidationStatus
    validation_error: str | None = None

    interpretation: TriageInterpretation | None = None
    keyword_signals: KeywordSignals

    rules_triggered: list[str] = Field(default_factory=list)
    rule_trace: list[RuleTraceEntry] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)

    final_route: Route
    suggested_route: Route | None = None
    final_urgency: Urgency
    final_disposition: Disposition
    escalation: EscalationLevel
    human_review: bool
    human_review_reason: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    decision_confidence: float

    processing_errors: list[str] = Field(default_factory=list)


class AuditStore(Protocol):
    def init_db(self) -> None: ...
    def write_record(self, record: AuditRecord) -> None: ...
    def get_record(self, audit_id: str) -> AuditRecord | None: ...
    def list_records(self, limit: int = 50) -> list[AuditRecord]: ...


def _dump(record: AuditRecord) -> dict[str, object]:
    data = record.model_dump(mode="json")
    row: dict[str, object] = {}
    for col in _ALL_COLUMNS:
        value = data.get(col)
        if col in _JSON_COLUMNS:
            row[col] = json.dumps(value, sort_keys=True)
        elif col == "human_review":
            row[col] = 1 if value else 0
        else:
            row[col] = value
    return row


def _load(row: sqlite3.Row) -> AuditRecord:
    data: dict[str, object] = {}
    for key in row.keys():
        if key == "row_seq":
            continue
        value = row[key]
        if key in _JSON_COLUMNS and value is not None:
            data[key] = json.loads(value)
        elif key == "human_review":
            data[key] = bool(value)
        else:
            data[key] = value
    return AuditRecord.model_validate(data)


class SqliteAuditStore:
    """Stdlib ``sqlite3``, one table, parameterised SQL, no ORM. Single shared
    connection guarded by a lock (single-process demo; supports ``:memory:``).
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._seq = 0
        self.init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def init_db(self) -> None:
        with self._lock:
            self._conn.executescript(_CREATE_SQL)
            self._conn.commit()
            cur = self._conn.execute(f"SELECT COALESCE(MAX(row_seq), 0) FROM {_TABLE}")
            self._seq = int(cur.fetchone()[0])

    def write_record(self, record: AuditRecord) -> None:
        row = _dump(record)
        columns = [*_ALL_COLUMNS, "row_seq"]
        placeholders = ", ".join("?" for _ in columns)
        with self._lock:
            self._seq += 1
            values = [row[col] for col in _ALL_COLUMNS] + [self._seq]
            self._conn.execute(
                f"INSERT INTO {_TABLE} ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()

    def get_record(self, audit_id: str) -> AuditRecord | None:
        with self._lock:
            cur = self._conn.execute(f"SELECT * FROM {_TABLE} WHERE audit_id = ?", (audit_id,))
            row = cur.fetchone()
        return _load(row) if row is not None else None

    def list_records(self, limit: int = 50) -> list[AuditRecord]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM {_TABLE} ORDER BY row_seq DESC LIMIT ?", (int(limit),)
            )
            rows: Iterable[sqlite3.Row] = cur.fetchall()
        return [_load(row) for row in rows]
