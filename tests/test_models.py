"""Schema + enum tests — TEST-PLAN §1.1 and the schema-validation rows of §1.2.

String-level provider parsing (fences, prose, UNPARSEABLE) is a later checkpoint
that adds ``validation.py``; here we validate the strict schema directly with
``model_validate``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_workflow_triage.models import (
    MAX_INPUT_CHARS,
    MAX_RAW_RESPONSE_CHARS,
    Category,
    Disposition,
    EscalationLevel,
    RiskSignal,
    Route,
    TriageInterpretation,
    TriageRequest,
    Urgency,
    ValidationResult,
    ValidationStatus,
    disposition_rank,
    escalation_rank,
    max_escalation,
    max_urgency,
    urgency_rank,
)

_VALID_INTERP = {
    "category": "BILLING",
    "urgency": "NORMAL",
    "summary": "Customer reports a duplicate charge.",
    "customer_intent": "refund a duplicate charge",
    "entities": {"invoice_number": "M-4821"},
    "risk_signals": ["DUPLICATE_CHARGE"],
    "missing_information": [],
    "model_confidence": 0.9,
    "reasoning_summary": "Two identical charges described; billing issue.",
}


# --------------------------------------------------------------------------- #
# TriageRequest
# --------------------------------------------------------------------------- #
def test_request_trims_text():
    assert TriageRequest(text="  hello  ").text == "hello"


def test_request_blank_channel_becomes_none():
    req = TriageRequest(text="hi", channel="   ", scenario_id="")
    assert req.channel is None
    assert req.scenario_id is None


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_request_empty_text_rejected(bad):
    with pytest.raises(ValidationError):
        TriageRequest(text=bad)


def test_request_oversize_text_rejected():
    with pytest.raises(ValidationError) as excinfo:
        TriageRequest(text="x" * (MAX_INPUT_CHARS + 1))
    assert str(MAX_INPUT_CHARS) in str(excinfo.value)


def test_request_at_the_cap_is_ok():
    assert len(TriageRequest(text="x" * MAX_INPUT_CHARS).text) == MAX_INPUT_CHARS


def test_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        TriageRequest(text="hi", priority="high")


# --------------------------------------------------------------------------- #
# TriageInterpretation — happy path
# --------------------------------------------------------------------------- #
def test_interpretation_valid():
    interp = TriageInterpretation.model_validate(_VALID_INTERP)
    assert interp.category is Category.BILLING
    assert interp.risk_signals == [RiskSignal.DUPLICATE_CHARGE]


def test_interpretation_forbids_unknown_key():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "route": "BILLING"})


def test_interpretation_missing_required_field_names_it():
    payload = {k: v for k, v in _VALID_INTERP.items() if k != "category"}
    with pytest.raises(ValidationError) as excinfo:
        TriageInterpretation.model_validate(payload)
    assert "category" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# TriageInterpretation — model_confidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [1.7, -0.1, "high", "0.9", True, False, None, [0.5]])
def test_model_confidence_bad_values_rejected(value):
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "model_confidence": value})


@pytest.mark.parametrize("value", [0.0, 1.0, 0.55, 1, 0])
def test_model_confidence_good_values_accepted(value):
    interp = TriageInterpretation.model_validate({**_VALID_INTERP, "model_confidence": value})
    assert isinstance(interp.model_confidence, float)


# --------------------------------------------------------------------------- #
# TriageInterpretation — entities / risk_signals / lengths
# --------------------------------------------------------------------------- #
def test_entities_unknown_key_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "entities": {"ssn": "1"}})


def test_entities_non_string_value_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "entities": {"amount": 149}})


def test_entities_value_over_200_chars_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "entities": {"url": "u" * 201}})


def test_risk_signals_unknown_enum_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "risk_signals": ["NUCLEAR"]})


def test_risk_signals_deduped_preserving_order():
    interp = TriageInterpretation.model_validate(
        {**_VALID_INTERP, "risk_signals": ["ANGRY_TONE", "DUPLICATE_CHARGE", "ANGRY_TONE"]}
    )
    assert interp.risk_signals == [RiskSignal.ANGRY_TONE, RiskSignal.DUPLICATE_CHARGE]


@pytest.mark.parametrize("field", ["summary", "customer_intent", "reasoning_summary"])
def test_text_fields_reject_empty(field):
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, field: ""})


def test_summary_over_400_chars_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "summary": "s" * 401})


def test_missing_information_entry_over_120_chars_rejected():
    with pytest.raises(ValidationError):
        TriageInterpretation.model_validate({**_VALID_INTERP, "missing_information": ["m" * 121]})


# --------------------------------------------------------------------------- #
# ValidationResult
# --------------------------------------------------------------------------- #
def test_validation_result_truncates_raw_response():
    result = ValidationResult(
        status=ValidationStatus.UNPARSEABLE, raw_response="z" * (MAX_RAW_RESPONSE_CHARS + 50)
    )
    assert result.raw_response is not None
    assert result.raw_response.endswith("…[truncated]")
    assert len(result.raw_response) == MAX_RAW_RESPONSE_CHARS + len("…[truncated]")


def test_validation_result_short_raw_response_untouched():
    result = ValidationResult(status=ValidationStatus.OK, raw_response="short")
    assert result.raw_response == "short"


# --------------------------------------------------------------------------- #
# Enums + ordering helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "enum_cls",
    [Category, Urgency, EscalationLevel, RiskSignal, Route, Disposition, ValidationStatus],
)
def test_enum_values_round_trip(enum_cls):
    for member in enum_cls:
        assert enum_cls(member.value) is member


def test_unknown_enum_value_raises():
    with pytest.raises(ValueError):
        Category("NOPE")


def test_urgency_ordering():
    assert urgency_rank(Urgency.LOW) < urgency_rank(Urgency.CRITICAL)
    assert max_urgency(Urgency.LOW, Urgency.HIGH, Urgency.NORMAL) is Urgency.HIGH
    assert max_urgency(Urgency.NORMAL) is Urgency.NORMAL


def test_escalation_ordering():
    assert escalation_rank(EscalationLevel.NONE) < escalation_rank(EscalationLevel.CRITICAL)
    assert (
        max_escalation(EscalationLevel.NONE, EscalationLevel.PRIORITY) is EscalationLevel.PRIORITY
    )


def test_disposition_ordering_matches_locked_hierarchy():
    assert disposition_rank(Disposition.ROUTED) < disposition_rank(Disposition.NEEDS_INFORMATION)
    assert disposition_rank(Disposition.NEEDS_INFORMATION) < disposition_rank(
        Disposition.HUMAN_REVIEW
    )
