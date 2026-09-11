"""Minimal stdlib-only JSON-over-HTTP helper shared by all providers."""

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
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"{url} returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"Could not reach {url}: {e.reason}") from e
