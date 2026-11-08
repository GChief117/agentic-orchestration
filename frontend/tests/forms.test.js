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

test("guided project editor hides keys, suggests roles, and preserves editable instructions", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  let result;
  const dialog = openProjectForm(dom.window.document, async (payload) => {
    result = payload;
  });
  dialog.querySelector("[data-example]").click();
  assert.equal(dialog.querySelector('[data-field="key"]'), null);
  submit(dialog, dom.window);
  await tick();
  assert.equal(dialog.querySelectorAll(".org-card").length, 3);
  const builder = dialog.querySelector('[data-org-agent="agent-2"]');
  builder.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  const instructions = dialog.querySelector('[data-field="instructions"]');
  instructions.value = "Custom instructions for this project.";
  instructions.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  const role = dialog.querySelector('[data-agent-field="role_template"]');
  role.value = "writer";
  role.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(
    dialog.querySelector('[data-field="instructions"]').value,
    "Custom instructions for this project.",
  );
  submit(dialog, dom.window);
  await tick();
  assert.ok(dialog.querySelector(".setup-review"));
  assert.equal(result, undefined);
  submit(dialog, dom.window);
  await tick();
  assert.equal(result.team_size, 3);
  assert.equal(result.agents[1].role, "Writing");
  assert.equal(
    result.agents[1].instructions,
    "Custom instructions for this project.",
  );
  assert.equal(new Set(result.agents.map((agent) => agent.key)).size, 3);
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
  await tick();
  submit(dialog, dom.window);
  await tick();
  submit(dialog, dom.window);
  submit(dialog, dom.window);
  await tick();
  assert.equal(calls, 1);
  assert.ok(dialog.querySelector('[type="submit"]').disabled);
  assert.ok(dialog.querySelector("[data-back]").disabled);
  finish();
  await tick();
  assert.equal(dialog.isConnected, false);
});
