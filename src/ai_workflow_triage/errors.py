"""Error types for ai-workflow-triage.

None of these ever carry an API key or an environment value in their message —
``ProviderError`` in particular is surfaced into the audit record, so its text
must stay safe to persist and display.
"""

from __future__ import annotations


class TriageError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(TriageError):
    """Configuration is missing or invalid (e.g. a real provider with no key)."""


class ProviderError(TriageError):
    """An AI provider call failed — timeout, network, upstream 5xx, or a refusal.

    The pipeline catches this and deterministically routes the request to
    ``HUMAN_REVIEW`` with ``validation_status = PROVIDER_ERROR``. The message
    must never contain a secret.
    """
