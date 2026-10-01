"""Google access tokens for server-to-server integrations (Drive, Gmail, ...).

Two credential types, because Google offers no single one that covers both
of our use cases for a personal (non-Workspace) account:

- Service account: a robot identity with its own email address. It can read
  any Drive file you share with that address, needs no human sign-in, and
  never expires. It CANNOT send mail from a personal Gmail account.
- Refresh token: a one-time authorization of your own Google account for a
  specific scope (here gmail.send), created once via Google's OAuth
  Playground and stored as an env var. This is the only way to send mail
  "as you" without Google Workspace domain-wide delegation.

Neither involves visitors signing in to this site.
"""

import base64
import binascii
import json
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import http
from .base import IntegrationError

TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh this long before Google's stated expiry so a token never dies
# mid-request.
EXPIRY_MARGIN_SECONDS = 120

_token_cache: dict[tuple, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def parse_service_account(raw: str) -> dict:
    """Accepts the downloaded key file's JSON either verbatim or base64
    encoded (some hosts mangle multi-line env vars; base64 sidesteps that)."""
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise IntegrationError(
                "Google service account credentials are malformed", status=500, detail=str(e)
            ) from e
    try:
        info = json.loads(text)
    except json.JSONDecodeError as e:
        raise IntegrationError(
            "Google service account credentials are malformed", status=500, detail=str(e)
        ) from e
    for key in ("client_email", "private_key"):
        if not info.get(key):
            raise IntegrationError(
                "Google service account credentials are malformed",
                status=500,
                detail=f"service account JSON is missing '{key}'",
            )
    return info


def service_account_token(info: dict, scopes: list[str]) -> str:
    """OAuth access token for a service account, via a self-signed RS256 JWT
    (https://developers.google.com/identity/protocols/oauth2/service-account)."""
    cache_key = ("sa", info["client_email"], tuple(sorted(scopes)))

    def mint() -> tuple[str, int]:
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if info.get("private_key_id"):
            header["kid"] = info["private_key_id"]
        claims = {
            "iss": info["client_email"],
            "scope": " ".join(scopes),
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = (
            f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
        ).encode("ascii")
        try:
            key = serialization.load_pem_private_key(info["private_key"].encode("utf-8"), password=None)
        except (ValueError, TypeError) as e:
            raise IntegrationError(
                "Google service account private key is invalid", status=500, detail=str(e)
            ) from e
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = f"{signing_input.decode('ascii')}.{_b64url(signature)}"
        data = http.request(
            "POST",
            TOKEN_URL,
            service="Google",
            form={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        ).json()
        return data["access_token"], int(data.get("expires_in", 3600))

    return _cached(cache_key, mint)


def refresh_token_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchanges a long-lived refresh token for a short-lived access token."""
    cache_key = ("rt", client_id, refresh_token[-12:])

    def mint() -> tuple[str, int]:
        try:
            data = http.request(
                "POST",
                TOKEN_URL,
                service="Google",
                form={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            ).json()
        except IntegrationError as e:
            if e.detail and "invalid_grant" in e.detail:
                # By far the most common failure: the OAuth consent screen
                # is still in "Testing" mode, where Google expires refresh
                # tokens after 7 days. Say so in the server log.
                e.detail += (
                    " -- the refresh token was revoked or expired. If your OAuth consent"
                    " screen is in 'Testing' mode, tokens expire after 7 days: publish the"
                    " app to 'In production' and generate a new refresh token."
                )
            raise
        return data["access_token"], int(data.get("expires_in", 3600))

    return _cached(cache_key, mint)


def _cached(key: tuple, mint) -> str:
    now = time.time()
    with _cache_lock:
        hit = _token_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        token, expires_in = mint()
    except KeyError as e:
        raise IntegrationError("Unexpected response from Google", detail=f"missing {e}") from e
    with _cache_lock:
        _token_cache[key] = (now + max(expires_in - EXPIRY_MARGIN_SECONDS, 60), token)
    return token


def clear_token_cache() -> None:
    with _cache_lock:
        _token_cache.clear()
