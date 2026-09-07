"""Schemas and enums for ai-workflow-triage.

Two schemas matter most:

* ``TriageInterpretation`` — the **strict** AI-output schema (``extra="forbid"``).
  It holds only *observations*. The words ``route``, ``escalation`` and
  ``human_review`` are deliberately absent — the model never decides those.
* ``TriageDecision`` — the final, deterministic output of the rule engine.

Everything the model returns is untrusted until it validates against
``TriageInterpretation``. A value that is the wrong type, out of range, an
unknown enum, or an unknown key fails validation, and the pipeline turns that
failure into ``HUMAN_REVIEW`` rather than a guess.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Hard schema-level cap on inbound text. ``config.TriageConfig.max_input_chars``
# mirrors this for the pipeline/API layer; a test pins them equal.
MAX_INPUT_CHARS = 8000
MAX_RAW_RESPONSE_CHARS = 4000
_MAX_ENTITY_VALUE_CHARS = 200
_MAX_MISSING_INFO_CHARS = 120


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Category(StrEnum):
    """The model's category *suggestion*. ``OTHER`` is an explicit "I don't know"."""

    SUPPORT = "SUPPORT"
    BILLING = "BILLING"
    ACCOUNT = "ACCOUNT"
    BUG = "BUG"
    FEATURE_REQUEST = "FEATURE_REQUEST"
    OUTAGE = "OUTAGE"
    SECURITY = "SECURITY"
    IMPLEMENTATION = "IMPLEMENTATION"
    OTHER = "OTHER"


class Urgency(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EscalationLevel(StrEnum):
    NONE = "NONE"
    PRIORITY = "PRIORITY"
    CRITICAL = "CRITICAL"


class RiskSignal(StrEnum):
    MENTIONS_SECURITY = "MENTIONS_SECURITY"
    MENTIONS_DATA_LOSS = "MENTIONS_DATA_LOSS"
    MENTIONS_LEGAL = "MENTIONS_LEGAL"
    MENTIONS_CHURN = "MENTIONS_CHURN"
    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    ACCOUNT_LOCKOUT = "ACCOUNT_LOCKOUT"
    PII_IN_MESSAGE = "PII_IN_MESSAGE"
    ANGRY_TONE = "ANGRY_TONE"
    DEADLINE_MENTIONED = "DEADLINE_MENTIONED"
    SERVICE_DOWN = "SERVICE_DOWN"
    PROMPT_INJECTION_SUSPECTED = "PROMPT_INJECTION_SUSPECTED"


class Route(StrEnum):
    SECURITY = "SECURITY"
    BILLING = "BILLING"
    RELIABILITY = "RELIABILITY"
    ENGINEERING = "ENGINEERING"
    PRODUCT = "PRODUCT"
    SUPPORT_TIER1 = "SUPPORT_TIER1"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Disposition(StrEnum):
    """The operational outcome class. Hierarchy (see ADR-0005 / SPEC §10):

    ``SECURITY`` terminal routing  >  ``HUMAN_REVIEW``  >  ``NEEDS_INFORMATION``
    >  ``ROUTED``.
    """

    ROUTED = "ROUTED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class ValidationStatus(StrEnum):
    OK = "OK"
    UNPARSEABLE = "UNPARSEABLE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROVIDER_ERROR = "PROVIDER_ERROR"


ENTITY_KEY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "account_email",
        "account_id",
        "invoice_number",
        "order_id",
        "amount",
        "error_code",
        "affected_system",
        "url",
        "date_mentioned",
        "plan_name",
        "environment",
    }
)


# --------------------------------------------------------------------------- #
# Ordering helpers (StrEnum members are not ordered)
# --------------------------------------------------------------------------- #
_URGENCY_RANK: dict[Urgency, int] = {
    Urgency.LOW: 0,
    Urgency.NORMAL: 1,
    Urgency.HIGH: 2,
    Urgency.CRITICAL: 3,
}
_ESCALATION_RANK: dict[EscalationLevel, int] = {
    EscalationLevel.NONE: 0,
    EscalationLevel.PRIORITY: 1,
    EscalationLevel.CRITICAL: 2,
}
_DISPOSITION_RANK: dict[Disposition, int] = {
    Disposition.ROUTED: 1,
    Disposition.NEEDS_INFORMATION: 2,
    Disposition.HUMAN_REVIEW: 3,
}


def urgency_rank(value: Urgency) -> int:
    return _URGENCY_RANK[value]


def escalation_rank(value: EscalationLevel) -> int:
    return _ESCALATION_RANK[value]


def disposition_rank(value: Disposition) -> int:
    return _DISPOSITION_RANK[value]


def max_urgency(*values: Urgency) -> Urgency:
    if not values:
        raise ValueError("max_urgency requires at least one value")
    return max(values, key=_URGENCY_RANK.__getitem__)


def max_escalation(*values: EscalationLevel) -> EscalationLevel:
    if not values:
        raise ValueError("max_escalation requires at least one value")
    return max(values, key=_ESCALATION_RANK.__getitem__)


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    channel: str | None = None
    scenario_id: str | None = None

    @field_validator("text")
    @classmethod
    def _trim_and_bound(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("request text must not be empty")
        if len(value) > MAX_INPUT_CHARS:
            raise ValueError(
                f"request text is {len(value)} characters; the limit is {MAX_INPUT_CHARS}"
            )
        return value

    @field_validator("channel", "scenario_id")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


# --------------------------------------------------------------------------- #
# Strict AI output schema
# --------------------------------------------------------------------------- #
class TriageInterpretation(BaseModel):
    """What the model observed. Strict: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    urgency: Urgency
    summary: str = Field(min_length=1, max_length=400)
    customer_intent: str = Field(min_length=1, max_length=200)
    entities: dict[str, str] = Field(default_factory=dict)
    risk_signals: list[RiskSignal] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    model_confidence: float
    reasoning_summary: str = Field(min_length=1, max_length=600)

    @field_validator("model_confidence", mode="before")
    @classmethod
    def _confidence_is_a_number(cls, value: object) -> float:
        # bool is a subclass of int — reject it explicitly. Strings ("0.9",
        # "high") and everything else are rejected too; only real numbers pass.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("model_confidence must be a number between 0.0 and 1.0")
        return float(value)

    @field_validator("model_confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("model_confidence must be between 0.0 and 1.0")
        return value

    @field_validator("entities", mode="before")
    @classmethod
    def _entity_values_are_strings(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("entities must be an object")
        for key, item in value.items():
            if not isinstance(item, str):
                raise ValueError(f"entity {key!r} must be a string value")
        return value

    @field_validator("entities")
    @classmethod
    def _entity_keys_and_lengths(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value) - ENTITY_KEY_ALLOWLIST)
        if unknown:
            raise ValueError(f"unknown entity keys: {', '.join(unknown)}")
        for key, item in value.items():
            if len(item) > _MAX_ENTITY_VALUE_CHARS:
                raise ValueError(f"entity {key!r} exceeds {_MAX_ENTITY_VALUE_CHARS} characters")
        return value

    @field_validator("risk_signals")
    @classmethod
    def _dedupe_preserving_order(cls, value: list[RiskSignal]) -> list[RiskSignal]:
        seen: list[RiskSignal] = []
        for signal in value:
            if signal not in seen:
                seen.append(signal)
        return seen

    @field_validator("missing_information")
    @classmethod
    def _missing_info_lengths(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > _MAX_MISSING_INFO_CHARS:
                raise ValueError(
                    f"each missing_information entry must be <= {_MAX_MISSING_INFO_CHARS} characters"
                )
        return value


class ValidationResult(BaseModel):
    """The outcome of parsing + validating one provider response."""

    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    interpretation: TriageInterpretation | None = None
    raw_response: str | None = None
    error_detail: str | None = None
    attempts: int = 1
    provider_name: str | None = None
    provider_model: str | None = None

    @field_validator("raw_response")
    @classmethod
    def _truncate_raw(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_RAW_RESPONSE_CHARS:
            return value[:MAX_RAW_RESPONSE_CHARS] + "…[truncated]"
        return value


# --------------------------------------------------------------------------- #
# Deterministically re-derived signals
# --------------------------------------------------------------------------- #
class KeywordSignals(BaseModel):
    """Booleans re-derived from the raw text by ``rules.keywords`` — independent
    of the model. ``matched`` records the literal fragments for the audit trail.
    """

    model_config = ConfigDict(extra="forbid")

    security_language: bool = False
    injection_language: bool = False
    billing_language: bool = False
    outage_language: bool = False
    data_loss_language: bool = False
    legal_language: bool = False
    churn_language: bool = False
    deadline_language: bool = False
    matched: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Final decision
# --------------------------------------------------------------------------- #
class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["DETERMINISTIC", "AI_OBSERVATION"]
    text: str
    rule_id: str | None = None


class RuleTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    applied: bool
    effect: str | None = None
    explanation: str


class TriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Route
    urgency: Urgency
    disposition: Disposition
    escalation: EscalationLevel
    human_review: bool
    human_review_reason: str | None = None
    decision_confidence: float
    suggested_route: Route | None = None
    reasons: list[Reason] = Field(default_factory=list)
    rules_triggered: list[str] = Field(default_factory=list)
    rule_trace: list[RuleTraceEntry] = Field(default_factory=list)
    # missing-information asks (from R07 / R09), preserved even when the final
    # disposition is HUMAN_REVIEW (SPEC §10, locked correction 3).
    missing_information: list[str] = Field(default_factory=list)
