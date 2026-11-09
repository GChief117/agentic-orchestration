import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openGoalForm, openTaskDetails } from "../src/forms.js";
import { mountApp } from "../src/app.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Build a connected project with one valid native-CLI research owner. */
function projectView(available = true) {
  return {
    project: { id: "p", name: "Project", research: { available, provider: "openai", name: "Connected Codex research", mode: "bridge", skills: ["pio-map-codebase"], reason: available ? undefined : "Connect the native research runtime first." } },
    agents: [{ key: "lead", id: "a", name: "Coordinator", role: "Coordination", reports_to: null, provider: "openai", model: "default", execution_mode: "cli", capabilities: ["model.generate"] }],
  };
}

/** A planner fixture with an editable result task and no provider calls. */
function plan() {
  return { team_revision: "revision", summary: "Inspect the implementation.", tasks: [{ id: "research", title: "Inspect implementation", agent: "lead", capability: "model.generate", parameters: { path: "report.md", prompt: "Inspect sources" }, depends_on: [] }] };
}

/** Submit the mounted form through the real application handler. */
function submit(dialog, window) {
  dialog.querySelector("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
}

test("connected projects default to native research and preserve explicit research/layout changes through review", async () => {
  const dom = new JSDOM("<body></body>");
  let planned, started;
  try {
    const dialog = openGoalForm(dom.window.document, projectView(), async (value) => { started = value; }, null, async (value) => { planned = value; return plan(); });
    assert.equal(dialog.querySelector('[data-field="research-mode"]').value, "native_cli");
    assert.equal(dialog.querySelector('[data-field="deliverable-layout"]').value, "visual_story");
    assert.match(dialog.textContent, /pio-map-codebase/);
    dialog.querySelector('[data-field="goal"]').value = "Inspect the stack with native tools";
    submit(dialog, dom.window);
    await tick();
    assert.equal(planned.research_mode, "native_cli");
    assert.equal(planned.deliverable.layout, "visual_story");
    dialog.querySelector('[data-field="research-mode"]').value = "source_context";
    dialog.querySelector('[data-field="deliverable-layout"]').value = "reference";
    dialog.querySelector('[data-task] [data-field="agent"]').dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.equal(dialog.querySelector('[data-field="research-mode"]').value, "source_context");
    assert.equal(dialog.querySelector('[data-field="deliverable-layout"]').value, "reference");
    dialog.querySelector("[data-edit-goal]").click();
    assert.equal(dialog.querySelector('[data-field="research-mode"]').value, "source_context");
    assert.equal(dialog.querySelector('[data-field="deliverable-layout"]').value, "reference");
    submit(dialog, dom.window);
    await tick();
    submit(dialog, dom.window);
    await tick();
    assert.equal(started.research_mode, "source_context");
    assert.equal(started.deliverable.layout, "reference");
  } finally { dom.window.close(); }
});

test("unavailable native research never appears selectable or enters the submitted plan", async () => {
  const dom = new JSDOM("<body></body>");
  let planned;
  try {
    const view = projectView(false);
    view.project.research.reason = '<img src=x onerror="bad()"> unavailable';
    const dialog = openGoalForm(dom.window.document, view, async () => {}, null, async (value) => { planned = value; return plan(); });
    assert.equal(dialog.querySelector('[data-field="research-mode"]'), null);
    assert.equal(dialog.querySelector("img"), null);
    dialog.querySelector('[data-field="goal"]').value = "Use the attached evidence";
    submit(dialog, dom.window);
    await tick();
    assert.equal(planned.research_mode, "source_context");
  } finally { dom.window.close(); }
});

test("Markdown hides visual layout without losing the selected story layout", () => {
  const dom = new JSDOM("<body></body>");
  try {
    const dialog = openGoalForm(dom.window.document, projectView(), async () => {}, null, async () => plan());
    const format = dialog.querySelector('[data-field="deliverable-format"]');
    format.value = "markdown";
    format.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.equal(dialog.querySelector("[data-layout-field]").hidden, true);
    format.value = "html";
    format.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.equal(dialog.querySelector("[data-layout-field]").hidden, false);
    assert.equal(dialog.querySelector('[data-field="deliverable-layout"]').value, "visual_story");
  } finally { dom.window.close(); }
});

test("research receipts separate recorded tool activity from configured skill guidance and escape it", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    const task = { id: "t", title: "Research", state: "accepted", capability: "model.generate", receipt: { research: { mode: "bridge", provider: "openai", skills: ["pio-map-codebase"], tool_events: [{ command: 'cat README.md <img src=x onerror="bad()">' }] } } };
    const dialog = await openTaskDetails(dom.window.document, task, { name: "Researcher" }, {});
    const evidence = dialog.querySelector('[aria-label="Research tools used"]');
    assert.match(evidence.textContent, /Skill guidance: pio-map-codebase/);
    assert.match(evidence.textContent, /Recorded tool activity \(1\)/);
    assert.match(evidence.textContent, /cat README.md/);
    assert.equal(evidence.querySelector("img"), null);
    assert.doesNotMatch(evidence.textContent, /VS Code/);
  } finally { dom.window.close(); }
});

test("existing exports keep reference layout until changed and persist a story choice across polling", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const view = projectView();
  const snapshot = {
    capacity: { execution_limit: 1 }, active_executions: 0,
    projects: [{ ...view.project, mode: "existing", charter: { objective: "Inspect", scope: [], constraints: [], success_criteria: [] }, team_size: 1, max_concurrency: 1, active_executions: 0 }],
    agents: [{ ...view.agents[0], project_id: "p", state: "idle", collaborators: [] }],
    goals: [{ id: "g", project_id: "p", title: "Report", state: "accepted", total: 1, accepted: 1, deliverable: { format: "pdf", style: "ocean" } }],
    tasks: [{ id: "t", project_id: "p", goal_id: "g", agent_id: "a", title: "Research", state: "accepted", capability: "model.generate", dependencies: [], receipt: { artifact: "report.md" } }], events: [],
  };
  let exported;
  const app = mountApp({ document: dom.window.document, pollInterval: 0, api: { state: async () => structuredClone(snapshot), generateDeliverable: async (_id, settings) => { exported = settings; return { delivery: { state: "ready" } }; } } });
  try {
    await app.ready;
    dom.window.document.querySelector('[data-project="p"]').click();
    assert.equal(dom.window.document.querySelector('[data-delivery-layout="g"]').value, "reference");
    const layout = dom.window.document.querySelector('[data-delivery-layout="g"]');
    layout.value = "visual_story";
    layout.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    await app.refresh();
    assert.equal(dom.window.document.querySelector('[data-delivery-layout="g"]').value, "visual_story");
    dom.window.document.querySelector('[data-generate-deliverable="g"]').click();
    await tick();
    assert.equal(exported.layout, "visual_story");
    assert.match(dom.window.document.querySelector("main").textContent, /Connected Codex research/);
  } finally { app.destroy(); dom.window.close(); }
});
