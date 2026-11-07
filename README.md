# Support Lab

A browser chat portfolio demonstrating customer-support workflows inspired by two completed AWS coursework projects: NovaMart support and a customer-support flow. The independent local implementation works without the original AWS lab.

**Try it now:** the default **Local workflow** mode runs real Python tools, validates synthetic orders and saves demo tickets/returns in SQLite. It is explicitly **rules-based, not an AI model**. It is not a prerecorded transcript, and it is not a reproduction of the original AWS deployment.

**Where is the AI agent?** `ollama_agent.py` contains an optional, real on-device AI agent loop: a locally installed model can choose tools, read their results and compose replies. Select **Local AI** after installing and configuring Ollama yourself. No model is bundled, installed or claimed to be running. **Original AWS backend** mode retains real invocation adapters, but the deployment is not connected or verified. These modes never silently fall back to one another.

## Run the interface

Python 3.10 or newer is sufficient. No package install, account, key or cloud resource is needed for the local workflow.

```sh
python3 app.py
```

Open http://127.0.0.1:8765. Use `--port 8766` if that port is occupied. Stop with Ctrl+C. The server binds only to the local computer. Changing projects starts a new chat. Example buttons only fill the composer; they never send a request.

## Try the working local workflows

- **NovaMart:** ask “List my orders” or “What is my tier?” The default synthetic customer is `CUST-001` (Premium). `CUST-002` is Standard.
- Ask “Return ORDER-1001”, then give a reason. The Python tool verifies ownership, delivered status and the return window before saving a local request. No money moves.
- **Support Flow:** describe a bug, then provide reproduction steps and your demo environment. The workflow saves a real local ticket and returns its generated ID.
- Ask “Show my saved records” to read persisted local tickets and return requests. Expand the tool-result panel to inspect the actual returned data.
- Type “cancel” to abandon a pending local workflow. New chat clears in-memory conversation state, not stored records.

The database is `local-data/support.sqlite3`, excluded from Git. All initial orders are fictional and seeded relative to the date the database is first created. This application does not access real customer records. The policies are newly written demo examples, not an authoritative merchant policy. The rules-based router supports the examples above and has limited language understanding; an unexpected phrase may need rewording.

## Optional on-device AI, later

Model setup is optional and separate. When you choose to do it, use the [official Ollama installer](https://ollama.com/download), start Ollama locally, and download a tool-capable model such as [Qwen3 1.7B](https://ollama.com/library/qwen3:1.7b) (about 1.4 GB):

```sh
ollama pull qwen3:1.7b
export OLLAMA_MODEL=qwen3:1.7b
python3 app.py
```

Then select **Local AI · Ollama**. The adapter only connects to `127.0.0.1:11434`, ignores proxy settings and uses the same local tools. It does not send prompts to a hosted model. Model inference can be slow and consume memory/battery; small models can make mistakes. Tool results are shown separately as evidence. No inference quality or end-to-end model result has been verified in this repository yet. The model is downloaded under its own license, not redistributed here.

## What is implemented

- Independent Python support tools with SQLite persistence, customer-scoped order lookups, code-enforced return checks and multi-turn bug collection.
- An optional local AI agent with a bounded tool loop and a fixed loopback-only Ollama endpoint.
- A responsive, accessible two-project chat UI with example prompts, connection details and clear request/error states.
- A server-side `InvokeAgentRuntime` adapter matching NovaMart's existing JSON contract: request fields `prompt`, `customer_id`, `session_id`; response field `result`.
- A Bedrock `InvokeFlow` adapter for a string input, including event-stream errors and multi-turn agent-node follow-ups. The input node name must match the deployed flow.
- Per-chat runtime continuity, bounded sessions, duplicate-send prevention, response limits and explicit uncertain-outcome handling.
- Local-only hosting, a same-origin request token, strict Host checks, no cross-origin access, a restricted static-file allowlist, and no prompt/response logging by this server.

This repository is an **independent local workflow demonstration plus optional AI/AWS adapters**, not a replacement for either project's AWS infrastructure. It does not create resources, deploy the original backend, seed data, change IAM, or provide a fake backend.

## Connect an existing AWS deployment

Only proceed with a demo AWS environment you control. Sending messages invokes the existing agent's tools and can write workflow state, create demo tickets/return requests, and incur AWS charges. Use synthetic data only. The upstream workflow controls its own logging and storage; clearing the browser chat does not delete AWS records.

1. Create a local virtual environment and install the optional AWS SDK from PyPI:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

2. Authenticate locally using your organization's supported AWS profile/SSO process. Do not paste keys into chat, the UI, source files, or GitHub. No credential creation is included here. The SDK uses its standard credential chain.

3. Set your region/profile and the applicable backend configuration in your terminal. `.env.example` documents names only; the app deliberately does not load `.env` automatically.

   ```sh
   export AWS_REGION=us-east-1
   export AWS_PROFILE=your-existing-profile
   # For NovaMart, use your already-deployed runtime ARN:
   export AGENTCORE_RUNTIME_ARN='your-existing-runtime-arn'
   # For Support Flow, use the existing flow, alias and exact input node:
   export BEDROCK_FLOW_ID='your-existing-flow-id'
   export BEDROCK_FLOW_ALIAS_ID='your-existing-alias-id'
   export BEDROCK_FLOW_INPUT_NODE='your-flow-input-node-name'
   ```

4. When you are ready for real calls and their costs/side effects, enable them and restart:

   ```sh
   export ENABLE_LIVE_AWS=1
   python app.py
   ```

Each project can be configured independently. “Configured · live access unverified” means only that local settings are present. Credentials, permissions and resource health are checked by AWS when a message is sent, not by the status page. Start with a synthetic policy question rather than an action-taking refund or ticket request.

### Backend prerequisites

**NovaMart:** an existing AgentCore runtime exposing the JSON contract above, valid credentials with `bedrock-agentcore:InvokeAgentRuntime` on that runtime, and its server-side Bedrock/model, DynamoDB, knowledge-base and observability dependencies. The submitted project uses multiple specialist agents; this UI does not repair or recreate that infrastructure. The session ID is maintained across messages, but the UI does not add conversational memory the backend lacks.

**Support Flow:** an existing prepared flow alias, permission to invoke it, and the flow's configured model, agent, knowledge base and Lambda resources. Initial input must accept a string at the named FlowInput node's `document` output. A structured-object input would need a deliberate adapter change. Multi-turn continuation uses the node name and execution ID returned by AWS. Each completed flow starts a fresh execution on the next message; no extra conversation history is injected. No exported flow definition was included in the available submission artifacts, so reproducible backend deployment remains an outstanding step.

Missing or expired credentials yield a setup error. Other errors/timeouts conservatively mark the outcome unconfirmed and block repeat sends in that session. **Do not blindly repeat a request:** AWS may already have performed an action. Check its execution first. No automatic request retry is enabled.

## Test without AWS

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node --check static/app.js
```

The Python tests cover real temporary SQLite writes, customer isolation, return eligibility, bug-report follow-ups, the optional AI tool contract, validation, host/origin protections, disconnected behavior, response contracts, stream errors, follow-up routing, session isolation and duplicate/uncertain requests. Fake AI/AWS clients exist **only in the tests**. The local UI uses actual Python functions and SQLite, while clearly disclosing that its replies are rules-based. Passing these tests is not proof that AWS resources exist or that an upstream integration test passes.

## Source and publication boundaries

This UI, local server, local workflow/tools, optional AI loop and adapter tests are new, independent files. No course starter implementation, prompts, diagrams, FAQ corpus, infrastructure templates, screenshots, grading tests or credentials are bundled. The optional AWS adapters use the projects' exposed runtime contracts and AWS SDK APIs; no course code is imported. The local workflow is a fresh implementation of general support concepts and does not claim to reproduce the full course architecture, Bedrock RAG, guardrails or tracing.

The original projects were completed through AWS/Udacity coursework. Their course repositories contain CC BY-NC-ND notices; this repository does not relicense or redistribute that material. Keep any course-dependent backend private until its sharing rights are established. Completing the course does not by itself settle redistribution permissions. This repository also does not establish that the original deployments are still available or that every original test assertion was valid.

No license has been selected for these new files yet. Do not add a license covering third-party material, or publish the original mixed-source code, without checking rights. Publishing these independent files does not operate a live AWS endpoint or make any AWS backend available.

## Hosting warning

This is a **local development server**, not a public production service. Do not expose it with a tunnel or bind it publicly. GitHub Pages can host static files, but cannot run this Python server or safely hold AWS credentials. A shareable live demo needs a separate authenticated backend, least-privilege IAM, synthetic data, tenant/session isolation, abuse controls, request budgets/rate limits, TLS, logging policies and deployment approval. Do not put AWS keys into browser JavaScript or a public repository.

## API references

- [AWS SDK: invoke_agent_runtime](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html)
- [AWS SDK: invoke_flow](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent-runtime/client/invoke_flow.html)
- [AWS: multi-turn flow conversations](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-multi-turn-invocation.html)

- [Ollama: tool calling](https://docs.ollama.com/capabilities/tool-calling)
