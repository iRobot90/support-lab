"""Offline tests only. Fake AWS clients verify adapters, not deployment health."""
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import Mock

from app import ChatApp, ChatError, ChatServer

ENV = {"ENABLE_LIVE_AWS": "1", "AGENTCORE_RUNTIME_ARN": "test-runtime-not-a-real-arn",
       "BEDROCK_FLOW_ID": "test-flow", "BEDROCK_FLOW_ALIAS_ID": "test-alias",
       "BEDROCK_FLOW_INPUT_NODE": "Start"}


class Events:
    def __init__(self, items):
        self.items, self.closed = items, False

    def __iter__(self):
        return iter(self.items)

    def close(self):
        self.closed = True


def request(project="novamart", session=None, **changes):
    data = {"project": project, "session_id": session or str(uuid.uuid4()),
            "message": "Test message", "customer_id": "CUST-001"}
    data.update(changes)
    return data


def flow_output(value="Test-only fixture", reason="SUCCESS", **extra):
    return {"executionId": "test-execution", "responseStream": Events([
        {"flowOutputEvent": {"content": {"document": value}}},
        {"flowCompletionEvent": {"completionReason": reason}},
    ]), **extra}


class AppTests(unittest.TestCase):
    def make_app(self):
        self.client = Mock()
        return ChatApp(ENV, lambda service: self.client, sdk_available=True)

    def test_status_never_touches_aws(self):
        factory = Mock(side_effect=AssertionError("No network permitted"))
        app = ChatApp({}, factory, sdk_available=False)
        status = app.status()
        self.assertFalse(status["providers"]["novamart"]["ready"])
        factory.assert_not_called()
        self.assertNotIn("AGENTCORE_RUNTIME_ARN", status.keys())

    def test_live_mode_requires_explicit_opt_in(self):
        env = {**ENV, "ENABLE_LIVE_AWS": "0"}
        app = ChatApp(env, sdk_available=True)
        with self.assertRaises(ChatError) as caught:
            app.chat(request())
        self.assertEqual(caught.exception.status, 503)

    def test_configured_is_not_verified(self):
        app = self.make_app()
        self.assertTrue(app.status()["providers"]["novamart"]["ready"])
        self.assertIn("not been verified", app.status()["notice"])
        self.assertNotIn(ENV["AGENTCORE_RUNTIME_ARN"], json.dumps(app.status()))

    def test_novamart_contract_and_session_continuity(self):
        app = self.make_app()
        streams = [io.BytesIO(b'{"result":"Test-only fixture"}') for _ in range(2)]
        self.client.invoke_agent_runtime.side_effect = [{"response": x} for x in streams]
        body = request()
        reply = app.chat(body)
        app.chat(body)
        calls = self.client.invoke_agent_runtime.call_args_list
        self.assertEqual(calls[0].kwargs["runtimeSessionId"], calls[1].kwargs["runtimeSessionId"])
        self.assertGreaterEqual(len(calls[0].kwargs["runtimeSessionId"]), 33)
        self.assertEqual(json.loads(calls[0].kwargs["payload"])["customer_id"], "CUST-001")
        self.assertEqual(reply["reply"], "Test-only fixture")
        self.assertEqual(reply["source"], "aws")
        self.assertTrue(all(x.closed for x in streams))

    def test_novamart_never_turns_malformed_result_into_answer(self):
        for payload in (b'bad json', b'[]', b'{"error":"private AWS detail"}', b'{"result":5}', b'{"result":""}'):
            with self.subTest(payload=payload):
                app = self.make_app()
                self.client.invoke_agent_runtime.return_value = {"response": io.BytesIO(payload)}
                with self.assertRaises(ChatError) as caught:
                    app.chat(request())
                self.assertTrue(caught.exception.uncertain)
                self.assertNotIn("private AWS detail", str(caught.exception))

    def test_flow_single_turn(self):
        app = self.make_app()
        output = flow_output({"ticket": "synthetic"})
        self.client.invoke_flow.return_value = output
        result = app.chat(request("support-flow"))
        self.assertIn('"ticket": "synthetic"', result["reply"])
        self.assertEqual(self.client.invoke_flow.call_args.kwargs["inputs"], [
            {"content": {"document": "Test message"}, "nodeName": "Start", "nodeOutputName": "document"}])
        self.assertTrue(output["responseStream"].closed)

    def test_flow_multi_turn_continues_then_resets(self):
        app = self.make_app()
        first = {"executionId": "test-execution", "responseStream": Events([
            {"flowMultiTurnInputRequestEvent": {"nodeName": "SupportAgent", "content": {"document": "Which browser?"}}},
            {"flowCompletionEvent": {"completionReason": "INPUT_REQUIRED"}},
        ])}
        self.client.invoke_flow.side_effect = [first, flow_output(), flow_output()]
        body = request("support-flow")
        self.assertEqual(app.chat(body)["state"], "input_required")
        body["message"] = "Test Browser"
        self.assertEqual(app.chat(body)["state"], "complete")
        second = self.client.invoke_flow.call_args.kwargs
        self.assertEqual(second["executionId"], "test-execution")
        self.assertEqual(second["inputs"][0]["nodeInputName"], "agentInputText")
        self.assertEqual(second["inputs"][0]["nodeName"], "SupportAgent")
        app.chat(body)
        self.assertNotIn("executionId", self.client.invoke_flow.call_args.kwargs)

    def test_flow_error_and_partial_stream_are_not_success(self):
        for events in ([{"accessDeniedException": {"message": "private"}}],
                       [{"flowOutputEvent": {"content": {"document": "partial"}}}],
                       [{"flowCompletionEvent": {"completionReason": "SUCCESS"}}]):
            app = self.make_app()
            self.client.invoke_flow.return_value = {"responseStream": Events(events)}
            with self.assertRaises(ChatError) as caught:
                app.chat(request("support-flow"))
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn("private", str(caught.exception))

    def test_flow_followup_requires_execution_context(self):
        app = self.make_app()
        self.client.invoke_flow.return_value = flow_output(reason="INPUT_REQUIRED")
        with self.assertRaises(ChatError):
            app.chat(request("support-flow"))

    def test_no_auto_retry_after_uncertain_failure(self):
        app = self.make_app()
        self.client.invoke_agent_runtime.side_effect = TimeoutError("private details")
        body = request()
        for _ in range(2):
            with self.assertRaises(ChatError) as caught:
                app.chat(body)
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn("private details", str(caught.exception))
        self.assertEqual(self.client.invoke_agent_runtime.call_count, 1)

    def test_missing_credentials_is_recoverable(self):
        app = self.make_app()
        NoCredentialsError = type("NoCredentialsError", (Exception,), {})
        self.client.invoke_agent_runtime.side_effect = NoCredentialsError()
        with self.assertRaises(ChatError) as caught:
            app.chat(request())
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(caught.exception.status, 503)

    def test_validation(self):
        app = self.make_app()
        bad = [None, [], request(project=[]), request(message=""), request(message="x" * 4001),
               request(message=3), request(session_id="bad"), request(customer_id="<script>")]
        for body in bad:
            with self.subTest(body=type(body)):
                with self.assertRaises(ChatError):
                    app.chat(body)
        self.client.invoke_agent_runtime.assert_not_called()

    def test_session_customer_is_fixed(self):
        app = self.make_app()
        self.client.invoke_agent_runtime.return_value = {"response": io.BytesIO(b'{"result":"Test"}')}
        body = request()
        app.chat(body)
        body["customer_id"] = "CUST-002"
        with self.assertRaises(ChatError) as caught:
            app.chat(body)
        self.assertEqual(caught.exception.status, 409)

    def test_busy_session_rejects_duplicate(self):
        app = self.make_app()
        body = request()
        session = app.get_session("novamart", body["session_id"], "CUST-001")
        session.lock.acquire()
        try:
            with self.assertRaises(ChatError) as caught:
                app.chat(body)
            self.assertEqual(caught.exception.status, 409)
        finally:
            session.lock.release()
        self.client.invoke_agent_runtime.assert_not_called()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ChatApp({}, sdk_available=False)
        cls.server = ChatServer(("127.0.0.1", 0), cls.app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def fetch(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, body, headers or {})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def headers(self, **extra):
        return {"Content-Type": "application/json", "Origin": self.base,
                "X-Chat-Token": self.app.token, **extra}

    def test_assets_and_security_headers(self):
        for path in ("/", "/styles.css", "/app.js", "/api/status"):
            code, headers, content = self.fetch(path)
            self.assertEqual(code, 200)
            self.assertGreater(len(content), 0)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_only_allowlisted_files_are_served(self):
        for path in ("/.env", "/app.py", "/../README.md", "/%2e%2e/app.py", "/.git/config"):
            self.assertEqual(self.fetch(path)[0], 404)

    def test_external_navigation_can_open_ui_but_not_read_status(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        self.assertEqual(self.fetch("/", headers=headers)[0], 200)
        self.assertEqual(self.fetch("/api/status", headers=headers)[0], 403)

    def test_bad_host_is_rejected(self):
        self.assertEqual(self.fetch("/api/status", headers={"Host": "attacker.example"})[0], 403)

    def test_cross_site_and_missing_token_rejected(self):
        for headers in ({"Content-Type": "application/json"}, self.headers(Origin="https://attacker.example"), self.headers(**{"Sec-Fetch-Site": "cross-site"})):
            self.assertEqual(self.fetch("/api/chat", b'{}', headers)[0], 403)

    def test_bad_json_and_content_type(self):
        self.assertEqual(self.fetch("/api/chat", b'{bad', self.headers())[0], 400)
        self.assertEqual(self.fetch("/api/chat", b'{}', self.headers(**{"Content-Type": "text/plain"}))[0], 415)

    def test_disconnected_chat_never_reports_success(self):
        code, _, body = self.fetch("/api/chat", json.dumps(request()).encode(), self.headers())
        self.assertEqual(code, 503)
        self.assertIn("not configured", json.loads(body)["error"])

    def test_oversize_request(self):
        self.assertEqual(self.fetch("/api/chat", b'x' * 25000, self.headers())[0], 413)


if __name__ == "__main__":
    unittest.main()
