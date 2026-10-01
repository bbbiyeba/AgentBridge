"""Third-party integrations used by the public site (contact email, Drive
files, Figma renders, ...).

Adding one:
  1. Create integrations/<name>.py with a subclass of base.Integration that
     declares its `settings` (env vars) and `routes()`. Call external APIs
     through http.request() so retries, timeouts and error handling match.
  2. Add the class to REGISTRY below.
  3. Run `python -m agentbridge integrations` to see which env vars it
     needs, set them (locally or in Vercel), then `--check` to verify.

Everything else -- mounting at /api/integrations/<name>/, 503 when
unconfigured, errors as JSON, rate limits, CORS -- comes from web.py.
"""

from flask import Blueprint

from .base import Integration, IntegrationError, NotConfiguredError, Route, Setting
from .drive import DriveIntegration
from .figma import FigmaIntegration
from .gmail import GmailIntegration

REGISTRY: dict[str, type[Integration]] = {
    cls.name: cls for cls in (GmailIntegration, DriveIntegration, FigmaIntegration)
}


def all_integrations() -> list[Integration]:
    return [cls() for cls in REGISTRY.values()]


def create_blueprint(integrations: list[Integration] | None = None) -> Blueprint:
    from .web import create_blueprint as _create

    return _create(integrations if integrations is not None else all_integrations())


__all__ = [
    "REGISTRY",
    "Integration",
    "IntegrationError",
    "NotConfiguredError",
    "Route",
    "Setting",
    "all_integrations",
    "create_blueprint",
]
