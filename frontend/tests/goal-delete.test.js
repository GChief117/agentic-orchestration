import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountApp } from "../src/app.js";
import { ApiClient } from "../src/api.js";

/** Provide completed, queued, and running goals to exercise the normal workspace controls. */
function workspace() {
  return {
    capacity: { execution_limit: 2 }, active_executions: 1,
    projects: [{ id: "p", name: "Project", mode: "greenfield", charter: { objective: "Work", scope: [], success_criteria: [], constraints: [] }, team_size: 1, max_concurrency: 2, active_executions: 1 }],
    agents: [{ id: "a", key: "lead", project_id: "p", name: "Coordinator", role: "Coordination", reports_to: null, collaborators: [], state: "busy" }],
    goals: ["accepted", "queued", "running"].map((state) => ({ id: state, project_id: "p", title: `${state} goal`, state, total: 1, accepted: state === "accepted" ? 1 : 0 })),
    tasks: ["accepted", "queued", "running"].map((state) => ({ id: `${state}-task`, project_id: "p", goal_id: state, agent_id: "a", title: "Work", state, capability: "terminal.python_version", dependencies: [] })),
    events: [], attempts: [],
  };
}

/** Wait for an asynchronous confirmation request and its subsequent workspace refresh. */
async function settle() {
  for (let tick = 0; tick < 5; tick++) await new Promise((resolve) => setImmediate(resolve));
}

test("deletion requires confirmation, keeps the selected project, and clears saved collapse state", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const state = workspace();
  const calls = [];
  const api = {
    state: async () => structuredClone(state),
    deleteGoal: async (id) => {
      calls.push(id);
      state.goals = state.goals.filter((goal) => goal.id !== id);
      state.tasks = state.tasks.filter((task) => task.goal_id !== id);
      return { ok: true };
    },
  };
  const app = mountApp({ document: dom.window.document, api, pollInterval: 0 });
  try {
    await app.ready;
    const document = dom.window.document;
    document.querySelector('[data-project="p"]').click();
    document.querySelector('[data-toggle-goal="accepted"]').click();
    document.querySelector('[data-delete-goal="accepted"]').click();
    assert.deepEqual(calls, []);
    let dialog = document.querySelector("dialog");
    assert.match(dialog.textContent, /Files and deliverables.*remain/);
    dialog.querySelector("[data-keep-goal]").click();
    assert.ok(document.querySelector('[data-goal="accepted"]'));
    document.querySelector('[data-delete-goal="accepted"]').click();
    dialog = document.querySelector("dialog");
    dialog.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await settle();
    assert.deepEqual(calls, ["accepted"]);
    assert.equal(document.querySelector('[data-goal="accepted"]'), null);
    assert.ok(document.querySelector('[data-goal="queued"]'));
    assert.ok(document.querySelector('[data-project="p"].current'));
    assert.deepEqual(JSON.parse(dom.window.localStorage.getItem("orchestration.goal-collapse.v1")), []);
    assert.equal(document.querySelector("dialog"), null);
  } finally { app.destroy(); dom.window.close(); }
});

test("active goals disable deletion and rejected requests leave the goal and confirmation intact", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const app = mountApp({ document: dom.window.document, api: {
    state: async () => workspace(),
    deleteGoal: async () => { throw new Error("Goal started running. Cancel it first."); },
  }, pollInterval: 0 });
  try {
    await app.ready;
    const document = dom.window.document;
    document.querySelector('[data-project="p"]').click();
    assert.equal(document.querySelector('[data-delete-goal="running"]').disabled, true);
    assert.equal(document.querySelector('[data-delete-goal="queued"]').disabled, false);
    document.querySelector('[data-delete-goal="queued"]').click();
    const dialog = document.querySelector("dialog");
    dialog.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await settle();
    assert.match(dialog.querySelector("[data-error]").textContent, /Cancel it first/);
    assert.equal(dialog.querySelector('[type="submit"]').disabled, false);
    assert.ok(document.querySelector('[data-goal="queued"]'));
  } finally { app.destroy(); dom.window.close(); }
});

test("API goal deletion uses the authenticated JSON mutation route", async () => {
  let request;
  const api = new ApiClient({ token: "session", fetch: async (path, options) => {
    request = { path, options };
    return { ok: true, json: async () => ({ ok: true }) };
  } });
  assert.deepEqual(await api.deleteGoal("goal/id"), { ok: true });
  assert.equal(request.path, "/api/goals/goal%2Fid/delete");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers.Authorization, "Bearer session");
  assert.equal(request.options.body, "{}");
});
