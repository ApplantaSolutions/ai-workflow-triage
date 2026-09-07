"""Finalizer + decision_confidence + the locked corrections — TEST-PLAN §1.6.

Covers every row of the SPEC §13 ``decision_confidence`` ladder and the five
locked CP2 corrections (SPEC §10 / §13 / R07 / R09 / R13).
"""

from __future__ import annotations

from ai_workflow_triage.config import DEFAULT_CONFIG
from ai_workflow_triage.decision import decide
from ai_workflow_triage.models import (
    Category,
    Disposition,
    EscalationLevel,
    RiskSignal,
    Route,
    Urgency,
    ValidationStatus,
)

from .conftest import make_ctx

_C = DEFAULT_CONFIG.confidence


# --------------------------------------------------------------------------- #
# decision_confidence ladder (SPEC §13) — every row
# --------------------------------------------------------------------------- #
def test_confidence_terminal_ai_unusable():
    decision = decide(
        make_ctx(validation_status=ValidationStatus.PROVIDER_ERROR, no_interpretation=True)
    )
    assert decision.decision_confidence == _C.ai_unusable == 0.98
    assert decision.disposition is Disposition.HUMAN_REVIEW


def test_confidence_terminal_security():
    decision = decide(make_ctx(text="we were hacked", category=Category.ACCOUNT))
    assert decision.decision_confidence == _C.security_terminal == 0.95


def test_confidence_keyword_and_ai_agree_billing():
    decision = decide(
        make_ctx(
            text="you charged me twice, please refund",
            category=Category.BILLING,
            entities={"account_email": "p@example-co.test"},
            risk_signals=[RiskSignal.DUPLICATE_CHARGE],
        )
    )
    assert decision.route is Route.BILLING
    assert decision.decision_confidence == _C.keyword_and_ai_agree == 0.90


def test_confidence_keyword_and_ai_agree_outage():
    decision = decide(
        make_ctx(
            text="the whole dashboard is down",
            category=Category.OUTAGE,
            risk_signals=[RiskSignal.SERVICE_DOWN],
        )
    )
    assert decision.route is Route.RELIABILITY
    assert decision.decision_confidence == _C.keyword_and_ai_agree


def test_confidence_single_rule_corroborated_bug_error_code():
    decision = decide(
        make_ctx(
            text="Save throws E-1042",
            category=Category.BUG,
            entities={"error_code": "E-1042"},
            missing_information=[],
        )
    )
    assert decision.route is Route.ENGINEERING
    assert decision.decision_confidence == _C.single_rule_corroborated == 0.80


def test_confidence_billing_signal_without_category_agreement():
    decision = decide(make_ctx(text="I want a refund on this invoice", category=Category.SUPPORT))
    assert decision.route is Route.BILLING
    assert decision.decision_confidence == _C.single_rule_corroborated


def test_confidence_ai_category_only_capped():
    decision = decide(make_ctx(category=Category.FEATURE_REQUEST, model_confidence=0.95))
    assert decision.route is Route.PRODUCT
    assert decision.decision_confidence == min(0.95, _C.ai_category_only_cap) == 0.75


def test_confidence_human_review_gate():
    decision = decide(
        make_ctx(
            text="help with the thing from the call", category=Category.OTHER, model_confidence=0.3
        )
    )
    assert decision.disposition is Disposition.HUMAN_REVIEW
    assert decision.decision_confidence == _C.human_review_gate == 0.70


def test_confidence_needs_information_gate():
    decision = decide(
        make_ctx(
            text="something is broken, please fix",
            category=Category.BUG,
            missing_information=["no steps to reproduce", "no error message"],
            model_confidence=0.6,
        )
    )
    assert decision.disposition is Disposition.NEEDS_INFORMATION
    assert decision.decision_confidence == _C.needs_information_gate == 0.75


def test_confidence_default_support():
    decision = decide(make_ctx(text="hi, quick question", category=Category.SUPPORT))
    assert decision.route is Route.SUPPORT_TIER1
    assert decision.decision_confidence == _C.default_support == 0.50


# --------------------------------------------------------------------------- #
# Finalizer behaviour
# --------------------------------------------------------------------------- #
def test_routed_decision_has_no_suggested_route():
    decision = decide(make_ctx(category=Category.FEATURE_REQUEST))
    assert decision.disposition is Disposition.ROUTED
    assert decision.suggested_route is None


def test_human_review_keeps_the_would_be_route_as_suggested():
    decision = decide(
        make_ctx(category=Category.FEATURE_REQUEST, risk_signals=[RiskSignal.MENTIONS_CHURN])
    )
    assert decision.route is Route.HUMAN_REVIEW
    assert decision.suggested_route is Route.PRODUCT
    assert decision.human_review is True


def test_needs_information_sets_route_and_keeps_suggested():
    decision = decide(
        make_ctx(text="please close my account", category=Category.ACCOUNT, entities={})
    )
    assert decision.route is Route.NEEDS_INFORMATION
    assert decision.suggested_route is Route.SUPPORT_TIER1
    assert decision.human_review is False


def test_reasons_end_with_exactly_one_ai_observation():
    decision = decide(
        make_ctx(category=Category.SUPPORT, reasoning_summary="Simple how-to question.")
    )
    ai_reasons = [r for r in decision.reasons if r.source == "AI_OBSERVATION"]
    assert len(ai_reasons) == 1
    assert ai_reasons[-1].text == "Simple how-to question."
    assert decision.reasons[-1].source == "AI_OBSERVATION"


def test_reasons_note_unusable_ai_when_interpretation_missing():
    decision = decide(
        make_ctx(validation_status=ValidationStatus.INVALID_SCHEMA, no_interpretation=True)
    )
    assert decision.reasons[-1].source == "AI_OBSERVATION"
    assert "unusable" in decision.reasons[-1].text.lower()


def test_security_terminal_not_downgraded_by_finalizer():
    decision = decide(
        make_ctx(
            text="did we get hacked? is our customer data safe?",
            category=Category.OTHER,
            model_confidence=0.2,
        )
    )
    assert decision.route is Route.SECURITY
    assert decision.disposition is Disposition.ROUTED
    assert decision.urgency is Urgency.CRITICAL
    assert decision.escalation is EscalationLevel.CRITICAL
    assert decision.human_review is False


# --------------------------------------------------------------------------- #
# Locked correction 1 — R07 is authoritative → NEEDS_INFORMATION by itself
# --------------------------------------------------------------------------- #
def test_bug_no_repro_alone_is_needs_information_not_human_review():
    decision = decide(
        make_ctx(
            text="something is broken on your end, please fix",
            category=Category.BUG,
            missing_information=["no steps to reproduce", "no error message", "no environment"],
            model_confidence=0.6,  # above threshold → R10 does not fire
        )
    )
    assert decision.disposition is Disposition.NEEDS_INFORMATION
    assert decision.human_review is False
    assert decision.urgency is Urgency.NORMAL
    assert decision.missing_information  # the ask is preserved


# --------------------------------------------------------------------------- #
# Locked correction 2 — R09 is authoritative → NEEDS_INFORMATION by itself
# --------------------------------------------------------------------------- #
def test_missing_account_id_alone_is_needs_information_not_human_review():
    decision = decide(
        make_ctx(
            text="Please close my account and delete my data. I don't want to be billed next month.",
            category=Category.ACCOUNT,
            entities={},
            model_confidence=0.75,
        )
    )
    assert decision.disposition is Disposition.NEEDS_INFORMATION
    assert decision.human_review is False
    assert decision.urgency is Urgency.NORMAL


# --------------------------------------------------------------------------- #
# Locked correction 3 — HUMAN_REVIEW supersedes NEEDS_INFORMATION; asks preserved
# --------------------------------------------------------------------------- #
def test_human_review_supersedes_needs_information_and_preserves_asks():
    decision = decide(
        make_ctx(
            text="it's broken, fix it",
            category=Category.BUG,
            missing_information=["no steps to reproduce"],
            model_confidence=0.4,  # R10 fires → HUMAN_REVIEW
        )
    )
    assert decision.disposition is Disposition.HUMAN_REVIEW
    assert decision.human_review is True
    assert decision.route is Route.HUMAN_REVIEW
    # NEEDS_INFORMATION ask from R07 is still in the audit record
    assert decision.missing_information
    assert decision.suggested_route is Route.SUPPORT_TIER1
    # never two dispositions at once
    assert decision.disposition is not Disposition.NEEDS_INFORMATION


# --------------------------------------------------------------------------- #
# Locked correction 4 — churn raises urgency only, no automatic escalation
# --------------------------------------------------------------------------- #
def test_churn_raises_urgency_but_does_not_escalate():
    decision = decide(
        make_ctx(
            text="we are cancelling our contract if this keeps up",
            category=Category.SUPPORT,
            risk_signals=[RiskSignal.MENTIONS_CHURN],
        )
    )
    assert decision.urgency is Urgency.HIGH
    assert decision.escalation is EscalationLevel.NONE


def test_feature_plus_churn_is_human_review_without_escalation():
    decision = decide(
        make_ctx(
            text="small idea: remember my filter. also if the SSO bug isn't fixed by Friday we cancel our contract.",
            category=Category.FEATURE_REQUEST,
            risk_signals=[RiskSignal.MENTIONS_CHURN, RiskSignal.DEADLINE_MENTIONED],
            model_confidence=0.55,
        )
    )
    assert decision.disposition is Disposition.HUMAN_REVIEW
    assert decision.suggested_route is Route.PRODUCT
    assert decision.urgency is Urgency.HIGH
    assert decision.escalation is EscalationLevel.NONE


# --------------------------------------------------------------------------- #
# Urgency is fully deterministic — the model's urgency is an observation only
# --------------------------------------------------------------------------- #
def test_model_saying_critical_does_not_set_final_urgency():
    decision = decide(
        make_ctx(
            text="how do I export a report to CSV?",
            category=Category.SUPPORT,
            urgency=Urgency.CRITICAL,  # model's read — ignored for the final number
        )
    )
    assert decision.urgency is Urgency.NORMAL


def test_outage_urgency_is_high_even_when_model_said_critical():
    decision = decide(
        make_ctx(
            text="URGENT the whole dashboard is down, we have a demo in 45 minutes",
            category=Category.OUTAGE,
            urgency=Urgency.CRITICAL,
            risk_signals=[RiskSignal.SERVICE_DOWN, RiskSignal.DEADLINE_MENTIONED],
        )
    )
    assert decision.urgency is Urgency.HIGH  # deterministic floor, not the model's CRITICAL
    assert decision.escalation is EscalationLevel.NONE


def test_feature_request_urgency_is_low():
    decision = decide(make_ctx(category=Category.FEATURE_REQUEST, urgency=Urgency.NORMAL))
    assert decision.urgency is Urgency.LOW
