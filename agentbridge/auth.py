""""Sign in with Google", used only to tie a visitor's saved API keys to
their account across devices/browsers. Entirely optional: if
GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET aren't set, is_configured() is False,
the login link is hidden, and everything falls back to the existing
per-browser localStorage flow.

No JWT library: the ID token is verified via Google's own tokeninfo
endpoint rather than checking the signature ourselves, keeping this
dependency-free like the rest of the app.
"""

import http.client
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class AuthError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID")) and bool(os.environ.get("GOOGLE_CLIENT_SECRET"))


def new_state() -> str:
    return secrets.token_urlsafe(24)


def build_authorize_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _fetch_json(req, what: str) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise AuthError(f"{what} failed: {detail}") from e
    except (OSError, http.client.HTTPException) as e:
        # URLError, timeouts, and dropped connections
        raise AuthError(f"Could not reach Google: {getattr(e, 'reason', e)}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise AuthError(f"{what} returned a response that isn't JSON") from e
    if not isinstance(data, dict):
        raise AuthError(f"{what} returned an unexpected response")
    return data


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Returns {"email": ..., "sub": ...} for the account that signed in."""
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]

    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    token_data = _fetch_json(req, "Google token exchange")

    id_token = token_data.get("id_token")
    if not id_token:
        raise AuthError("Google did not return an id_token")

    verify_url = f"{TOKENINFO_URL}?id_token={urllib.parse.quote(id_token)}"
    claims = _fetch_json(verify_url, "Google ID token verification")

    if claims.get("aud") != client_id:
        raise AuthError("Google ID token was not issued for this app")
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AuthError("Google ID token has an unexpected issuer")
    # tokeninfo returns this as the string "true"
    if str(claims.get("email_verified")).lower() != "true":
        raise AuthError("Google account email is not verified")

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise AuthError("Google ID token is missing email/sub")
    return {"email": email, "sub": sub}
