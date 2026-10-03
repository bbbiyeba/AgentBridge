"""Minimal Upstash Redis client over its REST API (plain HTTP, no redis
library), shared by the keystore and the cross-instance rate limiter.

Configured by UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN.
"""

import http.client
import json
import os
import urllib.error
import urllib.request


class UpstashError(RuntimeError):
    pass


def config() -> tuple[str, str] | None:
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    return url.rstrip("/"), token


def is_configured() -> bool:
    return config() is not None


def _post(path: str, payload, timeout: float):
    cfg = config()
    if not cfg:
        raise UpstashError("Upstash Redis is not configured (UPSTASH_REDIS_REST_URL/TOKEN)")
    url, token = cfg
    req = urllib.request.Request(
        url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise UpstashError(f"Upstash returned HTTP {e.code}: {detail}") from e
    except (OSError, http.client.HTTPException) as e:
        # URLError (can't connect) is an OSError; so are a read timeout and
        # a connection dropped mid-response, which URLError doesn't cover.
        raise UpstashError(f"Could not reach Upstash: {getattr(e, 'reason', e)}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UpstashError("Upstash returned a response that isn't JSON") from e


def _check(result):
    # Upstash reports command errors (bad token scope, wrong type) inside an
    # HTTP 200 body; without this check they'd read as an empty result.
    if not isinstance(result, dict) or result.get("error"):
        raise UpstashError(f"Upstash error: {result.get('error') if isinstance(result, dict) else result}")
    return result


def command(cmd: list, timeout: float = 15) -> dict:
    """Runs one command, e.g. ["GET", "key"]; returns {"result": ...}."""
    return _check(_post("", cmd, timeout))


def pipeline(cmds: list[list], timeout: float = 15) -> list[dict]:
    """Runs several commands in one round trip; returns one {"result": ...}
    per command, in order."""
    results = _post("/pipeline", cmds, timeout)
    if not isinstance(results, list) or len(results) != len(cmds):
        raise UpstashError(f"Upstash pipeline returned an unexpected response: {results!r}"[:300])
    return [_check(r) for r in results]
