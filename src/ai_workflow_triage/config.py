"""Tunable thresholds and heuristic values.

Every value lives here so a reviewer can see, in one place, every number that
shifts an outcome. Nothing is hard-coded in the rules or the finalizer.

``ConfidenceHeuristic`` values are **operational heuristics, not calibrated
probabilities.** ``decision_confidence`` answers "how sure is the pipeline that
this *disposition* is right?" — it is documented as a heuristic everywhere it
appears (SPEC §13), the same honesty stance as rubric-eval-harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfidenceHeuristic:
    """The fixed points of the ``decision_confidence`` ladder (SPEC §13)."""

    ai_unusable: float = 0.98
    """Terminal R01 → HUMAN_REVIEW: very sure it needs a human; routing unknown."""

    security_terminal: float = 0.95
    """Terminal R02: a deterministic hard signal fired."""

    keyword_and_ai_agree: float = 0.90
    """A deterministic keyword rule and the model's category agree."""

    single_rule_corroborated: float = 0.80
    """One routing rule fired, corroborated by an entity or a signal."""

    ai_category_only_cap: float = 0.75
    """Route driven only by the model's category → ``min(model_confidence, this)``."""

    needs_information_gate: float = 0.75
    """NEEDS_INFORMATION (R07 / R09): confident it needs info, not of a route."""

    human_review_gate: float = 0.70
    """HUMAN_REVIEW via low confidence / conflict / unknown / data-loss."""

    default_support: float = 0.50
    """R99 default-to-tier-1-support: no rule had an opinion."""


@dataclass(frozen=True)
class TriageConfig:
    min_confidence: float = 0.55
    """R10 fires when ``model_confidence`` is strictly below this."""

    escalation_forces_review: bool = True
    """Any ``escalation >= PRIORITY`` also forces ``human_review`` (SPEC §11).

    Currently only reachable via R03, which already sets ``human_review``; kept
    for future escalation rules.
    """

    max_input_chars: int = 8000
    """Pipeline/API-layer cap; mirrors ``models.MAX_INPUT_CHARS``."""

    confidence: ConfidenceHeuristic = field(default_factory=ConfidenceHeuristic)


DEFAULT_CONFIG = TriageConfig()
