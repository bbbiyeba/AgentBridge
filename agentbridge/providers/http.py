"""Minimal stdlib-only JSON-over-HTTP helper shared by all providers."""

import http.client
import json
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    """Raised when a provider HTTP call fails or returns something unexpected."""


def post_json(url: str, body: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
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
