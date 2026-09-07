"""Secret redaction for anything that might reach a log line, an error field,
or a judge-facing surface.

Used on `ValidationResult.error_detail` and on any provider exception text. It is
a safety net, not the primary control — the primary control is that keys only
ever live in the environment and are never passed into these code paths.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bre_[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{8,}=*"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b\s*[:=]\s*(?:bearer\s+)?\S+"
    ),
)

_REDACTED = "[REDACTED]"


def redact_secrets(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text
    for pattern in _PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    return cleaned
