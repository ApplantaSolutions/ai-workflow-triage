"""Regenerate `examples/audit-<id>.json` — one committed audit record per
synthetic scenario, produced through the real offline pipeline against a fixed
clock and deterministic ids.

A test (`tests/test_scenarios.py`) regenerates in memory and byte-compares to
the committed files, so any silent change to the engine, the rules, the audit
shape, or the scenarios fails CI.

    python scripts/build_examples.py            # write the files
    python scripts/build_examples.py --check    # exit 1 if any file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ai_workflow_triage.audit import SqliteAuditStore
from ai_workflow_triage.models import TriageRequest
from ai_workflow_triage.pipeline import process_request
from ai_workflow_triage.providers import MockProvider
from ai_workflow_triage.scenarios import load_scenarios

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_FIXED_CLOCK = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _fixed_now() -> datetime:
    return _FIXED_CLOCK


def render(scenario_id: str) -> str:
    request = TriageRequest(text=load_scenarios()[scenario_id].input_text, scenario_id=scenario_id)
    store = SqliteAuditStore(":memory:")
    try:
        result = process_request(
            request,
            MockProvider(),
            audit_store=store,
            now=_fixed_now,
            request_id=f"example-{scenario_id}",
        )
    finally:
        store.close()
    payload = {
        "scenario_id": scenario_id,
        "audit_persisted": result.audit_persisted,
        "audit_record": result.audit_record.model_dump(mode="json"),
        "decision": result.decision.model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    args = parser.parse_args(argv)

    _EXAMPLES_DIR.mkdir(exist_ok=True)
    stale: list[str] = []
    for scenario_id in load_scenarios():
        path = _EXAMPLES_DIR / f"audit-{scenario_id}.json"
        content = render(scenario_id)
        if args.check:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing != content:
                stale.append(scenario_id)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")

    if args.check and stale:
        print("STALE example files: " + ", ".join(stale), file=sys.stderr)
        return 1
    if not args.check:
        print(f"wrote {len(load_scenarios())} files to {_EXAMPLES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
