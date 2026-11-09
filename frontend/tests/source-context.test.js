import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openGoalForm } from "../src/forms.js";
import { guidedDraft } from "../src/presets.js";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("source selections survive filtering and are sent only when starting a model task", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    let payload;
    const agent = {
      ...guidedDraft().agents[0],
      provider: "openai",
      model: "test-model",
      capabilities: ["model.generate"],
    };
    const dialog = openGoalForm(
      dom.window.document,
      {
        project: { id: "p", name: "Work", source_path: "/work" },
        agents: [agent],
      },
      async (data) => {
        payload = data;
      },
      async () => ({
        files: [
          { path: "app.py", size: 40 },
          { path: "README.md", size: 20 },
        ],
        truncated: false,
      }),
    );
    await tick();
    assert.equal(payload, undefined);
    const pick = (path) => {
      const box = [...dialog.querySelectorAll("[data-source-file]")].find(
        (box) => box.value === path,
      );
      box.checked = true;
      box.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    };
    pick("app.py");
    const filter = dialog.querySelector("[data-source-filter]");
    filter.value = "readme";
    filter.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    pick("README.md");
    for (const [field, value] of Object.entries({
      "goal-title": "Review work",
      title: "Review",
      path: "review.txt",
      prompt: "Review these files",
    }))
      dialog.querySelector(`[data-field="${field}"]`).value = value;
    dialog
      .querySelector("form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await tick();
    assert.deepEqual(payload.tasks[0].parameters.source_files, [
      "app.py",
      "README.md",
    ]);
  } finally {
    dom.window.close();
  }
});
