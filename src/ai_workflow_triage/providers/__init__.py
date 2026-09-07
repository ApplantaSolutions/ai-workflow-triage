"""Provider abstraction — the AI interpretation seam.

The rest of the application depends on the ``Provider`` protocol, never on a
concrete provider. ``MockProvider`` is the default and the only one exercised by
tests or the offline demo. ``AnthropicProvider`` establishes the real seam; it
is never constructed, called, or network-tested here.
"""

from .base import Provider, ProviderRequest, ProviderResult
from .mock import MockProvider

__all__ = ["MockProvider", "Provider", "ProviderRequest", "ProviderResult"]
