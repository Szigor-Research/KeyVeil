const scenarioList = document.getElementById("scenario-list");
const scenarioSelect = document.getElementById("scenario-select");
const scenarioCount = document.getElementById("scenario-count");
const simulateButton = document.getElementById("simulate-btn");
const resetButton = document.getElementById("reset-btn");
const statusWord = document.getElementById("status-word");
const statusDetail = document.getElementById("status-detail");
const receiptJson = document.getElementById("receipt-json");
const decisionTrace = document.getElementById("decision-trace");
const traceCount = document.getElementById("trace-count");
const hashState = document.getElementById("hash-state");
const metaScenario = document.getElementById("meta-scenario");
const metaCap = document.getElementById("meta-cap");
const metaDaily = document.getElementById("meta-daily");
const metaBudgetScope = document.getElementById("meta-budget-scope");
const metaApproval = document.getElementById("meta-approval");
const metaToken = document.getElementById("meta-token");
const intentAmount = document.getElementById("intent-amount");
const intentRecipient = document.getElementById("intent-recipient");
const intentId = document.getElementById("intent-id");
const reservationId = document.getElementById("reservation-id");

let scenarios = [];
let selectedId = "";

function setVisual(status, receiptId) {
  document.body.dataset.status = status;
  if (typeof window.setCoreTheme === "function") {
    window.setCoreTheme(status, { receiptId: receiptId || null });
  }
}

function shortValue(value, start = 8, end = 6) {
  const text = String(value || "");
  if (!text || text.length <= start + end + 1) return text || "-";
  return `${text.slice(0, start)}...${text.slice(-end)}`;
}

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(Number(value || 0));
}

function selectedScenario() {
  return scenarios.find((scenario) => scenario.id === selectedId) || null;
}

function applyScenarioMeta(scenario) {
  if (!scenario) return;
  metaScenario.textContent = scenario.id;
  metaCap.textContent = money(scenario.max_per_tx_usd);
  metaDaily.textContent = money(scenario.daily_budget_usd);
  metaBudgetScope.textContent = scenario.budget_scope_id || "-";
  metaApproval.textContent = money(scenario.approval_threshold_usd);
  metaToken.textContent = (scenario.allowed_tokens || []).join(", ") || "-";
}

function setDecision(status, detail, receiptId = null) {
  const labels = {
    ready: "READY",
    processing: "EVALUATING",
    approved: "APPROVED",
    blocked: "BLOCKED",
    pending_human: "REVIEW REQUIRED",
  };
  statusWord.textContent = labels[status] || String(status).toUpperCase();
  statusDetail.textContent = detail || "";
  setVisual(status, receiptId);
}

function clearEvidence() {
  decisionTrace.replaceChildren();
  traceCount.textContent = "0 STEPS";
  receiptJson.textContent = "-";
  hashState.textContent = "NOT ISSUED";
  intentAmount.textContent = "-";
  intentRecipient.textContent = "-";
  intentId.textContent = "-";
  reservationId.textContent = "-";
}

function selectScenario(id) {
  selectedId = id;
  scenarioSelect.value = id;
  for (const button of scenarioList.querySelectorAll("button")) {
    button.setAttribute("aria-selected", button.dataset.id === id ? "true" : "false");
  }
  const scenario = selectedScenario();
  applyScenarioMeta(scenario);
  clearEvidence();
  setDecision("ready", scenario ? scenario.title : "No policy decision yet");
}

function renderTrace(lines) {
  decisionTrace.replaceChildren();
  for (const [index, line] of lines.entries()) {
    const item = document.createElement("li");
    const indexNode = document.createElement("span");
    indexNode.textContent = String(index + 1).padStart(2, "0");
    const textNode = document.createElement("p");
    textNode.textContent = line;
    item.append(indexNode, textNode);
    decisionTrace.appendChild(item);
  }
  traceCount.textContent = `${lines.length} ${lines.length === 1 ? "STEP" : "STEPS"}`;
}

function renderReceipt(receipt) {
  receiptJson.textContent = JSON.stringify(receipt, null, 2);
  hashState.textContent = receipt.receipt_hash ? "HASHED" : "NOT HASHED";
  intentAmount.textContent = `${money(receipt.amount_usd)} ${receipt.token}`;
  intentRecipient.textContent = shortValue(receipt.recipient, 8, 6);
  intentId.textContent = shortValue(receipt.intent_id, 12, 6);
  reservationId.textContent = shortValue(receipt.budget_reservation_id, 12, 6);
  const hits = (receipt.policy_hits || []).join(" / ");
  setDecision(receipt.status, hits, receipt.receipt_id);
}

async function simulate() {
  if (!selectedId) return;
  simulateButton.disabled = true;
  simulateButton.textContent = "SIMULATING...";
  clearEvidence();
  setDecision("processing", "Evaluating policy and budget boundaries");

  try {
    const response = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: selectedId }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok || !payload.receipt) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    renderTrace(payload.trace || []);
    renderReceipt(payload.receipt);
  } catch (error) {
    renderTrace([String(error)]);
    setDecision("blocked", "Simulation request failed");
  } finally {
    simulateButton.disabled = false;
    simulateButton.textContent = "SIMULATE";
  }
}

async function loadScenarios() {
  const response = await fetch("/api/scenarios");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json();
  scenarios = payload.scenarios || [];
  scenarioCount.textContent = String(scenarios.length);
  scenarioList.replaceChildren();
  scenarioSelect.replaceChildren();

  for (const scenario of scenarios) {
    const listItem = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.id = scenario.id;
    button.setAttribute("role", "option");
    button.innerHTML = `<strong></strong><span></span><em></em>`;
    button.querySelector("strong").textContent = scenario.title;
    button.querySelector("span").textContent = scenario.id;
    button.querySelector("em").textContent = scenario.expected_status.replace("_", " ");
    button.addEventListener("click", () => selectScenario(scenario.id));
    listItem.appendChild(button);
    scenarioList.appendChild(listItem);

    const option = document.createElement("option");
    option.value = scenario.id;
    option.textContent = `${scenario.title} (${scenario.expected_status.replace("_", " ")})`;
    scenarioSelect.appendChild(option);
  }

  if (scenarios.length) selectScenario(scenarios[0].id);
}

scenarioSelect.addEventListener("change", () => selectScenario(scenarioSelect.value));
simulateButton.addEventListener("click", simulate);
resetButton.addEventListener("click", () => selectScenario(selectedId));

setVisual("ready", null);
loadScenarios().catch((error) => {
  renderTrace([String(error)]);
  setDecision("blocked", "Scenario catalog unavailable");
  simulateButton.disabled = true;
});
