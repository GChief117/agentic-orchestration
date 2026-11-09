import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { goalPayload } from "../src/domain.js";
import { openGoalForm } from "../src/forms.js";

const agents = [{ id: "a", key: "operator", name: "Operator", role: "Setup", capabilities: ["bridge.setup.run", "terminal.python_version"] }];

test("setup tasks serialize only their selected step", () => {
  const draft = { id: "install", title: "Install dependencies", agent: "operator", capability: "bridge.setup.run", setup_step: "backend.install", depends_on: [] };
  assert.deepEqual(goalPayload("p", "Prepare workspace", [draft], agents).tasks[0].parameters, { step: "backend.install" });
  assert.throws(() => goalPayload("p", "Prepare workspace", [{ ...draft, setup_step: "" }], agents), /Setup step/);
});

test("manual setup choices survive rerender without exposing commands", async () => {
  const dom = new JSDOM("<body></body>");
  let submitted;
  try {
    const dialog = openGoalForm(dom.window.document, {
      project: { id: "p", name: "Workspace", setup_steps: [{ id: "backend.install", label: "Install packages" }, { id: "backend.verify", label: "Verify database" }] },
      agents,
    }, async (value) => { submitted = value; });
    dialog.querySelector('[data-field="goal-title"]').value = "Prepare workspace";
    dialog.querySelector('[data-field="title"]').value = "Verify setup";
    dialog.querySelector('[data-field="setup_step"]').value = "backend.verify";
    const select = dialog.querySelector('[data-field="capability"]');
    select.value = "terminal.python_version";
    select.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    const next = dialog.querySelector('[data-field="capability"]');
    next.value = "bridge.setup.run";
    next.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.equal(dialog.querySelector('[data-field="setup_step"]').value, "backend.verify");
    assert.equal(dialog.querySelector('[data-field="command"]'), null);
    dialog.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(submitted.tasks[0].parameters, { step: "backend.verify" });
  } finally { dom.window.close(); }
});
