"""The ordered rule table (R01..R14) plus R99.

Each rule is a small pure function ``_rNN(ctx) -> RuleOutcome | None`` paired
with a stable id in ``RULES`` (ADR-0002). Precedence is exactly **list order +
the terminal flag**, both visible in this one file:

* a rule returning ``None`` did not apply;
* a non-terminal outcome accumulates (first routing rule wins the route;
  urgency floor takes the max; ``human_review`` is sticky; reasons append);
* a terminal outcome (R01, R02) stops evaluation immediately.

Cross-rule conditions ("no terminal rule fired", "R06 did not fire") are
expressed through the shared predicate helpers, so the condition has a single
source of truth even when two rules read it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..config import TriageConfig
from ..models import (
    Category,
    Disposition,
    EscalationLevel,
    KeywordSignals,
    RiskSignal,
    Route,
    TriageInterpretation,
    Urgency,
    ValidationStatus,
)

# --------------------------------------------------------------------------- #
# Rule ids
# --------------------------------------------------------------------------- #
R01_AI_UNUSABLE = "R01_AI_UNUSABLE"
R02_SECURITY = "R02_SECURITY"
R03_DATA_LOSS_OR_LEGAL = "R03_DATA_LOSS_OR_LEGAL"
R04_OUTAGE = "R04_OUTAGE"
R05_BILLING = "R05_BILLING"
R06_BUG_WITH_REPRO = "R06_BUG_WITH_REPRO"
R07_BUG_NO_REPRO = "R07_BUG_NO_REPRO"
R08_FEATURE = "R08_FEATURE"
R09_MISSING_ACCOUNT_ID = "R09_MISSING_ACCOUNT_ID"
R10_LOW_MODEL_CONFIDENCE = "R10_LOW_MODEL_CONFIDENCE"
R11_CONFLICTING_SIGNALS = "R11_CONFLICTING_SIGNALS"
R12_UNKNOWN_CATEGORY = "R12_UNKNOWN_CATEGORY"
R13_ANGRY_OR_CHURN = "R13_ANGRY_OR_CHURN"
R14_DEADLINE = "R14_DEADLINE"
R99_DEFAULT_SUPPORT = "R99_DEFAULT_SUPPORT"


# --------------------------------------------------------------------------- #
# Context + outcome
# --------------------------------------------------------------------------- #
@dataclass
class RuleContext:
    text: str
    interpretation: TriageInterpretation | None
    validation_status: ValidationStatus
    keyword_signals: KeywordSignals
    config: TriageConfig

    @property
    def category(self) -> Category | None:
        return self.interpretation.category if self.interpretation else None

    @property
    def risk_signals(self) -> set[RiskSignal]:
        return set(self.interpretation.risk_signals) if self.interpretation else set()

    @property
    def entities(self) -> dict[str, str]:
        return dict(self.interpretation.entities) if self.interpretation else {}


@dataclass
class RuleOutcome:
    explanation: str
    terminal: bool = False
    route: Route | None = None
    urgency_floor: Urgency | None = None
    wants_low_urgency: bool = False
    escalation: EscalationLevel | None = None
    human_review: bool = False
    human_review_reason: str | None = None
    disposition: Disposition | None = None
    reason_text: str | None = None
    missing_info: str | None = None
    rule_id: str = ""  # stamped by the engine from the RULES table


# --------------------------------------------------------------------------- #
# Shared predicates (single source of truth for each condition)
# --------------------------------------------------------------------------- #
_REPRO_HINTS: tuple[str, ...] = (
    "reprodu",
    "steps to",
    "how to trigger",
    "cannot reproduce",
    "no repro",
    "repro step",
)
_ACCOUNT_ID_KEYS: tuple[str, ...] = ("account_email", "account_id", "invoice_number")
_SECURITY_RISK: frozenset[RiskSignal] = frozenset(
    {
        RiskSignal.MENTIONS_SECURITY,
        RiskSignal.ACCOUNT_LOCKOUT,
        RiskSignal.PII_IN_MESSAGE,
        RiskSignal.PROMPT_INJECTION_SUSPECTED,
    }
)


def is_ai_unusable(ctx: RuleContext) -> bool:
    return ctx.validation_status != ValidationStatus.OK


def is_security_signal(ctx: RuleContext) -> bool:
    kw = ctx.keyword_signals
    if kw.security_language or kw.injection_language:
        return True
    if ctx.interpretation is None:
        return False
    if ctx.category == Category.SECURITY:
        return True
    return bool(_SECURITY_RISK & ctx.risk_signals)


def is_data_loss_or_legal(ctx: RuleContext) -> bool:
    kw = ctx.keyword_signals
    if kw.data_loss_language or kw.legal_language:
        return True
    return bool({RiskSignal.MENTIONS_DATA_LOSS, RiskSignal.MENTIONS_LEGAL} & ctx.risk_signals)


def routes_to_outage(ctx: RuleContext) -> bool:
    if ctx.interpretation is None:
        return False
    if ctx.category == Category.OUTAGE:
        return True
    if RiskSignal.SERVICE_DOWN in ctx.risk_signals:
        return True
    return ctx.keyword_signals.outage_language and bool(ctx.entities.get("affected_system"))


def routes_to_billing(ctx: RuleContext) -> bool:
    if ctx.interpretation is not None and ctx.category == Category.BILLING:
        return True
    if RiskSignal.DUPLICATE_CHARGE in ctx.risk_signals:
        return True
    return ctx.keyword_signals.billing_language


def repro_missing(ctx: RuleContext) -> bool:
    if ctx.interpretation is None or ctx.category != Category.BUG:
        return False
    return any(
        hint in item.lower()
        for item in ctx.interpretation.missing_information
        for hint in _REPRO_HINTS
    )


def has_error_code(ctx: RuleContext) -> bool:
    return "error_code" in ctx.entities


def has_account_identifier(ctx: RuleContext) -> bool:
    return any(key in ctx.entities for key in _ACCOUNT_ID_KEYS)


def low_model_confidence(ctx: RuleContext) -> bool:
    interp = ctx.interpretation
    return interp is not None and interp.model_confidence < ctx.config.min_confidence


def churn_signal(ctx: RuleContext) -> bool:
    return ctx.keyword_signals.churn_language or RiskSignal.MENTIONS_CHURN in ctx.risk_signals


def deadline_signal(ctx: RuleContext) -> bool:
    return (
        ctx.keyword_signals.deadline_language or RiskSignal.DEADLINE_MENTIONED in ctx.risk_signals
    )


def conflict_description(ctx: RuleContext) -> str | None:
    """Narrow, explicit conflict families (SPEC §9.1 R11, locked correction 5).

    The security-language family is recognised here, but a message that matches
    it also trips ``is_security_signal`` → R02 fires first (terminal) and R11 is
    never reached. That is intentional: SECURITY terminal routing is the
    stronger response than a generic HUMAN_REVIEW.
    """

    interp = ctx.interpretation
    if interp is None:
        return None
    cat = interp.category
    if cat == Category.FEATURE_REQUEST and interp.urgency == Urgency.CRITICAL:
        return "feature request classified with CRITICAL urgency"
    if cat == Category.FEATURE_REQUEST and churn_signal(ctx):
        return "feature request combined with a churn / cancellation signal"
    if (
        cat in (Category.SUPPORT, Category.FEATURE_REQUEST)
        and ctx.keyword_signals.security_language
    ):
        return "a support / feature-request message contains security language"
    if ctx.keyword_signals.billing_language and ctx.keyword_signals.outage_language:
        return "the message matches two different routing families (billing and outage)"
    return None


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def _r01_ai_unusable(ctx: RuleContext) -> RuleOutcome | None:
    if not is_ai_unusable(ctx):
        return None
    status = ctx.validation_status.value
    return RuleOutcome(
        explanation=f"AI output was not usable ({status})",
        terminal=True,
        disposition=Disposition.HUMAN_REVIEW,
        human_review=True,
        human_review_reason=f"AI interpretation unusable ({status})",
        reason_text=(
            f"the AI response was {status.lower().replace('_', ' ')}; "
            "routed to a human instead of guessing a route"
        ),
    )


def _r02_security(ctx: RuleContext) -> RuleOutcome | None:
    if not is_security_signal(ctx):
        return None
    return RuleOutcome(
        explanation="security or prompt-injection signal detected",
        terminal=True,
        route=Route.SECURITY,
        urgency_floor=Urgency.CRITICAL,
        escalation=EscalationLevel.CRITICAL,
        disposition=Disposition.ROUTED,
        reason_text=(
            "security or prompt-injection language detected; routed to SECURITY at "
            "CRITICAL regardless of the model's suggested route"
        ),
    )


def _r03_data_loss_or_legal(ctx: RuleContext) -> RuleOutcome | None:
    if not is_data_loss_or_legal(ctx):
        return None
    return RuleOutcome(
        explanation="data-loss or legal exposure mentioned",
        escalation=EscalationLevel.PRIORITY,
        urgency_floor=Urgency.HIGH,
        human_review=True,
        human_review_reason="data-loss or legal exposure mentioned — a human must review",
        reason_text="possible data loss or legal exposure; escalated to PRIORITY and flagged for human review",
    )


def _r04_outage(ctx: RuleContext) -> RuleOutcome | None:
    if not routes_to_outage(ctx):
        return None
    return RuleOutcome(
        explanation="outage indicators present",
        route=Route.RELIABILITY,
        urgency_floor=Urgency.HIGH,
        disposition=Disposition.ROUTED,
        reason_text="outage indicators (category, service-down signal, or outage language + an affected system); routed to RELIABILITY",
    )


def _r05_billing(ctx: RuleContext) -> RuleOutcome | None:
    if not routes_to_billing(ctx):
        return None
    return RuleOutcome(
        explanation="billing indicators present",
        route=Route.BILLING,
        disposition=Disposition.ROUTED,
        reason_text="billing indicators (category, duplicate-charge signal, or billing language); routed to BILLING",
    )


def _r06_bug_with_repro(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.interpretation is None or ctx.category != Category.BUG:
        return None
    if not (has_error_code(ctx) or not repro_missing(ctx)):
        return None
    return RuleOutcome(
        explanation="bug report with an error code or reproduction detail",
        route=Route.ENGINEERING,
        disposition=Disposition.ROUTED,
        reason_text="bug report includes an error code or reproduction steps; routed to ENGINEERING",
    )


def _r07_bug_no_repro(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.interpretation is None or ctx.category != Category.BUG:
        return None
    if not repro_missing(ctx) or has_error_code(ctx):
        return None
    return RuleOutcome(
        explanation="bug report with no reproduction information",
        disposition=Disposition.NEEDS_INFORMATION,
        reason_text="bug report has no reproduction steps and no error code; need reproduction detail before routing to engineering",
        missing_info="reproduction steps or an error code",
    )


def _r08_feature(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.interpretation is None or ctx.category != Category.FEATURE_REQUEST:
        return None
    return RuleOutcome(
        explanation="feature request",
        route=Route.PRODUCT,
        wants_low_urgency=True,
        disposition=Disposition.ROUTED,
        reason_text="classified as a feature request; routed to PRODUCT at low urgency",
    )


def _r09_missing_account_id(ctx: RuleContext) -> RuleOutcome | None:
    if is_ai_unusable(ctx) or is_security_signal(ctx):
        return None
    if ctx.interpretation is None:
        return None
    if ctx.category not in (Category.BILLING, Category.ACCOUNT, Category.IMPLEMENTATION):
        return None
    if has_account_identifier(ctx):
        return None
    return RuleOutcome(
        explanation="billing/account/implementation request with no account identifier",
        disposition=Disposition.NEEDS_INFORMATION,
        reason_text="no account identifier (email, account ID, or invoice number) provided; need one before this can be actioned",
        missing_info="an account email, account ID, or invoice number",
    )


def _r10_low_model_confidence(ctx: RuleContext) -> RuleOutcome | None:
    if is_ai_unusable(ctx) or is_security_signal(ctx):
        return None
    if not low_model_confidence(ctx):
        return None
    assert ctx.interpretation is not None  # low_model_confidence guarantees it
    mc = ctx.interpretation.model_confidence
    threshold = ctx.config.min_confidence
    return RuleOutcome(
        explanation=f"model confidence {mc:.2f} is below the {threshold:.2f} threshold",
        disposition=Disposition.HUMAN_REVIEW,
        human_review=True,
        human_review_reason=f"model self-reported confidence {mc:.2f} is below the {threshold:.2f} threshold",
        reason_text=f"the model's self-reported confidence ({mc:.2f}) is below the {threshold:.2f} threshold; sent to a human",
    )


def _r11_conflicting_signals(ctx: RuleContext) -> RuleOutcome | None:
    if is_ai_unusable(ctx) or is_security_signal(ctx):
        return None
    description = conflict_description(ctx)
    if description is None:
        return None
    return RuleOutcome(
        explanation=f"conflicting signals: {description}",
        disposition=Disposition.HUMAN_REVIEW,
        human_review=True,
        human_review_reason=f"conflicting signals — {description}",
        reason_text=f"conflicting signals ({description}); sent to a human to disambiguate",
    )


def _r12_unknown_category(ctx: RuleContext) -> RuleOutcome | None:
    if is_ai_unusable(ctx) or is_security_signal(ctx):
        return None
    if ctx.interpretation is None or ctx.category != Category.OTHER:
        return None
    if routes_to_outage(ctx) or routes_to_billing(ctx):
        return None
    return RuleOutcome(
        explanation="unknown category (OTHER) with no deterministic route",
        disposition=Disposition.HUMAN_REVIEW,
        human_review=True,
        human_review_reason="the model could not categorise the request and no deterministic route applied",
        reason_text="the model returned category OTHER and no deterministic rule produced a route; sent to a human",
    )


def _r13_angry_or_churn(ctx: RuleContext) -> RuleOutcome | None:
    angry = RiskSignal.ANGRY_TONE in ctx.risk_signals
    if not (angry or churn_signal(ctx)):
        return None
    return RuleOutcome(
        explanation="angry tone or a churn / cancellation signal",
        urgency_floor=Urgency.HIGH,
        reason_text="angry tone or a churn / cancellation signal; urgency raised to at least HIGH (no automatic escalation)",
    )


def _r14_deadline(ctx: RuleContext) -> RuleOutcome | None:
    if not deadline_signal(ctx):
        return None
    return RuleOutcome(
        explanation="a deadline was mentioned",
        urgency_floor=Urgency.HIGH,
        reason_text="a deadline was mentioned; urgency raised to at least HIGH",
    )


def default_support_rule(*, route_already_set: bool, terminal_fired: bool) -> RuleOutcome | None:
    """R99 — the engine applies this last if no rule set a route."""

    if route_already_set or terminal_fired:
        return None
    return RuleOutcome(
        explanation="no routing rule applied",
        route=Route.SUPPORT_TIER1,
        disposition=Disposition.ROUTED,
        reason_text="no specific routing rule applied; defaulting to tier-1 support",
    )


# --------------------------------------------------------------------------- #
# The ordered table
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rule:
    rule_id: str
    evaluate: Callable[[RuleContext], RuleOutcome | None]


RULES: list[Rule] = [
    Rule(R01_AI_UNUSABLE, _r01_ai_unusable),
    Rule(R02_SECURITY, _r02_security),
    Rule(R03_DATA_LOSS_OR_LEGAL, _r03_data_loss_or_legal),
    Rule(R04_OUTAGE, _r04_outage),
    Rule(R05_BILLING, _r05_billing),
    Rule(R06_BUG_WITH_REPRO, _r06_bug_with_repro),
    Rule(R07_BUG_NO_REPRO, _r07_bug_no_repro),
    Rule(R08_FEATURE, _r08_feature),
    Rule(R09_MISSING_ACCOUNT_ID, _r09_missing_account_id),
    Rule(R10_LOW_MODEL_CONFIDENCE, _r10_low_model_confidence),
    Rule(R11_CONFLICTING_SIGNALS, _r11_conflicting_signals),
    Rule(R12_UNKNOWN_CATEGORY, _r12_unknown_category),
    Rule(R13_ANGRY_OR_CHURN, _r13_angry_or_churn),
    Rule(R14_DEADLINE, _r14_deadline),
]

RULE_IDS: list[str] = [rule.rule_id for rule in RULES] + [R99_DEFAULT_SUPPORT]
