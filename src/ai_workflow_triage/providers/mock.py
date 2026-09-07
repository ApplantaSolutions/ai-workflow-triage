"""Deterministic offline provider.

* With a known ``scenario_id`` → returns that scenario's exact ``mock_response``
  string verbatim (or raises ``ProviderError`` for a ``provider_error`` scenario).
  Parsing / validation happens downstream — the mock never "fixes" its output.
* With no ``scenario_id`` (free text) → returns a fixed generic interpretation
  (``category=OTHER``, ``model_confidence=0.5``). This is deliberately weak: the
  pipeline then, correctly, tends to route free text to HUMAN_REVIEW.
* Same input + same construction → identical result. No network, ever.

The corrective retry returns the **same** response again, unless the scenario
defines a ``corrected_response`` (none of the built-in 15 do), so the malformed
and invalid-schema scenarios stay failed after the retry — which is the point.
"""

from __future__ import annotations

import json

from ..scenarios import ScenarioSpec, get_scenario
from .base import ProviderError, ProviderRequest, ProviderResult

_GENERIC_INTERPRETATION: dict[str, object] = {
    "category": "OTHER",
    "urgency": "NORMAL",
    "summary": "Free-text message received; the offline mock does not analyse arbitrary text.",
    "customer_intent": "unable to determine from the message",
    "entities": {},
    "risk_signals": [],
    "missing_information": [
        "the offline mock provider does not classify free text; pass a scenario_id for a realistic interpretation"
    ],
    "model_confidence": 0.5,
    "reasoning_summary": (
        "Offline mock: no scenario selected, returning a low-confidence generic interpretation."
    ),
}

# Serialised once at import so every call returns a byte-identical string.
_GENERIC_RESPONSE = json.dumps(_GENERIC_INTERPRETATION, sort_keys=True)


class MockProvider:
    name = "mock"

    def __init__(self, *, scenario: ScenarioSpec | None = None) -> None:
        # An optional pinned scenario, for callers that want one MockProvider
        # bound to one scenario regardless of the request.
        self._pinned = scenario

    def classify(self, request: ProviderRequest) -> ProviderResult:
        spec = self._resolve(request)
        attempt = 2 if request.correction else 1

        if spec is None:
            return ProviderResult(
                text=_GENERIC_RESPONSE, provider_name=self.name, model=None, attempt=attempt
            )

        if spec.provider_error:
            raise ProviderError(spec.error_message or "simulated provider error")

        assert spec.mock_response is not None  # guaranteed by scenarios._to_spec
        return ProviderResult(
            text=spec.mock_response, provider_name=self.name, model=None, attempt=attempt
        )

    def _resolve(self, request: ProviderRequest) -> ScenarioSpec | None:
        if self._pinned is not None:
            return self._pinned
        if request.scenario_id is None:
            return None
        return get_scenario(request.scenario_id)
