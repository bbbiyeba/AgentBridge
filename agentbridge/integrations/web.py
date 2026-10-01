"""Mounts every registered integration under /api/integrations/<name>/.

The public landing site is a separate static deployment on a different
origin, so these routes send CORS headers for the origins listed in
INTEGRATIONS_ALLOWED_ORIGINS. CORS only stops other *websites* from
calling these endpoints from a browser -- it doesn't stop curl -- so the
real abuse protections are per-route rate limits and each integration's
own validation (e.g. the contact form's fixed recipient).
"""

import os

from flask import Blueprint, Response, current_app, jsonify, request

from ..ratelimit import SlidingWindowLimiter
from .base import Integration, IntegrationError, NotConfiguredError, Route

DEFAULT_ALLOWED_ORIGIN = "https://agentbridge-site-pi.vercel.app"


def allowed_origins() -> set[str]:
    raw = os.environ.get("INTEGRATIONS_ALLOWED_ORIGINS")
    if raw is None:
        raw = os.environ.get("LANDING_URL", DEFAULT_ALLOWED_ORIGIN)
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def create_blueprint(integrations: list[Integration]) -> Blueprint:
    bp = Blueprint("integrations", __name__, url_prefix="/api/integrations")

    @bp.after_request
    def cors(resp: Response) -> Response:
        origin = request.headers.get("Origin", "").rstrip("/")
        if origin and origin in allowed_origins():
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "content-type"
            resp.headers["Access-Control-Max-Age"] = "86400"
        resp.headers.add("Vary", "Origin")
        return resp

    @bp.get("")
    def status():
        """Which integrations are live, so the frontend can show or hide the
        matching features. Never includes which settings are missing --
        that's for the operator's CLI (python -m agentbridge integrations)."""
        data = {}
        for integ in integrations:
            entry = {"title": integ.title, "configured": integ.is_configured()}
            if entry["configured"]:
                entry.update(integ.public_info())
            data[integ.name] = entry
        resp = jsonify({"ok": True, "integrations": data})
        resp.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
        return resp

    for integ in integrations:
        for route in integ.routes():
            bp.add_url_rule(
                f"/{integ.name}{route.path}",
                endpoint=f"{integ.name}_{route.handler.__name__}",
                view_func=_make_view(integ, route),
                methods=list(route.methods),
            )
    return bp


def _make_view(integ: Integration, route: Route):
    limiter = SlidingWindowLimiter(*route.rate_limit) if route.rate_limit else None

    def view(**kwargs):
        if limiter and limiter.hit(f"{request.remote_addr}"):
            return _error("Too many requests; please try again in a few minutes", 429)
        try:
            integ.ensure_configured()
            result = route.handler(**kwargs)
        except NotConfiguredError as e:
            current_app.logger.warning("%s integration missing settings: %s", integ.name, ", ".join(e.missing))
            return _error(e.message, e.status)
        except IntegrationError as e:
            if e.detail:
                current_app.logger.warning("%s integration error: %s", integ.name, e.detail)
            return _error(e.message, e.status)
        except Exception:
            current_app.logger.exception("%s integration crashed", integ.name)
            return _error(f"{integ.title} integration failed unexpectedly", 500)
        if isinstance(result, Response):
            return result
        resp = jsonify({"ok": True, **result})
        resp.headers["Cache-Control"] = route.cache_control
        return resp

    return view


def _error(message: str, status: int):
    resp = jsonify({"ok": False, "error": message})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp
