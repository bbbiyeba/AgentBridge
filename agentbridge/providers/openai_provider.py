import os

from .http import ProviderError, post_json

API_URL = "https://api.openai.com/v1/chat/completions"


def chat(model: str, system: str, messages: list[dict], credential: str | None = None) -> str:
    api_key = credential or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ProviderError("No OpenAI API key available (OPENAI_API_KEY not set, and none supplied)")

    full_messages = [{"role": "system", "content": system}] + messages
    body = {"model": model, "messages": full_messages}
    headers = {"Authorization": f"Bearer {api_key}"}
    data = post_json(API_URL, body, headers)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"Unexpected OpenAI response shape: {data}") from e
