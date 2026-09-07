# ADR-0003 — SQLite via the standard library for the audit store

**Status:** accepted (CP1)

## Context

Every processed request must produce a durable, inspectable audit record. The
brief calls for SQLite and warns against Postgres / Redis / an ORM / cloud infra
for V1.

## Decision

One table, `triage_audit`, accessed through Python's stdlib `sqlite3` from a
thin `src/ai_workflow_triage/audit.py` (parameterized SQL, no ORM). The DB file path is
configurable; the demo/tests use a temp file or `:memory:`.

- `init_db()` is idempotent (`CREATE TABLE IF NOT EXISTS`).
- `write_record()` is **best-effort**: if it fails, the caller still gets the
  `TriageDecision` and `ProcessResult` carries `audit_persisted: false` plus a
  redacted `persistence_error`. A failed audit write must never lose the
  decision or crash the request. (The store's `write_record` itself raises; the
  *pipeline* is what makes it best-effort.)
- Structured parts (`interpretation`, `rule_trace`, `reasons`, `keyword_signals`,
  …) are stored as JSON text, one column each; `AuditRecord` round-trips them.
- **Never stored:** API keys, environment values, or anything resembling model
  chain-of-thought.

## Consequences

- Zero external infrastructure; the whole project runs from a checkout.
- Not built for concurrent writers — a single-process demo, documented as such.
- If this ever needed Postgres, `audit.py` is the only file that changes; the
  pipeline depends on its interface, not on SQLite.
