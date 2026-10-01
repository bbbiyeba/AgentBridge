"""Calendly booking widget for the site.

There's no API call or secret involved: the booking page is embedded
directly by the browser. It's still registered as an integration so its
one setting lives with all the others (set or change it on the backend,
no landing-site rebuild) and the frontend shows it only when it's set.
"""

import re

from .base import Integration, IntegrationError, Setting

# Only real Calendly booking pages: this URL ends up in an <iframe src>, so
# it must not be able to point the site at an arbitrary page.
URL_RE = re.compile(r"^https://calendly\.com/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)?/?$")


class CalendlyIntegration(Integration):
    name = "calendly"
    title = "Calendly"
    settings = (
        Setting(
            "CALENDLY_URL",
            "Your booking page, e.g. https://calendly.com/your-name or https://calendly.com/your-name/30min",
            secret=False,
        ),
    )

    def missing(self) -> list[str]:
        url = self.get("CALENDLY_URL")
        # A malformed URL counts as unset, so the widget stays hidden rather
        # than embedding something broken.
        return [] if url and URL_RE.match(url) else ["CALENDLY_URL"]

    def public_info(self) -> dict:
        return {"url": self.get("CALENDLY_URL").rstrip("/")}

    def check(self) -> str:
        url = self.get("CALENDLY_URL")
        if url and not URL_RE.match(url):
            raise IntegrationError(
                "CALENDLY_URL must look like https://calendly.com/<name> or https://calendly.com/<name>/<event>",
                status=500,
            )
        self.ensure_configured()
        return f"booking widget will embed {url}"
