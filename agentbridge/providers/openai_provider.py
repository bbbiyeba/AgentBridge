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
        choice = data["choices"][0]
        content = choice["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise ProviderError(f"Unexpected OpenAI response shape: {data}") from e
    if choice.get("finish_reason") == "length":
        raise ProviderError(
            "OpenAI's reply was cut off at the output limit before the JSON was complete. "
            "Ask the agent for a smaller change and try again."
        )
    if not content:
        # e.g. a refusal, which arrives as message.refusal with content null
        refusal = choice["message"].get("refusal")
        raise ProviderError(f"OpenAI returned no content{': ' + refusal if refusal else ''}")
    return content
