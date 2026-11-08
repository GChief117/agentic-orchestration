import test from "node:test";
import assert from "node:assert/strict";
import {
  goalPayload,
  projectPayload,
  starterProject,
  starterTasks,
  metrics,
  escapeHtml,
} from "../src/domain.js";
import { ApiClient, ApiError } from "../src/api.js";

const team = () => projectPayload(starterProject()).agents;

test("project roster size stays independent from execution capacity", () => {
  const draft = starterProject();
  draft.max_concurrency = "1";
  const project = projectPayload(draft);
  assert.equal(project.team_size, 3);
  assert.equal(project.max_concurrency, 1);
  assert.equal(project.agents[2].reports_to, "coordinator");
});

test("project form rejects duplicate roles' keys and reporting cycles", () => {
  const duplicate = starterProject();
  duplicate.agents[1].key = "coordinator";
  assert.throws(() => projectPayload(duplicate), /unique key/);
  const cycle = starterProject();
  cycle.agents[0].reports_to = "reviewer";
  assert.throws(() => projectPayload(cycle), /cycle/);
});

test("project capacity cannot exceed configured agent capacity", () => {
  const draft = starterProject();
  draft.max_concurrency = "4";
  assert.throws(() => projectPayload(draft), /combined capacity/);
});

test("goal payload names project, actors, dependencies, and expected bytes", () => {
  const agents = team();
  const goal = goalPayload(
    "project-1",
    "Verify artifacts",
    starterTasks(agents),
    agents,
  );
  assert.equal(goal.project_id, "project-1");
  assert.equal(goal.tasks[1].agent, "builder");
  assert.deepEqual(goal.tasks[2].depends_on, ["task-1", "task-2"]);
  assert.equal(
    goal.tasks[2].parameters.expected,
    goal.tasks[1].parameters.content,
  );
});

test("task form rejects unqualified actors and dependency cycles", () => {
  const agents = team();
  const tasks = starterTasks(agents);
  tasks[1].agent = "reviewer";
  assert.throws(
    () => goalPayload("p", "Goal", tasks, agents),
    /required capability/,
  );
  const cycle = starterTasks(agents);
  cycle[0].depends_on = ["task-3"];
  assert.throws(() => goalPayload("p", "Goal", cycle, agents), /cycle/);
});

test("file paths stay project-relative and empty expected text is retained", () => {
  const agents = team();
  const tasks = starterTasks(agents);
  tasks[1].path = "../other.txt";
  assert.throws(() => goalPayload("p", "Goal", tasks, agents), /relative path/);
  const check = [starterTasks(agents)[2]];
  check[0].depends_on = [];
  check[0].expected = "";
  assert.equal(
    goalPayload("p", "Empty file", check, agents).tasks[0].parameters.expected,
    "",
  );
});

test("workspace metrics use backend counts, not roster size as active work", () => {
  assert.deepEqual(
    metrics({
      projects: [{ id: "p" }],
      agents: [{}, {}, {}],
      tasks: [{ state: "accepted" }, { state: "queued" }],
      active_executions: 0,
      capacity: { execution_limit: 5 },
    }),
    { projects: 1, agents: 3, active: 0, capacity: 5, verified: 1 },
  );
});

test("API client transmits credentials only as headers and returns actionable errors", async () => {
  let request;
  const api = new ApiClient({
    token: "test-session",
    fetch: async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => ({ id: "created" }) };
    },
  });
  await api.cancelGoal("goal-1");
  assert.equal(request.url, "/api/cancel");
  assert.equal(request.options.headers.Authorization, "Bearer test-session");
  assert.deepEqual(JSON.parse(request.options.body), { id: "goal-1" });
  const denied = new ApiClient({
    token: "x",
    fetch: async () => ({
      status: 403,
      ok: false,
      json: async () => ({ error: "Denied" }),
    }),
  });
  await assert.rejects(
    denied.state(),
    (error) => error instanceof ApiError && /Reload/.test(error.message),
  );
});

test("dynamic content is escaped before HTML rendering", () => {
  assert.equal(
    escapeHtml('<img src=x onerror="bad()">'),
    "&lt;img src=x onerror=&quot;bad()&quot;&gt;",
  );
});
