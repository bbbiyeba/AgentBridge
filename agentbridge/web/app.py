import hmac
import os
import secrets

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import auth as google_auth
from .. import integrations
from .. import keystore
from ..config import Config
from ..mailboard import Mailboard
from ..orchestrator import KEYED_PROVIDERS, Orchestrator, build_file_tree
from ..providers import ProviderError
from ..ratelimit import make_limiter

# Rate limit for turn-triggering endpoints. Per-instance unless
# RATE_LIMIT_STORE=upstash (see ratelimit.py); BYOK means callers spend
# their own API credits, not the operator's, so per-instance is tolerable.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
_turn_limiter = make_limiter("turn", RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
# Rewriting the shared task is cheap for an attacker and costly for the next
# visitor (who runs it on their own key), so it gets a tighter limit.
_task_limiter = make_limiter("task", 10, 60)
# Everything else that does real work (file reads, prompt building, keystore).
_misc_limiter = make_limiter("misc", 120, 60)

MAX_TASK_CHARS = 20_000
MAX_SAVED_KEY_CHARS = 300

# The dashboard's HTML only loads its own script/stylesheet plus Google
# Fonts, so it can run under a strict policy: injected markup can't run
# script or exfiltrate anything even if an escaping bug ever slips in.
DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _rate_limited(key: str) -> bool:
    return _turn_limiter.hit(key)


class BadRequest(Exception):
    pass


def _json_object() -> dict:
    """The request's JSON body, which must be an object. Requiring the JSON
    content type also keeps plain cross-site HTML forms (which can't send
    application/json) from reaching these endpoints at all."""
    if not request.is_json:
        raise BadRequest("expected a JSON request body (content-type: application/json)")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise BadRequest("expected a JSON object")
    return body


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    # Trust the platform's proxy (Vercel, PythonAnywhere, etc.) for scheme,
    # host, and the real client IP (needed for rate limiting -- without
    # x_for, every request looks like it comes from the platform's internal
    # proxy address), so url_for(..., _external=True) builds correct
    # https:// OAuth redirect URIs and request.remote_addr is meaningful.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)

    # Without FLASK_SECRET_KEY each server instance invents its own key, so on
    # a multi-instance host (Vercel) a session cookie signed by one instance
    # is rejected by the next: logins fail with "Invalid OAuth state" or drop
    # at random. That's harmless while Google login is off, but with it on,
    # disable login loudly rather than let it half-work.
    secret_key = os.environ.get("FLASK_SECRET_KEY")
    login_blocked = google_auth.is_configured() and not secret_key
    if login_blocked:
        app.logger.error(
            "Google sign-in is configured but FLASK_SECRET_KEY is not set; sign-in is disabled "
            "until it is (sessions can't survive across server instances without a stable key)."
        )
    app.secret_key = secret_key or secrets.token_hex(32)

    def login_available() -> bool:
        return google_auth.is_configured() and not login_blocked

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # SameSite=Lax (not Strict): Strict would drop our own oauth_state
    # cookie on the top-level redirect back from accounts.google.com,
    # breaking login. Lax still blocks cross-site POST, which is what
    # actually matters for CSRF here.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Secure cookies on any real deployment (FLASK_SECRET_KEY set, or running
    # on Vercel, which is always HTTPS). Forcing it unconditionally would
    # break session cookies over plain http://localhost.
    app.config["SESSION_COOKIE_SECURE"] = bool(secret_key or os.environ.get("VERCEL"))

    # Optional shared secret gating POST /api/turn/submit -- see that route
    # for why. Unset by default so local/trusted use needs no extra setup.
    worker_submit_token = os.environ.get("WORKER_SUBMIT_TOKEN")

    mailboard = Mailboard(config.settings.mailboard_file)
    orchestrator = Orchestrator(config, mailboard)

    # Third-party integrations (Gmail, Drive, Figma, ...) used by the public
    # landing site, all under /api/integrations/. See integrations/__init__.py.
    app.register_blueprint(integrations.create_blueprint())

    @app.errorhandler(BadRequest)
    def bad_request(e):
        return jsonify({"ok": False, "error": str(e)}), 400

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if resp.mimetype == "text/html":
            resp.headers.setdefault("Content-Security-Policy", DASHBOARD_CSP)
        return resp

    def _limited(limiter, name: str):
        if limiter.hit(f"{name}:{request.remote_addr}"):
            return jsonify({"ok": False, "error": "rate limit exceeded, try again shortly"}), 429
        return None

    def _stored_credentials() -> dict:
        """The signed-in visitor's saved keys. A keystore failure is logged
        and remembered (g.keystore_error) so a later "missing key" error can
        say why, instead of telling the visitor they never saved one."""
        user = session.get("user")
        if not user or not keystore.is_configured():
            return {}
        try:
            return keystore.get_keys(user["sub"])
        except keystore.KeystoreError as e:
            app.logger.warning("couldn't load saved keys for %s: %s", user.get("sub"), e)
            g.keystore_error = str(e)
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
        data["ok"] = True
        data["agents"] = [
            {"name": a.name, "provider": a.provider, "model": a.model} for a in config.agents
        ]
        data["file_tree"] = build_file_tree(orchestrator.workspace)
        data["require_client_keys"] = config.settings.require_client_keys
        data["google_login_available"] = login_available()
        data["user"] = session.get("user")
        # On Vercel the ledger and workspace live in /tmp, which each
        # serverless instance has its own copy of and which resets at will.
        data["ephemeral_storage"] = bool(os.environ.get("VERCEL")) and str(
            config.settings.mailboard_file
        ).startswith("/tmp")
        return jsonify(data)

    @app.get("/auth/login")
    def auth_login():
        if not login_available():
            return jsonify({"ok": False, "error": "Google sign-in is not available on this server"}), 400
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
            # Google's raw error body is for the operator, not the visitor.
            app.logger.warning("Google sign-in failed: %s", e)
            return jsonify({"ok": False, "error": "Google sign-in failed; please try again"}), 400
        session["user"] = identity
        return redirect(url_for("dashboard"))

    # POST so another site can't sign visitors out with a plain link or <img>.
    @app.post("/auth/logout")
    def auth_logout():
        session.pop("user", None)
        return jsonify({"ok": True})

    @app.get("/api/keys")
    def get_saved_keys():
        if limited := _limited(_misc_limiter, "keys"):
            return limited
        user = session.get("user")
        if not user:
            return jsonify({"ok": False, "error": "not signed in"}), 401
        if not keystore.is_configured():
            return jsonify({"ok": False, "error": "server-side key storage is not configured"}), 400
        try:
            keys = keystore.get_keys(user["sub"])
        except keystore.KeystoreError as e:
            app.logger.warning("couldn't load saved keys for %s: %s", user.get("sub"), e)
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify({"ok": True, "keys": keys})

    @app.post("/api/keys")
    def save_keys():
        if limited := _limited(_misc_limiter, "keys"):
            return limited
        user = session.get("user")
        if not user:
            return jsonify({"ok": False, "error": "not signed in"}), 401
        if not keystore.is_configured():
            return jsonify({"ok": False, "error": "server-side key storage is not configured"}), 400
        keys = _json_object().get("keys") or {}
        if not isinstance(keys, dict):
            raise BadRequest("'keys' must be an object")
        for provider, value in keys.items():
            if provider not in keystore.ALLOWED_PROVIDERS:
                raise BadRequest(f"unknown provider '{provider}'")
            if not isinstance(value, str) or len(value) > MAX_SAVED_KEY_CHARS:
                raise BadRequest(f"'{provider}' key must be a string of at most {MAX_SAVED_KEY_CHARS} characters")
        try:
            keystore.set_keys(user["sub"], keys)
        except keystore.KeystoreError as e:
            app.logger.warning("couldn't save keys for %s: %s", user.get("sub"), e)
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify({"ok": True})

    @app.post("/api/task")
    def set_task():
        """The shared task every visitor's next turn runs. Anyone can set it
        (there's no login), so it's typed, size-capped and rate limited: an
        unbounded task is a way to make the next visitor's turn burn their
        own API credits on a huge prompt."""
        if limited := _limited(_task_limiter, "task"):
            return limited
        task = _json_object().get("task", "")
        if not isinstance(task, str):
            raise BadRequest("'task' must be a string")
        if len(task) > MAX_TASK_CHARS:
            raise BadRequest(f"task is too long (max {MAX_TASK_CHARS} characters)")
        mailboard.set_task(task)
        return jsonify({"ok": True})

    @app.post("/api/turn")
    def take_turn():
        if _rate_limited(f"turn:{request.remote_addr}"):
            return jsonify({"ok": False, "error": "rate limit exceeded, try again shortly"}), 429
        body = _json_object()
        agent = body.get("agent") or None
        if agent is not None and not isinstance(agent, str):
            raise BadRequest("'agent' must be a string")
        # Credentials are used only for this single call and are never
        # written to the mailboard, logged, or persisted server-side.
        # Headers take precedence so a terminal/curl caller can supply a key
        # without it ever touching the browser UI or localStorage:
        #   curl -X POST .../api/turn -H "content-type: application/json" \
        #        -H "X-Anthropic-Key: $ANTHROPIC_API_KEY" -d '{"agent":"architect"}'
        # Only anthropic/openai are accepted from the request body -- a
        # caller-supplied "ollama" key is actually a host URL the server
        # would then send outbound requests to (see providers/ollama_provider.py),
        # which would let any web visitor make this server issue arbitrary
        # HTTP requests (SSRF). Header- and account-based credentials below
        # are already restricted to these two providers by construction.
        supplied = body.get("keys") or {}
        if not isinstance(supplied, dict):
            raise BadRequest("'keys' must be an object")
        credentials = {k: v for k, v in supplied.items() if k in KEYED_PROVIDERS and isinstance(v, str) and v}
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
            message = str(e)
            if g.get("keystore_error"):
                message += f" (Your saved keys couldn't be loaded: {g.keystore_error})"
            return jsonify({"ok": False, "error": message}), 400
        except OSError as e:
            app.logger.exception("turn failed while writing the workspace")
            return jsonify({"ok": False, "error": f"couldn't write the agent's files: {e.strerror or e}"}), 500
        return jsonify({"ok": True, "entry": entry})

    @app.get("/api/turn/prepare")
    def prepare_turn():
        """For local workers: returns the prompt for the next (or named)
        turn without making any provider call or touching the filesystem.
        No API key is involved on this end at all."""
        if limited := _limited(_misc_limiter, "prepare"):
            return limited
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
            supplied_token = request.headers.get("X-Worker-Token", "")
            # Constant-time, so response timing doesn't leak the token.
            if not hmac.compare_digest(supplied_token.encode(), worker_submit_token.encode()):
                return jsonify({"ok": False, "error": "invalid or missing worker token"}), 401
        elif config.settings.require_client_keys:
            return jsonify({
                "ok": False,
                "error": "worker submissions are disabled on this deployment (set WORKER_SUBMIT_TOKEN to enable)",
            }), 403
        if _rate_limited(f"submit:{request.remote_addr}"):
            return jsonify({"ok": False, "error": "rate limit exceeded, try again shortly"}), 429
        body = _json_object()
        agent = body.get("agent")
        if not agent or not isinstance(agent, str):
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
        except OSError as e:
            app.logger.exception("worker submission failed while writing the workspace")
            return jsonify({"ok": False, "error": f"couldn't write the files: {e.strerror or e}"}), 500
        return jsonify({"ok": True, "entry": entry})

    @app.get("/api/file")
    def get_file():
        if limited := _limited(_misc_limiter, "file"):
            return limited
        rel = request.args.get("path", "")
        try:
            target = orchestrator._resolve_in_workspace(rel)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        if not target.exists() or not target.is_file():
            return jsonify({"ok": False, "error": "not found"}), 404
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return jsonify({"ok": False, "error": "that's a binary file, which can't be shown as text"}), 415
        return jsonify({"ok": True, "path": rel, "content": content})

    return app
