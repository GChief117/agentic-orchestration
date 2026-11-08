import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import { guidedDraft } from "../src/presets.js";
import {
  canReparent,
  organizationLayout,
  organizationTree,
} from "../src/organization.js";

/** Dispatch an SVG-compatible click, including in DOM environments without .click(). */
function click(window, element) {
  element.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
}
/** Provide a small pointer event fixture without launching a real browser. */
function pointer(window, element, type, x, y) {
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { clientX: x, clientY: y, pointerId: 1, button: 0 });
  element.dispatchEvent(event);
}

test("layout fits every role and centers the project owner over the tree", () => {
  const agents = guidedDraft().agents;
  for (let i = 4; i <= 8; i += 1)
    agents.push({ ...agents[1], key: `agent-${i}` });
  const layout = organizationLayout(agents);
  assert.equal(layout.nodes.length, 9);
  assert.equal(layout.nodes[0].x + layout.nodes[0].width / 2, layout.width / 2);
  assert.ok(
    layout.nodes.every(
      (node) =>
        node.x >= 0 &&
        node.x + node.width <= layout.width &&
        node.y + node.height <= layout.height,
    ),
  );
  assert.equal(canReparent(agents, "agent-1", "agent-2"), false);
});

test("chart selection edits roles, adds children, reparents, and removes without losing descendants", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());
  click(dom.window, root.querySelector('[data-org-agent="agent-2"]'));
  const input = root.querySelector('[data-field="name"]');
  input.value = "Lead builder";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(editor.snapshot().agents[1].name, "Lead builder");
  click(dom.window, root.querySelector('[data-chart-action="add"]'));
  assert.equal(editor.snapshot().agents.length, 4);
  const child = editor.snapshot().agents[3];
  assert.equal(child.reports_to, "agent-2");
  const manager = root.querySelector('[data-agent-field="reports_to"]');
  manager.value = "agent-3";
  manager.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  assert.equal(editor.snapshot().agents[3].reports_to, "agent-3");
  click(dom.window, root.querySelector('[data-org-agent="agent-3"]'));
  click(dom.window, root.querySelector('[data-chart-action="remove"]'));
  assert.equal(editor.snapshot().agents.length, 3);
  assert.equal(editor.snapshot().agents[2].reports_to, "agent-1");
  assert.equal(organizationTree(editor.snapshot().agents).error, null);
});

test("pointer drop reparents a role onto a valid manager", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const document = dom.window.document,
    root = document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());
  const source = root.querySelector('[data-org-agent="agent-3"]');
  const target = root.querySelector('[data-org-agent="agent-2"]');
  document.elementFromPoint = () => target;
  pointer(dom.window, source, "pointerdown", 0, 0);
  pointer(dom.window, target, "pointermove", 20, 20);
  pointer(dom.window, target, "pointerup", 20, 20);
  assert.equal(editor.snapshot().agents[2].reports_to, "agent-2");
});

test("fit and zoom change the viewport without changing the organization", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());
  const initial = root.querySelector("svg").getAttribute("viewBox");
  click(dom.window, root.querySelector('[data-chart-action="zoom-in"]'));
  assert.notEqual(root.querySelector("svg").getAttribute("viewBox"), initial);
  click(dom.window, root.querySelector('[data-chart-action="fit"]'));
  assert.equal(root.querySelector("svg").getAttribute("viewBox"), initial);
  assert.equal(editor.snapshot().agents.length, 3);
});

test("invalid descendant drops and cancelled gestures leave reporting links intact", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const document = dom.window.document,
    root = document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());
  const before = editor.snapshot();
  const source = root.querySelector('[data-org-agent="agent-1"]');
  const target = root.querySelector('[data-org-agent="agent-2"]');
  document.elementFromPoint = () => target;
  pointer(dom.window, source, "pointerdown", 0, 0);
  pointer(dom.window, target, "pointermove", 20, 20);
  pointer(dom.window, target, "pointerup", 20, 20);
  assert.deepEqual(editor.snapshot(), before);
  pointer(dom.window, target, "pointerdown", 0, 0);
  document.elementFromPoint = () =>
    root.querySelector('[data-org-agent="__owner__"]');
  pointer(dom.window, source, "pointermove", 20, 20);
  pointer(dom.window, source, "pointercancel", 20, 20);
  assert.deepEqual(editor.snapshot(), before);
});

test("large configured capacity does not create an unbounded dropdown", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const root = dom.window.document.querySelector("#editor");
  const agents = guidedDraft().agents;
  agents[0].max_concurrency = "100";
  const editor = mountOrganizationEditor({ root, agents, maxConcurrency: 2 });
  t.after(() => editor.destroy());
  assert.ok(
    root.querySelectorAll("[data-project-concurrency] option").length <= 10,
  );
});

test("physical card clicks select the correct agent even when pointerup targets the SVG", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());

  for (const [key, name] of [
    ["agent-2", "Builder"],
    ["agent-3", "Reviewer"],
  ]) {
    const svg = root.querySelector("[data-org-svg]");
    let captures = 0;
    svg.setPointerCapture = () => {
      captures += 1;
    };
    const card = root.querySelector(`[data-org-agent="${key}"]`);
    pointer(dom.window, card.querySelector("rect"), "pointerdown", 10, 10);
    pointer(dom.window, svg, "pointermove", 12, 11);
    assert.equal(
      captures,
      0,
      "small click movements must not capture the pointer",
    );
    pointer(dom.window, svg, "pointerup", 12, 11);
    assert.equal(root.querySelector(".inspector-heading h3").textContent, name);
    assert.equal(root.querySelector('[data-field="name"]').value, name);
    assert.ok(
      root
        .querySelector(`[data-org-agent="${key}"]`)
        .classList.contains("selected"),
    );
  }

  const instructions = root.querySelector('[data-field="instructions"]');
  instructions.value = "Review this project's outputs independently.";
  instructions.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(
    editor.snapshot().agents.find((agent) => agent.key === "agent-3")
      .instructions,
    instructions.value,
  );
  assert.notEqual(
    editor.snapshot().agents.find((agent) => agent.key === "agent-2")
      .instructions,
    instructions.value,
  );
});

test("pointer capture starts only after a drag crosses the movement threshold", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  t.after(() => dom.window.close());
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => editor.destroy());
  const svg = root.querySelector("svg");
  let captures = 0;
  svg.setPointerCapture = () => {
    captures += 1;
  };
  pointer(
    dom.window,
    root.querySelector('[data-org-agent="agent-2"]'),
    "pointerdown",
    0,
    0,
  );
  assert.equal(captures, 0);
  pointer(dom.window, svg, "pointermove", 20, 20);
  assert.equal(captures, 1);
  pointer(dom.window, svg, "pointercancel", 20, 20);
});
