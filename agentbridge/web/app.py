from flask import Flask, jsonify, render_template, request

from ..config import Config
from ..mailboard import Mailboard
from ..orchestrator import Orchestrator, build_file_tree
from ..providers import ProviderError


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    mailboard = Mailboard(config.settings.mailboard_file)
    orchestrator = Orchestrator(config, mailboard)

    @app.get("/")
    def index():
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
        return jsonify(data)

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
