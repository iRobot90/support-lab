"""Independent local support tools and deterministic workflow. Synthetic data only."""
from __future__ import annotations
from datetime import date, timedelta
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

POLICIES = {
    "returns": "Demo return policy: delivered orders can be returned within 30 days of the order date for Standard customers, or 60 days for Premium customers. A reason is required. A local request does not move money or arrange shipping.",
    "shipping": "Demo shipping policy: Standard shipping takes 3–5 business days after dispatch. Premium shipping takes 1–2 business days. These are fictional service targets, not tracking estimates.",
    "warranty": "Demo warranty policy: manufacturing defects are covered for 12 months. Accidental damage is excluded. This local application cannot approve a warranty claim or contact a seller.",
}


class LocalStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "support.sqlite3"
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY,tier TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,customer_id TEXT,item TEXT,status TEXT,order_date TEXT,amount_cents INTEGER);
                CREATE TABLE IF NOT EXISTS returns(id TEXT PRIMARY KEY,customer_id TEXT,order_id TEXT,reason TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(customer_id,order_id));
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,customer_id TEXT,description TEXT,steps TEXT,environment TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            conn.executemany("INSERT OR IGNORE INTO customers VALUES (?,?)", [("CUST-001", "Premium"), ("CUST-002", "Standard")])
            today = date.today()
            rows = [("ORDER-1001", "CUST-001", "Wireless headphones", "delivered", 7, 5900),
                    ("ORDER-1002", "CUST-001", "Desk lamp", "delivered", 45, 3250),
                    ("ORDER-1003", "CUST-001", "Travel backpack", "delivered", 72, 7900),
                    ("ORDER-2001", "CUST-002", "Notebook set", "shipped", 3, 1800)]
            conn.executemany("INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?)", [(*r[:4], (today-timedelta(days=r[4])).isoformat(), r[5]) for r in rows])
            conn.commit()
            return conn

    def run(self, tool, args, customer):
        if not isinstance(args, dict):
            return {"error": "Tool arguments must be an object."}
        try:
            if tool == "search_policies":
                query = str(args.get("query", "")).lower()
                matched = [k for k in POLICIES if k.rstrip("s") in query or (k == "returns" and "refund" in query)]
                return {"policies": {k: POLICIES[k] for k in matched or POLICIES}, "synthetic": True}
            with closing(self.connect()) as conn, conn:
                user = conn.execute("SELECT tier FROM customers WHERE id=?", (customer,)).fetchone()
                if not user:
                    return {"error": "Unknown demo customer. Use CUST-001 (Premium) or CUST-002 (Standard)."}
                if tool == "get_customer_tier":
                    return {"customer_id": customer, "tier": user["tier"], "synthetic": True}
                if tool == "list_customer_orders":
                    return {"orders": [dict(r) for r in conn.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY id", (customer,))], "synthetic": True}
                if tool in {"check_order", "check_return", "create_return"}:
                    order_id = args.get("order_id", "")
                    if not isinstance(order_id, str):
                        return {"error": "Provide an order ID."}
                    order = conn.execute("SELECT * FROM orders WHERE id=? AND customer_id=?", (order_id.upper(), customer)).fetchone()
                    if not order:
                        return {"error": "No matching order belongs to this demo customer."}
                    result = dict(order)
                    result["synthetic"] = True
                    if tool == "check_order":
                        return result
                    days = (date.today() - date.fromisoformat(order["order_date"])).days
                    window = 60 if user["tier"] == "Premium" else 30
                    eligible = order["status"] == "delivered" and 0 <= days <= window
                    result.update({"eligible": eligible, "days_since_order": days, "return_window_days": window})
                    if tool == "check_return" or not eligible:
                        return result
                    reason = args.get("reason", "")
                    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 400:
                        return {"error": "Provide a return reason between 3 and 400 characters."}
                    existing = conn.execute("SELECT id FROM returns WHERE customer_id=? AND order_id=?", (customer, order["id"])).fetchone()
                    if existing:
                        return {"request_id": existing["id"], "already_exists": True, "local_only": True, "money_moved": False}
                    request_id = "RET-" + uuid.uuid4().hex[:8].upper()
                    conn.execute("INSERT INTO returns(id,customer_id,order_id,reason) VALUES (?,?,?,?)", (request_id, customer, order["id"], reason.strip()))
                    return {"request_id": request_id, "order_id": order["id"], "local_only": True, "money_moved": False}
                if tool == "create_bug_report":
                    keys = ("description", "steps", "environment")
                    if any(not isinstance(args.get(k), str) or not 3 <= len(args[k].strip()) <= 2000 for k in keys):
                        return {"error": "Description, reproduction steps and environment must each contain 3–2,000 characters."}
                    ticket_id = "BUG-" + uuid.uuid4().hex[:8].upper()
                    conn.execute("INSERT INTO tickets(id,customer_id,description,steps,environment) VALUES (?,?,?,?,?)", (ticket_id, customer, *(args[k].strip() for k in keys)))
                    return {"ticket_id": ticket_id, "saved": True, "local_only": True, "not_sent_to_a_company": True}
                if tool == "list_local_records":
                    return {"tickets": [dict(r) for r in conn.execute("SELECT id,description,created_at FROM tickets WHERE customer_id=? ORDER BY created_at DESC LIMIT 10", (customer,))],
                            "returns": [dict(r) for r in conn.execute("SELECT id,order_id,reason FROM returns WHERE customer_id=? ORDER BY created_at DESC LIMIT 10", (customer,))], "local_only": True}
                return {"error": "Unknown tool. No action was taken."}
        except (sqlite3.Error, ValueError):
            return {"error": "The local data operation failed. Inspect the local database before repeating a write."}


class Workflow:
    def __init__(self, store):
        self.store = store

    def respond(self, project, message, customer, memory):
        events = []
        def call(name, **args):
            result = self.store.run(name, args, customer)
            events.append({"tool": name, "result": result})
            return result
        def answer(text, state="complete"):
            return {"reply": text, "state": state, "events": events, "agent": "Rules-based local workflow (no LLM)"}
        text = message.lower().strip()
        if text in {"cancel", "reset", "start over", "stop"}:
            memory.clear()
            return answer("Stopped the pending local workflow. Nothing was sent to a company or AWS.")
        if "saved" in text or "my tickets" in text or "local records" in text:
            result = call("list_local_records")
            if result.get("error"):
                return answer(result["error"])
            lines = [f"Ticket {r['id']}: {r['description']}" for r in result["tickets"]] + [f"Return {r['id']}: {r['order_id']} ({r['reason']})" for r in result["returns"]]
            return answer("Local records:\n" + "\n".join(lines) if lines else "No tickets or return requests have been saved for this demo customer yet.")
        pending = memory.get("pending")
        if pending == "bug":
            current = memory["bug"]
            if len(message.strip()) < 3:
                return answer("Please provide a little more detail, or type cancel.", "input_required")
            for key in ("description", "steps", "environment"):
                if not current.get(key):
                    current[key] = message.strip()
                    break
            if not current.get("steps"):
                return answer("What steps reproduce the issue? I will save the completed report only in this local demo.", "input_required")
            if not current.get("environment"):
                return answer("Which browser, operating system or app version did you use?", "input_required")
            result = call("create_bug_report", **current)
            memory.clear()
            if result.get("error"):
                return answer(result["error"])
            return answer(f"Saved {result['ticket_id']} in the local SQLite database.\n\nDescription: {current['description']}\nSteps: {current['steps']}\nEnvironment: {current['environment']}\n\nThis is a real local record using demo data. No ticket was sent to an external support team.")
        if pending == "return_reason":
            result = call("create_return", order_id=memory["order_id"], reason=message.strip())
            memory.clear()
            if result.get("error"):
                return answer(result["error"])
            if result.get("request_id"):
                prefix = "Already saved" if result.get("already_exists") else "Saved"
                return answer(f"{prefix} local return request {result['request_id']}. No payment was refunded, seller contacted or shipment arranged.")
            return answer("This order is not eligible under the local demo policy. No request was created.")
        if project == "support-flow" and any(word in text for word in ("bug", "error", "broken", "crash", "doesn't work", "not working")):
            memory.update({"pending": "bug", "bug": {"description": message.strip() if len(message.strip()) > 12 else ""}})
            return answer("What steps reproduce the issue? I will save the completed report only in this local demo." if memory["bug"]["description"] else "Describe the issue you want to report. This workflow saves demo tickets locally.", "input_required")
        match = re.search(r"\b(?:ORDER|ORD)-\d+\b", message, re.I)
        if "policy" in text or "shipping" in text or "warranty" in text or ("return" in text and any(w in text for w in ("window", "how long", "how many days"))):
            result = call("search_policies", query=message)
            return answer("\n\n".join(result["policies"].values()))
        if "tier" in text or "premium" in text or "standard" in text:
            result = call("get_customer_tier")
            return answer(result.get("error") or f"{customer} is a {result['tier']} customer in the local synthetic dataset.")
        if (pending == "return_order" and "orders" not in text) or ("return" in text or "refund" in text):
            if not match:
                memory.update({"pending": "return_order"})
                return answer("Which demo order would you like to check? Try ORDER-1001, or ask to list your orders. No money will be moved.", "input_required")
            result = call("check_return", order_id=match.group())
            memory.clear()
            if result.get("error"):
                return answer(result["error"])
            if not result["eligible"]:
                return answer(f"{result['id']} is not eligible: status is {result['status']}, age is {result['days_since_order']} days and the return window is {result['return_window_days']} days. No request was created.")
            memory.update({"pending": "return_reason", "order_id": result["id"]})
            return answer(f"{result['id']} ({result['item']}) meets the {result['return_window_days']}-day demo policy. Reply with a reason to create a local demo return request, or type cancel.", "input_required")
        if "order" in text:
            if match:
                result = call("check_order", order_id=match.group())
                return answer(result.get("error") or f"{result['id']}: {result['item']} · {result['status']} · ordered {result['order_date']}. Synthetic demo record.")
            result = call("list_customer_orders")
            if result.get("error"):
                return answer(result["error"])
            return answer("Synthetic orders for " + customer + ":\n" + "\n".join(f"• {r['id']}: {r['item']} · {r['status']} · {r['order_date']}" for r in result["orders"]))
        return answer("I am the local rules-based support assistant, not an AI model. I can look up demo orders, check return eligibility, explain demo policies or collect a bug report in Support Flow. Ask for 'my saved records' to inspect local tickets and returns. For open-ended AI conversation, select Local AI after configuring Ollama.")


def tool_schemas(project):
    specs = [
        ("get_customer_tier", "Look up the current synthetic customer's tier.", {}),
        ("list_customer_orders", "List the current customer's synthetic orders. Use before inventing order IDs.", {}),
        ("check_order", "Look up an order belonging to the current customer.", {"order_id": "Order ID from the local dataset."}),
        ("check_return", "Check delivery status, ownership and return window using code, without creating a request.", {"order_id": "Order ID."}),
        ("create_return", "Save a LOCAL DEMO return request only when the user asks. No payment or external action occurs. Validates eligibility in code.", {"order_id": "Order ID.", "reason": "Reason the user provided."}),
        ("search_policies", "Retrieve fictional demo policy text about returns, shipping or warranty.", {"query": "Policy topic."}),
        ("list_local_records", "Read saved local tickets and return requests for this demo customer.", {}),
    ]
    if project == "support-flow":
        specs.append(("create_bug_report", "Save a LOCAL DEMO bug report after collecting all details from the user. Never invent missing details. No external ticket is submitted.", {"description": "What happened.", "steps": "User-provided reproduction steps.", "environment": "User-provided browser, OS or app version."}))
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": {key: {"type": "string", "description": desc} for key, desc in fields.items()}, "required": list(fields), "additionalProperties": False}}} for name, description, fields in specs]
