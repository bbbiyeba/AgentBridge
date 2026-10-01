"""Contact form that sends real email through your own Gmail account.

The message always goes TO CONTACT_TO_EMAIL (fixed server-side) and is
sent FROM the Gmail account the refresh token belongs to. The visitor's
address only ever lands in Reply-To, so hitting "Reply" answers them, but
nobody can use this endpoint to send mail to anyone else.
"""

import base64
import re
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate

from flask import request

from . import google, http
from .base import Integration, IntegrationError, Route, Setting

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# Deliberately simple: the real validation is whether a reply reaches it.
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")
MAX_NAME = 100
MAX_EMAIL = 254
MAX_MESSAGE = 5000


class GmailIntegration(Integration):
    name = "gmail"
    title = "Gmail"
    settings = (
        Setting("GMAIL_CLIENT_ID", "OAuth client ID (Google Cloud > APIs & Services > Credentials).", secret=False),
        Setting("GMAIL_CLIENT_SECRET", "OAuth client secret for that client."),
        Setting(
            "GMAIL_REFRESH_TOKEN",
            "Refresh token for YOUR Gmail account with the gmail.send scope (made once via OAuth Playground).",
        ),
        Setting("CONTACT_TO_EMAIL", "Inbox that receives contact form messages.", secret=False),
        Setting(
            "CONTACT_SUBJECT_PREFIX",
            "Prefix for the email subject.",
            required=False,
            secret=False,
            default="[Site contact]",
        ),
    )

    def routes(self) -> list[Route]:
        # 5 messages per 10 minutes per IP: plenty for a real person,
        # tedious for a spam script.
        return [Route("/contact", self.contact, methods=("POST",), rate_limit=(5, 600))]

    def _access_token(self) -> str:
        return google.refresh_token_access_token(
            self.require("GMAIL_CLIENT_ID"),
            self.require("GMAIL_CLIENT_SECRET"),
            self.require("GMAIL_REFRESH_TOKEN"),
        )

    def contact(self):
        body = request.get_json(silent=True) or {}
        # Honeypot: a field hidden from humans with CSS. Bots fill every
        # input; real visitors leave it blank. Pretend success so the bot
        # doesn't learn it was caught.
        if str(body.get("website") or "").strip():
            return {"sent": True}
        name = _clean_line(body.get("name"), "name", MAX_NAME)
        email = _clean_line(body.get("email"), "email", MAX_EMAIL)
        if not EMAIL_RE.match(email):
            raise IntegrationError("Please enter a valid email address", status=400)
        message = str(body.get("message") or "").strip()
        if not message:
            raise IntegrationError("Message can't be empty", status=400)
        if len(message) > MAX_MESSAGE:
            raise IntegrationError(f"Message is too long (max {MAX_MESSAGE} characters)", status=400)
        self.send(name=name, reply_to=email, text=message)
        return {"sent": True}

    def send(self, *, name: str, reply_to: str, text: str) -> None:
        msg = EmailMessage()
        msg["To"] = self.require("CONTACT_TO_EMAIL")
        msg["Subject"] = f"{self.get('CONTACT_SUBJECT_PREFIX')} {name}"
        msg["Date"] = formatdate(localtime=False)
        try:
            # Address() quotes/encodes the display name itself, so a name
            # containing commas, quotes or angle brackets can't smuggle in
            # extra recipients.
            local, _, domain = reply_to.rpartition("@")
            msg["Reply-To"] = Address(display_name=name, username=local, domain=domain)
        except (ValueError, IndexError) as e:
            raise IntegrationError("Please enter a valid email address", status=400) from e
        msg.set_content(f"From: {name} <{reply_to}>\n\n{text}\n")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        http.request(
            "POST",
            SEND_URL,
            service=self.title,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json_body={"raw": raw},
            # A send can't be safely retried blind: the first attempt may
            # have succeeded before the error, and a retry would duplicate it.
            retries=0,
        )

    def check(self) -> str:
        self.ensure_configured()
        self._access_token()
        return f"refresh token is valid; messages go to {self.get('CONTACT_TO_EMAIL')}"


def _clean_line(value, field: str, max_len: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise IntegrationError(f"Please fill in your {field}", status=400)
    if len(text) > max_len:
        raise IntegrationError(f"{field.capitalize()} is too long", status=400)
    if any(c in text for c in "\r\n\x00"):
        # Header injection guard: these end up in email headers.
        raise IntegrationError(f"{field.capitalize()} contains invalid characters", status=400)
    return text
