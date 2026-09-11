import os

from .http import ProviderError, post_json

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def chat(model: str, system: str, messages: list[dict], credential: str | None = None) -> str:
    api_key = credential or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderError("No Anthropic API key available (ANTHROPIC_API_KEY not set, and none supplied)")

    body = {
        "model": model,
        "max_tokens": 4096,
        "system": system,
        "messages": messages,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    data = post_json(API_URL, body, headers)
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text:
        raise ProviderError(f"Anthropic response had no text content: {data}")
    return text
