"""Shared factories for the offline deterministic-core tests.

No network, no API key, no provider. Everything here builds a ``RuleContext``
by hand so each rule / the engine / the finalizer can be exercised in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_workflow_triage.config import DEFAULT_CONFIG, TriageConfig
from ai_workflow_triage.models import (
    Category,
    KeywordSignals,
    TriageInterpretation,
    Urgency,
    ValidationStatus,
)
from ai_workflow_triage.rules.keywords import extract_signals
from ai_workflow_triage.rules.table import RuleContext

_INTERP_DEFAULTS: dict[str, Any] = {
    "category": Category.SUPPORT,
    "urgency": Urgency.NORMAL,
    "summary": "A customer sent a message.",
    "customer_intent": "get help with something",
    "entities": {},
    "risk_signals": [],
    "missing_information": [],
    "model_confidence": 0.9,
    "reasoning_summary": "Routine request; nothing unusual.",
}


def make_interpretation(**overrides: Any) -> TriageInterpretation:
    data = {**_INTERP_DEFAULTS, **overrides}
    return TriageInterpretation(**data)


def make_ctx(
    *,
    text: str = "Hi, I have a question about the product.",
    validation_status: ValidationStatus = ValidationStatus.OK,
    keyword_signals: KeywordSignals | dict[str, Any] | None = None,
    config: TriageConfig = DEFAULT_CONFIG,
    no_interpretation: bool = False,
    **interp_overrides: Any,
) -> RuleContext:
    interpretation = None if no_interpretation else make_interpretation(**interp_overrides)
    if keyword_signals is None:
        signals = extract_signals(text)
    elif isinstance(keyword_signals, dict):
        signals = KeywordSignals(**keyword_signals)
    else:
        signals = keyword_signals
    return RuleContext(
        text=text,
        interpretation=interpretation,
        validation_status=validation_status,
        keyword_signals=signals,
        config=config,
    )


@pytest.fixture
def ctx_factory():
    return make_ctx


@pytest.fixture
def interp_factory():
    return make_interpretation
