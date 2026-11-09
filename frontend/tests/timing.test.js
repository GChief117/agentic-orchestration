import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { durationLabel, elapsedLabel, renderGoalTiming, renderTaskTiming, renderWorkspace } from "../src/views.js";
import { openGoalForm } from "../src/forms.js";
import { mountApp } from "../src/app.js";

const timing = {
  elapsed_seconds: 90,
  remaining_seconds: 180,
  remaining_low_seconds: 120,
  remaining_high_seconds: 240,
  eta_at: 1900000000,
  confidence: "low",
  basis: "history",
  sample_count: 3,
  active_tasks: 2,
  waiting_tasks: 5,
};

/** Read visible text from the same timing markup used by the application. */
function text(markup) {
  const dom = new JSDOM(markup);
  try { return dom.window.document.body.textContent; }
  finally { dom.window.close(); }
}

test("goal timing shows qualified ranges, local finish time, elapsed, and queue state", () => {
  const markup = renderGoalTiming(timing, { state: "running" });
  assert.match(text(markup), /Estimated remaining/);
  assert.match(text(markup), /About 2 min–4 min/);
  assert.match(text(markup), /Estimated finishAround/);
  assert.match(text(markup), /Elapsed1 min 30 sec/);
  assert.match(text(markup), /2 running · 5 waiting/);
  assert.match(text(markup), /Low confidence/);
  assert.match(text(markup), /Based on 3 completed tasks/);
  assert.match(markup, /<time datetime="/);
});

test("cold-start allowances are not presented as precise finish predictions", () => {
  const markup = renderGoalTiming({ ...timing, basis: "execution_limit", sample_count: 0, remaining_seconds: null, eta_at: null }, { preview: true });
  const visible = text(markup);
  assert.match(visible, /Initial time allowanceUp to 4 min/);
  assert.match(visible, /No duration history yet/);
  assert.match(visible, /proposed plan/);
  assert.doesNotMatch(visible, /Elapsed|running|Estimated finish/);
});

test("finished, failed, and cancelled goals show measured duration without future ETA", () => {
  for (const state of ["accepted", "failed", "cancelled", "blocked"]) {
    const visible = text(renderGoalTiming(timing, { state }));
    assert.match(visible, /Total elapsed1 min 30 sec/);
    assert.doesNotMatch(visible, /Estimated|Around|remaining|running/);
  }
  assert.equal(durationLabel(null), null);
  assert.equal(durationLabel(Number.NaN), null);
  assert.equal(durationLabel(-10), null);
  assert.equal(durationLabel(3660), "1 hr 1 min");
  assert.equal(elapsedLabel(11), "11 sec");
  assert.equal(elapsedLabel(163), "2 min 43 sec");
  assert.equal(elapsedLabel(3600), "1 hr");
  assert.equal(elapsedLabel(null), null);
  assert.equal(elapsedLabel(Number.NaN), null);
  assert.equal(elapsedLabel(-10), null);
});

test("unknown timing never turns null values into a zero-second promise", () => {
  const visible = text(renderGoalTiming({ ...timing, remaining_seconds: null, remaining_low_seconds: null, remaining_high_seconds: null, eta_at: null, sample_count: 0, basis: "unknown" }));
  assert.match(visible, /ETA unavailable/);
  assert.doesNotMatch(visible, /NaN|Invalid Date|Around|\b0 sec/);
});

test("task timing distinguishes queue wait, remaining completion, and measured duration", () => {
  const queued = text(renderTaskTiming({ ...timing, starts_in_seconds: 120 }, { state: "queued" }));
  assert.match(queued, /Estimated finish in: About 2 min–4 min/);
  assert.match(queued, /Estimated start in 2 min/);
  assert.doesNotMatch(queued, /Elapsed/);
  const running = text(renderTaskTiming(timing, { state: "running" }));
  assert.match(running, /Elapsed: 1 min 30 sec/);
  const done = text(renderTaskTiming(timing, { state: "accepted" }));
  assert.match(done, /Duration: 1 min 30 sec/);
  assert.doesNotMatch(done, /Estimated/);
});

test("goal cards use the backend timing record", () => {
  const snapshot = {
    projects: [{ id: "p", name: "Example", charter: { objective: "Example goal" } }],
    agents: [], tasks: [],
    goals: [{ id: "g", project_id: "p", title: "Review source", state: "running", total: 7, accepted: 0, timing }],
  };
  const markup = renderWorkspace(snapshot, { selectedProject: "p", tab: "work" });
  assert.match(markup, /goal-timing/);
  assert.match(text(markup), /2 running · 5 waiting/);
});

test("coordinator plan timing appears for review before starting work", async () => {
  const dom = new JSDOM("<body></body>");
  let started = false;
  try {
    const dialog = openGoalForm(dom.window.document, {
      project: { id: "p", name: "Example" },
      agents: [{ key: "lead", name: "Coordinator", role: "Coordination", provider: "openai", model: "default", execution_mode: "cli", capabilities: ["model.generate"] }],
    }, async () => { started = true; }, null, async () => ({
      summary: "Review the source", coordinator_key: "lead", team_revision: "revision", timing: { ...timing, task_timings: { "task-1": timing } },
      tasks: [{ id: "task-1", title: "Review source", agent: "lead", capability: "model.generate", parameters: { prompt: "Review source", path: "result.md" }, depends_on: [] }],
    }));
    dialog.querySelector('[data-field="goal"]').value = "Review the source";
    dialog.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(started, false);
    assert.match(dialog.textContent, /Proposed-plan estimate/);
    assert.match(dialog.textContent, /Proposed task estimate/);
    assert.match(dialog.textContent, /edits may change its duration/);
    dialog.close();
  } finally { dom.window.close(); }
});

test("visible live response and busy chart indicators update without collapsing previews", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const snapshot = {
    capacity: { execution_limit: 2 }, active_executions: 1,
    projects: [{ id: "p", name: "Example", mode: "greenfield", charter: { objective: "Example goal", scope: [], success_criteria: [], constraints: [] }, team_size: 1, max_concurrency: 1, active_executions: 1 }],
    agents: [{ id: "a", key: "lead", project_id: "p", name: "Coordinator", role: "Coordination", reports_to: null, collaborators: [], state: "busy", provider: "openai", model: "default" }],
    goals: [{ id: "g", project_id: "p", title: "Review source", state: "running", total: 1, accepted: 0, timing }],
    tasks: [{ id: "t", project_id: "p", goal_id: "g", agent_id: "a", title: "Draft findings", state: "running", capability: "model.generate", dependencies: [], timing, activity: { phase: "writing", message: "Writing response", preview: "<script>Visible text</script>", provider: "openai", model: "example-model" } }],
  };
  const app = mountApp({ document: dom.window.document, api: { state: async () => structuredClone(snapshot) }, pollInterval: 0 });
  try {
    await app.ready;
    dom.window.document.querySelector('[data-project="p"]').click();
    const preview = dom.window.document.querySelector('[data-activity-preview="t"]');
    assert.ok(preview);
    assert.match(dom.window.document.querySelector(".goal-activity").textContent, /Coordinator/);
    assert.match(dom.window.document.querySelector(".activity-model").textContent, /Model: openai · example-model/);
    assert.equal(preview.querySelector("pre").textContent, "<script>Visible text</script>");
    assert.equal(preview.querySelector("script"), null);
    assert.equal(dom.window.document.querySelectorAll(".node-busy").length, 1);
    assert.equal(dom.window.document.querySelectorAll(".task-row.is-running").length, 1);
    preview.open = true;
    snapshot.tasks[0].activity.preview = "More visible response";
    await app.refresh();
    const updated = dom.window.document.querySelector('[data-activity-preview="t"]');
    assert.equal(updated.open, true);
    assert.equal(updated.querySelector("pre").textContent, "More visible response");
    snapshot.tasks[0].state = "accepted";
    snapshot.agents[0].state = "idle";
    snapshot.goals[0].state = "accepted";
    await app.refresh();
    assert.equal(dom.window.document.querySelector(".goal-activity"), null);
    assert.equal(dom.window.document.querySelector(".node-busy"), null);
  } finally { app.destroy(); dom.window.close(); }
});
