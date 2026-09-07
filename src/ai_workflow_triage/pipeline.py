"""The full offline processing path — orchestration only, no decision logic.

    TriageRequest
      → interpret(request, provider)      (AI boundary; one corrective retry)
      → extract_signals(request.text)     (deterministic, from the ORIGINAL text)
      → RuleContext → decide()            (CP2 engine — the single source of truth)
      → AuditRecord
      → audit_store.write_record()        (best-effort)
      → ProcessResult

A provider / parse / schema failure flows into the engine as a non-OK
``validation_status`` (the unusable-AI state), which R01 turns into a
conservative ``HUMAN_REVIEW`` — never a confident normal route. An audit-write
failure never loses the decision: ``audit_persisted`` goes ``False`` and a
redacted ``persistence_error`` is returned.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from . import SCHEMA_VERSION, __version__
from .audit import AuditRecord, AuditStore
from .config import DEFAULT_CONFIG, TriageConfig
from .decision import decide
from .models import TriageDecision, TriageRequest, ValidationStatus
from .providers.base import Provider
from .redaction import redact_secrets
from .rules.keywords import extract_signals
from .rules.table import RuleContext
from .validation import interpret

__all__ = ["ProcessResult", "process_request"]


@dataclass(frozen=True)
class ProcessResult:
    request_id: str
    decision: TriageDecision
    audit_record: AuditRecord
    audit_persisted: bool
    persistence_error: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def process_request(
    request: TriageRequest,
    provider: Provider,
    *,
    config: TriageConfig = DEFAULT_CONFIG,
    audit_store: AuditStore | None = None,
    now: Callable[[], datetime] | None = None,
    request_id: str | None = None,
) -> ProcessResult:
    clock = now or _utc_now
    rid = request_id or str(uuid4())
    processing_errors: list[str] = []

    validation = interpret(request, provider, config=config)
    if validation.status is not ValidationStatus.OK:
        processing_errors.append(f"AI interpretation unusable ({validation.status.value})")

    signals = extract_signals(request.text)
    ctx = RuleContext(
        text=request.text,
        interpretation=validation.interpretation,
        validation_status=validation.status,
        keyword_signals=signals,
        config=config,
    )
    decision = decide(ctx)

    record = AuditRecord(
        audit_id=rid,
        request_id=rid,
        received_at=clock().isoformat(),
        schema_version=SCHEMA_VERSION,
        harness_version=__version__,
        input_text=request.text,
        channel=request.channel,
        provider_name=validation.provider_name or provider.name,
        provider_model=validation.provider_model,
        provider_attempts=validation.attempts,
        validation_status=validation.status,
        validation_error=validation.error_detail,
        interpretation=validation.interpretation,
        keyword_signals=signals,
        rules_triggered=decision.rules_triggered,
        rule_trace=decision.rule_trace,
        reasons=decision.reasons,
        final_route=decision.route,
        suggested_route=decision.suggested_route,
        final_urgency=decision.urgency,
        final_disposition=decision.disposition,
        escalation=decision.escalation,
        human_review=decision.human_review,
        human_review_reason=decision.human_review_reason,
        missing_information=decision.missing_information,
        decision_confidence=decision.decision_confidence,
        processing_errors=processing_errors,
    )

    audit_persisted = False
    persistence_error: str | None = None
    if audit_store is not None:
        try:
            audit_store.write_record(record)
            audit_persisted = True
        except Exception as exc:  # noqa: BLE001 - any store failure is non-fatal here
            persistence_error = (
                redact_secrets(f"{type(exc).__name__}: {exc}") or "audit write failed"
            )

    return ProcessResult(
        request_id=rid,
        decision=decision,
        audit_record=record,
        audit_persisted=audit_persisted,
        persistence_error=persistence_error,
    )
