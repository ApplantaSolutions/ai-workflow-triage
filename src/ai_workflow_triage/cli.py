"""Command-line entry point.

    triage analyze --scenario security-prompt-injection
    triage analyze --text "the whole dashboard is down"
    triage analyze --scenario billing-double-charge --json
    triage validate-scenarios
    triage --version

The human-readable output puts "WHAT THE AI OBSERVED" and "DETERMINISTIC
DECISION" in separate blocks — the whole point of the project.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__
from .audit import SqliteAuditStore
from .errors import TriageError
from .models import TriageRequest
from .pipeline import ProcessResult, process_request
from .providers import MockProvider
from .scenarios import get_scenario, load_scenarios


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage", description=__doc__)
    parser.add_argument("--version", action="version", version=f"triage {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="triage one request")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="raw inbound request text")
    source.add_argument("--scenario", help="a synthetic scenario id (uses its input text)")
    analyze.add_argument(
        "--provider",
        choices=["mock"],
        default="mock",
        help="interpretation provider (default: mock)",
    )
    analyze.add_argument("--db", help="path to a SQLite audit database (optional)")
    analyze.add_argument("--json", action="store_true", help="emit the full result as JSON")

    sub.add_parser(
        "validate-scenarios", help="check every synthetic scenario loads and is consistent"
    )
    return parser


def _render_human(result: ProcessResult) -> str:
    record = result.audit_record
    decision = result.decision
    lines: list[str] = []

    lines.append("=" * 60)
    lines.append(f"REQUEST  {result.request_id}")
    lines.append("=" * 60)
    lines.append(record.input_text)
    lines.append("")

    lines.append("-- WHAT THE AI OBSERVED " + "-" * 36)
    lines.append(
        f"validation status : {record.validation_status.value} (attempt {record.provider_attempts})"
    )
    if record.interpretation is not None:
        interp = record.interpretation
        lines.append(f"category          : {interp.category.value}")
        lines.append(f"urgency (model)   : {interp.urgency.value}   <- observation only")
        lines.append(f"customer intent   : {interp.customer_intent}")
        lines.append(
            f"risk signals      : {', '.join(s.value for s in interp.risk_signals) or '(none)'}"
        )
        lines.append(f"missing info      : {', '.join(interp.missing_information) or '(none)'}")
        lines.append(f"model confidence  : {interp.model_confidence:.2f}")
        lines.append(f"reasoning summary : {interp.reasoning_summary}")
    else:
        lines.append(f"(no usable interpretation: {record.validation_error or 'unavailable'})")
    lines.append("")

    lines.append("-- DETERMINISTIC DECISION " + "-" * 34)
    lines.append(f"route             : {decision.route.value}")
    if decision.suggested_route is not None:
        lines.append(f"suggested route   : {decision.suggested_route.value}")
    lines.append(f"urgency           : {decision.urgency.value}   <- rule-derived")
    lines.append(f"disposition       : {decision.disposition.value}")
    lines.append(f"escalation        : {decision.escalation.value}")
    lines.append(f"human review      : {decision.human_review}")
    if decision.human_review_reason:
        lines.append(f"  reason          : {decision.human_review_reason}")
    if decision.missing_information:
        lines.append(f"info needed       : {', '.join(decision.missing_information)}")
    lines.append(
        f"decision confidence: {decision.decision_confidence:.2f}  (heuristic, not a probability)"
    )
    lines.append(f"rules triggered   : {', '.join(decision.rules_triggered)}")
    lines.append("")

    lines.append("-- WHY " + "-" * 53)
    for reason in decision.reasons:
        tag = "[DETERMINISTIC]" if reason.source == "DETERMINISTIC" else "[AI OBSERVATION]"
        rule = f" {reason.rule_id}" if reason.rule_id else ""
        lines.append(f"{tag}{rule}: {reason.text}")
    lines.append("")

    persisted = "yes" if result.audit_persisted else "no"
    lines.append(f"audit persisted   : {persisted}")
    if result.persistence_error:
        lines.append(f"persistence error : {result.persistence_error}")
    return "\n".join(lines)


def _cmd_analyze(args: argparse.Namespace) -> int:
    if args.scenario is not None:
        spec = get_scenario(args.scenario)
        request = TriageRequest(text=spec.input_text, scenario_id=spec.id)
    else:
        request = TriageRequest(text=args.text)

    provider = MockProvider()
    store = SqliteAuditStore(args.db) if args.db else None
    result = process_request(request, provider, audit_store=store)

    if args.json:
        payload = {
            "request_id": result.request_id,
            "audit_persisted": result.audit_persisted,
            "persistence_error": result.persistence_error,
            "decision": result.decision.model_dump(mode="json"),
            "audit_record": result.audit_record.model_dump(mode="json"),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_render_human(result))

    # exit 0 for a produced decision; the decision itself may be HUMAN_REVIEW.
    return 0 if result.decision.route is not None else 1


def _cmd_validate_scenarios(_args: argparse.Namespace) -> int:
    from .models import Disposition, EscalationLevel, Route, Urgency

    scenarios = load_scenarios()
    problems: list[str] = []
    for spec in scenarios.values():
        try:
            Route(spec.expected.route)
            Urgency(spec.expected.urgency)
            Disposition(spec.expected.disposition)
            EscalationLevel(spec.expected.escalation)
        except ValueError as exc:
            problems.append(f"{spec.id}: {exc}")
        # every scenario, run through the full offline pipeline, must land on
        # its declared expected block
        request = TriageRequest(text=spec.input_text, scenario_id=spec.id)
        result = process_request(request, MockProvider())
        got = (
            result.decision.route.value,
            result.decision.urgency.value,
            result.decision.disposition.value,
            result.decision.escalation.value,
            result.decision.human_review,
        )
        want = (
            spec.expected.route,
            spec.expected.urgency,
            spec.expected.disposition,
            spec.expected.escalation,
            spec.expected.human_review,
        )
        if got != want:
            problems.append(f"{spec.id}: pipeline produced {got}, expected {want}")

    if problems:
        print("SCENARIO PROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"OK — {len(scenarios)} synthetic scenarios load and match their expected pipeline result."
    )
    return 0


def _make_output_utf8_safe() -> None:
    # Some Windows consoles default to a legacy code page; avoid a crash on the
    # em dash / arrows in reasons. Best-effort only.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _make_output_utf8_safe()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return _cmd_analyze(args)
        if args.command == "validate-scenarios":
            return _cmd_validate_scenarios(args)
    except TriageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2  # unreachable


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
