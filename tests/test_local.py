"""Real local persistence tests and isolated AI contract tests; no cloud or model calls."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from app import ChatApp, ChatError
from local_support import LocalStore, Workflow
from ollama_agent import OllamaAgent, OllamaError


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = LocalStore(self.temp.name)
        self.workflow = Workflow(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_files_created_until_a_tool_runs(self):
        self.assertFalse(self.store.path.exists())

    def test_orders_are_customer_scoped(self):
        a = self.store.run("list_customer_orders", {}, "CUST-001")
        b = self.store.run("list_customer_orders", {}, "CUST-002")
        self.assertEqual(len(a["orders"]), 3)
        self.assertEqual(len(b["orders"]), 1)
        self.assertTrue(self.store.run("check_order", {"order_id": "ORDER-1001"}, "CUST-002").get("error"))

    def test_sql_like_ids_are_plain_data(self):
        result = self.store.run("check_order", {"order_id": "' OR 1=1 --"}, "CUST-001")
        self.assertIn("error", result)
        self.assertEqual(len(self.store.run("list_customer_orders", {}, "CUST-001")["orders"]), 3)

    def test_return_eligibility_and_local_persistence(self):
        for order, eligible in (("ORDER-1001", True), ("ORDER-1002", True), ("ORDER-1003", False)):
            self.assertEqual(self.store.run("check_return", {"order_id": order}, "CUST-001")["eligible"], eligible)
        args = {"order_id": "ORDER-1001", "reason": "Synthetic fit issue"}
        first = self.store.run("create_return", args, "CUST-001")
        second = self.store.run("create_return", args, "CUST-001")
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertTrue(second["already_exists"])
        self.assertFalse(first["money_moved"])
        self.assertEqual(len(LocalStore(self.temp.name).run("list_local_records", {}, "CUST-001")["returns"]), 1)

    def test_ineligible_or_reasonless_return_does_not_write(self):
        result = self.store.run("create_return", {"order_id": "ORDER-1003", "reason": "Test reason"}, "CUST-001")
        self.assertFalse(result["eligible"])
        self.assertIn("error", self.store.run("create_return", {"order_id": "ORDER-1001"}, "CUST-001"))
        self.assertEqual(self.store.run("list_local_records", {}, "CUST-001")["returns"], [])

    def test_bug_report_collects_details_and_really_saves(self):
        state = {}
        first = self.workflow.respond("support-flow", "Checkout returns a 500 error", "CUST-001", state)
        self.assertEqual(first["state"], "input_required")
        second = self.workflow.respond("support-flow", "Open checkout and click Pay", "CUST-001", state)
        self.assertEqual(second["state"], "input_required")
        third = self.workflow.respond("support-flow", "Safari on macOS", "CUST-001", state)
        self.assertIn("Saved BUG-", third["reply"])
        self.assertEqual(third["events"][0]["tool"], "create_bug_report")
        records = self.store.run("list_local_records", {}, "CUST-001")
        self.assertEqual(len(records["tickets"]), 1)
        self.assertEqual(records["tickets"][0]["description"], "Checkout returns a 500 error")
        self.assertEqual(self.store.run("list_local_records", {}, "CUST-002")["tickets"], [])

    def test_cancel_clears_pending_without_writing(self):
        state = {}
        self.workflow.respond("support-flow", "The demo app has a bug", "CUST-001", state)
        self.workflow.respond("support-flow", "cancel", "CUST-001", state)
        self.assertEqual(state, {})
        self.assertFalse(self.store.path.exists())

    def test_return_workflow_can_list_orders_while_pending(self):
        state = {}
        self.workflow.respond("novamart", "I want a return", "CUST-001", state)
        listing = self.workflow.respond("novamart", "List my orders", "CUST-001", state)
        self.assertIn("ORDER-1001", listing["reply"])
        checked = self.workflow.respond("novamart", "ORDER-1001", "CUST-001", state)
        self.assertIn("Reply with a reason", checked["reply"])
        result = self.workflow.respond("novamart", "The size is wrong", "CUST-001", state)
        self.assertIn("Saved local return", result["reply"])

    def test_default_workflow_discloses_no_ai(self):
        result = self.workflow.respond("novamart", "Who are you?", "CUST-001", {})
        self.assertIn("not an AI model", result["reply"])
        self.assertEqual(result["agent"], "Rules-based local workflow (no LLM)")

    def test_local_app_works_without_boto_or_aws_settings(self):
        app = ChatApp({"LOCAL_DATA_DIR": self.temp.name}, sdk_available=False)
        self.assertTrue(app.status()["modes"]["local"]["ready"])
        self.assertFalse(app.status()["modes"]["ollama"]["ready"])
        body = {"project": "novamart", "backend": "local", "session_id": str(uuid.uuid4()), "message": "List my orders"}
        response = app.chat(body)
        self.assertEqual(response["source"], "local")
        self.assertIn("ORDER-1001", response["reply"])
        self.assertEqual(response["events"][0]["tool"], "list_customer_orders")

    def test_local_ai_is_explicitly_unavailable_without_model(self):
        app = ChatApp({"LOCAL_DATA_DIR": self.temp.name}, sdk_available=False)
        with self.assertRaises(ChatError) as caught:
            app.chat({"project": "novamart", "backend": "ollama", "session_id": str(uuid.uuid4()), "message": "Hello"})
        self.assertEqual(caught.exception.status, 503)
        self.assertFalse(self.store.path.exists())

    def test_optional_ai_calls_real_local_tool_through_fake_model_contract(self):
        requests = []
        replies = iter([
            {"message": {"tool_calls": [{"function": {"name": "get_customer_tier", "arguments": {}}}]}},
            {"message": {"content": "Test fixture: the tool says Premium."}},
        ])
        def transport(payload):
            requests.append(json.loads(json.dumps(payload)))
            return next(replies)
        agent = OllamaAgent("test-model", self.store, transport)
        history = []
        result = agent.respond("novamart", "What is my tier?", "CUST-001", history)
        self.assertEqual(result["events"][0]["result"]["tier"], "Premium")
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertIn("Premium", requests[1]["messages"][-1]["content"])
        self.assertEqual(result["agent"], "Local AI · test-model")
        self.assertEqual(history[-1]["content"], "Test fixture: the tool says Premium.")

    def test_optional_ai_rejects_unknown_tool_and_hides_thinking(self):
        replies = iter([
            {"message": {"tool_calls": [{"function": {"name": "execute_shell", "arguments": {"command": "invalid"}}}]}},
            {"message": {"thinking": "hidden", "content": "<think>also hidden</think>Visible fixture"}},
        ])
        result = OllamaAgent("test-model", self.store, lambda _: next(replies)).respond("novamart", "Test", "CUST-001", [])
        self.assertEqual(result["reply"], "Visible fixture")
        self.assertIn("not allowed", result["events"][0]["result"]["error"])
        self.assertFalse(self.store.path.exists())

    def test_optional_ai_has_no_rules_based_fallback(self):
        with self.assertRaises(OllamaError):
            OllamaAgent("test-model", self.store, lambda _: {"message": {"content": ""}}).respond("novamart", "Test", "CUST-001", [])


if __name__ == "__main__":
    unittest.main()
