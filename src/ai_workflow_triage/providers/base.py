"""The provider protocol and its request/result value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..errors import ProviderError

__all__ = ["Provider", "ProviderError", "ProviderRequest", "ProviderResult"]


@dataclass(frozen=True)
class ProviderRequest:
    """What a provider is asked to interpret.

    ``system`` / ``user`` are the already-built prompt (see ``prompt.py``).
    ``scenario_id`` is a demo-only hint the ``MockProvider`` uses to pick a
    canned response; a real provider ignores it. ``correction`` is ``True`` on
    the single corrective retry.
    """

    system: str
    user: str
    scenario_id: str | None = None
    correction: bool = False


@dataclass(frozen=True)
class ProviderResult:
    """The raw text a provider returned. Parsing / validation happens downstream."""

    text: str
    provider_name: str
    model: str | None = None
    attempt: int = 1


@runtime_checkable
class Provider(Protocol):
    """Minimal seam: given a request, return raw text or raise ``ProviderError``."""

    name: str

    def classify(self, request: ProviderRequest) -> ProviderResult: ...
