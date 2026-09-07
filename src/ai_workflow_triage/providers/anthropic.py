"""Real provider adapter — establishes the seam, never exercised in V1.

Not constructed, called, or network-tested by the test suite or the offline
demo. It reads `ANTHROPIC_API_KEY` and `TRIAGE_MODEL` from the environment only,
and **both must be set explicitly** — there is no default model name. The
`anthropic` package is an optional dependency (`pip install
'ai-workflow-triage[real]'`) and is imported lazily so the rest of the package
never depends on it.
"""

from __future__ import annotations

import os

from ..errors import ConfigError, ProviderError
from .base import ProviderRequest, ProviderResult

_MAX_TOKENS = 1024


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model = model or os.environ.get("TRIAGE_MODEL")
        if not self._api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Use the mock provider, or set the key to run the real one."
            )
        if not self._model:
            raise ConfigError(
                "TRIAGE_MODEL is not set. Set it explicitly to the model id you intend to use; "
                "the adapter does not guess a model name."
            )

    def classify(self, request: ProviderRequest) -> ProviderResult:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "the 'anthropic' package is not installed; run: pip install 'ai-workflow-triage[real]'"
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
            )
        except Exception as exc:  # noqa: BLE001 - normalise every SDK failure to one type
            # Deliberately does not include str(exc) — it could carry request context.
            raise ProviderError(f"anthropic request failed: {type(exc).__name__}") from exc

        text = "".join(
            getattr(block, "text", "")
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )
        return ProviderResult(
            text=text,
            provider_name=self.name,
            model=self._model,
            attempt=2 if request.correction else 1,
        )
