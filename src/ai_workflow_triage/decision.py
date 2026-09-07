"""Finalizer + the ``decision_confidence`` heuristic.

``finalize`` turns an ``EngineState`` into a ``TriageDecision``:

* terminal R01 → HUMAN_REVIEW (routing itself is unknown);
* terminal R02 → SECURITY / CRITICAL (never downgraded — ADR-0005);
* otherwise resolve the disposition by the locked hierarchy
  ``HUMAN_REVIEW > NEEDS_INFORMATION > ROUTED`` (SPEC §10, correction 3),
  keeping the would-be route as ``suggested_route`` and preserving every
  missing-information ask in ``missing_information``.

Urgency is **fully deterministic**: it starts at NORMAL, R08 asks for LOW, and
risk rules raise a floor (max wins). The model's ``urgency`` field is an
observation and is never adopted as the final number — the one place the rules
read it is R11, to *detect* a "feature request + CRITICAL" conflict.
"""

from __future__ import annotations

from .models import (
    Category,
    Disposition,
    EscalationLevel,
    Reason,
    Route,
    TriageDecision,
    Urgency,
    disposition_rank,
    escalation_rank,
    max_urgency,
)
from .rules.engine import EngineState, run_rules
from .rules.table import (
    R01_AI_UNUSABLE,
    R02_SECURITY,
    R04_OUTAGE,
    R05_BILLING,
    R06_BUG_WITH_REPRO,
    R08_FEATURE,
    R99_DEFAULT_SUPPORT,
    RuleContext,
)


def _ai_observation_reason(ctx: RuleContext, unusable: bool) -> Reason:
    if unusable or ctx.interpretation is None:
        return Reason(
            source="AI_OBSERVATION",
            text="AI interpretation was unusable and was not relied on for the routing decision.",
        )
    return Reason(source="AI_OBSERVATION", text=ctx.interpretation.reasoning_summary)


def _reasons(state: EngineState, ctx: RuleContext, *, unusable: bool) -> list[Reason]:
    return [*state.reasons, _ai_observation_reason(ctx, unusable)]


def _final_urgency(state: EngineState) -> Urgency:
    floor = max_urgency(*state.urgency_floors) if state.urgency_floors else None
    if state.wants_low_urgency and (floor is None or floor == Urgency.LOW):
        return Urgency.LOW
    if floor is None:
        return Urgency.NORMAL
    return max_urgency(Urgency.NORMAL, floor)


def _ai_category_agrees(ctx: RuleContext, route_rule_id: str | None) -> bool:
    if ctx.interpretation is None:
        return False
    if route_rule_id == R04_OUTAGE:
        return ctx.interpretation.category == Category.OUTAGE
    if route_rule_id == R05_BILLING:
        return ctx.interpretation.category == Category.BILLING
    return False


def compute_decision_confidence(
    state: EngineState, ctx: RuleContext, disposition: Disposition
) -> float:
    """The SPEC §13 ladder. Every value is a config heuristic, not a probability."""

    c = ctx.config.confidence
    if state.terminal_rule_id == R01_AI_UNUSABLE:
        return c.ai_unusable
    if state.terminal_rule_id == R02_SECURITY:
        return c.security_terminal
    if disposition == Disposition.HUMAN_REVIEW:
        return c.human_review_gate
    if disposition == Disposition.NEEDS_INFORMATION:
        return c.needs_information_gate

    route_rule_id = state.route_rule_id
    model_confidence = ctx.interpretation.model_confidence if ctx.interpretation else 0.5
    capped = round(min(model_confidence, c.ai_category_only_cap), 4)

    if route_rule_id in (R04_OUTAGE, R05_BILLING):
        return (
            c.keyword_and_ai_agree
            if _ai_category_agrees(ctx, route_rule_id)
            else c.single_rule_corroborated
        )
    if route_rule_id == R06_BUG_WITH_REPRO:
        return c.single_rule_corroborated if "error_code" in ctx.entities else capped
    if route_rule_id == R08_FEATURE:
        return capped
    if route_rule_id in (R99_DEFAULT_SUPPORT, None):
        return c.default_support
    return capped


def finalize(state: EngineState, ctx: RuleContext) -> TriageDecision:
    if state.terminal_rule_id == R01_AI_UNUSABLE:
        return TriageDecision(
            route=Route.HUMAN_REVIEW,
            urgency=Urgency.NORMAL,
            disposition=Disposition.HUMAN_REVIEW,
            escalation=EscalationLevel.NONE,
            human_review=True,
            human_review_reason=state.human_review_reason,
            decision_confidence=ctx.config.confidence.ai_unusable,
            suggested_route=None,
            reasons=_reasons(state, ctx, unusable=True),
            rules_triggered=state.rules_triggered,
            rule_trace=state.rule_trace,
            missing_information=state.missing_info_reasons,
        )

    if state.terminal_rule_id == R02_SECURITY:
        return TriageDecision(
            route=Route.SECURITY,
            urgency=Urgency.CRITICAL,
            disposition=Disposition.ROUTED,
            escalation=EscalationLevel.CRITICAL,
            human_review=False,
            human_review_reason=None,
            decision_confidence=ctx.config.confidence.security_terminal,
            suggested_route=None,
            reasons=_reasons(state, ctx, unusable=False),
            rules_triggered=state.rules_triggered,
            rule_trace=state.rule_trace,
            missing_information=state.missing_info_reasons,
        )

    urgency = _final_urgency(state)
    escalation = state.escalation
    human_review = state.human_review

    if ctx.config.escalation_forces_review and escalation_rank(escalation) >= escalation_rank(
        EscalationLevel.PRIORITY
    ):
        human_review = True

    candidates = list(state.dispositions)
    if human_review:
        candidates.append(Disposition.HUMAN_REVIEW)
    if not candidates:
        candidates.append(Disposition.ROUTED)
    disposition = max(candidates, key=disposition_rank)

    accumulated_route = state.route
    if accumulated_route is None:  # R99 guarantees a route on this path
        raise RuntimeError("no route was set on the accumulation path (R99 should have)")

    if disposition == Disposition.ROUTED:
        route: Route = accumulated_route
        suggested_route: Route | None = None
        human_review = False
    elif disposition == Disposition.NEEDS_INFORMATION:
        route = Route.NEEDS_INFORMATION
        suggested_route = accumulated_route
        human_review = False
    else:  # HUMAN_REVIEW
        route = Route.HUMAN_REVIEW
        suggested_route = accumulated_route
        human_review = True

    confidence = compute_decision_confidence(state, ctx, disposition)
    human_review_reason = state.human_review_reason if human_review else None
    if human_review and human_review_reason is None:
        human_review_reason = "the escalation level requires a human review"

    return TriageDecision(
        route=route,
        urgency=urgency,
        disposition=disposition,
        escalation=escalation,
        human_review=human_review,
        human_review_reason=human_review_reason,
        decision_confidence=confidence,
        suggested_route=suggested_route,
        reasons=_reasons(state, ctx, unusable=False),
        rules_triggered=state.rules_triggered,
        rule_trace=state.rule_trace,
        missing_information=state.missing_info_reasons,
    )


def decide(ctx: RuleContext) -> TriageDecision:
    """Convenience: run the ordered rules, then finalize."""

    return finalize(run_rules(ctx), ctx)
