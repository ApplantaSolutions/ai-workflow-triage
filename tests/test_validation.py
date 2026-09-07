"""Strict parsing + the one-corrective-retry boundary — TEST-PLAN §1.2 + CP3 §4-5."""

from __future__ import annotations

import re

import pytest

from ai_workflow_triage.errors import ProviderError
from ai_workflow_triage.models import (
    MAX_RAW_RESPONSE_CHARS,
    Category,
    RiskSignal,
    TriageRequest,
    Urgency,
    ValidationStatus,
)
from ai_workflow_triage.providers import MockProvider
from ai_workflow_triage.providers.base import ProviderRequest, ProviderResult
from ai_workflow_triage.validation import interpret, parse_interpretation

_GOOD = {
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


def _json(overrides: dict | None = None) -> str:
    import json

    payload = {**_GOOD, **(overrides or {})}
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Happy-path parsing
# --------------------------------------------------------------------------- #
def test_clean_json_object_ok():
    interp, status, detail = parse_interpretation(_json())
    assert status is ValidationStatus.OK
    assert detail is None
    assert interp is not None and interp.category is Category.BILLING


def test_json_in_fences_ok():
    raw = f"```json\n{_json()}\n```"
    _, status, _ = parse_interpretation(raw)
    assert status is ValidationStatus.OK


def test_json_with_surrounding_prose_ok():
    raw = f"Sure — here is the classification:\n\n{_json()}\n\nLet me know if you need more."
    _, status, _ = parse_interpretation(raw)
    assert status is ValidationStatus.OK


def test_nested_object_and_string_braces_do_not_confuse_the_scanner():
    raw = _json(
        {"summary": "the {widget} broke when {clicked}", "entities": {"error_code": "E{9}"}}
    )
    _, status, _ = parse_interpretation(raw)
    assert status is ValidationStatus.OK


@pytest.mark.parametrize("value", [0.9, 1, 0, 0.0, 1.0])
def test_model_confidence_numeric_values_ok(value):
    _, status, _ = parse_interpretation(_json({"model_confidence": value}))
    assert status is ValidationStatus.OK


# --------------------------------------------------------------------------- #
# Rejections
# --------------------------------------------------------------------------- #
def test_not_json_is_unparseable():
    _, status, detail = parse_interpretation("I think this is a billing problem, honestly.")
    assert status is ValidationStatus.UNPARSEABLE
    assert detail


def test_empty_response_is_unparseable():
    _, status, _ = parse_interpretation("   \n  ")
    assert status is ValidationStatus.UNPARSEABLE


def test_truncated_json_is_unparseable():
    _, status, _ = parse_interpretation('some text {"category": "BILL')
    assert status is ValidationStatus.UNPARSEABLE


def test_json_array_is_invalid_schema():
    _, status, detail = parse_interpretation('[{"category": "BILLING"}]')
    assert status is ValidationStatus.INVALID_SCHEMA
    assert "array" in detail


def test_json_scalar_is_invalid_schema():
    _, status, _ = parse_interpretation("42")
    assert status is ValidationStatus.INVALID_SCHEMA


def test_multiple_objects_is_unparseable():
    raw = f"{_json()}\n\nand an alternative:\n\n{_json({'category': 'SUPPORT'})}"
    _, status, detail = parse_interpretation(raw)
    assert status is ValidationStatus.UNPARSEABLE
    assert "2 JSON objects" in detail


def test_missing_required_field_names_it():
    payload = {k: v for k, v in _GOOD.items() if k != "category"}
    import json

    _, status, detail = parse_interpretation(json.dumps(payload))
    assert status is ValidationStatus.INVALID_SCHEMA
    assert "category" in detail


def test_unknown_top_level_key_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"route": "BILLING"}))
    assert status is ValidationStatus.INVALID_SCHEMA


@pytest.mark.parametrize("field", ["category", "urgency"])
def test_bad_enum_is_invalid_schema(field):
    _, status, detail = parse_interpretation(_json({field: "WAT"}))
    assert status is ValidationStatus.INVALID_SCHEMA
    assert field in detail


@pytest.mark.parametrize("value", [1.7, -0.2, "high", True])
def test_bad_model_confidence_is_invalid_schema(value):
    _, status, _ = parse_interpretation(_json({"model_confidence": value}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_unknown_entity_key_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"entities": {"ssn": "x"}}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_non_string_entity_value_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"entities": {"amount": 149}}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_unknown_risk_signal_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"risk_signals": ["NUKE"]}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_empty_summary_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"summary": ""}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_oversize_summary_is_invalid_schema():
    _, status, _ = parse_interpretation(_json({"summary": "s" * 401}))
    assert status is ValidationStatus.INVALID_SCHEMA


def test_schema_error_detail_reports_only_field_locations_not_input_values():
    # the parser summary lists `loc: msg` — it never echoes the offending input,
    # so message content (which could contain PII or a key) cannot leak here.
    fake = "sk-ant-EXAMPLEfake012345"  # obviously not a real key
    poisoned = _json({"summary": f"leak test {fake}", "urgency": "WAT"})
    _, status, detail = parse_interpretation(poisoned)
    assert status is ValidationStatus.INVALID_SCHEMA
    assert detail is not None
    assert "urgency" in detail
    assert fake not in detail
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", detail)


# --------------------------------------------------------------------------- #
# interpret() — the one-corrective-retry boundary
# --------------------------------------------------------------------------- #
class _ScriptedProvider:
    """Yields queued responses (str) or raises (ProviderError instance) in order."""

    name = "scripted"

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls: list[ProviderRequest] = []

    def classify(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        item = self._script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return ProviderResult(text=str(item), provider_name=self.name, attempt=len(self.calls))


def _request(text: str = "I was double billed, check invoice 4901") -> TriageRequest:
    return TriageRequest(text=text)


def test_interpret_ok_on_first_attempt():
    provider = _ScriptedProvider([_json()])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.OK
    assert result.attempts == 1
    assert result.interpretation is not None
    assert len(provider.calls) == 1
    assert provider.calls[0].correction is False


def test_interpret_retries_once_then_succeeds():
    provider = _ScriptedProvider(["not json at all", _json()])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.OK
    assert result.attempts == 2
    assert len(provider.calls) == 2
    assert provider.calls[1].correction is True
    assert "did not satisfy the required JSON contract" in provider.calls[1].user


def test_interpret_retries_once_then_gives_up_unparseable():
    provider = _ScriptedProvider(["garbage", "still garbage"])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.UNPARSEABLE
    assert result.attempts == 2
    assert result.interpretation is None
    assert result.raw_response == "still garbage"


def test_interpret_retries_once_then_gives_up_invalid_schema():
    bad = _json({"urgency": "WAT"})
    provider = _ScriptedProvider([bad, bad])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.INVALID_SCHEMA
    assert result.attempts == 2


def test_interpret_does_not_retry_a_provider_error():
    provider = _ScriptedProvider([ProviderError("upstream 503")])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.PROVIDER_ERROR
    assert result.attempts == 1
    assert len(provider.calls) == 1
    assert "upstream 503" in (result.error_detail or "")


def test_interpret_provider_error_on_the_retry():
    provider = _ScriptedProvider(["bad output", ProviderError("timeout on retry")])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.PROVIDER_ERROR
    assert result.attempts == 2
    assert result.raw_response == "bad output"


def test_interpret_provider_error_detail_is_scrubbed():
    # fake key (contains "EXAMPLE" so the repo secret scanner ignores the line)
    provider = _ScriptedProvider([ProviderError("auth failed, key sk-ant-EXAMPLEfake0123456")])
    result = interpret(_request(), provider)
    assert "sk-ant-" not in (result.error_detail or "")


def test_interpret_raw_response_is_truncated():
    huge = "z" * (MAX_RAW_RESPONSE_CHARS + 500)
    provider = _ScriptedProvider([huge, huge])
    result = interpret(_request(), provider)
    assert result.status is ValidationStatus.UNPARSEABLE
    assert result.raw_response is not None
    assert result.raw_response.endswith("…[truncated]")
    assert len(result.raw_response) == MAX_RAW_RESPONSE_CHARS + len("…[truncated]")


# --------------------------------------------------------------------------- #
# interpret() end-to-end with the real MockProvider + scenario ids
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("scenario_id", "expected_status"),
    [
        ("support-howto", ValidationStatus.OK),
        ("security-prompt-injection", ValidationStatus.OK),
        ("malformed-ai-output", ValidationStatus.UNPARSEABLE),
        ("invalid-schema-ai-output", ValidationStatus.INVALID_SCHEMA),
        ("provider-error", ValidationStatus.PROVIDER_ERROR),
    ],
)
def test_interpret_with_mock_provider_scenarios(scenario_id, expected_status):
    request = TriageRequest(text="placeholder", scenario_id=scenario_id)
    result = interpret(request, MockProvider())
    assert result.status is expected_status
    if expected_status is ValidationStatus.OK:
        assert result.interpretation is not None
    if scenario_id in {"malformed-ai-output", "invalid-schema-ai-output"}:
        assert result.attempts == 2  # one corrective retry was spent


def test_prompt_injection_scenario_still_validates_and_carries_the_signal():
    request = TriageRequest(text="placeholder", scenario_id="security-prompt-injection")
    result = interpret(request, MockProvider())
    assert result.status is ValidationStatus.OK
    assert result.interpretation is not None
    assert RiskSignal.PROMPT_INJECTION_SUSPECTED in result.interpretation.risk_signals
    # the model was partly fooled — it said SUPPORT / LOW. The deterministic
    # layer (CP2) is what overrides that; here we only confirm the boundary
    # faithfully preserved the model's (wrong) read.
    assert result.interpretation.category is Category.SUPPORT
    assert result.interpretation.urgency is Urgency.LOW
