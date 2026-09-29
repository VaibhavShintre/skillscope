const form = document.querySelector("#project-form");
const results = document.querySelector("#results");
const recommendationsNode = document.querySelector("#recommendations");
const privacyDialog = document.querySelector("#privacy-dialog");
let currentResult = null;
let showingAll = false;

const connectionErrorMessage = "SkillScope's local server is not reachable. Keep the PowerShell window running, then open http://127.0.0.1:8765 in your browser. Do not open index.html directly or through an editor preview.";

async function apiFetch(path, options = {}) {
  if (!/^https?:$/.test(window.location.protocol)) {
    throw new Error(connectionErrorMessage);
  }
  try {
    return await fetch(path, options);
  } catch (error) {
    throw new Error(connectionErrorMessage, {cause: error});
  }
}

async function checkServerConnection() {
  const status = document.querySelector("#server-status");
  try {
    const response = await apiFetch("/api/health", {cache: "no-store"});
    if (!response.ok) throw new Error(connectionErrorMessage);
    status.innerHTML = '<span class="status-dot"></span> Local server connected';
    status.classList.remove("disconnected");
  } catch (error) {
    status.innerHTML = '<span class="status-dot"></span> Local server disconnected';
    status.classList.add("disconnected");
    const formError = document.querySelector("#form-error");
    formError.textContent = error.message;
    formError.hidden = false;
  }
}

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

document.querySelectorAll(".posture-card input").forEach((input) => {
  input.addEventListener("change", () => {
    document.querySelectorAll(".posture-card").forEach((card) => card.classList.remove("selected"));
    input.closest(".posture-card").classList.add("selected");
  });
});

document.querySelector("#sample-button").addEventListener("click", () => {
  document.querySelector("#name").value = "Northstar Agencies";
  document.querySelector("#description").value = "A multi-tenant SaaS workspace for marketing agencies to manage clients, campaigns and analytics.";
  document.querySelector("#prd").value = "Teams need organization accounts, role-based permissions, email invitations, a responsive analytics dashboard, Stripe subscriptions with per-seat billing, and downloadable client reports. Critical flows need browser testing and accessible keyboard navigation.";
  document.querySelector("#technical-spec").value = "Use Next.js App Router and React with TypeScript. Store organization, membership and campaign data in PostgreSQL with Drizzle. Use Clerk for authentication, Stripe webhooks for billing, Tailwind for styling, Playwright for end-to-end tests, GitHub Actions for CI, and deploy on Vercel with Sentry monitoring.";
  document.querySelector("#stack").value = "Next.js, React, TypeScript, PostgreSQL, Drizzle, Clerk, Stripe, Tailwind, Playwright, Vercel, Sentry";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#analyze-button");
  const error = document.querySelector("#form-error");
  error.hidden = true;
  button.disabled = true;
  button.textContent = "Analyzing project intent…";
  const payload = {
    name: document.querySelector("#name").value,
    description: document.querySelector("#description").value,
    prd: document.querySelector("#prd").value,
    technical_spec: document.querySelector("#technical-spec").value,
    stack: document.querySelector("#stack").value.split(",").map((item) => item.trim()).filter(Boolean),
    posture: document.querySelector('input[name="posture"]:checked').value,
    capability_weights: {},
  };
  try {
    const response = await apiFetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const problem = await response.json();
      throw new Error(problem.detail ? JSON.stringify(problem.detail) : "Analysis failed");
    }
    currentResult = await response.json();
    showingAll = false;
    renderResults(currentResult);
    results.hidden = false;
    results.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = "Build recommendation <span>→</span>";
  }
});

function renderResults(data) {
  document.querySelector("#result-title").textContent = `${capitalize(data.project.posture)} catalog for ${data.project.name}`;
  document.querySelector("#evidence-notice").textContent = data.evidence_notice;
  document.querySelector("#selected-count").textContent = data.selected_count;
  document.querySelector("#coverage").textContent = `${data.weighted_coverage.toFixed(0)}%`;
  document.querySelector("#capability-count").textContent = data.capabilities.length;
  document.querySelector("#gap-count").textContent = data.gaps.length;
  document.querySelector("#export-json").href = `/api/analyses/${data.analysis_id}/report.json`;
  document.querySelector("#export-json").download = `${slug(data.project.name)}-skillscope.json`;
  document.querySelector("#export-markdown").href = `/api/analyses/${data.analysis_id}/report.md`;
  document.querySelector("#export-markdown").download = `${slug(data.project.name)}-skillscope.md`;

  document.querySelector("#capabilities").innerHTML = data.capabilities.map((capability) => `
    <article class="capability">
      <strong>${escapeHtml(capability.name)}</strong>
      <select class="capability-weight" data-capability="${escapeHtml(capability.id)}" aria-label="Importance for ${escapeHtml(capability.name)}">
        ${[1, 2, 3, 4, 5].map((value) => `<option value="${value}" ${value === capability.weight ? "selected" : ""}>${value} — ${["", "Optional", "Helpful", "Important", "High", "Critical"][value]}</option>`).join("")}
      </select>
      <p>${escapeHtml(capability.description)} ${escapeHtml(capability.evidence.join(" · "))}</p>
    </article>
  `).join("");

  const gaps = document.querySelector("#gaps");
  gaps.hidden = data.gaps.length === 0;
  gaps.innerHTML = data.gaps.length ? `<strong>Coverage gaps</strong><br>${data.gaps.map(escapeHtml).join(" · ")}` : "";
  renderRecommendations();
}

document.querySelector("#recalculate").addEventListener("click", async () => {
  if (!currentResult) return;
  const button = document.querySelector("#recalculate");
  button.disabled = true;
  button.textContent = "Recalculating…";
  const capabilityWeights = {};
  document.querySelectorAll(".capability-weight").forEach((input) => {
    capabilityWeights[input.dataset.capability] = Number(input.value);
  });
  const payload = {...currentResult.project, capability_weights: capabilityWeights};
  try {
    const response = await apiFetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error("Could not recalculate the recommendation.");
    currentResult = await response.json();
    renderResults(currentResult);
  } finally {
    button.disabled = false;
    button.textContent = "Recalculate with weights";
  }
});

function renderRecommendations() {
  if (!currentResult) return;
  const items = showingAll ? currentResult.recommendations : currentResult.recommendations.filter((item) => item.selected);
  recommendationsNode.innerHTML = items.map((item) => {
    const components = item.components;
    const componentRows = [
      ["Coverage", components.coverage],
      ["Criticality", components.criticality],
      ["Compatibility", components.compatibility],
      ["Uniqueness", components.uniqueness],
      ["Source verification", components.health],
      ...(components.trigger_quality === null ? [] : [["Trigger quality", components.trigger_quality]]),
    ];
    return `
      <details class="recommendation ${item.selected ? "selected-skill" : ""}">
        <summary>
          <span class="skill-name"><span class="rank">${item.rank}</span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.evidence_source)}</small></span></span>
          <span class="tier ${escapeHtml(item.tier)}">${escapeHtml(item.tier)}</span>
          <span class="score">${item.importance_score.toFixed(1)}</span>
        </summary>
        <div class="detail">
          <p>${escapeHtml(item.description)}</p>
          ${item.source_url ? `<p class="skill-source">Verified package: <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.publisher || "View source")}</a></p>` : ""}
          <strong>Why this rank</strong>
          <ul class="reason-list">
            ${item.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}
            ${item.cautions.map((caution) => `<li class="caution">${escapeHtml(caution)}</li>`).join("")}
          </ul>
          <div class="component-grid">
            ${componentRows.map(([name, value]) => `<div><span>${escapeHtml(name)}</span><strong>${Number(value).toFixed(0)}</strong></div>`).join("")}
          </div>
        </div>
      </details>
    `;
  }).join("");
  document.querySelector("#show-all").textContent = showingAll ? "Show recommended only" : `Show all ${currentResult.candidate_count} candidates`;
}

document.querySelector("#show-all").addEventListener("click", () => {
  showingAll = !showingAll;
  renderRecommendations();
});

document.querySelector("#privacy-button").addEventListener("click", async () => {
  await loadPrivacy();
  privacyDialog.showModal();
});

document.querySelector("#monitoring-mode").addEventListener("change", updateScopeVisibility);

async function loadPrivacy() {
  const [privacyResponse, dataResponse] = await Promise.all([apiFetch("/api/privacy"), apiFetch("/api/local-data")]);
  const privacy = await privacyResponse.json();
  const summary = await dataResponse.json();
  document.querySelector("#monitoring-mode").value = privacy.monitoring_mode;
  document.querySelector("#cloud-sync").checked = privacy.cloud_sync;
  document.querySelector("#telemetry").checked = privacy.anonymous_telemetry;
  document.querySelector("#automatic-spend").checked = privacy.automatic_api_spend;
  document.querySelector("#automatic-changes").checked = privacy.automatic_changes;
  document.querySelectorAll("#scope-settings input").forEach((input) => {
    input.checked = privacy.monitored_scopes.includes(input.value);
  });
  document.querySelector("#local-data-summary").textContent = `${summary.analyses} saved analyses · ${summary.database_path}`;
  updateScopeVisibility();
}

function updateScopeVisibility() {
  document.querySelector("#scope-settings").hidden = document.querySelector("#monitoring-mode").value === "manual";
}

document.querySelector("#save-privacy").addEventListener("click", async () => {
  const status = document.querySelector("#privacy-status");
  const payload = {
    consent_version: "1",
    monitoring_mode: document.querySelector("#monitoring-mode").value,
    monitored_scopes: [...document.querySelectorAll("#scope-settings input:checked")].map((input) => input.value),
    cloud_sync: document.querySelector("#cloud-sync").checked,
    anonymous_telemetry: document.querySelector("#telemetry").checked,
    automatic_api_spend: document.querySelector("#automatic-spend").checked,
    monthly_spend_limit_usd: 0,
    automatic_changes: document.querySelector("#automatic-changes").checked,
    retention_days: 30,
  };
  const response = await apiFetch("/api/privacy", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  status.textContent = response.ok ? "Choices saved locally." : "Could not save choices.";
  if (response.ok) setTimeout(() => { status.textContent = ""; }, 2400);
});

document.querySelector("#delete-data").addEventListener("click", async () => {
  if (!window.confirm("Delete all locally saved analyses and privacy settings? This cannot be undone.")) return;
  const response = await apiFetch("/api/local-data", {method: "DELETE"});
  if (response.ok) {
    currentResult = null;
    results.hidden = true;
    await loadPrivacy();
    document.querySelector("#privacy-status").textContent = "All local SkillScope data was deleted.";
  }
});

const capitalize = (value) => value.charAt(0).toUpperCase() + value.slice(1);
const slug = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

checkServerConnection();
