import os
import secrets

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import auth as google_auth
from .. import keystore
from ..config import Config
from ..mailboard import Mailboard
from ..orchestrator import Orchestrator, build_file_tree
from ..providers import ProviderError


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    # Trust the platform's proxy (Vercel, PythonAnywhere, etc.) for scheme/host,
    # so url_for(..., _external=True) builds correct https:// OAuth redirect URIs.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    # Falls back to a random key when unset: fine when Google login isn't
    # configured anyway; set FLASK_SECRET_KEY to keep sessions alive across
    # restarts when it is.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

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
        body = request.get_json(silent=True) or {}
        agent = body.get("agent") or None
        # Credentials are used only for this single call and are never
        # written to the mailboard, logged, or persisted server-side.
        # Headers take precedence so a terminal/curl caller can supply a key
        # without it ever touching the browser UI or localStorage:
        #   curl -X POST .../api/turn -H "X-Anthropic-Key: $ANTHROPIC_API_KEY" -d '{"agent":"architect"}'
        credentials = dict(body.get("keys") or {})
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
        workspace and ledger. No API key is ever sent to this endpoint."""
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
