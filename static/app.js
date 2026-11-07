"use strict";
const projects = {
  novamart: {
    title: "Meet NovaMart.", subtitle: "One conversation. A team of specialist agents.", agent: "NovaMart support", icon: "N",
    route: ["Your question", "Orchestrator", "Specialist agents", "Customer response"],
    description: "Order lookups, return decisions and policy retrieval, coordinated through the existing AgentCore runtime.",
    placeholder: "Ask a question about your order…", empty: "Support, with a team behind it.",
    intro: "Ask about an order, explore a return policy or check a demo customer’s tier. Responses come from your deployed NovaMart agent.",
    prompts: [["Return policy", "What is the return policy for a Premium customer?"], ["Customer tier", "What is my customer tier?"], ["My orders", "List my recent orders."]]
  },
  "support-flow": {
    title: "Meet Support Flow.", subtitle: "From a support question to the next useful step.", agent: "Customer support flow", icon: "F",
    route: ["Your question", "Bedrock Flow", "Support route", "Flow response"],
    description: "A chat bridge to your deployed Bedrock Flow. Follow-up questions continue the same flow execution when supported.",
    placeholder: "Describe an issue or ask a support question…", empty: "A simple way to test the flow.",
    intro: "Try a bug report, a policy question or an unsupported request. This interface displays the actual output of your configured flow.",
    prompts: [["Report a bug", "The checkout button returns an error. How can I report this?"], ["Return policy", "What is your return policy?"], ["Ask for help", "Can you help with a wholesale order?"]]
  }
};
const $ = (id) => document.getElementById(id);
const state = {project: "novamart", status: null, backend: "local", busy: false, session: crypto.randomUUID(), messages: [], uncertain: false, generation: 0};
function element(tag, cls, text) { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; }
function providerStatus() { return state.backend === "aws" ? state.status?.providers[state.project] : state.status?.modes[state.backend]; }
function ready() { return !!providerStatus()?.ready; }
function updateControls() {
  $("send").disabled = state.busy || state.uncertain || !ready() || !$("message").value.trim();
  $("message").disabled = state.busy;
  $("customer").disabled = state.busy || state.messages.length > 0;
  $("new-chat").disabled = state.busy;
  $("backend").disabled = state.busy;
  document.querySelectorAll("[data-project], .suggestions button").forEach((b) => b.disabled = state.busy);
  $("char-count").textContent = `${$("message").value.length.toLocaleString()} / 4,000`;
  $("send-hint").textContent = state.busy ? "Processing. Please do not repeat this request." : state.uncertain ? "Outcome unconfirmed. Check saved records before starting over." : !ready() ? "Backend not configured. No substitute answers." : state.backend === "aws" ? "Live calls may incur costs and change demo data." : state.backend === "ollama" ? "Real on-device AI. Local tools and synthetic data only." : "Rules-based workflow, not AI. Records stay on this computer.";
}
function renderMessages() {
  const box = $("messages"); box.replaceChildren();
  if (!state.messages.length) {
    const empty = element("div", "empty");
    empty.append(element("div", "empty-icon", "✳"), element("h2", "", projects[state.project].empty), element("p", "", state.backend === "local" ? "This mode runs real Python tools against a local SQLite database of synthetic orders and tickets. It follows a rules-based workflow, not an AI model. Select Local AI when you install a model later." : state.backend === "ollama" ? "After you configure a local model, the AI agent can choose support tools and answer from their results. No model is running until that separate setup is complete." : projects[state.project].intro));
    box.append(empty);
  }
  for (const item of state.messages) {
    const msg = element("div", `message ${item.role}`);
    msg.append(element("span", "message-label", item.role === "user" ? "You" : item.role === "error" ? "Connection notice" : projects[state.project].agent), element("div", "bubble", item.text));
    if (item.meta) msg.append(element("span", "message-meta", item.meta));
    box.append(msg);
    if (item.events?.length) {
      const details = element("details", "tool-events"); details.append(element("summary", "", `${item.events.length} local tool result${item.events.length > 1 ? "s" : ""} · inspect evidence`));
      item.events.forEach(event => { details.append(element("p", "", event.tool), element("pre", "", JSON.stringify(event.result, null, 2))); });
      box.append(details);
    }
  }
  box.scrollTop = box.scrollHeight;
}
function renderStatus() {
  const provider = providerStatus();
  const isLocal = state.backend === "local";
  $("connection-status").textContent = !state.status ? "Local server unavailable" : isLocal ? "Local workflow ready · no LLM" : provider?.ready ? "Configured · live access unverified" : state.backend === "ollama" ? "AI model not configured" : "AWS not connected";
  $("connection-note").textContent = !state.status ? "The Python server is unavailable. Restart it and refresh." : isLocal ? "Working local demo. Real lookups and saved records, synthetic data, deterministic replies. No AWS or model required." : state.backend === "ollama" ? (provider?.ready ? `AI support agent: ${provider.agent}. Inference and tools run on this Mac.` : "The AI agent adapter is included, but no model is installed or connected. Local workflow is available now.") : provider?.ready ? "Live AWS mode. Use synthetic demo data only. Sending can incur charges and run the project’s tools." : "Original AWS backend is not connected. Use Local workflow to test the independent demo without it.";
  $("connection-note").classList.toggle("ready", !!provider?.ready);
  $("mode-description").textContent = isLocal ? "Real local tools. Synthetic demo data." : state.backend === "ollama" ? "AI agent: on-device model + local tools." : "The original deployed project, when available.";
  $("missing").replaceChildren();
  for (const missing of provider?.missing ?? ["Start the local Python server and refresh."]) $("missing").append(element("li", "", missing));
  if (provider?.ready) $("missing").append(element("li", "", isLocal ? "Ready. No account, download or cloud bill. SQLite records are stored in local-data/ (gitignored)." : "Local settings are present. Backend availability is checked when you send."));
  updateControls();
}
function renderProject() {
  const p = projects[state.project];
  for (const [id, value] of [["title", p.title], ["subtitle", p.subtitle], ["agent-name", p.agent], ["project-description", p.description]]) $(id).textContent = value;
  if (state.backend !== "aws") {
    $("subtitle").textContent = state.backend === "ollama" ? "An on-device AI agent with real local support tools." : "A working local support workflow. No cloud account needed.";
    $("project-description").textContent = state.backend === "ollama" ? "A locally installed model can choose Python tools, read their results and compose support replies. Model setup is optional." : "Independent Python rules and SQLite tools demonstrate order checks, policies and ticket collection using synthetic data.";
    $("agent-name").textContent = p.agent + (state.backend === "ollama" ? " · AI agent" : " · workflow");
  }
  document.querySelector(".agent-avatar").textContent = p.icon;
  $("message").placeholder = p.placeholder;
  $("customer-field").hidden = state.project !== "novamart";
  document.querySelectorAll("[data-project]").forEach(b => { const selected = b.dataset.project === state.project; b.classList.toggle("selected", selected); b.setAttribute("aria-pressed", String(selected)); });
  $("route").replaceChildren();
  const route = state.backend === "local" ? ["Your question", "Python workflow", "Local SQLite tools", "Result + tool evidence"] : state.backend === "ollama" ? ["Your question", "Local AI agent", "Local SQLite tools", "Model response"] : p.route;
  route.forEach((text, index) => { if (index) $("route").append(element("div", "route-connector")); const node = element("div", "route-node"); node.append(element("i"), document.createTextNode(text)); $("route").append(node); });
  $("suggestions").replaceChildren();
  const prompts = state.backend === "aws" ? p.prompts : [...p.prompts, ["Saved records", "Show my saved records"]];
  prompts.forEach(([label, text]) => { const button = element("button", "", `${label} ↗`); button.type = "button"; button.addEventListener("click", () => { $("message").value = text; updateControls(); $("message").focus(); }); $("suggestions").append(button); });
  renderMessages(); renderStatus();
}
function resetChat(project = state.project) {
  if (state.busy) return;
  state.project = project; state.messages = []; state.session = crypto.randomUUID(); state.uncertain = false; state.generation++;
  $("message").value = ""; renderProject();
}
async function refreshStatus() {
  $("refresh").disabled = true;
  try { const response = await fetch("/api/status", {cache: "no-store"}); if (!response.ok) throw new Error(); state.status = await response.json(); }
  catch { state.status = null; }
  finally { $("refresh").disabled = false; renderStatus(); }
}
function toggleSetup(open) { $("setup").hidden = !open; $("setup-toggle").setAttribute("aria-expanded", String(open)); }
$("setup-toggle").addEventListener("click", () => toggleSetup($("setup").hidden));
$("close-setup").addEventListener("click", () => { toggleSetup(false); $("setup-toggle").focus(); });
$("refresh").addEventListener("click", refreshStatus);
$("backend").addEventListener("change", () => { state.backend = $("backend").value; resetChat(); });
$("new-chat").addEventListener("click", () => resetChat());
document.querySelectorAll("[data-project]").forEach(b => b.addEventListener("click", () => { if (b.dataset.project !== state.project) resetChat(b.dataset.project); }));
$("message").addEventListener("input", updateControls);
$("message").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("send").disabled) $("chat-form").requestSubmit(); } });
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("send").disabled || !$("chat-form").reportValidity()) return;
  const message = $("message").value.trim();
  state.busy = true; state.messages.push({role: "user", text: message}); $("message").value = ""; renderMessages(); updateControls();
  const generation = state.generation;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 150000);
  try {
    const response = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json", "X-Chat-Token": state.status.csrf_token}, signal: controller.signal,
      body: JSON.stringify({backend: state.backend, project: state.project, session_id: state.session, message, customer_id: $("customer").value.trim() || "CUST-001"})});
    const data = await response.json();
    if (generation !== state.generation) return;
    if (!response.ok) { state.uncertain = !!data.uncertain; state.messages.push({role: "error", text: data.error || "The request failed. No response was confirmed."}); }
    else { state.messages.push({role: "assistant", text: data.reply, events: data.events, meta: `${data.agent || "AWS response"} · ${(data.elapsed_ms / 1000).toFixed(1)}s${data.state === "input_required" ? " · Follow-up requested" : ""}`}); }
  } catch {
    state.uncertain = true;
    state.messages.push({role: "error", text: "The connection ended before a result was confirmed. The backend may still be processing the request. Check its records before repeating it."});
  } finally { clearTimeout(timeout); state.busy = false; renderMessages(); updateControls(); $("message").focus(); }
});
window.addEventListener("beforeunload", (event) => { if (state.busy) { event.preventDefault(); event.returnValue = ""; } });
renderProject(); refreshStatus();
