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


def chat(provider: str, model: str, system: str, messages: list[dict]) -> str:
    try:
        module = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown provider '{provider}'. Available: {', '.join(_PROVIDERS)}"
        ) from None
    return module.chat(model, system, messages)


__all__ = ["chat", "ProviderError"]
