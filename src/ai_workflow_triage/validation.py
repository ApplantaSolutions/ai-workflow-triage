"""Strict, conservative parsing of a provider response, plus the interpretation
boundary (`interpret`) with exactly one corrective retry.

Nothing invalid is ever silently repaired into a valid interpretation. Small
formatting tolerance only: a bare JSON object, a fenced object, or one
unambiguous object embedded in prose. Anything else — missing/unknown fields,
bad enums, wrong types, a bool confidence, oversize fields, an array or scalar
instead of an object, multiple candidate objects, or unparseable text — becomes
`UNPARSEABLE` / `INVALID_SCHEMA`, and the pipeline turns that into HUMAN_REVIEW.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from .config import DEFAULT_CONFIG, TriageConfig
from .errors import ProviderError
from .models import TriageInterpretation, TriageRequest, ValidationResult, ValidationStatus
from .prompt import build_correction_prompt, build_interpreter_prompt
from .providers.base import Provider, ProviderRequest
from .redaction import redact_secrets

__all__ = ["interpret", "parse_interpretation"]

_MAX_ERROR_DETAIL = 600


@dataclass(frozen=True)
class _Parsed:
    value: object


def _try_json(text: str) -> _Parsed | None:
    try:
        return _Parsed(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        return None


def _scan_top_level_objects(text: str) -> list[str]:
    """Every balanced ``{...}`` substring at brace-depth 0, string-aware."""

    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                objects.append(text[start : i + 1])
                start = -1
    return objects


def _kind(value: object) -> str:
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _summarize_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    detail = "; ".join(parts[:6])
    if len(parts) > 6:
        detail += f"; (+{len(parts) - 6} more)"
    return detail


def parse_interpretation(
    raw: str,
) -> tuple[TriageInterpretation | None, ValidationStatus, str | None]:
    """Parse + validate one provider response. No provider call, no retry."""

    stripped = raw.strip()
    if not stripped:
        return None, ValidationStatus.UNPARSEABLE, "empty response"

    whole = _try_json(stripped)
    if whole is not None:
        if isinstance(whole.value, dict):
            return _validate(whole.value)
        return (
            None,
            ValidationStatus.INVALID_SCHEMA,
            f"expected a JSON object, got a JSON {_kind(whole.value)}",
        )

    candidates: list[dict[str, object]] = []
    for chunk in _scan_top_level_objects(stripped):
        parsed = _try_json(chunk)
        if parsed is not None and isinstance(parsed.value, dict):
            candidates.append(parsed.value)

    if not candidates:
        return None, ValidationStatus.UNPARSEABLE, "no JSON object found in the response"
    if len(candidates) > 1:
        return (
            None,
            ValidationStatus.UNPARSEABLE,
            f"found {len(candidates)} JSON objects; cannot choose one unambiguously",
        )
    return _validate(candidates[0])


def _validate(
    obj: dict[str, object],
) -> tuple[TriageInterpretation | None, ValidationStatus, str | None]:
    try:
        interpretation = TriageInterpretation.model_validate(obj)
    except ValidationError as exc:
        return None, ValidationStatus.INVALID_SCHEMA, _summarize_validation_error(exc)
    return interpretation, ValidationStatus.OK, None


def _clip(detail: str | None) -> str | None:
    cleaned = redact_secrets(detail)
    if cleaned is not None and len(cleaned) > _MAX_ERROR_DETAIL:
        cleaned = cleaned[:_MAX_ERROR_DETAIL] + "…"
    return cleaned


def interpret(
    request: TriageRequest,
    provider: Provider,
    *,
    config: TriageConfig = DEFAULT_CONFIG,
) -> ValidationResult:
    """Provider → parse → (one corrective retry) → ValidationResult.

    A ``ProviderError`` is never retried — it becomes ``PROVIDER_ERROR`` at once.
    A parse/schema failure gets exactly one corrective retry through the same
    provider seam.
    """

    del config  # no config knobs at this seam in V1; kept for signature stability

    provider_name = getattr(provider, "name", None)
    prompt = build_interpreter_prompt(request.text)
    first_request = ProviderRequest(
        system=prompt.system, user=prompt.user, scenario_id=request.scenario_id
    )

    try:
        first = provider.classify(first_request)
    except ProviderError as exc:
        return ValidationResult(
            status=ValidationStatus.PROVIDER_ERROR,
            raw_response=None,
            error_detail=_clip(str(exc)) or "provider error",
            attempts=1,
            provider_name=provider_name,
        )

    interpretation, status, detail = parse_interpretation(first.text)
    if status == ValidationStatus.OK:
        return ValidationResult(
            status=status,
            interpretation=interpretation,
            raw_response=first.text,
            attempts=1,
            provider_name=first.provider_name,
            provider_model=first.model,
        )

    correction = build_correction_prompt(
        request.text, prior_response=first.text, error=detail or "invalid output"
    )
    retry_request = ProviderRequest(
        system=correction.system,
        user=correction.user,
        scenario_id=request.scenario_id,
        correction=True,
    )

    try:
        second = provider.classify(retry_request)
    except ProviderError as exc:
        return ValidationResult(
            status=ValidationStatus.PROVIDER_ERROR,
            raw_response=first.text,
            error_detail=_clip(str(exc)) or "provider error on corrective retry",
            attempts=2,
            provider_name=first.provider_name,
            provider_model=first.model,
        )

    interpretation, status, detail2 = parse_interpretation(second.text)
    if status == ValidationStatus.OK:
        return ValidationResult(
            status=status,
            interpretation=interpretation,
            raw_response=second.text,
            attempts=2,
            provider_name=second.provider_name,
            provider_model=second.model,
        )

    return ValidationResult(
        status=status,
        raw_response=second.text,
        error_detail=_clip(detail2 or detail or "invalid output after corrective retry"),
        attempts=2,
        provider_name=second.provider_name,
        provider_model=second.model,
    )
