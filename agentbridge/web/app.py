import os
import secrets
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import auth as google_auth
from .. import keystore
from ..config import Config
from ..mailboard import Mailboard
from ..orchestrator import KEYED_PROVIDERS, Orchestrator, build_file_tree
from ..providers import ProviderError

# Simple in-memory sliding-window rate limit for turn-triggering endpoints.
# Resets on every cold start and isn't shared across serverless instances,
# so it's not a hard guarantee on platforms like Vercel -- but it's free,
# dependency-free, and still meaningfully slows down abuse within one warm
# instance, which is the realistic threat here (BYOK means callers spend
# their own API credits, not the operator's).
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
_rate_limit_buckets: dict[str, deque] = defaultdict(deque)


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    bucket = _rate_limit_buckets[key]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    bucket.append(now)
    return False


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    # Trust the platform's proxy (Vercel, PythonAnywhere, etc.) for scheme,
    # host, and the real client IP (needed for rate limiting -- without
    # x_for, every request looks like it comes from the platform's internal
    # proxy address), so url_for(..., _external=True) builds correct
    # https:// OAuth redirect URIs and request.remote_addr is meaningful.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)
    # Falls back to a random key when unset: fine when Google login isn't
    # configured anyway; set FLASK_SECRET_KEY to keep sessions alive across
    # restarts when it is.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # SameSite=Lax (not Strict): Strict would drop our own oauth_state
    # cookie on the top-level redirect back from accounts.google.com,
    # breaking login. Lax still blocks cross-site POST, which is what
    # actually matters for CSRF here.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Secure cookies only once FLASK_SECRET_KEY is set, which the README
    # already asks for specifically on real deployments -- forcing Secure
    # unconditionally would silently break session cookies (and therefore
    # login) when testing over plain http://localhost.
    app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("FLASK_SECRET_KEY"))

    # Optional shared secret gating POST /api/turn/submit -- see that route
    # for why. Unset by default so local/trusted use needs no extra setup.
    worker_submit_token = os.environ.get("WORKER_SUBMIT_TOKEN")

    mailboard = Mailboard(config.settings.mailboard_file)
    orchestrator = Orchestrator(config, mailboard)

    def _stored_credentials() -> dict:
        user = session.get("user")
        if not user or not keystore.is_configured():
            return {}
        try:
            return keystore.get_keys(user["sub"])
        except keystore.KeystoreError:
            return {}

    @app.get("/")
    def landing():
        # The marketing site is a separate static build (landing/) deployed
        # as its own Vercel project. Override with LANDING_URL if that ever
        # moves; this default is where it lives today.
        landing_url = os.environ.get("LANDING_URL", "https://agentbridge-site-pi.vercel.app")
        return redirect(landing_url)

    @app.get("/app")
    def dashboard():
        return render_template(
            "index.html",
            agents=[{"name": a.name, "provider": a.provider, "model": a.model} for a in config.agents],
            require_client_keys=config.settings.require_client_keys,
        )

    @app.get("/api/state")
    def state():
        data = mailboard.state()
        data["agents"] = [
            {"name": a.name, "provider": a.provider, "model": a.model} for a in config.agents
        ]
        data["file_tree"] = build_file_tree(orchestrator.workspace)
        data["require_client_keys"] = config.settings.require_client_keys
        data["google_login_available"] = google_auth.is_configured()
        data["user"] = session.get("user")
        return jsonify(data)

    @app.get("/auth/login")
    def auth_login():
        if not google_auth.is_configured():
            return jsonify({"ok": False, "error": "Google sign-in is not configured on this server"}), 400
        state_token = google_auth.new_state()
        session["oauth_state"] = state_token
        redirect_uri = url_for("auth_callback", _external=True)
        return redirect(google_auth.build_authorize_url(redirect_uri, state_token))

    @app.get("/auth/callback")
    def auth_callback():
        if not request.args.get("state") or request.args.get("state") != session.get("oauth_state"):
            return jsonify({"ok": False, "error": "Invalid OAuth state"}), 400
        session.pop("oauth_state", None)
        code = request.args.get("code")
        if not code:
            return jsonify({"ok": False, "error": "Missing authorization code"}), 400
        redirect_uri = url_for("auth_callback", _external=True)
        try:
            identity = google_auth.exchange_code(code, redirect_uri)
        except google_auth.AuthError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        session["user"] = identity
        return redirect(url_for("dashboard"))

    @app.get("/auth/logout")
    def auth_logout():
        session.pop("user", None)
        return redirect(url_for("dashboard"))

    @app.get("/api/keys")
    def get_saved_keys():
        user = session.get("user")
        if not user:
            return jsonify({"ok": False, "error": "not signed in"}), 401
        if not keystore.is_configured():
            return jsonify({"ok": False, "error": "server-side key storage is not configured"}), 400
        try:
            keys = keystore.get_keys(user["sub"])
        except keystore.KeystoreError as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify({"ok": True, "keys": keys})

    @app.post("/api/keys")
    def save_keys():
        user = session.get("user")
        if not user:
            return jsonify({"ok": False, "error": "not signed in"}), 401
        if not keystore.is_configured():
            return jsonify({"ok": False, "error": "server-side key storage is not configured"}), 400
        body = request.get_json(silent=True) or {}
        try:
            keystore.set_keys(user["sub"], body.get("keys") or {})
        except keystore.KeystoreError as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify({"ok": True})

    @app.post("/api/task")
    def set_task():
        body = request.get_json(force=True, silent=True) or {}
        mailboard.set_task(body.get("task", ""))
        return jsonify({"ok": True})

    @app.post("/api/turn")
    def take_turn():
        if _rate_limited(f"turn:{request.remote_addr}"):
            return jsonify({"ok": False, "error": "rate limit exceeded, try again shortly"}), 429
        body = request.get_json(silent=True) or {}
        agent = body.get("agent") or None
        # Credentials are used only for this single call and are never
        # written to the mailboard, logged, or persisted server-side.
        # Headers take precedence so a terminal/curl caller can supply a key
        # without it ever touching the browser UI or localStorage:
        #   curl -X POST .../api/turn -H "X-Anthropic-Key: $ANTHROPIC_API_KEY" -d '{"agent":"architect"}'
        # Only anthropic/openai are accepted from the request body -- a
        # caller-supplied "ollama" key is actually a host URL the server
        # would then send outbound requests to (see providers/ollama_provider.py),
        # which would let any web visitor make this server issue arbitrary
        # HTTP requests (SSRF). Header- and account-based credentials below
        # are already restricted to these two providers by construction.
        credentials = {k: v for k, v in (body.get("keys") or {}).items() if k in KEYED_PROVIDERS}
        if request.headers.get("X-Anthropic-Key"):
            credentials["anthropic"] = request.headers["X-Anthropic-Key"]
        if request.headers.get("X-Openai-Key"):
            credentials["openai"] = request.headers["X-Openai-Key"]
        # Signed-in visitors fall back to their saved key when the request
        # didn't explicitly include one for that provider.
        for provider, key in _stored_credentials().items():
            credentials.setdefault(provider, key)
        try:
            entry = orchestrator.take_turn(agent, credentials=credentials)
        except (ProviderError, ValueError, KeyError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "entry": entry})

    @app.get("/api/turn/prepare")
    def prepare_turn():
        """For local workers: returns the prompt for the next (or named)
        turn without making any provider call or touching the filesystem.
        No API key is involved on this end at all."""
        agent = request.args.get("agent") or None
        try:
            prepared = orchestrator.prepare_turn(agent)
        except KeyError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, **prepared})

    @app.post("/api/turn/submit")
    def submit_turn():
        """For local workers: applies a turn's results (already computed on
        the caller's own machine, with the caller's own key) to the shared
        workspace and ledger. No API key is ever sent to this endpoint.

        This endpoint has no credential of its own to check, only whatever
        WORKER_SUBMIT_TOKEN the operator configures -- so on a public,
        BYOK-mode deployment with no token set, anyone could write arbitrary
        "results" straight into the shared workspace/ledger. Refuse that
        combination outright rather than silently staying open.
        """
        if worker_submit_token:
            if request.headers.get("X-Worker-Token") != worker_submit_token:
                return jsonify({"ok": False, "error": "invalid or missing worker token"}), 401
        elif config.settings.require_client_keys:
            return jsonify({
                "ok": False,
                "error": "worker submissions are disabled on this deployment (set WORKER_SUBMIT_TOKEN to enable)",
            }), 403
        if _rate_limited(f"submit:{request.remote_addr}"):
            return jsonify({"ok": False, "error": "rate limit exceeded, try again shortly"}), 429
        body = request.get_json(silent=True) or {}
        agent = body.get("agent")
        if not agent:
            return jsonify({"ok": False, "error": "'agent' is required"}), 400
        try:
            entry = orchestrator.apply_turn(
                agent,
                body.get("message", ""),
                body.get("files") or [],
                body.get("handoff"),
            )
        except (KeyError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "entry": entry})

    @app.get("/api/file")
    def get_file():
        rel = request.args.get("path", "")
        try:
            target = orchestrator._resolve_in_workspace(rel)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if not target.exists() or not target.is_file():
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "path": rel, "content": target.read_text(encoding="utf-8")})

    return app
