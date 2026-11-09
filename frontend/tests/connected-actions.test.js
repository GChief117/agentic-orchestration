import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { goalPayload } from "../src/domain.js";
import { openGoalForm } from "../src/forms.js";
import { renderWorkspace } from "../src/views.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const actions = ["bridge.notes.create", "bridge.demo.launch"];

/** A connected operator role can perform exactly the explicit workspace actions. */
function agents() {
  return [{ id: "a", key: "operator", project_id: "p", name: "Workspace operator", role: "Operations", capabilities: [...actions], reports_to: null, collaborators: [], state: "idle" }];
}

/** Send the visible manual editor through its actual submit handler. */
function submit(dialog, window) {
  dialog.querySelector("form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
}

test("Notes drafts serialize title and body without treating the destination as a source file", () => {
  const payload = goalPayload("p", "Prepare note", [{ id: "note", title: "Draft message", agent: "operator", capability: "bridge.notes.create", note_title: "Demo follow-up", content: "Hello Jason, the demo is ready to review.", depends_on: [] }], agents());
  assert.deepEqual(payload.tasks[0].parameters, { title: "Demo follow-up", content: "Hello Jason, the demo is ready to review." });
  assert.equal(Object.hasOwn(payload.tasks[0].parameters, "path"), false);
});

test("dependency-backed note content is retained and must name a declared handoff", () => {
  const team = [{ ...agents()[0], capabilities: [...actions, "files.write"] }];
  const draft = [{ id: "draft", title: "Prepare words", agent: "operator", capability: "files.write", path: "draft.txt", content: "Hello Jason", depends_on: [] }, { id: "note", title: "Open the note", agent: "operator", capability: "bridge.notes.create", note_title: "Demo", content_task: "draft", depends_on: ["draft"] }];
  assert.deepEqual(goalPayload("p", "Demo", draft, team).tasks[1].parameters, { title: "Demo", content_task: "draft" });
  draft[1].depends_on = [];
  assert.throws(() => goalPayload("p", "Demo", draft, team), /one of its dependencies/);
});

test("demo launch serializes a validated port and defaults to 8765", () => {
  const draft = { id: "demo", title: "Launch demo", agent: "operator", capability: "bridge.demo.launch", depends_on: [] };
  assert.deepEqual(goalPayload("p", "Demo", [draft], agents()).tasks[0].parameters, { port: 8765 });
  draft.port = "57013";
  assert.deepEqual(goalPayload("p", "Demo", [draft], agents()).tasks[0].parameters, { port: 57013 });
  draft.port = "65536";
  assert.throws(() => goalPayload("p", "Demo", [draft], agents()), /1024 to 65535/);
  draft.port = "1023";
  assert.throws(() => goalPayload("p", "Demo", [draft], agents()), /1024 to 65535/);
});

test("manual Notes and demo fields survive action rerenders and submit exact bridge parameters", async () => {
  const dom = new JSDOM("<body></body>");
  let submitted;
  try {
    const dialog = openGoalForm(dom.window.document, { project: { id: "p", name: "Connected project" }, agents: agents() }, async (payload) => { submitted = payload; });
    dialog.querySelector('[data-field="goal-title"]').value = "Prepare demo follow-up";
    dialog.querySelector('[data-task] [data-field="title"]').value = "Create the Notes draft";
    dialog.querySelector('[data-field="note_title"]').value = "Message to Jason";
    dialog.querySelector('[data-field="content"]').value = "Hi Jason, I have a local demo to show you.";
    const changeAction = (value) => {
      const select = dialog.querySelector('[data-field="capability"]');
      select.value = value;
      select.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    };
    changeAction("bridge.demo.launch");
    assert.equal(dialog.querySelector('[data-field="port"]').value, "8765");
    changeAction("bridge.notes.create");
    assert.equal(dialog.querySelector('[data-field="note_title"]').value, "Message to Jason");
    assert.equal(dialog.querySelector('[data-field="content"]').value, "Hi Jason, I have a local demo to show you.");
    assert.equal(dialog.querySelector('[data-field="path"]'), null);
    submit(dialog, dom.window);
    await tick();
    assert.deepEqual(submitted.tasks[0].parameters, { title: "Message to Jason", content: "Hi Jason, I have a local demo to show you." });
    assert.match(dialog.textContent, /does not send a message/);
  } finally { dom.window.close(); }
});

test("overview shows registered connected capabilities as text without claiming completed execution", () => {
  const snapshot = { capacity: { execution_limit: 1 }, active_executions: 0, projects: [{ id: "p", name: "Demo", mode: "existing", charter: { objective: "Show workflow", scope: [], constraints: [], success_criteria: [] }, team_size: 1, max_concurrency: 1, active_executions: 0, workspace_capabilities: [...actions] }], agents: agents(), goals: [], tasks: [], events: [] };
  const dom = new JSDOM(renderWorkspace(snapshot, { selectedProject: "p", tab: "overview" }));
  try {
    assert.match(dom.window.document.body.textContent, /Connected workspace actions/);
    assert.match(dom.window.document.body.textContent, /Create a Notes draft on connected Mac/);
    assert.match(dom.window.document.body.textContent, /Launch the connected workspace demo/);
    assert.match(dom.window.document.body.textContent, /matching permission/);
  } finally { dom.window.close(); }
});
