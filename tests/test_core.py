"""Tests for the core app: mailboard, orchestrator, web routes, keystore,
providers, worker, auth and config.

Run with:  python -m unittest discover tests

No network: urllib's urlopen is replaced wherever a test reaches an
external service, and provider calls are mocked at agentbridge.orchestrator.chat.
"""

import base64
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from agentbridge import auth, config, keystore, worker
from agentbridge.config import AgentConfig, Config, Settings
from agentbridge.mailboard import Mailboard
from agentbridge.orchestrator import Orchestrator, build_file_contents, parse_response
from agentbridge.providers import ProviderError, anthropic_provider, openai_provider
from agentbridge.ratelimit import SlidingWindowLimiter
from agentbridge.web import app as app_module
from agentbridge.web.app import create_app


class FakeResp:
    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = {}

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


def make_config(tmp: Path, require_client_keys=True, budget=60_000) -> Config:
    return Config(
        agents=[AgentConfig("architect", "anthropic", "m1", "r"), AgentConfig("reviewer", "openai", "m2", "r")],
        settings=Settings(tmp / "ws", tmp / "mb.json", 6, 20, require_client_keys, budget),
    )


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


# --------------------------------------------------------------------------- #
# Mailboard
# --------------------------------------------------------------------------- #
class MailboardTests(TmpDirCase):
    def test_concurrent_appends_are_all_kept_with_unique_ids(self):
        mb = Mailboard(self.tmp / "mb.json")
        errors = []

        def writer():
            for _ in range(40):
                try:
                    mb.append("a", "anthropic", "m", "msg", [], None)
                except Exception as e:  # noqa: BLE001 - recorded and asserted below
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        entries = json.loads((self.tmp / "mb.json").read_text())["entries"]
        self.assertEqual(len(entries), 320)
        self.assertEqual(sorted(e["id"] for e in entries), list(range(1, 321)))
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])

    def test_append_sets_next_agent_and_task_round_trips(self):
        mb = Mailboard(self.tmp / "mb.json")
        mb.set_task("build it")
        entry = mb.append("a", "anthropic", "m", "did a thing", [], "reviewer")
        self.assertEqual(entry["id"], 1)
        self.assertEqual(mb.next_agent, "reviewer")
        self.assertEqual(mb.task, "build it")

    def test_existing_ledger_is_not_reset(self):
        Mailboard(self.tmp / "mb.json").append("a", "anthropic", "m", "x", [], None)
        self.assertEqual(len(Mailboard(self.tmp / "mb.json").entries()), 1)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class ParseResponseTests(unittest.TestCase):
    def test_raw_json(self):
        self.assertEqual(
            parse_response('{"message": "hi", "files": [], "handoff": "reviewer"}'),
            {"message": "hi", "files": [], "handoff": "reviewer"},
        )

    def test_fenced_block_containing_fences_in_content(self):
        text = 'Sure!\n```json\n{"message": "a", "files": [{"path": "r.md", "content": "```x```"}]}\n```'
        self.assertEqual(parse_response(text)["files"][0]["content"], "```x```")

    def test_first_of_several_fenced_blocks(self):
        text = '```\n{"message": "one"}\n```\nand\n```\n{"message": "two"}\n```'
        self.assertEqual(parse_response(text)["message"], "one")

    def test_null_files_and_empty_handoff_normalized(self):
        self.assertEqual(parse_response('{"message": "m", "files": null, "handoff": ""}'),
                         {"message": "m", "files": [], "handoff": None})

    def test_rejects_wrong_types_and_shapes(self):
        for bad in ['"a string"', "[1, 2]", '{"files": []}', '{"message": {"x": 1}}',
                    '{"message": "m", "files": {}}', '{"message": "m", "handoff": ["a"]}', "not json", "", None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_response(bad)


class OrchestratorTests(TmpDirCase):
    def setUp(self):
        super().setUp()
        self.config = make_config(self.tmp)
        self.mb = Mailboard(self.config.settings.mailboard_file)
        self.orch = Orchestrator(self.config, self.mb)
        self.ws = self.config.settings.workspace_dir

    def assert_rejected_without_side_effects(self, files, message="m"):
        before = sorted(p.relative_to(self.ws).as_posix() for p in self.ws.rglob("*"))
        with self.assertRaises(ValueError):
            self.orch.apply_turn("architect", message, files, None)
        self.assertEqual(sorted(p.relative_to(self.ws).as_posix() for p in self.ws.rglob("*")), before)
        self.assertEqual(self.mb.entries(), [])

    def test_bad_entry_anywhere_means_nothing_is_written(self):
        self.assert_rejected_without_side_effects([{"path": "ok.txt", "content": "x"}, {"path": "../evil", "content": "x"}])
        self.assert_rejected_without_side_effects([{"path": "", "content": "x"}])
        self.assert_rejected_without_side_effects([{"path": ".", "content": "x"}])
        self.assert_rejected_without_side_effects([{"path": "a.txt", "content": None}])
        self.assert_rejected_without_side_effects(["not a dict"])
        self.assert_rejected_without_side_effects([{"path": "sub", "content": "x"}, {"path": "sub/f", "content": "y"}])
        self.assert_rejected_without_side_effects([{"path": "a.txt", "content": "1"}, {"path": "a.txt", "content": "2"}])
        self.assert_rejected_without_side_effects([], message={"not": "a string"})

    def test_writes_files_with_diffs_and_logs_the_turn(self):
        entry = self.orch.apply_turn("architect", "init", [{"path": "src/app.py", "content": "print(1)\n"}], None)
        self.assertEqual((self.ws / "src/app.py").read_text(), "print(1)\n")
        self.assertEqual(entry["files"][0]["action"], "created")
        entry = self.orch.apply_turn("architect", "edit", [{"path": "src/app.py", "content": "print(2)\n"}], None)
        self.assertEqual(entry["files"][0]["action"], "modified")
        self.assertIn("-print(1)", entry["files"][0]["diff"])

    def test_handoff_is_case_insensitive_and_unknown_names_are_dropped(self):
        self.assertEqual(self.orch.apply_turn("architect", "m", [], "Reviewer")["handoff"], "reviewer")
        entry = self.orch.apply_turn("architect", "m", [], "ghost")
        self.assertIsNone(entry["handoff"])
        self.assertIn("'ghost' ignored", entry["message"])
        self.assertIsNone(self.mb.next_agent)

    def test_stale_next_agent_falls_back_to_first_agent(self):
        self.mb._write({**self.mb.state(), "next_agent": "removed-agent"})
        self.assertEqual(self.orch.prepare_turn()["agent"]["name"], "architect")

    def test_prompt_includes_file_contents_recent_first_within_budget(self):
        self.orch.apply_turn("architect", "m", [{"path": "a.py", "content": "A" * 10}], None)
        self.orch.apply_turn("architect", "m", [{"path": "z.py", "content": "Z" * 10}], None)
        prompt = self.orch.prepare_turn("reviewer")["user_prompt"]
        self.assertIn('<file path="z.py">\nZZZZZZZZZZ\n</file>', prompt)
        self.assertLess(prompt.index('path="z.py"'), prompt.index('path="a.py"'))

    def test_file_contents_skips_binary_and_notes_over_budget(self):
        (self.ws / "img.bin").write_bytes(b"\xff\xd8\xff\x00")
        (self.ws / "big.txt").write_text("x" * 50)
        (self.ws / ".secret").write_text("hidden")
        out = build_file_contents(self.ws, budget=20)
        self.assertIn("img.bin (binary)", out)
        self.assertIn("big.txt (50 chars, over the context budget)", out)
        self.assertNotIn("hidden", out)

    def test_take_turn_requires_client_key_when_configured(self):
        with self.assertRaisesRegex(ValueError, "requires your own anthropic API key"):
            self.orch.take_turn("architect", credentials={})

    def test_take_turn_end_to_end_with_mocked_provider(self):
        reply = json.dumps({"message": "made it", "files": [{"path": "x.txt", "content": "hi"}], "handoff": "reviewer"})
        with mock.patch("agentbridge.orchestrator.chat", return_value=reply) as chat:
            entry = self.orch.take_turn("architect", credentials={"anthropic": "sk-test"})
        self.assertEqual(chat.call_args.kwargs["credential"], "sk-test")
        self.assertEqual((self.ws / "x.txt").read_text(), "hi")
        self.assertEqual(entry["handoff"], "reviewer")

    def test_run_honors_zero_turns(self):
        self.assertEqual(self.orch.run(max_turns=0), [])


# --------------------------------------------------------------------------- #
# Web routes
# --------------------------------------------------------------------------- #
class WebAppTests(TmpDirCase):
    env: dict = {}

    def setUp(self):
        super().setUp()
        for name, limiter in (("_turn_limiter", (20, 60)), ("_task_limiter", (10, 60)), ("_misc_limiter", (120, 60))):
            patcher = mock.patch.object(app_module, name, SlidingWindowLimiter(*limiter))
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, self.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.config = make_config(self.tmp)
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.ws = self.config.settings.workspace_dir


class RouteTests(WebAppTests):
    def test_state_and_dashboard_headers(self):
        state = self.client.get("/api/state").get_json()
        self.assertTrue(state["ok"])
        self.assertFalse(state["ephemeral_storage"])
        resp = self.client.get("/app")
        self.assertIn("script-src 'self'", resp.headers["Content-Security-Policy"])
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")

    def test_task_validation_and_rate_limit(self):
        self.assertEqual(self.client.post("/api/task", data='{"task":"x"}', content_type="text/plain").status_code, 400)
        self.assertEqual(self.client.post("/api/task", json={"task": {"a": 1}}).status_code, 400)
        self.assertEqual(self.client.post("/api/task", json={"task": "x" * 20_001}).status_code, 400)
        self.assertEqual(self.client.post("/api/task", json={"task": "build"}).status_code, 200)
        self.assertEqual(self.client.get("/api/state").get_json()["task"], "build")
        codes = [self.client.post("/api/task", json={"task": "t"}).status_code for _ in range(10)]
        self.assertEqual(codes[-1], 429)

    def test_turn_rejects_malformed_bodies(self):
        self.assertEqual(self.client.post("/api/turn", json=[1]).status_code, 400)
        self.assertEqual(self.client.post("/api/turn", json={"keys": ["a"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/turn", json={"agent": 5}).status_code, 400)

    def test_turn_success_and_provider_error(self):
        reply = json.dumps({"message": "ok", "files": []})
        with mock.patch("agentbridge.orchestrator.chat", return_value=reply):
            resp = self.client.post("/api/turn", json={"agent": "architect", "keys": {"anthropic": "k"}})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        with mock.patch("agentbridge.orchestrator.chat", side_effect=ProviderError("upstream down")):
            resp = self.client.post("/api/turn", json={"agent": "architect", "keys": {"anthropic": "k"}})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("upstream down", resp.get_json()["error"])

    def test_ollama_key_from_body_is_never_used(self):
        reply = json.dumps({"message": "ok", "files": []})
        with mock.patch("agentbridge.orchestrator.chat", return_value=reply) as chat:
            self.client.post("/api/turn", json={"agent": "architect", "keys": {"anthropic": "k", "ollama": "http://169.254.169.254"}})
        self.assertEqual(chat.call_args.kwargs["credential"], "k")

    def test_file_endpoint(self):
        (self.ws / "a.txt").write_text("hello")
        (self.ws / "b.bin").write_bytes(b"\xff\x00")
        self.assertEqual(self.client.get("/api/file?path=a.txt").get_json()["content"], "hello")
        self.assertEqual(self.client.get("/api/file?path=b.bin").status_code, 415)
        self.assertEqual(self.client.get("/api/file?path=../mb.json").status_code, 400)
        self.assertEqual(self.client.get("/api/file?path=missing").status_code, 404)

    def test_logout_is_post_only(self):
        self.assertEqual(self.client.get("/auth/logout").status_code, 405)
        self.assertEqual(self.client.post("/auth/logout", json={}).status_code, 200)

    def test_submit_disabled_on_public_deployment_without_token(self):
        self.assertEqual(self.client.post("/api/turn/submit", json={"agent": "architect"}).status_code, 403)


class SubmitTokenTests(WebAppTests):
    env = {"WORKER_SUBMIT_TOKEN": "s3cret"}

    def test_token_required_and_checked(self):
        body = {"agent": "architect", "message": "remote turn", "files": [{"path": "w.txt", "content": "w"}]}
        self.assertEqual(self.client.post("/api/turn/submit", json=body).status_code, 401)
        self.assertEqual(self.client.post("/api/turn/submit", json=body, headers={"X-Worker-Token": "nope"}).status_code, 401)
        resp = self.client.post("/api/turn/submit", json=body, headers={"X-Worker-Token": "s3cret"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual((self.ws / "w.txt").read_text(), "w")


class LoginGatingTests(TmpDirCase):
    def available(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return create_app(make_config(self.tmp)).test_client().get("/api/state").get_json()["google_login_available"]

    def test_login_disabled_without_stable_secret(self):
        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.available({"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"}))
        self.assertTrue(self.available({"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y", "FLASK_SECRET_KEY": "s"}))


class SavedKeysRouteTests(WebAppTests):
    env = {"UPSTASH_REDIS_REST_URL": "https://redis.example", "UPSTASH_REDIS_REST_TOKEN": "t",
           "KEY_ENCRYPTION_SECRET": "secret", "FLASK_SECRET_KEY": "s"}

    def sign_in(self):
        with self.client.session_transaction() as sess:
            sess["user"] = {"email": "a@b.c", "sub": "123"}

    def test_requires_sign_in_and_validates_values(self):
        self.assertEqual(self.client.post("/api/keys", json={"keys": {"anthropic": "k"}}).status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.post("/api/keys", json={"keys": {"anthropic": "x" * 301}}).status_code, 400)
        self.assertEqual(self.client.post("/api/keys", json={"keys": {"ollama": "http://x"}}).status_code, 400)
        self.assertEqual(self.client.post("/api/keys", json={"keys": ["a"]}).status_code, 400)

    def test_keystore_failure_is_explained_in_turn_errors(self):
        self.sign_in()
        with mock.patch.object(keystore, "get_keys", side_effect=keystore.KeystoreError("Upstash unreachable")):
            with self.assertLogs(level="WARNING"):
                resp = self.client.post("/api/turn", json={"agent": "architect"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("saved keys couldn't be loaded: Upstash unreachable", resp.get_json()["error"])


# --------------------------------------------------------------------------- #
# Keystore (fake Upstash)
# --------------------------------------------------------------------------- #
class FakeRedis:
    def __init__(self):
        self.data = {}

    def __call__(self, req, timeout=None):
        cmd = json.loads(req.data)
        if cmd[0] == "GET":
            return FakeResp({"result": self.data.get(cmd[1])})
        if cmd[0] == "SET":
            self.data[cmd[1]] = cmd[2]
            return FakeResp({"result": "OK"})
        raise AssertionError(cmd)


class KeystoreTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        for p in (
            mock.patch("urllib.request.urlopen", self.redis),
            mock.patch.dict(os.environ, {"UPSTASH_REDIS_REST_URL": "https://r", "UPSTASH_REDIS_REST_TOKEN": "t",
                                         "KEY_ENCRYPTION_SECRET": "one"}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_round_trip_is_encrypted_at_rest(self):
        keystore.set_keys("u1", {"anthropic": "sk-ant-secret", "evil": "x"})
        self.assertNotIn("sk-ant-secret", json.dumps(self.redis.data))
        self.assertEqual(keystore.get_keys("u1"), {"anthropic": "sk-ant-secret"})
        self.assertEqual(keystore.get_keys("nobody"), {})

    def test_rotated_secret_raises_and_old_ciphertext_is_backed_up(self):
        keystore.set_keys("u1", {"anthropic": "old-key"})
        old_blob = self.redis.data["agentbridge:keys:u1"]
        with mock.patch.dict(os.environ, {"KEY_ENCRYPTION_SECRET": "two"}):
            with self.assertLogs(level="ERROR"), self.assertRaises(keystore.KeystoreError):
                keystore.get_keys("u1")
            with self.assertLogs(level="ERROR"):
                keystore.set_keys("u1", {"anthropic": "new-key"})
            self.assertEqual(keystore.get_keys("u1"), {"anthropic": "new-key"})
        backups = [k for k in self.redis.data if ":undecryptable:" in k]
        self.assertEqual([self.redis.data[k] for k in backups], [old_blob])

    def test_upstash_failures_raise(self):
        cases = [
            lambda req, timeout=None: FakeResp({"error": "WRONGPASS"}),
            mock.Mock(side_effect=TimeoutError()),
            mock.Mock(side_effect=http.client.RemoteDisconnected("x")),
            lambda req, timeout=None: FakeResp(b"<html>"),
        ]
        for effect in cases:
            with mock.patch("urllib.request.urlopen", effect), self.assertRaises(keystore.KeystoreError):
                keystore.get_keys("u1")


# --------------------------------------------------------------------------- #
# Providers, worker, auth, config
# --------------------------------------------------------------------------- #
class ProviderTests(unittest.TestCase):
    def call(self, effect, provider=anthropic_provider):
        with mock.patch("urllib.request.urlopen", effect):
            return provider.chat("m", "s", [{"role": "user", "content": "u"}], credential="k")

    def test_transport_failures_become_provider_errors(self):
        for effect in (mock.Mock(side_effect=TimeoutError()), mock.Mock(side_effect=http.client.RemoteDisconnected("x")),
                       mock.Mock(side_effect=http_error(529, b"overloaded")), lambda *a, **k: FakeResp(b"<html>504</html>")):
            with self.assertRaises(ProviderError):
                self.call(effect)

    def test_anthropic_stop_reasons(self):
        with self.assertRaisesRegex(ProviderError, "cut off"):
            self.call(lambda *a, **k: FakeResp({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}))
        with self.assertRaisesRegex(ProviderError, "declined"):
            self.call(lambda *a, **k: FakeResp({"stop_reason": "refusal", "content": []}))
        reply = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "{}"}]}
        self.assertEqual(self.call(lambda *a, **k: FakeResp(reply)), "{}")

    def test_anthropic_request_uses_raised_output_limit(self):
        seen = {}

        def capture(req, timeout=None):
            seen.update(json.loads(req.data))
            return FakeResp({"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]})

        self.call(capture)
        self.assertEqual(seen["max_tokens"], anthropic_provider.MAX_TOKENS)
        self.assertGreaterEqual(seen["max_tokens"], 16000)

    def test_openai_null_content_and_length(self):
        with self.assertRaisesRegex(ProviderError, "no content"):
            self.call(lambda *a, **k: FakeResp({"choices": [{"finish_reason": "stop", "message": {"content": None}}]}), openai_provider)
        with self.assertRaisesRegex(ProviderError, "cut off"):
            self.call(lambda *a, **k: FakeResp({"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}), openai_provider)


class WorkerTests(unittest.TestCase):
    def test_agent_name_is_url_encoded(self):
        seen = []

        def server(req, timeout=None):
            seen.append(req if isinstance(req, str) else req.full_url)
            return FakeResp({"ok": False, "error": "nope"})

        with mock.patch("urllib.request.urlopen", server), self.assertRaises(worker.WorkerError):
            worker.run_one_turn("http://h", "my agent&x=1")
        self.assertEqual(seen[0], "http://h/api/turn/prepare?agent=my+agent%26x%3D1")

    def test_non_json_and_timeouts_are_worker_errors(self):
        for effect in (lambda *a, **k: FakeResp(b"<html>login</html>"), mock.Mock(side_effect=TimeoutError())):
            with mock.patch("urllib.request.urlopen", effect), self.assertRaises(worker.WorkerError):
                worker._get("http://h/api/state")

    def test_full_turn_against_fake_server(self):
        posted = {}

        def server(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if "/prepare" in url:
                return FakeResp({"ok": True, "agent": {"name": "architect", "provider": "anthropic", "model": "m"},
                                 "system": "s", "user_prompt": "u"})
            posted.update(json.loads(req.data))
            return FakeResp({"ok": True, "entry": {"id": 1}})

        with mock.patch("urllib.request.urlopen", server), \
             mock.patch("agentbridge.worker.chat", return_value='{"message": "done", "files": []}'), \
             mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            self.assertEqual(worker.run_one_turn("http://h", "architect"), {"id": 1})
        self.assertEqual(posted["message"], "done")


class AuthTests(unittest.TestCase):
    def exchange(self, claims):
        responses = iter([FakeResp({"id_token": "tok"}), FakeResp(claims)])
        with mock.patch("urllib.request.urlopen", lambda *a, **k: next(responses)), \
             mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "cs"}):
            return auth.exchange_code("code", "https://x/cb")

    def test_valid_token(self):
        claims = {"aud": "cid", "iss": "https://accounts.google.com", "email_verified": "true", "email": "a@b.c", "sub": "1"}
        self.assertEqual(self.exchange(claims), {"email": "a@b.c", "sub": "1"})

    def test_rejects_wrong_audience_issuer_or_unverified_email(self):
        good = {"aud": "cid", "iss": "accounts.google.com", "email_verified": "true", "email": "a@b.c", "sub": "1"}
        for change in ({"aud": "other"}, {"iss": "evil.example"}, {"email_verified": "false"}):
            with self.assertRaises(auth.AuthError, msg=change):
                self.exchange({**good, **change})

    def test_network_failure_is_auth_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "cs"}):
            with self.assertRaises(auth.AuthError):
                auth.exchange_code("code", "https://x/cb")


class ConfigTests(TmpDirCase):
    def load(self, text):
        path = self.tmp / "c.yaml"
        path.write_text(text)
        return config.load_config(path)

    AGENTS = "agents:\n  - {name: a, provider: anthropic, model: m, role: r}\n"

    def test_quoted_false_is_false_and_empty_settings_ok(self):
        self.assertFalse(self.load(self.AGENTS + 'settings:\n  require_client_keys: "false"\n').settings.require_client_keys)
        self.assertTrue(self.load(self.AGENTS + "settings:\n  require_client_keys: true\n").settings.require_client_keys)
        self.assertEqual(self.load(self.AGENTS + "settings:\n").settings.max_turns, 20)

    def test_invalid_bool_is_an_error(self):
        with self.assertRaises(ValueError):
            self.load(self.AGENTS + "settings:\n  require_client_keys: maybe\n")

    def test_committed_configs_load(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("config.example.yaml", "config.deploy.yaml", "config.vercel.yaml"):
            self.assertTrue(config.load_config(root / name).agents, name)


if __name__ == "__main__":
    unittest.main()
