import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { changeRole, guidedDraft, makeAgent, ROLES } from "../src/presets.js";
import {
  organizationTree,
  renderOrganizationChart,
} from "../src/organization.js";
import { createDictation } from "../src/speech.js";
import { openProjectForm } from "../src/project-form.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function submit(dialog, window) {
  dialog
    .querySelector("form")
    .dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }),
    );
}

test("role suggestions provide editable instructions and keep stable internal identities", () => {
  const original = makeAgent("builder", "agent-7", "agent-1");
  const updated = changeRole(original, "researcher");
  assert.equal(updated.key, "agent-7");
  assert.equal(updated.name, "Researcher");
  assert.equal(updated.instructions, ROLES.researcher.instructions);
  assert.equal(updated.reports_to, "agent-1");
  const custom = changeRole(
    { ...original, instructions: "Keep this edit." },
    "writer",
  );
  assert.equal(custom.instructions, "Keep this edit.");
});

test("organization chart nests reports and rejects cycles", () => {
  const draft = guidedDraft();
  const tree = organizationTree(draft.agents);
  assert.equal(tree.roots.length, 1);
  assert.equal(tree.roots[0].children.length, 2);
  assert.ok(renderOrganizationChart(draft.agents).includes("Project owner"));
  const persisted = draft.agents.map((agent, index) => ({
    ...agent,
    id: `stored-${index}`,
    reports_to: index ? "stored-0" : null,
  }));
  assert.equal(organizationTree(persisted).roots[0].children.length, 2);
  draft.agents[0].reports_to = draft.agents[1].key;
  assert.match(organizationTree(draft.agents).error, /cycle/);
});

test("guided setup provides suggestions and no manual unique-key input", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  let result;
  const dialog = openProjectForm(dom.window.document, async (payload) => {
    result = payload;
  });
  dialog.querySelector('[data-preset="research"]').click();
  assert.ok(
    dialog.querySelector('[data-field="objective"]').value.includes("report"),
  );
  assert.equal(dialog.querySelector('[data-field="key"]'), null);
  assert.equal(dialog.querySelector("[data-dictate]"), null);
  submit(dialog, dom.window);
  await tick();
  assert.equal(dialog.querySelectorAll("[data-role]").length, 4);
  assert.equal(dialog.querySelectorAll(".org-card").length, 4);
  const reports = dialog.querySelectorAll('[data-field="reports_to"]')[3];
  reports.value = "agent-2";
  reports.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(dialog.querySelectorAll(".org-tree > li > ul > li").length, 2);
  submit(dialog, dom.window);
  await tick();
  submit(dialog, dom.window);
  await tick();
  assert.equal(result.team_size, 4);
  assert.equal(result.agents[3].reports_to, "agent-2");
});

test("dictation is opt-in, appends text, and aborts cleanly", (t) => {
  const dom = new JSDOM("<textarea>Build a tool</textarea>");
  t.after(() => dom.window.close());
  const instances = [];
  class Recognition {
    constructor() {
      instances.push(this);
    }
    start() {
      this.started = true;
    }
    stop() {
      this.stopped = true;
    }
    abort() {
      this.aborted = true;
    }
  }
  dom.window.SpeechRecognition = Recognition;
  const controller = createDictation(dom.window);
  assert.equal(instances.length, 0);
  const field = dom.window.document.querySelector("textarea");
  controller.start(field, () => {});
  assert.equal(instances.length, 1);
  instances[0].onresult({ results: [[{ transcript: "for my team" }]] });
  assert.equal(field.value, "Build a tool for my team");
  instances[0].onresult({ results: [[{ transcript: "for everyone" }]] });
  assert.equal(field.value, "Build a tool for everyone");
  controller.destroy();
  assert.ok(instances[0].aborted);
  assert.equal(instances[0].onresult, null);
});

test("the guided voice button starts only on click and its transcript reaches the charter", async (t) => {
  const dom = new JSDOM("<!doctype html><body></body>");
  t.after(() => dom.window.close());
  const instances = [];
  class Recognition {
    constructor() {
      instances.push(this);
    }
    start() {}
    stop() {}
    abort() {
      this.aborted = true;
    }
  }
  dom.window.SpeechRecognition = Recognition;
  let result;
  const dialog = openProjectForm(dom.window.document, async (payload) => {
    result = payload;
  });
  assert.equal(instances.length, 0);
  dialog.querySelector("[data-dictate]").click();
  assert.equal(instances.length, 1);
  instances[0].onresult({
    results: [[{ transcript: "Make onboarding easier for our team" }]],
  });
  submit(dialog, dom.window);
  await tick();
  assert.ok(instances[0].aborted);
  submit(dialog, dom.window);
  await tick();
  submit(dialog, dom.window);
  await tick();
  assert.equal(result.charter.objective, "Make onboarding easier for our team");
  assert.equal(result.name, "Make onboarding easier for our team");
});
