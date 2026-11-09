import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { guidedDraft, changeRole } from "../src/presets.js";
import { openModelConnections } from "../src/model-connections.js";
import { openGoalForm } from "../src/forms.js";
import { projectPayload } from "../src/domain.js";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Dispatch native change events on a selected value without a real browser. */
function change(dom, element, value) {
  element.value = value;
  element.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}

test("agents keep model assignments and reciprocal collaboration links", () => {
  const dom = new JSDOM('<div id="editor"></div>');
  try {
    const root = dom.window.document.querySelector("#editor");
    const editor = mountOrganizationEditor({
      root,
      agents: guidedDraft().agents,
      maxConcurrency: 2,
      selectedKey: "agent-2",
      modelProviders: [
        { id: "openai", configured: true, models: ["test-model"] },
      ],
    });
    change(dom, root.querySelector("[data-agent-provider]"), "openai");
    change(dom, root.querySelector("[data-agent-model]"), "test-model");
    const peer = root.querySelector('[data-collaborator][value="agent-3"]');
    peer.checked = true;
    peer.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    const team = editor.snapshot().agents;
    assert.equal(team[1].provider, "openai");
    assert.equal(team[1].model, "test-model");
    assert.ok(team[1].capabilities.includes("model.generate"));
    assert.deepEqual(team[1].collaborators, ["agent-3"]);
    assert.deepEqual(team[2].collaborators, ["agent-2"]);
    assert.equal(root.querySelectorAll(".org-collaborations path").length, 1);
    const writer = changeRole(team[1], "writer");
    assert.equal(writer.model, "test-model");
    assert.deepEqual(writer.collaborators, ["agent-3"]);
    editor.destroy();
  } finally {
    dom.window.close();
  }
});

test("local agents use native CLI models and preserve execution mode in saved rosters", () => {
  const dom = new JSDOM('<div id="editor"></div>');
  try {
    const root = dom.window.document.querySelector("#editor");
    const draft = guidedDraft();
    const editor = mountOrganizationEditor({
      root, agents: draft.agents, maxConcurrency: 2,
      modelProviders: [{
        id: "gemini", configured: true, models: ["api-model"],
        cli: { configured: true, state: "signed_in", models: ["default", "native-model"] },
      }],
    });
    assert.equal(root.querySelector("[data-agent-execution-mode]").value, "cli");
    change(dom, root.querySelector("[data-agent-provider]"), "gemini");
    assert.equal(root.querySelector("[data-agent-model]").value, "default");
    change(dom, root.querySelector("[data-agent-model]"), "native-model");
    const team = editor.snapshot();
    const payload = projectPayload({ ...draft, ...team, name: "CLI team", objective: "Run tasks" });
    assert.equal(payload.agents[0].execution_mode, "cli");
    assert.equal(payload.agents[0].model, "native-model");
    change(dom, root.querySelector("[data-agent-execution-mode]"), "api");
    assert.equal(root.querySelector("[data-agent-model]").value, "");
    assert.ok(root.querySelector('[data-agent-model] option[value="api-model"]'));
    assert.equal(editor.snapshot().agents[0].execution_mode, "api");
    editor.destroy();
  } finally {
    dom.window.close();
  }
});

test("API connection form clears keys and never stores them in the browser", async () => {
  const dom = new JSDOM("<body></body>", { url: "http://localhost" });
  try {
    const providers = [
      { id: "openai", name: "OpenAI", configured: false, models: [] },
    ];
    const calls = [];
    const api = {
      async models() {
        return { providers };
      },
      async connectModel(provider, key) {
        calls.push([provider, key]);
        return {
          providers: [
            { ...providers[0], configured: true, models: ["test-model"] },
          ],
        };
      },
    };
    const dialog = openModelConnections(dom.window.document, api, false);
    await tick();
    dialog.querySelector("[data-api-key]").value = "private-test-key";
    dialog
      .querySelector("form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    assert.deepEqual(calls, [["openai", "private-test-key"]]);
    assert.equal(dialog.querySelector("[data-api-key]").value, "");
    assert.equal(dom.window.localStorage.length, 0);
    assert.equal(dialog.textContent.includes("private-test-key"), false);
    dialog.close();
  } finally {
    dom.window.close();
  }
});

test("connections start on local CLI, ignore stale API loads, and clear keys when switching", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    let modelCalls = 0;
    let finishFirstLoad;
    const result = {
      providers: [{ id: "openai", name: "OpenAI", configured: false, models: [] }],
    };
    const api = {
      async providers() {
        return { providers: [] };
      },
      models() {
        modelCalls += 1;
        if (modelCalls === 1)
          return new Promise((resolve) => {
            finishFirstLoad = resolve;
          });
        return Promise.resolve(result);
      },
    };
    const dialog = openModelConnections(dom.window.document, api);
    await tick();
    const tabs = [...dialog.querySelectorAll('[role="tab"]')];
    assert.deepEqual(tabs.map((tab) => tab.textContent), ["Local CLI", "API keys"]);
    assert.equal(tabs[0].getAttribute("aria-selected"), "true");
    assert.equal(modelCalls, 0);
    assert.ok(dialog.querySelector("[data-providers]"));
    tabs[1].click();
    tabs[0].click();
    finishFirstLoad(result);
    await tick();
    assert.ok(dialog.querySelector("[data-providers]"));
    assert.equal(dialog.querySelector("[data-api-key]"), null);
    tabs[0].dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key: "ArrowRight", bubbles: true,
      }),
    );
    await tick();
    assert.equal(tabs[1].getAttribute("aria-selected"), "true");
    assert.equal(dom.window.document.activeElement, tabs[1]);
    const key = dialog.querySelector("[data-api-key]");
    key.value = "unsubmitted-private-key";
    tabs[1].dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key: "ArrowLeft", bubbles: true,
      }),
    );
    await tick();
    assert.equal(key.value, "");
    assert.equal(dom.window.document.activeElement, tabs[0]);
    assert.equal(dom.window.document.querySelectorAll("dialog").length, 1);
    dialog.close();
  } finally {
    dom.window.close();
  }
});

test("model task form submits prompt, output path, and token budget", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    let payload;
    const agent = {
      ...guidedDraft().agents[0],
      provider: "claude",
      model: "test-model",
      capabilities: ["model.generate"],
    };
    const dialog = openGoalForm(
      dom.window.document,
      { project: { id: "project", name: "Test" }, agents: [agent] },
      async (result) => {
        payload = result;
      },
    );
    assert.match(dialog.textContent, /Requested output tokens/);
    assert.match(dialog.textContent, /not a billing cap/);
    for (const [field, value] of Object.entries({
      "goal-title": "Create a plan",
      title: "Plan",
      path: "plan.txt",
      prompt: "Write the plan",
      max_tokens: "512",
    }))
      dialog.querySelector(`[data-field="${field}"]`).value = value;
    dialog
      .querySelector("form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    assert.equal(payload.tasks[0].capability, "model.generate");
    assert.deepEqual(payload.tasks[0].parameters, {
      path: "plan.txt",
      prompt: "Write the plan",
      max_tokens: 512,
    });
  } finally {
    dom.window.close();
  }
});
