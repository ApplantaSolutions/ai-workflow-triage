"""AuditRecord + SqliteAuditStore — TEST-PLAN §1.9."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from ai_workflow_triage.audit import AuditRecord, SqliteAuditStore
from ai_workflow_triage.models import (
    Category,
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


def _record(**overrides) -> AuditRecord:
    base = dict(
        audit_id="a-1",
        request_id="a-1",
        received_at="2026-01-02T03:04:05+00:00",
        schema_version=1,
        harness_version="0.1.0",
        input_text="you charged me twice on invoice M-1",
        channel="email",
        provider_name="mock",
        provider_model=None,
        provider_attempts=1,
        validation_status=ValidationStatus.OK,
        validation_error=None,
        interpretation=TriageInterpretation(
            category=Category.BILLING,
            urgency=Urgency.NORMAL,
            summary="duplicate charge",
            customer_intent="refund",
            entities={"invoice_number": "M-1"},
            risk_signals=[],
            missing_information=[],
            model_confidence=0.9,
            reasoning_summary="billing issue",
        ),
        keyword_signals=KeywordSignals(
            billing_language=True, matched={"billing_language": ["charged me twice"]}
        ),
        rules_triggered=["R05_BILLING", "R99_DEFAULT_SUPPORT"],
        rule_trace=[
            RuleTraceEntry(
                rule_id="R05_BILLING", applied=True, effect="route=BILLING", explanation="billing"
            )
        ],
        reasons=[Reason(source="DETERMINISTIC", rule_id="R05_BILLING", text="billing indicators")],
        final_route=Route.BILLING,
        suggested_route=None,
        final_urgency=Urgency.NORMAL,
        final_disposition=Disposition.ROUTED,
        escalation=EscalationLevel.NONE,
        human_review=False,
        human_review_reason=None,
        missing_information=[],
        decision_confidence=0.9,
        processing_errors=[],
    )
    base.update(overrides)
    return AuditRecord(**base)


@pytest.fixture
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


def test_audit_record_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        AuditRecord(**{**_record().model_dump(), "audit_persisted": True})


def test_init_db_is_idempotent(store):
    store.init_db()
    store.init_db()  # no error


def test_write_then_get_round_trips_every_field(store):
    record = _record()
    store.write_record(record)
    loaded = store.get_record("a-1")
    assert loaded is not None
    assert loaded.model_dump() == record.model_dump()


def test_get_missing_returns_none(store):
    assert store.get_record("nope") is None


def test_list_records_is_newest_first_and_respects_limit(store):
    for i in range(5):
        store.write_record(_record(audit_id=f"a-{i}", request_id=f"a-{i}"))
    rows = store.list_records(limit=3)
    assert [r.audit_id for r in rows] == ["a-4", "a-3", "a-2"]


def test_record_with_no_interpretation_persists_cleanly(store):
    record = _record(
        audit_id="b-1",
        request_id="b-1",
        interpretation=None,
        validation_status=ValidationStatus.UNPARSEABLE,
        validation_error="no JSON object found in the response",
        final_route=Route.HUMAN_REVIEW,
        final_disposition=Disposition.HUMAN_REVIEW,
        human_review=True,
        human_review_reason="AI interpretation unusable (UNPARSEABLE)",
        rules_triggered=["R01_AI_UNUSABLE"],
        rule_trace=[
            RuleTraceEntry(
                rule_id="R01_AI_UNUSABLE", applied=True, effect="terminal", explanation="unusable"
            )
        ],
        reasons=[
            Reason(
                source="AI_OBSERVATION",
                text="AI interpretation was unusable and was not relied on.",
            )
        ],
        decision_confidence=0.98,
        processing_errors=["AI interpretation unusable (UNPARSEABLE)"],
    )
    store.write_record(record)
    loaded = store.get_record("b-1")
    assert loaded is not None
    assert loaded.interpretation is None
    assert loaded.validation_status is ValidationStatus.UNPARSEABLE


def test_no_key_or_env_value_in_any_stored_column(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-EXAMPLEfake0123456789abcdef")
    store.write_record(_record())
    # dump every column of every row back out and scan it
    with store._lock:  # noqa: SLF001 - test inspects the raw rows on purpose
        rows = store._conn.execute("SELECT * FROM triage_audit").fetchall()
    blob = " ".join(str(v) for row in rows for v in tuple(row))
    assert "sk-ant-" not in blob
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", blob)


def test_a_second_store_on_a_file_db_reads_prior_rows(tmp_path):
    db = tmp_path / "audit.sqlite"
    first = SqliteAuditStore(db)
    first.write_record(_record())
    first.close()
    second = SqliteAuditStore(db)
    try:
        assert second.get_record("a-1") is not None
        assert len(second.list_records()) == 1
    finally:
        second.close()
