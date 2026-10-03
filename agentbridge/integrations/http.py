"""The one HTTP client every integration uses (stdlib only, like providers/http.py).

Centralizing it means timeouts, retries on transient failures, response
size limits and error translation behave the same for every third-party
service, instead of each integration growing its own slightly different
copy of urllib boilerplate.
"""

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .base import IntegrationError

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise IntegrationError("Unexpected response from upstream service", detail=str(e)) from e


def request(
    method: str,
    url: str,
    *,
    service: str,
    headers: dict | None = None,
    params: dict | None = None,
    json_body: dict | None = None,
    form: dict | None = None,
    timeout: float = 15,
    retries: int = 2,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Response:
    """Performs one request, retrying 429/5xx and network errors with
    exponential backoff (0.5s, 1s, ...). `service` names the upstream in
    error messages, e.g. "Google Drive". Raises IntegrationError on any
    non-2xx final result."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("content-type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs.setdefault("content-type", "application/x-www-form-urlencoded")

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise IntegrationError(
                        f"{service} returned more data than this server will relay",
                        status=502,
                        detail=f"{url}: response exceeded {max_bytes} bytes",
                    )
                return Response(resp.status, dict(resp.headers.items()), body)
        except urllib.error.HTTPError as e:
            detail = e.read(4096).decode("utf-8", errors="replace")
            if e.code in RETRYABLE_STATUSES and attempt < retries:
                attempt += 1
                time.sleep(_backoff(attempt, e.headers.get("Retry-After")))
                continue
            # Some APIs (GitHub) report an exhausted quota as a 403, which
            # would otherwise read as "bad credentials". Their headers say
            # which it really is. Not retried: the quota may reset in an hour.
            code = 429 if e.code == 403 and (e.headers or {}).get("X-RateLimit-Remaining") == "0" else e.code
            raise IntegrationError(
                _public_message(service, code),
                status=_status_for(code),
                detail=f"{method} {_redact(url)} -> HTTP {e.code}: {detail}",
            ) from e
        # URLError covers failing to connect; a connection dropped mid-response
        # surfaces as a bare OSError (ConnectionResetError, RemoteDisconnected)
        # or http.client.IncompleteRead instead. All are transient: retry.
        except (OSError, http.client.HTTPException) as e:
            if attempt < retries:
                attempt += 1
                time.sleep(_backoff(attempt, None))
                continue
            reason = getattr(e, "reason", e)
            raise IntegrationError(
                f"Could not reach {service}", status=504, detail=f"{method} {_redact(url)}: {reason}"
            ) from e


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            # Honor the server's hint, but never stall a web request for long.
            return min(float(retry_after), 5.0)
        except ValueError:
            pass
    return 0.5 * (2 ** (attempt - 1))


def _status_for(upstream: int) -> int:
    # Our own credentials being rejected is a server misconfiguration, not
    # the visitor's fault, so it surfaces as a 502 rather than a 401/403.
    if upstream == 404:
        return 404
    if upstream == 429:
        return 503
    return 502


def _public_message(service: str, upstream: int) -> str:
    if upstream in (401, 403):
        return f"{service} rejected this server's credentials"
    if upstream == 404:
        return f"{service} could not find the requested item"
    if upstream == 429:
        return f"{service} is rate limiting requests; try again shortly"
    return f"{service} request failed"


def _redact(url: str) -> str:
    """Drops query strings from logged URLs -- some APIs accept keys there."""
    return url.split("?", 1)[0]
