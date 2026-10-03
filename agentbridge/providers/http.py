"""Minimal stdlib-only JSON-over-HTTP helper shared by all providers."""

import http.client
import json
import os
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    """Raised when a provider HTTP call fails or returns something unexpected."""


def provider_timeout(default: float) -> float:
    """PROVIDER_TIMEOUT_SECONDS, if set, overrides every provider's default.
    Set it a little below your host's request limit (Vercel's function
    max duration, say) so a slow model reply fails with a clear "didn't
    respond" error instead of the platform killing the request mid-turn."""
    raw = os.environ.get("PROVIDER_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ProviderError(f"PROVIDER_TIMEOUT_SECONDS must be a number of seconds, got {raw!r}") from None
    if value <= 0:
        raise ProviderError("PROVIDER_TIMEOUT_SECONDS must be greater than 0")
    return value


def post_json(url: str, body: dict, headers: dict, timeout: float = 120) -> dict:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", **headers},
            method="POST",
        )
    except ValueError as e:
        # e.g. OLLAMA_HOST set to "localhost:11434" without http://
        raise ProviderError(f"Invalid provider URL {url!r}: {e}") from e
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"{url} returned HTTP {e.code}: {detail}") from e
    except TimeoutError as e:
        # A slow generation that outlasts `timeout` raises a bare
        # TimeoutError, which URLError doesn't cover.
        raise ProviderError(f"{url} didn't respond within {timeout}s") from e
    except (OSError, http.client.HTTPException) as e:
        # URLError (can't connect) plus connections dropped mid-response.
        raise ProviderError(f"Could not reach {url}: {getattr(e, 'reason', e)}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProviderError(f"{url} returned a response that isn't JSON: {raw[:300]!r}") from e
