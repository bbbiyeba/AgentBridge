import os

from .http import ProviderError, post_json, provider_timeout

DEFAULT_HOST = "http://localhost:11434"


def chat(model: str, system: str, messages: list[dict], credential: str | None = None) -> str:
    host = (credential or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)).rstrip("/")
    full_messages = [{"role": "system", "content": system}] + messages
    body = {"model": model, "messages": full_messages, "stream": False}
    # Local models are slow, hence the longer default.
    data = post_json(f"{host}/api/chat", body, headers={}, timeout=provider_timeout(300))
    try:
        return data["message"]["content"]
    except KeyError as e:
        raise ProviderError(f"Unexpected Ollama response shape: {data}") from e
