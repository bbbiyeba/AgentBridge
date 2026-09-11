"""Provider-agnostic chat interface.

chat(provider, model, system, messages) dispatches to the right backend
(Anthropic, OpenAI, or a local Ollama server) based on `provider`, so the
orchestrator never needs to know which API it's actually talking to.
"""

from . import anthropic_provider, ollama_provider, openai_provider
from .http import ProviderError

_PROVIDERS = {
    "anthropic": anthropic_provider,
    "openai": openai_provider,
    "ollama": ollama_provider,
}


def chat(
    provider: str,
    model: str,
    system: str,
    messages: list[dict],
    credential: str | None = None,
) -> str:
    """credential overrides the provider's env-var default: an API key for
    anthropic/openai, or a host URL for ollama. Pass None to use the env var."""
    try:
        module = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown provider '{provider}'. Available: {', '.join(_PROVIDERS)}"
        ) from None
    return module.chat(model, system, messages, credential=credential)


__all__ = ["chat", "ProviderError"]
