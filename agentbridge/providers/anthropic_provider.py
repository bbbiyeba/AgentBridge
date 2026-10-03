import os

from .http import ProviderError, post_json, provider_timeout

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# A turn returns the full content of every file it touches, so 4096 tokens
# (~16KB) truncated multi-file turns mid-JSON. 16000 is the recommended
# ceiling for a non-streaming request; see the stop_reason check below.
MAX_TOKENS = 16000


def chat(model: str, system: str, messages: list[dict], credential: str | None = None) -> str:
    api_key = credential or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderError("No Anthropic API key available (ANTHROPIC_API_KEY not set, and none supplied)")

    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    data = post_json(API_URL, body, headers, timeout=provider_timeout(120))
    stop_reason = data.get("stop_reason")
    if stop_reason == "max_tokens":
        raise ProviderError(
            f"Anthropic's reply was cut off at the {MAX_TOKENS}-token output limit before the JSON was "
            "complete. Ask the agent for a smaller change (fewer or shorter files) and try again."
        )
    if stop_reason == "refusal":
        details = data.get("stop_details") or {}
        category = f" ({details.get('category')})" if details.get("category") else ""
        raise ProviderError(f"The model declined this request{category}.")
    # Thinking blocks may precede the answer; only text blocks are the reply.
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text:
        raise ProviderError(f"Anthropic response had no text content: {data}")
    return text
