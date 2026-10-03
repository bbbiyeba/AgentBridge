"""Encrypted, per-user storage for API keys, backed by Upstash Redis's REST
API (plain HTTP - no redis client library needed).

Only reachable through a signed-in Google session (see auth.py). Keys are
encrypted at rest with a server-held key (KEY_ENCRYPTION_SECRET) so a raw
dump of the Redis database doesn't hand over plaintext API keys.
"""

import base64
import hashlib
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

ALLOWED_PROVIDERS = ("anthropic", "openai")

log = logging.getLogger(__name__)


class KeystoreError(RuntimeError):
    pass


def _redis_config() -> tuple[str, str] | None:
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    return url.rstrip("/"), token


def is_configured() -> bool:
    return _redis_config() is not None and bool(os.environ.get("KEY_ENCRYPTION_SECRET"))


def _fernet() -> Fernet:
    secret = os.environ["KEY_ENCRYPTION_SECRET"].encode("utf-8")
    try:
        return Fernet(secret)
    except ValueError:
        # Accept any arbitrary secret string, not just a pre-made Fernet key.
        derived = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        return Fernet(derived)


def _redis_command(command: list) -> dict:
    config = _redis_config()
    if not config:
        raise KeystoreError("Upstash Redis is not configured (UPSTASH_REDIS_REST_URL/TOKEN)")
    url, token = config
    req = urllib.request.Request(
        url,
        data=json.dumps(command).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise KeystoreError(f"Upstash returned HTTP {e.code}: {detail}") from e
    except (OSError, http.client.HTTPException) as e:
        # URLError (can't connect) is an OSError; so are a read timeout and
        # a connection dropped mid-response, which URLError doesn't cover.
        raise KeystoreError(f"Could not reach Upstash: {getattr(e, 'reason', e)}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise KeystoreError("Upstash returned a response that isn't JSON") from e
    # Upstash reports command errors (bad token scope, wrong type) inside
    # an HTTP 200 body; without this they'd read as "no saved keys".
    if not isinstance(result, dict) or result.get("error"):
        raise KeystoreError(f"Upstash error: {result.get('error') if isinstance(result, dict) else result}")
    return result


def _redis_key(user_id: str) -> str:
    return f"agentbridge:keys:{user_id}"


class UndecryptableKeysError(KeystoreError):
    pass


def _decrypt(raw: str) -> dict:
    try:
        decrypted = _fernet().decrypt(raw.encode("utf-8")).decode("utf-8")
        data = json.loads(decrypted)
    except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UndecryptableKeysError(
            "your saved keys couldn't be decrypted (the server's KEY_ENCRYPTION_SECRET may have changed)"
        ) from e
    if not isinstance(data, dict):
        raise UndecryptableKeysError("your saved keys are in an unexpected format")
    return data


def get_keys(user_id: str) -> dict:
    """Returns e.g. {"anthropic": "sk-ant-..."} for whichever keys this user
    has saved, or {} if they've saved none. Stored keys that can't be
    decrypted raise, rather than reading as "none saved": that usually
    means KEY_ENCRYPTION_SECRET changed, which the operator needs to know."""
    raw = _redis_command(["GET", _redis_key(user_id)]).get("result")
    if not raw:
        return {}
    try:
        data = _decrypt(raw)
    except UndecryptableKeysError:
        log.error("saved keys for user %s can't be decrypted; KEY_ENCRYPTION_SECRET may have changed", user_id)
        raise
    return {k: v for k, v in data.items() if k in ALLOWED_PROVIDERS and isinstance(v, str) and v}


def set_keys(user_id: str, keys: dict) -> None:
    cleaned = {k: v for k, v in keys.items() if k in ALLOWED_PROVIDERS and isinstance(v, str) and v}
    key = _redis_key(user_id)
    existing = _redis_command(["GET", key]).get("result")
    if existing:
        try:
            _decrypt(existing)
        except UndecryptableKeysError:
            # Overwriting would destroy the only copy. Keep it, so restoring
            # the old KEY_ENCRYPTION_SECRET can still recover it.
            backup = f"{key}:undecryptable:{int(time.time())}"
            _redis_command(["SET", backup, existing])
            log.error("kept undecryptable saved keys for user %s at %s before overwriting", user_id, backup)
    payload = _fernet().encrypt(json.dumps(cleaned).encode("utf-8")).decode("utf-8")
    _redis_command(["SET", key, payload])
