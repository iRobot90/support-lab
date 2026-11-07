"""Local-only chat bridge to existing AWS projects. No simulated agent responses."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid
from local_support import LocalStore, Workflow
from ollama_agent import OllamaAgent, OllamaError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC = Path(__file__).with_name("static")
MAX_BODY = 24_000
MAX_REPLY = 1_000_000
PROVIDERS = {"novamart": "NovaMart", "support-flow": "Customer support flow"}


class ChatError(Exception):
    def __init__(self, message, status=400, uncertain=False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


@dataclass
class Session:
    customer: str
    runtime_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str | None = None
    input_node: str | None = None
    touched: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)
    uncertain: bool = False
    workflow: dict = field(default_factory=dict)
    history: list = field(default_factory=list)


class ChatApp:
    def __init__(self, env=None, client_factory=None, sdk_available=None):
        self.env = dict(os.environ if env is None else env)
        self.sdk_available = (importlib.util.find_spec("boto3") is not None
                              if sdk_available is None else sdk_available)
        self.client_factory = client_factory or self.aws_client
        self.token = secrets.token_urlsafe(32)
        self.sessions = {}
        self.session_lock = threading.Lock()
        # Bound in-flight AWS work across tabs. This is not a public rate limiter.
        self.capacity = threading.BoundedSemaphore(2)
        self.local_store = LocalStore(self.env.get("LOCAL_DATA_DIR") or Path(__file__).with_name("local-data"))
        self.workflow = Workflow(self.local_store)

    def status(self):
        providers = {}
        required = {
            "novamart": ["AGENTCORE_RUNTIME_ARN"],
            "support-flow": ["BEDROCK_FLOW_ID", "BEDROCK_FLOW_ALIAS_ID", "BEDROCK_FLOW_INPUT_NODE"],
        }
        for key, fields in required.items():
            missing = [name for name in fields if not self.env.get(name, "").strip()]
            if not self.sdk_available:
                missing.append("boto3 (optional AWS dependency)")
            if self.env.get("ENABLE_LIVE_AWS") != "1":
                missing.append("ENABLE_LIVE_AWS=1")
            providers[key] = {"name": PROVIDERS[key], "ready": not missing, "missing": missing}
        # Do not resolve credentials, contact AWS, or expose resource IDs here.
        return {"providers": providers, "modes": {
                    "local": {"ready": True, "missing": [], "agent": "Rules-based local workflow (no LLM)"},
                    "ollama": {"ready": bool(self.env.get("OLLAMA_MODEL")), "missing": [] if self.env.get("OLLAMA_MODEL") else ["Install Ollama and a local model, then set OLLAMA_MODEL"], "agent": "Local AI · " + self.env.get("OLLAMA_MODEL", "model not configured")}}, "csrf_token": self.token,
                "notice": "Local configuration only. AWS access has not been verified."}

    def aws_client(self, service):
        import boto3
        from botocore.config import Config
        session = boto3.Session(
            profile_name=self.env.get("AWS_PROFILE") or None,
            region_name=self.env.get("AWS_REGION", "us-east-1"),
        )
        return session.client(service, config=Config(
            connect_timeout=10, read_timeout=120,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ))

    def get_session(self, provider, session_id, customer):
        key = (provider, session_id)
        now = time.monotonic()
        with self.session_lock:
            expired = [k for k, s in self.sessions.items()
                       if now - s.touched > 3600 and not s.lock.locked()]
            for k in expired:
                del self.sessions[k]
            if key not in self.sessions:
                if len(self.sessions) >= 100:
                    raise ChatError("Session limit reached. Restart the local server.", 429)
                self.sessions[key] = Session(customer)
            session = self.sessions[key]
            if session.customer != customer:
                raise ChatError("Start a new chat before changing the demo customer.", 409)
            session.touched = now
            return session

    def chat(self, body):
        if not isinstance(body, dict):
            raise ChatError("The request must be a JSON object.")
        provider = body.get("project")
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ChatError("Choose a supported project.")
        backend = body.get("backend", "aws")
        if backend not in ("local", "ollama", "aws"):
            raise ChatError("Choose a supported backend.")
        message = body.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ChatError("Write a message between 1 and 4,000 characters.")
        session_id = body.get("session_id", "")
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", session_id):
            raise ChatError("Start a new chat to create a valid session.")
        try:
            uuid.UUID(session_id)
        except ValueError as exc:
            raise ChatError("Invalid session identifier.") from exc
        customer = body.get("customer_id", "CUST-001")
        if not isinstance(customer, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", customer):
            raise ChatError("Use a synthetic customer ID with letters, digits, hyphens or underscores.")
        if backend == "aws" and not self.status()["providers"][provider]["ready"]:
            raise ChatError("AWS is not configured. Open Connection details for the missing settings.", 503)
        if backend == "ollama" and not self.env.get("OLLAMA_MODEL"):
            raise ChatError("Local AI needs Ollama and an installed model. The local workflow works without them.", 503)
        session = self.get_session(provider if backend == "aws" else backend + ":" + provider, session_id, customer)
        if not session.lock.acquire(blocking=False):
            raise ChatError("This chat already has a request in progress.", 409)
        acquired = False
        try:
            if session.uncertain:
                raise ChatError("The previous outcome is unconfirmed. Check the backend records before starting a new chat.", 409, True)
            acquired = self.capacity.acquire(blocking=False)
            if not acquired:
                raise ChatError("Two requests are already running. Try again when they finish.", 429)
            start = time.monotonic()
            try:
                if backend == "local":
                    result = self.workflow.respond(provider, message.strip(), customer, session.workflow)
                elif backend == "ollama":
                    result = OllamaAgent(self.env["OLLAMA_MODEL"], self.local_store).respond(provider, message.strip(), customer, session.history)
                else:
                    result = (self.invoke_novamart(message.strip(), customer, session)
                              if provider == "novamart" else self.invoke_flow(message.strip(), session))
            except OllamaError as exc:
                session.uncertain = True
                raise ChatError(str(exc) + " Check saved local records before repeating any action.", 503, True) from exc
            except ChatError as exc:
                session.uncertain = exc.uncertain
                raise
            except Exception as exc:
                # Do not reveal AWS exception strings: they may contain resource IDs or user text.
                name = type(exc).__name__
                if name in {"NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
                            "SSOTokenLoadError", "UnauthorizedSSOTokenError"}:
                    raise ChatError("AWS sign-in is missing or expired. Sign in locally, then retry.", 503) from exc
                session.uncertain = True
                raise ChatError("AWS did not return a confirmed result. Check permissions, configuration and AWS logs before repeating the request." if backend == "aws" else "The local backend did not return a confirmed result. Check saved local records before repeating the request.", 502, True) from exc
            result.update({"provider": provider, "source": backend, "elapsed_ms": round((time.monotonic() - start) * 1000)})
            return result
        finally:
            session.touched = time.monotonic()
            if acquired:
                self.capacity.release()
            session.lock.release()

    def invoke_novamart(self, message, customer, session):
        client = self.client_factory("bedrock-agentcore")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=self.env["AGENTCORE_RUNTIME_ARN"],
            runtimeSessionId=session.runtime_id,
            contentType="application/json", accept="application/json",
            payload=json.dumps({"prompt": message, "customer_id": customer,
                                "session_id": session.runtime_id}).encode("utf-8"),
        )
        stream = response["response"]
        try:
            raw = stream.read(MAX_REPLY + 1)
        finally:
            stream.close()
        if len(raw) > MAX_REPLY:
            raise ChatError("The runtime response exceeded the local display limit.", 502, True)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ChatError("The runtime did not return the expected JSON response.", 502, True) from exc
        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("result"), str) or not data["result"].strip():
            raise ChatError("The runtime did not return a final result. Check its response contract and logs.", 502, True)
        return {"reply": data["result"], "state": "complete"}

    def invoke_flow(self, message, session):
        client = self.client_factory("bedrock-agent-runtime")
        item = {"content": {"document": message}}
        if session.execution_id and session.input_node:
            item.update({"nodeName": session.input_node, "nodeInputName": "agentInputText"})
        else:
            item.update({"nodeName": self.env["BEDROCK_FLOW_INPUT_NODE"], "nodeOutputName": "document"})
        params = {"flowIdentifier": self.env["BEDROCK_FLOW_ID"],
                  "flowAliasIdentifier": self.env["BEDROCK_FLOW_ALIAS_ID"],
                  "inputs": [item], "enableTrace": False}
        if session.execution_id:
            params["executionId"] = session.execution_id
        response = client.invoke_flow(**params)
        stream = response["responseStream"]
        parts, next_node, complete = [], None, None
        size = 0
        try:
            for event in stream:
                if any(key.endswith("Exception") for key in event):
                    raise ChatError("The flow returned an AWS error. Check its execution before repeating.", 502, True)
                for name in ("flowOutputEvent", "flowMultiTurnInputRequestEvent"):
                    if name in event:
                        value = event[name].get("content", {}).get("document")
                        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                        size += len(text)
                        if size > MAX_REPLY:
                            raise ChatError("The flow response exceeded the local display limit.", 502, True)
                        parts.append(text)
                        if name == "flowMultiTurnInputRequestEvent":
                            next_node = event[name].get("nodeName")
                if "flowCompletionEvent" in event:
                    complete = event["flowCompletionEvent"].get("completionReason")
        finally:
            stream.close()
        if complete not in {"SUCCESS", "INPUT_REQUIRED"} or not parts:
            raise ChatError("The flow ended without a confirmed output. Check the AWS execution.", 502, True)
        if complete == "INPUT_REQUIRED":
            if not next_node or not response.get("executionId"):
                raise ChatError("The flow requested input without valid continuation details.", 502, True)
            session.execution_id, session.input_node = response["executionId"], next_node
        else:
            session.execution_id = session.input_node = None
        return {"reply": "\n\n".join(parts), "state": "input_required" if complete == "INPUT_REQUIRED" else "complete"}


class ChatServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, ChatHandler)


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "LocalChat/1"
    sys_version = ""

    def log_message(self, *_):
        pass  # Never log prompts, customer IDs, authorization headers, or AWS responses.

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def same_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_bytes(self, status, content, mime="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    def json_response(self, status, data):
        self.send_bytes(status, json.dumps(data).encode("utf-8"))

    def do_GET(self):
        if (not self.same_host() or (self.headers.get("Sec-Fetch-Site") == "cross-site"
                and not (self.path == "/" and self.headers.get("Sec-Fetch-Mode") == "navigate"))):
            return self.json_response(403, {"error": "Open this app directly on localhost."})
        if self.path == "/api/status":
            return self.json_response(200, self.server.app.status())
        routes = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if self.path not in routes:
            return self.json_response(404, {"error": "Not found."})
        path, mime = routes[self.path]
        self.send_bytes(200, (STATIC / path).read_bytes(), mime)

    def do_POST(self):
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        token = self.headers.get("X-Chat-Token", "")
        if (not self.same_host() or origin != f"http://{host}"
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or not secrets.compare_digest(token, self.server.app.token)):
            return self.json_response(403, {"error": "Reload the local app and try again."})
        if self.path != "/api/chat":
            return self.json_response(404, {"error": "Not found."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json_response(415, {"error": "JSON is required."})
        if self.headers.get("Transfer-Encoding"):
            return self.json_response(400, {"error": "Chunked requests are not supported."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                return self.json_response(413, {"error": "Request size is invalid."})
            body = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError, TimeoutError):
            return self.json_response(400, {"error": "Invalid JSON request."})
        try:
            self.json_response(200, self.server.app.chat(body))
        except ChatError as exc:
            self.json_response(exc.status, {"error": str(exc), "uncertain": exc.uncertain})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    app = ChatApp()
    with ChatServer(("127.0.0.1", args.port), app) as server:
        print(f"Local chat: http://127.0.0.1:{args.port}", flush=True)
        print("Local workflow is ready. AWS and local AI are optional, separately configured backends.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
