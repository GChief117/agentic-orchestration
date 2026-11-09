import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountApp } from "../src/app.js";

/** Build a real workspace with one finished and one active goal. */
function workspace() {
  return {
    capacity: { execution_limit: 2 }, active_executions: 1,
    projects: [{ id: "p", name: "Project", mode: "greenfield", charter: { objective: "Work", scope: [], success_criteria: [], constraints: [] }, team_size: 1, max_concurrency: 2, active_executions: 1 }],
    agents: [{ id: "a", key: "lead", project_id: "p", name: "Coordinator", role: "Coordination", reports_to: null, collaborators: [], state: "busy", provider: "openai", model: "default" }],
    goals: [
      { id: "finished", project_id: "p", title: "Finished report", state: "accepted", total: 1, accepted: 1, deliverable: { format: "pdf", style: "editorial" }, delivery: { state: "ready", format: "pdf", name: "report.pdf" } },
      { id: "active", project_id: "p", title: "Active report", state: "running", total: 1, accepted: 0 },
    ],
    tasks: [
      { id: "done", project_id: "p", goal_id: "finished", agent_id: "a", title: "Write report", state: "accepted", capability: "model.generate", dependencies: [], receipt: { artifact: "report.md" } },
      { id: "working", project_id: "p", goal_id: "active", agent_id: "a", title: "Research", state: "running", capability: "model.generate", dependencies: [] },
    ], events: [],
  };
}

/** Open the same project through normal application navigation. */
async function openProject(app, document) {
  await app.ready;
  document.querySelector('[data-project="p"]').click();
}

test("goal collapse hides details but keeps status and the final download accessible across refresh and navigation", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const state = workspace();
  const api = { state: async () => structuredClone(state) };
  const app = mountApp({ document: dom.window.document, api, pollInterval: 0 });
  try {
    await openProject(app, dom.window.document);
    let toggle = dom.window.document.querySelector('[data-toggle-goal="finished"]');
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    toggle.click();
    toggle = dom.window.document.querySelector('[data-toggle-goal="finished"]');
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(dom.window.document.querySelector('[data-goal-body="finished"]').hidden, true);
    assert.equal(dom.window.document.querySelector('[data-goal-body="active"]').hidden, false);
    const download = dom.window.document.querySelector('[data-download-goal="finished"]');
    assert.ok(download);
    assert.equal(download.closest('[data-goal-body="finished"]'), null);
    assert.match(toggle.closest(".goal-panel").querySelector(".panel-heading").textContent, /Finished report/);
    await app.refresh();
    assert.equal(dom.window.document.querySelector('[data-goal-body="finished"]').hidden, true);
    dom.window.document.querySelector('[data-action="projects"]').click();
    dom.window.document.querySelector('[data-project="p"]').click();
    assert.equal(dom.window.document.querySelector('[data-goal-body="finished"]').hidden, true);
    dom.window.document.querySelector('[data-toggle-goal="finished"]').click();
    assert.equal(dom.window.document.querySelector('[data-goal-body="finished"]').hidden, false);
  } finally { app.destroy(); dom.window.close(); }
});

test("collapse choices survive remount without persisting credentials or project contents", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  const api = { state: async () => structuredClone(workspace()) };
  let app = mountApp({ document: dom.window.document, api, pollInterval: 0 });
  try {
    await openProject(app, dom.window.document);
    dom.window.document.querySelector('[data-toggle-goal="finished"]').click();
    app.destroy();
    app = mountApp({ document: dom.window.document, api, pollInterval: 0 });
    await openProject(app, dom.window.document);
    assert.equal(dom.window.document.querySelector('[data-goal-body="finished"]').hidden, true);
    const stored = Object.keys(dom.window.localStorage).map((key) => dom.window.localStorage.getItem(key)).join("\n");
    assert.doesNotMatch(stored, /Bearer|report\.md|Coordinator|Finished report/);
  } finally { app.destroy(); dom.window.close(); }
});

test("collapse remains usable if browser preference storage is unavailable", async () => {
  const dom = new JSDOM('<body><div id="app"></div><div id="feedback"></div></body>', { url: "http://localhost" });
  Object.defineProperty(dom.window, "localStorage", { get() { throw new Error("Storage blocked"); } });
  const app = mountApp({ document: dom.window.document, api: { state: async () => structuredClone(workspace()) }, pollInterval: 0 });
  try {
    await openProject(app, dom.window.document);
    dom.window.document.querySelector('[data-toggle-goal="active"]').click();
    assert.equal(dom.window.document.querySelector('[data-goal-body="active"]').hidden, true);
    await app.refresh();
    assert.equal(dom.window.document.querySelector('[data-goal-body="active"]').hidden, true);
    dom.window.document.querySelector('[data-toggle-goal="active"]').click();
    assert.equal(dom.window.document.querySelector('[data-goal-body="active"]').hidden, false);
  } finally { app.destroy(); dom.window.close(); }
});
