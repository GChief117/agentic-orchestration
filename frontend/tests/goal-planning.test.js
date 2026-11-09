import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openGoalForm } from "../src/forms.js";
import { ApiClient } from "../src/api.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Build assigned model agents with a root coordinator and one contributor. */
function team() {
  return [
    { key: "lead", name: "Coordinator", role: "Coordination", reports_to: null },
    { key: "builder", name: "Builder", role: "Implementation", reports_to: "lead" },
  ].map((agent) => ({ ...agent, provider: "openai", model: "default", execution_mode: "cli", capabilities: ["model.generate"] }));
}

/** Return a validated-looking coordinator result without calling a provider. */
function plan(revision = "team-revision") {
  return {
    summary: "Inspect the selected source, then review the findings.",
    coordinator_key: "lead",
    team_revision: revision,
    tasks: [
      { id: "task-1", title: "Inspect project", agent: "builder", capability: "model.generate", parameters: { prompt: "Inspect the source", path: "results/inspect.md", source_files: ["README.md"] }, depends_on: [] },
      { id: "task-2", title: "Review findings", agent: "lead", capability: "model.generate", parameters: { prompt: "Review the findings", path: "results/review.md", source_files: ["README.md"] }, depends_on: ["task-1"] },
    ],
  };
}

/** Submit the currently visible stage without depending on native dialog APIs. */
function submit(dialog, dom) {
  dialog.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
}

test("goal description and selected sources produce a review before tasks can start", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    const planned = [], started = [];
    let finishFiles;
    const files = new Promise((resolve) => { finishFiles = resolve; });
    const dialog = openGoalForm(dom.window.document,
      { project: { id: "project", name: "Work", source_path: "/project" }, agents: team() },
      async (payload) => { started.push(payload); },
      () => files,
      async (payload) => { planned.push(payload); return plan(); },
    );
    assert.equal(dialog.querySelector("[data-task]"), null);
    assert.equal(dialog.querySelector('[data-field="max_tokens"]'), null);
    assert.equal(dialog.querySelector('[data-field="path"]'), null);
    assert.equal(dialog.querySelector('[data-field="coordinator"]').value, "lead");
    dialog.querySelector('[data-field="goal"]').value = "Summarize this project’s technology";
    finishFiles({ files: [{ path: "README.md", size: 80 }], truncated: false });
    await tick();
    assert.equal(dialog.querySelector('[data-field="goal"]').value, "Summarize this project’s technology");
    const source = dialog.querySelector("[data-source-file]");
    source.checked = true;
    source.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    submit(dialog, dom);
    await tick();
    assert.deepEqual(planned, [{ goal: "Summarize this project’s technology", coordinator: "lead", source_files: ["README.md"], deliverable: { format: "pdf", style: "editorial", layout: "visual_story" }, research_mode: "source_context" }]);
    assert.equal(started.length, 0);
    assert.equal(dialog.querySelectorAll("[data-task]").length, 2);
    assert.match(dialog.textContent, /Inspect the selected source/);
    assert.equal(dialog.querySelector('[data-field="goal-title"]').value, "Summarize this project’s technology");
    assert.equal(dialog.querySelector("[data-task-advanced]").open, false);
    dialog.querySelector('[data-task="task-1"] [data-field="title"]').value = "Inspect technology choices";
    submit(dialog, dom);
    await tick();
    assert.equal(started.length, 1);
    assert.equal(started[0].team_revision, "team-revision");
    assert.equal(started[0].tasks[0].title, "Inspect technology choices");
    assert.deepEqual(started[0].tasks[1].depends_on, ["task-1"]);
    assert.deepEqual(started[0].tasks[0].parameters.source_files, ["README.md"]);
    assert.equal(Object.hasOwn(started[0].tasks[0].parameters, "max_tokens"), false);
  } finally { dom.window.close(); }
});

test("plan errors keep the goal editable and revision errors require explicit replan", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    let plans = 0, submissions = 0;
    const dialog = openGoalForm(dom.window.document,
      { project: { id: "project", name: "Work" }, agents: team() },
      async () => { submissions += 1; throw new Error("The team changed. Plan again."); },
      null,
      async () => { plans += 1; if (plans === 1) throw new Error("Coordinator unavailable"); return plan(); },
    );
    dialog.querySelector('[data-field="goal"]').value = "Review the project";
    submit(dialog, dom);
    await tick();
    assert.match(dialog.querySelector("[data-error]").textContent, /Coordinator unavailable/);
    assert.equal(dialog.querySelector('[data-field="goal"]').value, "Review the project");
    submit(dialog, dom);
    await tick();
    submit(dialog, dom);
    await tick();
    assert.equal(submissions, 1);
    assert.equal(plans, 2);
    assert.match(dialog.querySelector("[data-error]").textContent, /team changed/);
    assert.equal(dialog.querySelectorAll("[data-task]").length, 2);
    dialog.querySelector("[data-edit-goal]").click();
    assert.equal(dialog.querySelector('[data-field="goal"]').value, "Review the project");
    assert.equal(plans, 2);
  } finally { dom.window.close(); }
});

test("manual task definition remains available without calling the coordinator", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    let calls = 0, payload;
    const dialog = openGoalForm(dom.window.document,
      { project: { id: "project", name: "Work" }, agents: team() },
      async (result) => { payload = result; }, null,
      async () => { calls += 1; return plan(); },
    );
    dialog.querySelector('[data-field="goal"]').value = "Review the project";
    dialog.querySelector("[data-manual-goal]").click();
    assert.equal(dialog.querySelector('[data-field="prompt"]').value, "Review the project");
    submit(dialog, dom);
    await tick();
    assert.equal(calls, 0);
    assert.equal(payload.tasks.length, 1);
    assert.equal(Object.hasOwn(payload, "team_revision"), false);
  } finally { dom.window.close(); }
});

test("planning API uses the project endpoint and structured request body", async () => {
  let requested;
  const api = new ApiClient({ token: "session", fetch: async (url, options) => {
    requested = { url, options };
    return { ok: true, json: async () => plan() };
  } });
  const payload = { goal: "Review source", coordinator: "lead", source_files: [] };
  await api.planGoal("project/id", payload);
  assert.equal(requested.url, "/api/projects/project%2Fid/plan");
  assert.deepEqual(JSON.parse(requested.options.body), payload);
});

test("bridge source selection keeps each planned task's own files through review and submission", async () => {
  const dom = new JSDOM("<body></body>");
  const paths = ["seed/README.md", "engine/src/index.js", "app/config.yaml", "app/README.md"];
  let planned, started;
  let filesRequested = 0;
  try {
    const dialog = openGoalForm(dom.window.document,
      { project: { id: "project", name: "Remote work", has_source_bridge: true }, agents: team() },
      async (payload) => { started = payload; },
      async () => {
        filesRequested += 1;
        return { files: paths.map((path) => ({ path, size: 80 })), bridge: { name: "Polaris", hostname: "polaris-host" }, truncated: false };
      },
      async (payload) => {
        planned = payload;
        const result = plan();
        result.tasks[0].parameters.source_files = [paths[1]];
        result.tasks[1].parameters.source_files = [paths[2], paths[3]];
        return result;
      },
    );
    await tick();
    assert.equal(filesRequested, 1);
    assert.match(dialog.textContent, /Connected source bridge: Polaris · polaris-host/);
    const seed = [...dialog.querySelectorAll("[data-source-file]")].find((box) => box.value === paths[0]);
    seed.checked = true;
    seed.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    dialog.querySelector('[data-field="goal"]').value = "Review both repositories";
    submit(dialog, dom);
    await tick();
    assert.deepEqual(planned.source_files, [paths[0]]);
    const selected = (id) => [...dialog.querySelectorAll(`[data-task="${id}"] [data-source-file]:checked`)].map((box) => box.value);
    assert.deepEqual(selected("task-1"), [paths[1]]);
    assert.deepEqual(selected("task-2"), [paths[2], paths[3]]);
    const agent = dialog.querySelector('[data-task="task-1"] [data-field="agent"]');
    agent.value = "lead";
    agent.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.deepEqual(selected("task-1"), [paths[1]]);
    assert.deepEqual(selected("task-2"), [paths[2], paths[3]]);
    submit(dialog, dom);
    await tick();
    assert.deepEqual(started.tasks[0].parameters.source_files, [paths[1]]);
    assert.deepEqual(started.tasks[1].parameters.source_files, [paths[2], paths[3]]);
  } finally { dom.window.close(); }
});
