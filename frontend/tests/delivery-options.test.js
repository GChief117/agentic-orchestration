import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { ApiClient } from "../src/api.js";
import { openGoalForm } from "../src/forms.js";
import { renderWorkspace } from "../src/views.js";
import { mountApp } from "../src/app.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Create a model-enabled chart for goal intake and review tests. */
function view() {
  return {
    project: { id: "project", name: "Report project" },
    agents: [{ key: "lead", name: "Coordinator", role: "Coordination", reports_to: null, provider: "openai", model: "default", execution_mode: "cli", capabilities: ["model.generate"] }],
  };
}

/** Return a reviewed plan without calling a real provider. */
function plan() {
  return {
    summary: "Write the requested report.", coordinator_key: "lead", team_revision: "revision",
    tasks: [{ id: "report", title: "Write report", agent: "lead", capability: "model.generate", parameters: { path: "report.md", prompt: "Write a report" }, depends_on: [] }],
  };
}

/** Submit the active stage through its actual event handler. */
function submit(dialog, window) {
  dialog.querySelector("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
}

test("new goals default to a PDF deliverable and send its visual style to planning and execution", async () => {
  const dom = new JSDOM("<body></body>");
  let planned, started;
  try {
    const dialog = openGoalForm(dom.window.document, view(), async (payload) => { started = payload; }, null, async (payload) => { planned = payload; return plan(); });
    assert.equal(dialog.querySelector('[data-field="deliverable-format"]').value, "pdf");
    assert.equal(dialog.querySelector('[data-field="deliverable-style"]').value, "editorial");
    dialog.querySelector('[data-field="goal"]').value = "Explain the architecture";
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(planned.deliverable, { format: "pdf", style: "editorial" });
    assert.equal(dialog.querySelector('[data-field="deliverable-format"]').value, "pdf");
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(started.deliverable, { format: "pdf", style: "editorial" });
  } finally { dom.window.close(); }
});

test("deliverable choices survive planning, review edits, and returning to the goal", async () => {
  const dom = new JSDOM("<body></body>");
  const requests = [];
  let started;
  try {
    const dialog = openGoalForm(dom.window.document, view(), async (payload) => { started = payload; }, null, async (payload) => { requests.push(payload); return plan(); });
    dialog.querySelector('[data-field="goal"]').value = "Make a presentation-ready architecture report";
    dialog.querySelector('[data-field="deliverable-format"]').value = "html";
    dialog.querySelector('[data-field="deliverable-style"]').value = "neon";
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(requests[0].deliverable, { format: "html", style: "neon" });
    assert.equal(dialog.querySelector('[data-field="deliverable-format"]').value, "html");
    assert.equal(dialog.querySelector('[data-field="deliverable-style"]').value, "neon");
    dialog.querySelector('[data-field="deliverable-style"]').value = "ocean";
    dialog.querySelector("[data-edit-goal]").click();
    assert.equal(dialog.querySelector('[data-field="deliverable-format"]').value, "html");
    assert.equal(dialog.querySelector('[data-field="deliverable-style"]').value, "ocean");
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(requests[1].deliverable, { format: "html", style: "ocean" });
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(started.deliverable, { format: "html", style: "ocean" });
  } finally { dom.window.close(); }
});

test("deliverable API keeps authentication out of URLs and downloads binary PDF bytes", async () => {
  const calls = [];
  const blob = new Blob([new Uint8Array([37, 80, 68, 70, 45])], { type: "application/pdf" });
  const api = new ApiClient({ token: "private-session", fetch: async (url, options) => {
    calls.push({ url, options });
    if (options.method === "POST") return { ok: true, json: async () => ({ delivery: { state: "ready", format: "pdf", name: "report.pdf" } }) };
    return { ok: true, headers: new Headers({ "Content-Disposition": 'attachment; filename="report.pdf"', "Content-Type": "application/pdf" }), blob: async () => blob };
  } });
  await api.generateDeliverable("goal/id", { format: "pdf", style: "ocean" });
  const result = await api.downloadDeliverable("goal/id");
  assert.deepEqual(JSON.parse(calls[0].options.body), { format: "pdf", style: "ocean" });
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[1].options.method, "GET");
  for (const call of calls) {
    assert.equal(call.url, "/api/goals/goal%2Fid/deliverable");
    assert.equal(call.options.headers.Authorization, "Bearer private-session");
    assert.doesNotMatch(call.url, /private-session|token/);
  }
  assert.equal(result.blob, blob);
  assert.equal(result.name, "report.pdf");
});

test("failed binary downloads return the server error without producing a file", async () => {
  const api = new ApiClient({ token: "session", fetch: async () => ({ ok: false, status: 400, json: async () => ({ error: "The report is still being prepared" }) }) });
  await assert.rejects(api.downloadDeliverable("g"), /still being prepared/);
});

test("a finished goal exposes the requested report format while retaining editable source artifacts", () => {
  const snapshot = {
    projects: [{ id: "p", name: "Project", charter: { objective: "Produce a report" } }],
    agents: [{ id: "a", name: "Writer", project_id: "p" }],
    goals: [{ id: "g", project_id: "p", title: "Report", state: "accepted", accepted: 1, total: 1, deliverable: { format: "pdf", style: "ocean" }, delivery: { state: "ready", format: "pdf", style: "ocean", name: "architecture.pdf", bytes: 2048 } }],
    tasks: [{ id: "t", project_id: "p", goal_id: "g", agent_id: "a", title: "Draft report", state: "accepted", capability: "model.generate", dependencies: [], receipt: { artifact: "report.md" } }],
  };
  const dom = new JSDOM(renderWorkspace(snapshot, { selectedProject: "p", tab: "work" }));
  try {
    assert.ok(dom.window.document.querySelector('[data-download-goal="g"]'));
    assert.match(dom.window.document.querySelector('[data-download-goal="g"]').textContent, /PDF/i);
    assert.ok(dom.window.document.querySelector('[data-generate-deliverable="g"]'));
    assert.equal(dom.window.document.querySelector('[data-delivery-format="g"]').value, "pdf");
    assert.equal(dom.window.document.querySelector('[data-delivery-style="g"]').value, "ocean");
    assert.ok(dom.window.document.querySelector('[data-inspect="t"]'));
    assert.ok(dom.window.document.querySelector('[data-download-artifact="t"]'));
  } finally { dom.window.close(); }
});

test("existing-goal export choices survive polling and failed delivery can retry without claiming success", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const snapshot = {
    capacity: { execution_limit: 1 }, active_executions: 0,
    projects: [{ id: "p", name: "Project", mode: "greenfield", charter: { objective: "Work", scope: [], success_criteria: [], constraints: [] }, team_size: 1, max_concurrency: 1, active_executions: 0 }],
    agents: [{ id: "a", key: "lead", project_id: "p", name: "Coordinator", role: "Coordination", reports_to: null, collaborators: [], state: "idle", provider: "openai", model: "default" }],
    goals: [{ id: "g", project_id: "p", title: "Report", state: "accepted", total: 1, accepted: 1, deliverable: { format: "markdown", style: "editorial" } }],
    tasks: [{ id: "done", project_id: "p", goal_id: "g", agent_id: "a", title: "Write report", state: "accepted", capability: "model.generate", dependencies: [], receipt: { artifact: "report.md" } }], events: [],
  };
  const calls = [];
  let finish;
  const app = mountApp({ document: dom.window.document, pollInterval: 0, api: {
    state: async () => structuredClone(snapshot),
    generateDeliverable: (id, preferences) => {
      calls.push({ id, preferences });
      return new Promise((resolve) => { finish = resolve; });
    },
  } });
  try {
    await app.ready;
    dom.window.document.querySelector('[data-project="p"]').click();
    for (const [selector, value] of [['[data-delivery-format="g"]', "html"], ['[data-delivery-style="g"]', "ocean"]]) {
      const control = dom.window.document.querySelector(selector);
      control.value = value;
      control.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    }
    await app.refresh();
    assert.equal(dom.window.document.querySelector('[data-delivery-format="g"]').value, "html");
    assert.equal(dom.window.document.querySelector('[data-delivery-style="g"]').value, "ocean");
    dom.window.document.querySelector('[data-generate-deliverable="g"]').click();
    assert.equal(dom.window.document.querySelector('[data-generate-deliverable="g"]').disabled, true);
    await app.refresh();
    assert.equal(dom.window.document.querySelector('[data-generate-deliverable="g"]').disabled, true);
    assert.deepEqual(calls, [{ id: "g", preferences: { format: "html", style: "ocean" } }]);
    finish({ delivery: { state: "failed", error: "Renderer could not complete the file" } });
    await tick();
    assert.match(dom.window.document.querySelector("#workspace-error").textContent, /Renderer could not complete/);
    assert.doesNotMatch(dom.window.document.querySelector("#feedback").textContent, /ready to download/);
    assert.equal(dom.window.document.querySelector('[data-download-goal="g"]'), null);
    assert.equal(dom.window.document.querySelector('[data-generate-deliverable="g"]').disabled, false);
    dom.window.document.querySelector('[data-generate-deliverable="g"]').click();
    snapshot.goals[0].delivery = { state: "ready", format: "html", style: "ocean", name: "report.html", bytes: 200 };
    finish({ delivery: snapshot.goals[0].delivery });
    await tick();
    assert.equal(calls.length, 2);
    assert.ok(dom.window.document.querySelector('[data-download-goal="g"]'));
    assert.match(dom.window.document.querySelector("#feedback").textContent, /ready to download/);
  } finally { app.destroy(); dom.window.close(); }
});
