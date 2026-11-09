import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { openTeamForm } from "../src/team-form.js";
import { openProjectForm } from "../src/project-form.js";
import { projectPayload } from "../src/domain.js";
import { guidedDraft } from "../src/presets.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const savedModel = {
  execution_mode: "cli",
  provider: "claude",
  model: "sonnet",
  execution_speed: "standard",
};
const catalog = [
  { id: "openai", cli: { configured: true, models: ["default", "team-model"] } },
  { id: "claude", cli: { configured: true, models: ["default", "sonnet"] } },
  { id: "gemini", cli: { configured: true, models: ["default", "gemini-test"] } },
];

/** Exercise the same native select event path as the shared controls. */
function choose(dom, root, selector, value) {
  const field = root.querySelector(selector);
  field.value = value;
  field.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}

/** Submit an editor without relying on a browser's implicit submit behavior. */
function submit(dom, root) {
  root.querySelector("form").dispatchEvent(
    new dom.window.Event("submit", { bubbles: true, cancelable: true }),
  );
}

/** Build independently configured agents to catch accidental model propagation. */
function mixedAgents() {
  return guidedDraft().agents.map((agent, index) => ({
    ...agent,
    provider: index === 1 ? "gemini" : "openai",
    model: index === 1 ? "gemini-test" : "team-model",
  }));
}

test("a saved team default reopens independently of mixed agent overrides", () => {
  const dom = new JSDOM('<main id="editor"></main>');
  const root = dom.window.document.querySelector("#editor");
  const agents = mixedAgents();
  const editor = mountOrganizationEditor({
    root, agents, maxConcurrency: 2, modelProviders: catalog, teamModel: savedModel,
  });
  try {
    assert.equal(root.querySelector("[data-team-provider]").value, "claude");
    assert.equal(root.querySelector("[data-team-model]").value, "sonnet");
    assert.match(root.querySelector("[data-team-model-status]").textContent, /Saved team default/);
    assert.match(root.querySelector("[data-team-model-status]").textContent, /Mixed/);
    assert.deepEqual(editor.snapshot().team_model, savedModel);
    assert.deepEqual(editor.snapshot().agents.map((agent) => agent.model), agents.map((agent) => agent.model));
    root.querySelector('[data-chart-action="add"]').click();
    const added = editor.snapshot().agents.at(-1);
    for (const [key, value] of Object.entries(savedModel)) assert.equal(added[key], value);
  } finally { editor.destroy(); dom.window.close(); }
});

test("saving a selected team default does not require applying it to agent overrides", async () => {
  const dom = new JSDOM("<body></body>");
  const agents = mixedAgents();
  let payload;
  const dialog = openTeamForm(dom.window.document, {
    project: {
      name: "Project", max_concurrency: 2, team_model: savedModel,
      charter: { objective: "Review work", scope: ["Review"], constraints: [], success_criteria: ["Reviewed"] },
    }, agents, tasks: [],
  }, async (value) => { payload = value; }, null, catalog);
  try {
    choose(dom, dialog, "[data-team-provider]", "gemini");
    choose(dom, dialog, "[data-team-model]", "gemini-test");
    assert.match(dialog.querySelector("[data-team-model-status]").textContent, /Save organization/);
    submit(dom, dialog);
    await tick();
    assert.equal(payload.team_model.provider, "gemini");
    assert.equal(payload.team_model.model, "gemini-test");
    assert.deepEqual(payload.agents.map((agent) => agent.model), agents.map((agent) => agent.model));
    assert.equal(dialog.isConnected, false);
  } finally { if (dialog.isConnected) dialog.close(); dom.window.close(); }
});

test("project setup retains the team selection across review and backward navigation", async () => {
  const dom = new JSDOM("<body></body>");
  let payload;
  const dialog = openProjectForm(dom.window.document, async (value) => { payload = value; }, catalog);
  try {
    dialog.querySelector("[data-example]").click();
    submit(dom, dialog);
    choose(dom, dialog, "[data-team-provider]", "claude");
    choose(dom, dialog, "[data-team-model]", "sonnet");
    submit(dom, dialog);
    dialog.querySelector("[data-back]").click();
    assert.equal(dialog.querySelector("[data-team-provider]").value, "claude");
    assert.equal(dialog.querySelector("[data-team-model]").value, "sonnet");
    submit(dom, dialog);
    submit(dom, dialog);
    await tick();
    assert.deepEqual(payload.team_model, savedModel);
    assert.ok(payload.agents.every((agent) => agent.provider == null));
  } finally { if (dialog.isConnected) dialog.close(); dom.window.close(); }
});

test("team model payload validation rejects unsupported execution settings", () => {
  const draft = guidedDraft("software");
  draft.team_model = { ...savedModel, execution_mode: "api", execution_speed: "ultrafast" };
  assert.throws(() => projectPayload(draft), /require Local CLI/);
  draft.team_model = { ...savedModel, model: "-unsafe" };
  assert.throws(() => projectPayload(draft), /valid team model/);
});

test("hosted editor uses API settings without changing the persisted input object", () => {
  const dom = new JSDOM('<main id="editor"></main>');
  const root = dom.window.document.querySelector("#editor");
  const original = { ...savedModel, execution_speed: "fast" };
  const editor = mountOrganizationEditor({
    root, agents: mixedAgents(), maxConcurrency: 2, teamModel: original,
    modelProviders: [{ id: "claude", configured: true, models: ["sonnet"] }],
  });
  try {
    assert.equal(root.querySelector("[data-team-execution-mode]"), null);
    assert.equal(editor.snapshot().team_model.execution_mode, "api");
    assert.equal(editor.snapshot().team_model.execution_speed, "standard");
    assert.equal(original.execution_mode, "cli");
    assert.equal(original.execution_speed, "fast");
    assert.match(root.querySelector("[data-team-model-status]").textContent, /Save organization/);
  } finally { editor.destroy(); dom.window.close(); }
});
