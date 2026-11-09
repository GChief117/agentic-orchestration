import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { guidedDraft } from "../src/presets.js";

const providers = [
  {
    id: "openai", name: "OpenAI", configured: true, models: ["api-model"],
    cli: {
      configured: true, state: "signed_in", models: ["default", "team-model"],
      speed_modes: ["standard", "fast", "ultrafast"],
    },
  },
  {
    id: "gemini", name: "Gemini", configured: false, models: [],
    cli: {
      configured: true, state: "signed_in", models: ["default"],
      speed_modes: ["standard"],
    },
  },
];

/** Change the active form control using the same event as a native select. */
function choose(dom, root, selector, value) {
  const control = root.querySelector(selector);
  control.value = value;
  control.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}

/** Mount a chart editor with isolated sample agents and automatic cleanup. */
function setup(t, agents = guidedDraft().agents, catalog = providers) {
  const dom = new JSDOM('<main id="editor"></main>');
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root, agents, maxConcurrency: 2, modelProviders: catalog,
  });
  t.after(() => { editor.destroy(); dom.window.close(); });
  return { dom, root, editor };
}

test("shared model selection is a draft until explicitly applied to every agent", (t) => {
  const agents = guidedDraft().agents.map((agent, index) => ({ ...agent, id: `saved-${index}` }));
  const { dom, root, editor } = setup(t, agents);
  const before = editor.snapshot().agents;
  assert.equal(root.firstElementChild.getAttribute("aria-label"), "Shared team model");
  assert.equal(root.querySelector("[data-team-execution-mode]").options[0].value, "cli");
  assert.equal(root.querySelector("[data-team-provider]").value, "openai");
  choose(dom, root, "[data-team-model]", "team-model");
  choose(dom, root, "[data-team-speed]", "ultrafast");
  assert.deepEqual(editor.snapshot().agents, before);
  root.querySelector('[data-chart-action="apply-team-model"]').click();
  for (const [index, agent] of editor.snapshot().agents.entries()) {
    assert.equal(agent.execution_mode, "cli");
    assert.equal(agent.provider, "openai");
    assert.equal(agent.model, "team-model");
    assert.equal(agent.execution_speed, "ultrafast");
    assert.ok(agent.capabilities.includes("model.generate"));
    for (const field of ["id", "key", "role", "instructions", "reports_to"])
      assert.equal(agent[field], before[index][field]);
    assert.deepEqual(agent.collaborators, before[index].collaborators);
  }
  assert.match(root.querySelector("[data-team-model-status]").textContent, /share one model/);
});

test("new roles inherit the applied model and individual overrides remain optional", (t) => {
  const { dom, root, editor } = setup(t);
  choose(dom, root, "[data-team-model]", "team-model");
  choose(dom, root, "[data-team-speed]", "fast");
  root.querySelector('[data-chart-action="apply-team-model"]').click();
  choose(dom, root, "[data-agent-provider]", "gemini");
  assert.match(root.querySelector("[data-team-model-status]").textContent, /Mixed/);
  assert.equal(editor.snapshot().agents[0].provider, "gemini");
  root.querySelector('[data-chart-action="add"]').click();
  const added = editor.snapshot().agents.at(-1);
  assert.equal(added.provider, "openai");
  assert.equal(added.model, "team-model");
  assert.equal(added.execution_speed, "fast");
  assert.equal(added.reports_to, "agent-1");
  root.querySelector('[data-chart-action="apply-team-model"]').click();
  assert.ok(editor.snapshot().agents.every((agent) => agent.provider === "openai"));
});

test("reopening a uniformly configured team infers its shared model for new roles", (t) => {
  const agents = guidedDraft().agents.map((agent) => ({
    ...agent, execution_mode: "cli", provider: "openai", model: "team-model",
    execution_speed: "fast",
  }));
  const { dom, root, editor } = setup(t, agents);
  assert.equal(root.querySelector("[data-team-model]").value, "team-model");
  assert.equal(root.querySelector("[data-team-speed]").value, "fast");
  choose(dom, root, "[data-team-model]", "default");
  root.querySelector('[data-chart-action="add"]').click();
  assert.equal(editor.snapshot().agents.at(-1).model, "team-model");
});

test("mixed configurations are untouched until Apply and hosted teams use API controls", (t) => {
  const agents = guidedDraft().agents;
  Object.assign(agents[0], { execution_mode: "api", provider: "openai", model: "api-model" });
  Object.assign(agents[1], { execution_mode: "cli", provider: "gemini", model: "default" });
  const { root, editor } = setup(t, agents);
  assert.match(root.querySelector("[data-team-model-status]").textContent, /Mixed/);
  assert.equal(editor.snapshot().agents[0].execution_mode, "api");
  assert.equal(editor.snapshot().agents[1].provider, "gemini");
  const hosted = setup(t, guidedDraft().agents, [{
    id: "openai", name: "OpenAI", configured: true, models: ["api-model"],
  }]);
  assert.equal(hosted.root.querySelector("[data-team-execution-mode]"), null);
  assert.equal(hosted.root.querySelector("[data-team-model]").value, "api-model");
  hosted.root.querySelector('[data-chart-action="apply-team-model"]').click();
  assert.ok(hosted.editor.snapshot().agents.every((agent) => agent.execution_mode === "api"));
});

test("unverified saved speed cannot be propagated to the team until supported", (t) => {
  const agents = guidedDraft().agents.map((agent) => ({
    ...agent, provider: "openai", model: "saved-model", execution_speed: "ultrafast",
  }));
  const { root, editor } = setup(t, agents, [{
    id: "openai", cli: { configured: false, state: "unverified", models: [] },
  }]);
  const before = editor.snapshot().agents;
  root.querySelector('[data-chart-action="apply-team-model"]').click();
  assert.match(root.querySelector("[data-chart-message]").textContent, /Verify/);
  assert.deepEqual(editor.snapshot().agents, before);
});
