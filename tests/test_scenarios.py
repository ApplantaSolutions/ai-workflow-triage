"""Data-driven regression guard — TEST-PLAN §2.3.

Regenerates every `examples/audit-<id>.json` in memory (fixed clock, deterministic
ids) and byte-compares to the committed file. Fails if the engine, the rules, the
audit shape, or a scenario silently changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.build_examples as build_examples

from ai_workflow_triage.scenarios import load_scenarios

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("scenario_id", sorted(load_scenarios()))
def test_committed_example_matches_regeneration(scenario_id):
    committed = _EXAMPLES / f"audit-{scenario_id}.json"
    assert committed.exists(), f"missing {committed.name}; run: python scripts/build_examples.py"
    expected = committed.read_text(encoding="utf-8")
    regenerated = build_examples.render(scenario_id)
    assert regenerated == expected, (
        f"{committed.name} is stale — run: python scripts/build_examples.py"
    )


def test_there_is_exactly_one_example_per_scenario():
    scenario_ids = set(load_scenarios())
    example_ids = {p.stem.removeprefix("audit-") for p in _EXAMPLES.glob("audit-*.json")}
    assert example_ids == scenario_ids


def test_no_example_contains_a_key_shaped_string():
    import re

    for path in _EXAMPLES.glob("audit-*.json"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sk-(ant-)?[A-Za-z0-9]{16,}", text)
        assert "ANTHROPIC_API_KEY" not in text
