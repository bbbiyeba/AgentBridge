"""Encrypted, per-user storage for API keys, backed by Upstash Redis's REST
API (plain HTTP - no redis client library needed).

Only reachable through a signed-in Google session (see auth.py). Keys are
encrypted at rest with a server-held key (KEY_ENCRYPTION_SECRET) so a raw
dump of the Redis database doesn't hand over plaintext API keys.
"""

import base64
import hashlib
import json
import logging
import os
import time

from cryptography.fernet import Fernet, InvalidToken

from . import upstash

ALLOWED_PROVIDERS = ("anthropic", "openai")

log = logging.getLogger(__name__)


class KeystoreError(RuntimeError):
    pass


def is_configured() -> bool:
    return upstash.is_configured() and bool(os.environ.get("KEY_ENCRYPTION_SECRET"))


def _fernet() -> Fernet:
    secret = os.environ["KEY_ENCRYPTION_SECRET"].encode("utf-8")
    try:
        return Fernet(secret)
    except ValueError:
        # Accept any arbitrary secret string, not just a pre-made Fernet key.
        derived = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        return Fernet(derived)


def _redis_command(command: list) -> dict:
    try:
        return upstash.command(command)
    except upstash.UpstashError as e:
        raise KeystoreError(str(e)) from e


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
