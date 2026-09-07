"""MockProvider + the scenario loader — TEST-PLAN §1.7 + CP3 §2."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_workflow_triage.errors import ConfigError, ProviderError
from ai_workflow_triage.models import (
    Category,
    Disposition,
    EscalationLevel,
    Route,
    Urgency,
    ValidationStatus,
)
from ai_workflow_triage.providers import MockProvider, ProviderRequest
from ai_workflow_triage.scenarios import get_scenario, load_scenarios, scenario_ids
from ai_workflow_triage.validation import parse_interpretation

_SYS = "system"
_USR = "user"


def _req(scenario_id: str | None = None, *, correction: bool = False) -> ProviderRequest:
    return ProviderRequest(system=_SYS, user=_USR, scenario_id=scenario_id, correction=correction)


# --------------------------------------------------------------------------- #
# Scenario data integrity
# --------------------------------------------------------------------------- #
def test_fifteen_scenarios_load():
    specs = load_scenarios()
    assert len(specs) == 15
    assert "security-prompt-injection" in specs


def test_expected_blocks_use_valid_enum_values():
    for spec in load_scenarios().values():
        Route(spec.expected.route)
        Urgency(spec.expected.urgency)
        Disposition(spec.expected.disposition)
        EscalationLevel(spec.expected.escalation)
        assert isinstance(spec.expected.human_review, bool)


def test_non_error_scenarios_have_a_string_mock_response():
    for spec in load_scenarios().values():
        if spec.provider_error:
            assert spec.mock_response is None
            assert spec.error_message
        else:
            assert isinstance(spec.mock_response, str)


def test_scenario_input_texts_are_synthetic():
    joined = " ".join(s.input_text for s in load_scenarios().values()).lower()
    assert "meridiantools-example.com" in joined
    for real_looking in ("gmail.com", "applanta", "shiftrights"):
        assert real_looking not in joined


# --------------------------------------------------------------------------- #
# MockProvider behaviour
# --------------------------------------------------------------------------- #
def test_known_scenario_returns_exact_mock_response_string():
    spec = get_scenario("billing-double-charge")
    result = MockProvider().classify(_req("billing-double-charge"))
    assert result.text == spec.mock_response
    assert result.provider_name == "mock"
    assert result.model is None


def test_same_scenario_is_deterministic():
    a = MockProvider().classify(_req("bug-with-repro"))
    b = MockProvider().classify(_req("bug-with-repro"))
    assert a == b


def test_provider_error_scenario_raises_provider_error():
    with pytest.raises(ProviderError, match="simulated upstream timeout"):
        MockProvider().classify(_req("provider-error"))


def test_malformed_scenario_is_returned_verbatim_not_repaired():
    result = MockProvider().classify(_req("malformed-ai-output"))
    assert result.text == get_scenario("malformed-ai-output").mock_response
    # parsing (downstream) is what rejects it
    _, status, _ = parse_interpretation(result.text)
    assert status is ValidationStatus.UNPARSEABLE


def test_free_text_returns_a_deterministic_generic_interpretation():
    a = MockProvider().classify(_req(None))
    b = MockProvider().classify(_req(None))
    assert a.text == b.text
    interp, status, _ = parse_interpretation(a.text)
    assert status is ValidationStatus.OK
    assert interp is not None
    assert interp.category is Category.OTHER
    assert interp.model_confidence == 0.5


def test_unknown_scenario_id_raises_config_error():
    with pytest.raises(ConfigError, match="unknown scenario_id"):
        MockProvider().classify(_req("does-not-exist"))


def test_corrective_retry_returns_the_same_response_by_default():
    first = MockProvider().classify(_req("invalid-schema-ai-output"))
    retry = MockProvider().classify(_req("invalid-schema-ai-output", correction=True))
    assert retry.text == first.text
    assert retry.attempt == 2


def test_pinned_scenario_ignores_request_scenario_id():
    provider = MockProvider(scenario=get_scenario("bug-with-repro"))
    result = provider.classify(_req("billing-double-charge"))
    assert result.text == get_scenario("bug-with-repro").mock_response


def test_scenario_ids_helper():
    assert set(scenario_ids()) == set(load_scenarios())


def test_mock_module_imports_no_network_library():
    import ai_workflow_triage.providers.mock as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for banned in (
        "import requests",
        "import httpx",
        "import urllib",
        "import socket",
        "import anthropic",
    ):
        assert banned not in source
