"""Tests for agentbridge.integrations.

Run with:  python -m unittest discover tests

No network: urllib's urlopen is replaced by FakeUpstream, which plays the
part of Google/Figma, so the real request-building, JWT signing, retry and
error-translation code all runs.
"""

import base64
import email
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from email import policy
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Flask

from agentbridge import integrations
from agentbridge.config import AgentConfig, Config, Settings
from agentbridge.integrations import google
from agentbridge.web.app import create_app

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "site-reader@example.iam.gserviceaccount.com",
    "private_key_id": "kid123",
    "private_key": PRIVATE_KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode(),
}
ORIGIN = "https://agentbridge-site-pi.vercel.app"


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeUpstream:
    """Routes requests to handlers keyed by (method, url-without-query)."""

    def __init__(self):
        self.handlers = {}
        self.calls = []

    def on(self, method, url, handler):
        self.handlers[(method, url)] = handler

    def __call__(self, req, timeout=None):
        base, _, query = req.full_url.partition("?")
        call = {
            "method": req.get_method(),
            "url": base,
            "query": dict(urllib.parse.parse_qsl(query)),
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": req.data,
        }
        self.calls.append(call)
        handler = self.handlers.get((call["method"], base))
        if handler is None:
            raise AssertionError(f"unexpected request {call['method']} {base}")
        status, body, *rest = handler(call)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", rest[0] if rest else {}, io.BytesIO(body))
        return FakeResp(status, body, rest[0] if rest else {})


def token_ok(call):
    return 200, {"access_token": "ya29.test", "expires_in": 3600}


class IntegrationTestCase(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        google.clear_token_cache()
        self.upstream = FakeUpstream()
        patches = [
            mock.patch("urllib.request.urlopen", self.upstream),
            mock.patch("agentbridge.integrations.http.time.sleep"),
            mock.patch.dict(os.environ, {"INTEGRATIONS_ALLOWED_ORIGINS": ORIGIN, **self.env}, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        app = Flask(__name__)
        app.register_blueprint(integrations.create_blueprint())
        self.client = app.test_client()


class StatusAndPlumbingTests(IntegrationTestCase):
    env = {"DRIVE_FILES": "resume=FILE1"}  # missing service account -> unconfigured

    def test_status_lists_every_integration_without_leaking_settings(self):
        data = self.client.get("/api/integrations").get_json()
        self.assertEqual(set(data["integrations"]), {"gmail", "drive", "figma", "github", "calendly"})
        self.assertFalse(data["integrations"]["drive"]["configured"])
        self.assertNotIn("files", data["integrations"]["drive"])
        self.assertNotIn("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(data))

    def test_unconfigured_route_returns_503_json(self):
        resp = self.client.get("/api/integrations/drive/files/resume")
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.get_json()["ok"])
        self.assertEqual(self.upstream.calls, [])

    def test_cors_only_for_allowed_origin(self):
        ok = self.client.get("/api/integrations", headers={"Origin": ORIGIN})
        self.assertEqual(ok.headers.get("Access-Control-Allow-Origin"), ORIGIN)
        bad = self.client.get("/api/integrations", headers={"Origin": "https://evil.example"})
        self.assertIsNone(bad.headers.get("Access-Control-Allow-Origin"))

    def test_preflight_for_contact_post(self):
        resp = self.client.options(
            "/api/integrations/gmail/contact",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), ORIGIN)
        self.assertIn("content-type", resp.headers.get("Access-Control-Allow-Headers", ""))

    def test_registered_on_the_real_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(
                agents=[AgentConfig("a", "anthropic", "m", "r")],
                settings=Settings(Path(tmp), Path(tmp) / "mb.json", 6, 20, True),
            )
            client = create_app(config).test_client()
            self.assertEqual(client.get("/api/integrations").status_code, 200)


class DriveTests(IntegrationTestCase):
    env = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": base64.b64encode(json.dumps(SERVICE_ACCOUNT).encode()).decode(),
        "DRIVE_FILES": "resume=FILE1, doc=GDOC1",
    }

    def setUp(self):
        super().setUp()
        self.upstream.on("POST", google.TOKEN_URL, token_ok)
        self.upstream.on(
            "GET",
            "https://www.googleapis.com/drive/v3/files/FILE1",
            lambda c: (200, b"%PDF-1.7 resume")
            if c["query"].get("alt") == "media"
            else (200, {"name": 'Bryce "CV".pdf', "mimeType": "application/pdf", "modifiedTime": "2026-10-01"}),
        )

    def test_status_exposes_aliases_only(self):
        drive = self.client.get("/api/integrations").get_json()["integrations"]["drive"]
        self.assertTrue(drive["configured"])
        self.assertEqual(drive["files"], ["doc", "resume"])

    def test_serves_file_bytes_with_safe_headers(self):
        resp = self.client.get("/api/integrations/drive/files/resume?download=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"%PDF-1.7 resume")
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertEqual(resp.headers["Content-Disposition"], 'attachment; filename="Bryce _CV_.pdf"')
        self.assertIn("s-maxage", resp.headers["Cache-Control"])

    def test_service_account_jwt_is_validly_signed(self):
        self.client.get("/api/integrations/drive/files/resume")
        token_call = next(c for c in self.upstream.calls if c["url"] == google.TOKEN_URL)
        form = dict(urllib.parse.parse_qsl(token_call["body"].decode()))
        header_b64, claims_b64, sig_b64 = form["assertion"].split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        PRIVATE_KEY.public_key().verify(
            base64.urlsafe_b64decode(pad(sig_b64)),
            f"{header_b64}.{claims_b64}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(base64.urlsafe_b64decode(pad(claims_b64)))
        self.assertEqual(claims["iss"], SERVICE_ACCOUNT["client_email"])
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/drive.readonly")

    def test_second_request_is_served_from_cache(self):
        self.client.get("/api/integrations/drive/files/resume")
        n = len(self.upstream.calls)
        self.client.get("/api/integrations/drive/files/resume")
        self.assertEqual(len(self.upstream.calls), n)

    def test_google_doc_is_exported_as_pdf(self):
        self.upstream.on(
            "GET",
            "https://www.googleapis.com/drive/v3/files/GDOC1",
            lambda c: (200, {"name": "Resume", "mimeType": "application/vnd.google-apps.document"}),
        )
        self.upstream.on(
            "GET",
            "https://www.googleapis.com/drive/v3/files/GDOC1/export",
            lambda c: (200, b"%PDF exported") if c["query"]["mimeType"] == "application/pdf" else (400, b""),
        )
        resp = self.client.get("/api/integrations/drive/files/doc")
        self.assertEqual(resp.data, b"%PDF exported")
        self.assertIn('filename="Resume.pdf"', resp.headers["Content-Disposition"])

    def test_active_content_is_never_rendered_inline(self):
        self.upstream.on(
            "GET",
            "https://www.googleapis.com/drive/v3/files/FILE1",
            lambda c: (200, b"<script>alert(1)</script>")
            if c["query"].get("alt") == "media"
            else (200, {"name": "page.html", "mimeType": "text/html"}),
        )
        resp = self.client.get("/api/integrations/drive/files/resume")
        self.assertTrue(resp.headers["Content-Disposition"].startswith("attachment"))
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_pdf_is_shown_inline_by_default(self):
        resp = self.client.get("/api/integrations/drive/files/resume")
        self.assertTrue(resp.headers["Content-Disposition"].startswith("inline"))

    def test_unknown_alias_is_404_without_calling_drive(self):
        resp = self.client.get("/api/integrations/drive/files/FILE1")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.upstream.calls, [])

    def test_upstream_auth_failure_hides_details(self):
        self.upstream.on(
            "GET",
            "https://www.googleapis.com/drive/v3/files/FILE1",
            lambda c: (403, b'{"error": "secret internal detail about FILE1"}'),
        )
        resp = self.client.get("/api/integrations/drive/files/resume")
        self.assertEqual(resp.status_code, 502)
        self.assertNotIn("secret internal detail", resp.get_data(as_text=True))

    def test_dropped_connection_is_retried_then_reported_cleanly(self):
        import http.client

        drops = []

        def drop(c):
            drops.append(1)
            raise http.client.RemoteDisconnected("Remote end closed connection")

        self.upstream.on("GET", "https://www.googleapis.com/drive/v3/files/FILE1", drop)
        resp = self.client.get("/api/integrations/drive/files/resume/meta")
        self.assertEqual(len(drops), 3)  # first try + 2 retries
        self.assertEqual(resp.status_code, 504)
        self.assertEqual(resp.get_json()["error"], "Could not reach Google Drive")

    def test_non_numeric_cache_setting_is_a_clear_misconfiguration(self):
        with mock.patch.dict(os.environ, {"DRIVE_CACHE_SECONDS": "5m"}):
            with self.assertLogs(level="WARNING") as logs:
                resp = self.client.get("/api/integrations/drive/files/resume")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json()["error"], "Google Drive is misconfigured on this server")
        self.assertIn("DRIVE_CACHE_SECONDS must be a number", "\n".join(logs.output))

    def test_transient_errors_are_retried(self):
        attempts = []

        def flaky(c):
            if c["query"].get("alt") == "media":
                return 200, b"%PDF"
            attempts.append(1)
            if len(attempts) < 3:
                return 503, b"busy"
            return 200, {"name": "x.pdf", "mimeType": "application/pdf"}

        self.upstream.on("GET", "https://www.googleapis.com/drive/v3/files/FILE1", flaky)
        resp = self.client.get("/api/integrations/drive/files/resume/meta")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(attempts), 3)


class GmailTests(IntegrationTestCase):
    env = {
        "GMAIL_CLIENT_ID": "cid",
        "GMAIL_CLIENT_SECRET": "csecret",
        "GMAIL_REFRESH_TOKEN": "1//refresh",
        "CONTACT_TO_EMAIL": "owner@example.com",
    }
    SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def setUp(self):
        super().setUp()
        self.upstream.on("POST", google.TOKEN_URL, token_ok)
        self.sent = []
        self.upstream.on("POST", self.SEND, lambda c: (self.sent.append(c) or 200, {"id": "m1"}))

    def post(self, **body):
        payload = {"name": "Ada Lovelace", "email": "ada@example.org", "message": "Hello!", **body}
        return self.client.post("/api/integrations/gmail/contact", json=payload, headers={"Origin": ORIGIN})

    def sent_message(self):
        raw = json.loads(self.sent[-1]["body"])["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)

    def test_sends_to_fixed_inbox_with_reply_to_visitor(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200, resp.get_json())
        msg = self.sent_message()
        self.assertEqual(msg["To"], "owner@example.com")
        self.assertEqual(msg["Reply-To"], "Ada Lovelace <ada@example.org>")
        self.assertIn("Hello!", msg.get_content())
        self.assertEqual(self.sent[-1]["headers"]["authorization"], "Bearer ya29.test")
        token_form = dict(urllib.parse.parse_qsl(self.upstream.calls[0]["body"].decode()))
        self.assertEqual(token_form["grant_type"], "refresh_token")

    def test_visitor_cannot_add_recipients_via_name(self):
        resp = self.post(name='Mallory, victim@example.net <"x>')
        self.assertEqual(resp.status_code, 200)
        msg = self.sent_message()
        self.assertEqual(msg["To"], "owner@example.com")
        self.assertEqual([a.addr_spec for a in msg["Reply-To"].addresses], ["ada@example.org"])

    def test_header_injection_rejected(self):
        resp = self.post(name="Eve\nBcc: victim@example.net")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.sent, [])

    def test_unicode_line_breaks_in_name_are_400_not_500(self):
        for name in ("Eve Bcc: x@y.z", "Eve x", "Eve\x85x", "Eve\x0bx"):
            self.assertEqual(self.post(name=name).status_code, 400, repr(name))
        self.assertEqual(self.sent, [])

    def test_internationalized_domain_becomes_reachable_punycode(self):
        self.assertEqual(self.post(email="zoe@exämple.org").status_code, 200)
        self.assertEqual([a.addr_spec for a in self.sent_message()["Reply-To"].addresses], ["zoe@xn--exmple-cua.org"])

    def test_non_ascii_local_part_is_rejected_not_garbled(self):
        resp = self.post(email="josé@example.org")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("before the @", resp.get_json()["error"])
        self.assertEqual(self.sent, [])

    def test_non_object_json_body_is_400(self):
        resp = self.client.post("/api/integrations/gmail/contact", json=["not", "an", "object"])
        self.assertEqual(resp.status_code, 400)

    def test_validation(self):
        self.assertEqual(self.post(email="not-an-email").status_code, 400)
        self.assertEqual(self.post(message="   ").status_code, 400)
        self.assertEqual(self.post(message="x" * 5001).status_code, 400)
        self.assertEqual(self.sent, [])

    def test_honeypot_pretends_success_without_sending(self):
        resp = self.post(website="http://spam.example")
        self.assertEqual(resp.get_json(), {"ok": True, "sent": True})
        self.assertEqual(self.sent, [])

    def test_rate_limited_after_five(self):
        codes = [self.post().status_code for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])

    def test_send_is_not_retried(self):
        self.upstream.on("POST", self.SEND, lambda c: (self.sent.append(c) or 503, b"down"))
        self.assertEqual(self.post().status_code, 502)
        self.assertEqual(len(self.sent), 1)

    def test_expired_refresh_token_is_a_502_with_logged_hint(self):
        self.upstream.on("POST", google.TOKEN_URL, lambda c: (400, b'{"error": "invalid_grant"}'))
        with self.assertLogs(level="WARNING") as logs:
            resp = self.post()
        self.assertEqual(resp.status_code, 502)
        self.assertNotIn("invalid_grant", resp.get_data(as_text=True))
        self.assertIn("In production", "\n".join(logs.output))


class FigmaTests(IntegrationTestCase):
    env = {"FIGMA_TOKEN": "figd_x", "FIGMA_FILE_KEY": "KEY1", "FIGMA_NODE_IDS": "1-2, 3:4"}

    def setUp(self):
        super().setUp()
        self.upstream.on(
            "GET",
            "https://api.figma.com/v1/files/KEY1/nodes",
            lambda c: (
                200,
                {
                    "name": "Portfolio",
                    "lastModified": "2026-09-30",
                    "nodes": {"1:2": {"document": {"name": "Home"}}, "3:4": {"document": {"name": "Empty"}}},
                },
            ),
        )
        self.upstream.on(
            "GET",
            "https://api.figma.com/v1/images/KEY1",
            lambda c: (200, {"err": None, "images": {"1:2": "https://s3.example/home.png", "3:4": None}}),
        )

    def test_images_normalizes_ids_and_skips_unrendered(self):
        data = self.client.get("/api/integrations/figma/images").get_json()
        self.assertEqual(data["images"], [{"id": "1:2", "name": "Home", "url": "https://s3.example/home.png"}])
        self.assertEqual(data["file"]["name"], "Portfolio")
        nodes_call = self.upstream.calls[0]
        self.assertEqual(nodes_call["query"]["ids"], "1:2,3:4")
        self.assertEqual(nodes_call["headers"]["x-figma-token"], "figd_x")

    def test_defaults_to_first_page_frames(self):
        with mock.patch.dict(os.environ, {"FIGMA_NODE_IDS": ""}):
            self.upstream.on(
                "GET",
                "https://api.figma.com/v1/files/KEY1",
                lambda c: (
                    200,
                    {
                        "name": "Portfolio",
                        "document": {
                            "children": [
                                {
                                    "children": [
                                        {"id": "1:2", "name": "Home", "type": "FRAME"},
                                        {"id": "9:9", "name": "stray text", "type": "TEXT"},
                                    ]
                                }
                            ]
                        },
                    },
                ),
            )
            data = self.client.get("/api/integrations/figma/images").get_json()
        self.assertEqual([i["id"] for i in data["images"]], ["1:2"])


class HelperTests(unittest.TestCase):
    def test_backoff_survives_hostile_retry_after(self):
        from agentbridge.integrations.http import _backoff

        for hint in ("-1", "nan", "inf", "-inf", "Wed, 21 Oct 2026 07:28:00 GMT", "999"):
            delay = _backoff(1, hint)
            self.assertTrue(0 <= delay <= 5, (hint, delay))

    def test_status_survives_one_broken_integration(self):
        class Broken(integrations.Integration):
            name, title = "broken", "Broken"

            def public_info(self):
                raise RuntimeError("boom")

        app = Flask(__name__)
        app.register_blueprint(integrations.create_blueprint([Broken(environ={}), *integrations.all_integrations()]))
        with self.assertLogs(level="ERROR"):
            resp = app.test_client().get("/api/integrations")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["integrations"]
        self.assertFalse(data["broken"]["configured"])
        self.assertIn("gmail", data)

    def test_parse_service_account_accepts_raw_json(self):
        info = google.parse_service_account(json.dumps(SERVICE_ACCOUNT))
        self.assertEqual(info["client_email"], SERVICE_ACCOUNT["client_email"])

    def test_parse_service_account_rejects_garbage(self):
        with self.assertRaises(integrations.IntegrationError):
            google.parse_service_account("not json or base64!")

    def test_every_integration_declares_settings_and_starts_unconfigured(self):
        for name, cls in integrations.REGISTRY.items():
            integ = cls(environ={})
            self.assertEqual(integ.name, name)
            self.assertTrue(integ.settings)
            self.assertFalse(integ.is_configured())


class GitHubTests(IntegrationTestCase):
    env = {"GITHUB_USERNAME": "bbbiyeba", "GITHUB_TOKEN": "ghp_test"}

    REPOS = [
        {"name": "AgentBridge", "stargazers_count": 12, "forks_count": 2, "language": "Python",
         "html_url": "https://github.com/bbbiyeba/AgentBridge", "pushed_at": "2026-10-01T00:00:00Z"},
        {"name": "site", "stargazers_count": 3, "language": "TypeScript", "pushed_at": "2026-09-01T00:00:00Z"},
        {"name": "old", "stargazers_count": 50, "language": "Python", "archived": True},
        {"name": "someone-elses", "stargazers_count": 999, "language": "Rust", "fork": True},
    ]

    def setUp(self):
        super().setUp()
        self.upstream.on(
            "GET",
            "https://api.github.com/users/bbbiyeba",
            lambda c: (200, {"login": "bbbiyeba", "name": "Bryce", "public_repos": 4, "followers": 9,
                             "html_url": "https://github.com/bbbiyeba", "avatar_url": "https://a/x.png"}),
        )
        self.upstream.on("GET", "https://api.github.com/users/bbbiyeba/repos", lambda c: (200, self.REPOS))
        self.upstream.on(
            "POST",
            "https://api.github.com/graphql",
            lambda c: (200, {"data": {"user": {"contributionsCollection": {
                "contributionCalendar": {"totalContributions": 321}}}}}),
        )

    def test_stats(self):
        data = self.client.get("/api/integrations/github/stats").get_json()
        self.assertEqual(data["totals"], {"public_repos": 4, "stars": 65, "followers": 9,
                                          "contributions_last_year": 321})
        # forks excluded from stars and languages; archived repos aren't featured
        self.assertEqual(data["languages"], [{"name": "Python", "repos": 2}, {"name": "TypeScript", "repos": 1}])
        self.assertEqual([r["name"] for r in data["featured"]], ["AgentBridge", "site"])
        repos_call = next(c for c in self.upstream.calls if c["url"].endswith("/repos"))
        self.assertEqual(repos_call["headers"]["authorization"], "Bearer ghp_test")

    def test_featured_override(self):
        with mock.patch.dict(os.environ, {"GITHUB_FEATURED_REPOS": "site, missing"}):
            with self.assertLogs(level="WARNING") as logs:
                data = self.client.get("/api/integrations/github/stats").get_json()
        self.assertEqual([r["name"] for r in data["featured"]], ["site"])
        self.assertIn("missing", "\n".join(logs.output))

    def test_contributions_failure_degrades_gracefully(self):
        self.upstream.on("POST", "https://api.github.com/graphql", lambda c: (401, b"bad creds"))
        resp = self.client.get("/api/integrations/github/stats")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["totals"]["contributions_last_year"])

    def test_no_token_skips_graphql(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            data = self.client.get("/api/integrations/github/stats").get_json()
        self.assertIsNone(data["totals"]["contributions_last_year"])
        self.assertFalse(any(c["url"].endswith("/graphql") for c in self.upstream.calls))
        self.assertNotIn("authorization", self.upstream.calls[0]["headers"])

    def test_quota_exhaustion_is_reported_as_rate_limit_not_bad_credentials(self):
        self.upstream.on(
            "GET",
            "https://api.github.com/users/bbbiyeba",
            lambda c: (403, b'{"message": "API rate limit exceeded"}', {"X-RateLimit-Remaining": "0"}),
        )
        resp = self.client.get("/api/integrations/github/stats")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("rate limiting", resp.get_json()["error"])

    def test_paginates_large_accounts(self):
        page1 = [{"name": f"r{i}", "stargazers_count": 1} for i in range(100)]
        self.upstream.on(
            "GET",
            "https://api.github.com/users/bbbiyeba/repos",
            lambda c: (200, page1 if c["query"]["page"] == "1" else [{"name": "last", "stargazers_count": 5}]),
        )
        data = self.client.get("/api/integrations/github/stats").get_json()
        self.assertEqual(data["totals"]["stars"], 105)


class CalendlyTests(IntegrationTestCase):
    def status(self, url):
        with mock.patch.dict(os.environ, {"CALENDLY_URL": url}):
            return self.client.get("/api/integrations").get_json()["integrations"]["calendly"]

    def test_valid_url_is_exposed(self):
        self.assertEqual(
            self.status("https://calendly.com/bryce-biyeba/30min/"),
            {"title": "Calendly", "configured": True, "url": "https://calendly.com/bryce-biyeba/30min"},
        )

    def test_non_calendly_urls_are_rejected(self):
        for bad in ("https://evil.example/calendly.com/x", "javascript:alert(1)", "http://calendly.com/x",
                    "https://calendly.com/x/y/z", "https://calendly.com.evil.example/x"):
            self.assertEqual(self.status(bad), {"title": "Calendly", "configured": False}, bad)


if __name__ == "__main__":
    unittest.main()
