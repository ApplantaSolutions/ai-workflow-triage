"""Ordered evaluation of the rule table.

``run_rules`` walks ``RULES`` in order, accumulating non-terminal outcomes and
short-circuiting on the first terminal one, then applies R99 (default route).
It returns an ``EngineState`` — the raw accumulator. Turning that into a
``TriageDecision`` is ``decision.finalize``'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import (
    Disposition,
    EscalationLevel,
    Reason,
    Route,
    RuleTraceEntry,
    Urgency,
    max_escalation,
)
from .table import (
    R99_DEFAULT_SUPPORT,
    RULES,
    RuleContext,
    RuleOutcome,
    default_support_rule,
)


@dataclass
class EngineState:
    route: Route | None = None
    route_rule_id: str | None = None
    urgency_floors: list[Urgency] = field(default_factory=list)
    wants_low_urgency: bool = False
    escalation: EscalationLevel = EscalationLevel.NONE
    human_review: bool = False
    human_review_reason: str | None = None
    human_review_reasons: list[str] = field(default_factory=list)
    dispositions: list[Disposition] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)
    rules_triggered: list[str] = field(default_factory=list)
    rule_trace: list[RuleTraceEntry] = field(default_factory=list)
    terminal_rule_id: str | None = None
    missing_info_reasons: list[str] = field(default_factory=list)


def _effect_summary(outcome: RuleOutcome) -> str:
    parts: list[str] = []
    if outcome.route is not None:
        parts.append(f"route={outcome.route.value}")
    if outcome.urgency_floor is not None:
        parts.append(f"urgency_floor={outcome.urgency_floor.value}")
    if outcome.wants_low_urgency:
        parts.append("wants_low_urgency")
    if outcome.escalation is not None:
        parts.append(f"escalation={outcome.escalation.value}")
    if outcome.human_review:
        parts.append("human_review")
    if outcome.disposition is not None:
        parts.append(f"disposition={outcome.disposition.value}")
    if outcome.terminal:
        parts.append("terminal")
    return ", ".join(parts) if parts else "reason recorded"


def _apply(state: EngineState, outcome: RuleOutcome) -> None:
    state.rules_triggered.append(outcome.rule_id)

    if outcome.route is not None and state.route is None:
        state.route = outcome.route
        state.route_rule_id = outcome.rule_id
    if outcome.urgency_floor is not None:
        state.urgency_floors.append(outcome.urgency_floor)
    if outcome.wants_low_urgency:
        state.wants_low_urgency = True
    if outcome.escalation is not None:
        state.escalation = max_escalation(state.escalation, outcome.escalation)
    if outcome.human_review:
        state.human_review = True
        if outcome.human_review_reason:
            state.human_review_reasons.append(outcome.human_review_reason)
            if state.human_review_reason is None:
                state.human_review_reason = outcome.human_review_reason
    if outcome.disposition is not None:
        state.dispositions.append(outcome.disposition)
    if outcome.reason_text:
        state.reasons.append(
            Reason(source="DETERMINISTIC", rule_id=outcome.rule_id, text=outcome.reason_text)
        )
    if outcome.missing_info:
        state.missing_info_reasons.append(outcome.missing_info)


def run_rules(ctx: RuleContext) -> EngineState:
    state = EngineState()

    for rule in RULES:
        outcome = rule.evaluate(ctx)
        if outcome is None:
            state.rule_trace.append(
                RuleTraceEntry(
                    rule_id=rule.rule_id, applied=False, effect=None, explanation="did not apply"
                )
            )
            continue
        outcome.rule_id = rule.rule_id
        state.rule_trace.append(
            RuleTraceEntry(
                rule_id=rule.rule_id,
                applied=True,
                effect=_effect_summary(outcome),
                explanation=outcome.explanation,
            )
        )
        _apply(state, outcome)
        if outcome.terminal:
            state.terminal_rule_id = rule.rule_id
            break

    default_outcome = default_support_rule(
        route_already_set=state.route is not None,
        terminal_fired=state.terminal_rule_id is not None,
    )
    if default_outcome is not None:
        default_outcome.rule_id = R99_DEFAULT_SUPPORT
        state.rule_trace.append(
            RuleTraceEntry(
                rule_id=R99_DEFAULT_SUPPORT,
                applied=True,
                effect=_effect_summary(default_outcome),
                explanation=default_outcome.explanation,
            )
        )
        _apply(state, default_outcome)
    else:
        state.rule_trace.append(
            RuleTraceEntry(
                rule_id=R99_DEFAULT_SUPPORT,
                applied=False,
                effect=None,
                explanation="a route was already set, or a terminal rule fired",
            )
        )

    return state
