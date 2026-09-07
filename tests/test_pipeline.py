"""Full offline pipeline — TEST-PLAN §2.1 + the failure matrix rows."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_workflow_triage.audit import SqliteAuditStore
from ai_workflow_triage.models import (
    Category,
    Disposition,
    Route,
    TriageRequest,
    Urgency,
    ValidationStatus,
)
from ai_workflow_triage.pipeline import process_request
from ai_workflow_triage.providers import MockProvider
from ai_workflow_triage.scenarios import load_scenarios


@pytest.fixture
def store():
    s = SqliteAuditStore(":memory:")
    yield s
    s.close()


def _fixed_now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Every synthetic scenario lands on its expected block
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario_id", sorted(load_scenarios()))
def test_scenario_matches_expected_block(scenario_id, store):
    spec = load_scenarios()[scenario_id]
    request = TriageRequest(text=spec.input_text, scenario_id=scenario_id)
    result = process_request(request, MockProvider(), audit_store=store)
    d = result.decision
    assert d.route.value == spec.expected.route
    assert d.urgency.value == spec.expected.urgency
    assert d.disposition.value == spec.expected.disposition
    assert d.escalation.value == spec.expected.escalation
    assert d.human_review == spec.expected.human_review
    assert result.audit_persisted is True


# --------------------------------------------------------------------------- #
# AI-observation vs deterministic-decision split is preserved in the record
# --------------------------------------------------------------------------- #
def test_audit_record_keeps_ai_and_deterministic_separate():
    request = TriageRequest(text="x", scenario_id="security-prompt-injection")
    result = process_request(request, MockProvider())
    rec = result.audit_record
    # AI said SUPPORT / LOW
    assert rec.interpretation is not None
    assert rec.interpretation.category is Category.SUPPORT
    assert rec.interpretation.urgency is Urgency.LOW
    # deterministic layer said SECURITY / CRITICAL
    assert rec.final_route is Route.SECURITY
    assert rec.final_urgency is Urgency.CRITICAL
    assert "R02_SECURITY" in rec.rules_triggered
    # reasons carry both sources
    sources = {r.source for r in rec.reasons}
    assert sources == {"DETERMINISTIC", "AI_OBSERVATION"}


# --------------------------------------------------------------------------- #
# Failure behaviour — never a confident normal route
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("scenario_id", "status"),
    [
        ("malformed-ai-output", ValidationStatus.UNPARSEABLE),
        ("invalid-schema-ai-output", ValidationStatus.INVALID_SCHEMA),
        ("provider-error", ValidationStatus.PROVIDER_ERROR),
    ],
)
def test_unusable_ai_becomes_human_review(scenario_id, status, store):
    spec = load_scenarios()[scenario_id]
    result = process_request(
        TriageRequest(text=spec.input_text, scenario_id=scenario_id),
        MockProvider(),
        audit_store=store,
    )
    assert result.audit_record.validation_status is status
    assert result.decision.route is Route.HUMAN_REVIEW
    assert result.decision.disposition is Disposition.HUMAN_REVIEW
    assert result.decision.human_review is True
    assert result.audit_record.processing_errors  # explicit, not swallowed


def test_oversize_input_is_rejected_at_request_construction():
    # the request never reaches the pipeline / provider — it fails to construct
    with pytest.raises(ValidationError):
        TriageRequest(text="x" * 9000)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_same_input_same_decision_and_record_modulo_ids_and_time():
    req = TriageRequest(text="x", scenario_id="billing-double-charge")
    a = process_request(req, MockProvider(), now=_fixed_now, request_id="fixed")
    b = process_request(req, MockProvider(), now=_fixed_now, request_id="fixed")
    assert a.decision.model_dump() == b.decision.model_dump()
    assert a.audit_record.model_dump() == b.audit_record.model_dump()


# --------------------------------------------------------------------------- #
# Audit persistence failure is non-fatal
# --------------------------------------------------------------------------- #
def test_persistence_failure_still_returns_the_decision():
    class _FailingStore:
        def init_db(self) -> None: ...
        def write_record(self, record) -> None:
            raise RuntimeError("disk is on fire near /var/lib and key sk-ant-EXAMPLEfake0123456789")

        def get_record(self, audit_id):  # pragma: no cover
            return None

        def list_records(self, limit=50):  # pragma: no cover
            return []

    result = process_request(
        TriageRequest(text="x", scenario_id="support-howto"),
        MockProvider(),
        audit_store=_FailingStore(),
    )
    assert result.decision.route is Route.SUPPORT_TIER1  # decision still produced
    assert result.audit_persisted is False
    assert result.persistence_error is not None
    assert "sk-ant-" not in result.persistence_error  # redacted
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", result.persistence_error)


def test_no_audit_store_is_fine():
    result = process_request(
        TriageRequest(text="x", scenario_id="support-howto"), MockProvider(), audit_store=None
    )
    assert result.audit_persisted is False
    assert result.persistence_error is None
    assert result.audit_record is not None


# --------------------------------------------------------------------------- #
# Provider identity flows into the record
# --------------------------------------------------------------------------- #
def test_record_captures_provider_and_attempts():
    result = process_request(
        TriageRequest(text="x", scenario_id="invalid-schema-ai-output"), MockProvider()
    )
    assert result.audit_record.provider_name == "mock"
    assert result.audit_record.provider_attempts == 2  # one corrective retry was spent
    assert result.audit_record.provider_model is None


def test_free_text_tends_to_human_review():
    result = process_request(TriageRequest(text="please help with the thing"), MockProvider())
    # generic OTHER / 0.5 → R10 + R12
    assert result.decision.disposition is Disposition.HUMAN_REVIEW
