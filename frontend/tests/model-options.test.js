import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { makeAgent } from "../src/presets.js";

/** Change an editor select through the same event path as a user. */
function change(dom, field, value) {
  field.value = value;
  field.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}

test("Claude defaults are usable and native model descriptions follow selection", () => {
  const dom = new JSDOM('<div id="editor"></div>');
  const root = dom.window.document.querySelector("#editor");
  const agent = { ...makeAgent("coordinator", "lead"), provider: "claude", model: "" };
  const editor = mountOrganizationEditor({
    root, agents: [agent], maxConcurrency: 1,
    modelProviders: [{ id: "claude", cli: {
      configured: true, models: ["default", "opus", "sonnet"],
      model_details: {
        default: { label: "Default (recommended)", description: "Use the CLI's default model." },
        opus: { label: "Opus", description: "Complex reasoning and analysis.", resolved_model: "claude-test-opus" },
        sonnet: { label: "Sonnet", description: "Balanced coding and everyday work." },
      },
    } }],
  });
  try {
    const select = root.querySelector("[data-agent-model]");
    assert.equal(select.value, "default");
    assert.equal(editor.snapshot().agents[0].model, "default");
    assert.ok([...select.options].some((option) => option.textContent === "Opus"));
    change(dom, select, "opus");
    assert.match(root.querySelector("[data-node-inspector]").textContent, /Complex reasoning and analysis/);
    assert.match(root.querySelector("[data-node-inspector]").textContent, /claude-test-opus/);
  } finally { editor.destroy(); dom.window.close(); }
});

test("Antigravity uses readable names and distinguishes Gemini from additional models", () => {
  const dom = new JSDOM('<div id="editor"></div>');
  const root = dom.window.document.querySelector("#editor");
  const agent = { ...makeAgent("builder", "builder"), provider: "gemini", model: "gemini-test-high" };
  const editor = mountOrganizationEditor({
    root, agents: [agent], maxConcurrency: 1,
    modelProviders: [{ id: "gemini", cli: {
      configured: true, models: ["default", "gemini-test-high", "partner-test"],
      model_details: {
        "gemini-test-high": { label: "Gemini test (High)", description: "Gemini model via Antigravity.", group: "Gemini models" },
        "partner-test": { label: "Partner <img src=x>", description: "Other <script>model</script>", group: "Other models through Antigravity" },
      },
    } }],
  });
  try {
    const select = root.querySelector("[data-agent-model]");
    assert.deepEqual([...select.querySelectorAll("optgroup")].map((group) => group.label), ["Gemini models", "Other models through Antigravity"]);
    assert.equal(select.selectedOptions[0].textContent, "Gemini test (High)");
    change(dom, select, "partner-test");
    assert.match(root.querySelector("[data-node-inspector]").textContent, /Other <script>model<\/script>/);
    assert.equal(root.querySelector("img, script"), null);
    assert.equal(root.querySelector('[data-team-model] option[value="partner-test"]').textContent, "Partner <img src=x>");
  } finally { editor.destroy(); dom.window.close(); }
});
