import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openProjectForm, openGoalForm } from "../src/forms.js";
import { projectPayload, starterProject } from "../src/domain.js";
import { renderWorkspace } from "../src/views.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function submit(dialog, window) {
  dialog
    .querySelector("form")
    .dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
}

test("project editor submits a roster and can display client-side validation", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  let result;
  const dialog = openProjectForm(dom.window.document, async (payload) => {
    result = payload;
  });
  dialog.querySelector("[data-example]").click();
  dialog.querySelector('[data-field="max_concurrency"]').value = "99";
  submit(dialog, dom.window);
  await tick();
  assert.match(
    dialog.querySelector("[data-error]").textContent,
    /combined capacity/,
  );
  assert.equal(result, undefined);
  dialog.querySelector('[data-field="max_concurrency"]').value = "2";
  submit(dialog, dom.window);
  await tick();
  assert.equal(result.team_size, 3);
  assert.equal(result.agents[1].role, "Implementation");
});

test("goal editor restricts actions to the selected actor and submits dependency choices", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  const project = projectPayload(starterProject());
  let result;
  const dialog = openGoalForm(
    dom.window.document,
    { project: { id: "p", name: project.name }, agents: project.agents },
    async (payload) => {
      result = payload;
    },
  );
  dialog.querySelector("[data-example]").click();
  const writer = dialog.querySelectorAll("[data-task]")[1];
  assert.equal(
    writer.querySelectorAll('[data-field="capability"] option').length,
    1,
  );
  assert.equal(
    writer.querySelector('[data-field="capability"]').value,
    "files.write",
  );
  submit(dialog, dom.window);
  await tick();
  assert.equal(result.tasks.length, 3);
  assert.deepEqual(result.tasks[2].depends_on, ["task-1", "task-2"]);
});

test("project names and activity text cannot inject markup", (t) => {
  const dangerous = '<img src=x onerror="alert(1)">';
  const state = {
    projects: [
      {
        id: "p",
        name: dangerous,
        charter: { objective: dangerous },
        team_size: 0,
        active_executions: 0,
        max_concurrency: 1,
      },
    ],
    agents: [],
    goals: [],
    tasks: [],
    events: [],
    capacity: { execution_limit: 1 },
    active_executions: 0,
  };
  const dom = new JSDOM(renderWorkspace(state));
  t.after(() => dom.window.close());
  assert.equal(dom.window.document.querySelectorAll("img").length, 0);
  assert.ok(dom.window.document.body.textContent.includes(dangerous));
});

test("pending submissions disable editors and cannot be submitted twice", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  let calls = 0,
    finish;
  const deferred = new Promise((resolve) => {
    finish = resolve;
  });
  const dialog = openProjectForm(dom.window.document, async () => {
    calls += 1;
    await deferred;
  });
  dialog.querySelector("[data-example]").click();
  submit(dialog, dom.window);
  submit(dialog, dom.window);
  await tick();
  assert.equal(calls, 1);
  assert.ok(dialog.querySelector("[data-add-agent]").disabled);
  assert.ok(dialog.querySelector('[data-field="name"]').disabled);
  finish();
  await tick();
  assert.equal(dialog.isConnected, false);
});
