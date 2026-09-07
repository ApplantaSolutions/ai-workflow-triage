"""Loader for the synthetic scenario set (`data/synthetic_scenarios.json`).

Every scenario is invented — fictional company "Meridian Tools", fake users,
fake invoice numbers and error codes. Nothing here is real customer data or
derived from any other project. See `docs/SYNTHETIC-SCENARIOS.md`.

The file lives under the package (`ai_workflow_triage/data/`) rather than a
repo-root `scenarios/` directory so it ships with the wheel and resolves through
`importlib.resources`. The CP1 docs said `scenarios/synthetic.json`; this is the
reconciled location.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from .errors import ConfigError

_DATA_PACKAGE = "ai_workflow_triage.data"
_DATA_FILE = "synthetic_scenarios.json"

_EXPECTED_KEYS = frozenset({"route", "urgency", "disposition", "escalation", "human_review"})


@dataclass(frozen=True)
class ScenarioExpectation:
    route: str
    urgency: str
    disposition: str
    escalation: str
    human_review: bool


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    title: str
    input_text: str
    mock_response: str | None
    provider_error: bool
    error_message: str | None
    expected: ScenarioExpectation


def _raw_entries() -> list[dict[str, Any]]:
    source = resources.files(_DATA_PACKAGE).joinpath(_DATA_FILE).read_text(encoding="utf-8")
    data = json.loads(source)
    if not isinstance(data, list):
        raise ConfigError(f"{_DATA_FILE} must contain a JSON array")
    return data


def _to_spec(entry: dict[str, Any]) -> ScenarioSpec:
    expected = entry.get("expected")
    if not isinstance(expected, dict) or set(expected) != _EXPECTED_KEYS:
        raise ConfigError(
            f"scenario {entry.get('id')!r}: 'expected' must have exactly {sorted(_EXPECTED_KEYS)}"
        )
    provider_error = bool(entry.get("provider_error", False))
    mock_response = entry.get("mock_response")
    if provider_error and mock_response is not None:
        raise ConfigError(
            f"scenario {entry.get('id')!r}: a provider_error scenario must not also set mock_response"
        )
    if not provider_error and not isinstance(mock_response, str):
        raise ConfigError(
            f"scenario {entry.get('id')!r}: mock_response must be a string unless provider_error is true"
        )
    return ScenarioSpec(
        id=str(entry["id"]),
        title=str(entry["title"]),
        input_text=str(entry["input_text"]),
        mock_response=mock_response,
        provider_error=provider_error,
        error_message=entry.get("error_message"),
        expected=ScenarioExpectation(
            route=str(expected["route"]),
            urgency=str(expected["urgency"]),
            disposition=str(expected["disposition"]),
            escalation=str(expected["escalation"]),
            human_review=bool(expected["human_review"]),
        ),
    )


@lru_cache(maxsize=1)
def load_scenarios() -> dict[str, ScenarioSpec]:
    specs: dict[str, ScenarioSpec] = {}
    for entry in _raw_entries():
        spec = _to_spec(entry)
        if spec.id in specs:
            raise ConfigError(f"duplicate scenario id: {spec.id}")
        specs[spec.id] = spec
    return specs


def scenario_ids() -> tuple[str, ...]:
    return tuple(load_scenarios())


def get_scenario(scenario_id: str) -> ScenarioSpec:
    try:
        return load_scenarios()[scenario_id]
    except KeyError as exc:
        raise ConfigError(f"unknown scenario_id: {scenario_id!r}") from exc
