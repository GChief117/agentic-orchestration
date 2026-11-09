import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { guidedDraft, changeRole } from "../src/presets.js";
import { openModelConnections } from "../src/model-connections.js";
import { openGoalForm } from "../src/forms.js";
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
