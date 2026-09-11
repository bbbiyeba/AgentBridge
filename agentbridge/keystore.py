"""Encrypted, per-user storage for API keys, backed by Upstash Redis's REST
API (plain HTTP - no redis client library needed).

Only reachable through a signed-in Google session (see auth.py). Keys are
encrypted at rest with a server-held key (KEY_ENCRYPTION_SECRET) so a raw
dump of the Redis database doesn't hand over plaintext API keys.
"""

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

ALLOWED_PROVIDERS = ("anthropic", "openai")


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
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise KeystoreError(f"Upstash returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise KeystoreError(f"Could not reach Upstash: {e.reason}") from e


def _redis_key(user_id: str) -> str:
    return f"agentbridge:keys:{user_id}"


def get_keys(user_id: str) -> dict:
    """Returns e.g. {"anthropic": "sk-ant-..."} for whichever keys this user has saved."""
    result = _redis_command(["GET", _redis_key(user_id)])
    raw = result.get("result")
    if not raw:
        return {}
    try:
        decrypted = _fernet().decrypt(raw.encode("utf-8")).decode("utf-8")
        data = json.loads(decrypted)
    except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in ALLOWED_PROVIDERS and v}


def set_keys(user_id: str, keys: dict) -> None:
    cleaned = {k: v for k, v in keys.items() if k in ALLOWED_PROVIDERS and v}
    payload = _fernet().encrypt(json.dumps(cleaned).encode("utf-8")).decode("utf-8")
    _redis_command(["SET", _redis_key(user_id), payload])
