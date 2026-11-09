import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { mountOrganizationEditor } from "../src/organization-editor.js";
import {
  connectAgents,
  organizationLayout,
  collaborationPath,
} from "../src/organization.js";
import { guidedDraft } from "../src/presets.js";

/** Mount a draft without network or model calls. */
function fixture(t) {
  const dom = new JSDOM('<div id="editor"></div>');
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft().agents,
    maxConcurrency: 2,
  });
  t.after(() => {
    editor.destroy();
    dom.window.close();
  });
  return { dom, root, editor };
}
/** Dispatch SVG-compatible input in the DOM-only test environment. */
function pointer(dom, element, type, x = 0, y = 0, pointerId = 1) {
  const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { clientX: x, clientY: y, pointerId, button: 0 });
  element.dispatchEvent(event);
}
function click(dom, element) {
  element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
}

/** Ensure the two peer edges exist while the reporting tree remains unchanged. */
function assertLinked(editor, managers) {
  const agents = editor.snapshot().agents;
  assert.deepEqual(
    agents.map((agent) => agent.reports_to),
    managers,
  );
  assert.deepEqual(agents[1].collaborators, [agents[2].key]);
  assert.deepEqual(agents[2].collaborators, [agents[1].key]);
}

test("dragging a plus handle links sibling agents without moving either card", (t) => {
  const { dom, root, editor } = fixture(t);
  const managers = editor.snapshot().agents.map((agent) => agent.reports_to);
  const source = root.querySelector('[data-org-agent="agent-2"]');
  const target = root.querySelector('[data-org-agent="agent-3"]');
  const start = source.getAttribute("transform");
  dom.window.document.elementFromPoint = () => target;
  pointer(dom, source.querySelector("[data-connect-handle]"), "pointerdown");
  pointer(dom, target, "pointermove", 50, 50);
  assert.equal(source.getAttribute("transform"), start);
  assert.ok(root.querySelector("[data-peer-preview]").getAttribute("d"));
  assert.ok(target.classList.contains("peer-target"));
  pointer(dom, target, "pointerup", 50, 50);
  assertLinked(editor, managers);
  assert.equal(root.querySelectorAll(".org-collaborations path").length, 1);
});

test("connect mode links two clicked cards, including a manager and descendant", (t) => {
  const { dom, root, editor } = fixture(t);
  const managers = editor.snapshot().agents.map((agent) => agent.reports_to);
  click(dom, root.querySelector('[data-chart-action="connect-mode"]'));
  click(dom, root.querySelector('[data-org-agent="agent-1"]'));
  click(dom, root.querySelector('[data-org-agent="agent-3"]'));
  assert.deepEqual(
    editor.snapshot().agents.map((agent) => agent.reports_to),
    managers,
  );
  assert.deepEqual(editor.snapshot().agents[0].collaborators, ["agent-3"]);
  assert.deepEqual(editor.snapshot().agents[2].collaborators, ["agent-1"]);
});

test("physical click sequence links once and suppresses the synthetic click", (t) => {
  const { dom, root, editor } = fixture(t);
  click(dom, root.querySelector('[data-chart-action="connect-mode"]'));
  for (const key of ["agent-2", "agent-3"]) {
    const node = root.querySelector(`[data-org-agent="${key}"]`);
    pointer(dom, node, "pointerdown");
    pointer(dom, root.querySelector("svg"), "pointerup");
    click(dom, root.querySelector(`[data-org-agent="${key}"]`));
  }
  assert.equal(root.querySelectorAll(".org-collaborations path").length, 1);
  assert.equal(root.querySelectorAll(".connection-source").length, 0);
  assert.deepEqual(editor.snapshot().agents[1].collaborators, ["agent-3"]);
});

test("invalid and cancelled peer drops leave relationships and positions intact", (t) => {
  const { dom, root, editor } = fixture(t);
  const initial = editor.snapshot();
  for (const targetKey of ["agent-2", "__owner__", null]) {
    const source = root.querySelector('[data-org-agent="agent-2"]');
    const target = targetKey
      ? root.querySelector(`[data-org-agent="${targetKey}"]`)
      : root.querySelector("svg");
    dom.window.document.elementFromPoint = () => target;
    pointer(dom, source.querySelector("[data-connect-handle]"), "pointerdown");
    pointer(dom, target, "pointermove", 40, 40);
    pointer(dom, target, "pointerup", 40, 40);
    assert.deepEqual(editor.snapshot(), initial);
  }
  const source = root.querySelector('[data-org-agent="agent-2"]');
  dom.window.document.elementFromPoint = () =>
    root.querySelector('[data-org-agent="agent-3"]');
  pointer(dom, source.querySelector("[data-connect-handle]"), "pointerdown");
  pointer(dom, source, "pointermove", 40, 40);
  pointer(dom, source, "pointercancel", 40, 40);
  assert.deepEqual(editor.snapshot(), initial);
  assert.equal(root.querySelector("[data-peer-preview]").getAttribute("d"), "");
});

test("keyboard connection mode can add peers and Escape restores normal selection", (t) => {
  const { dom, root, editor } = fixture(t);
  click(dom, root.querySelector('[data-chart-action="connect-mode"]'));
  for (const key of ["agent-2", "agent-3"])
    root.querySelector(`[data-org-agent="${key}"]`).dispatchEvent(
      new dom.window.KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      }),
    );
  assert.deepEqual(editor.snapshot().agents[1].collaborators, ["agent-3"]);
  root.dispatchEvent(
    new dom.window.KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    }),
  );
  assert.equal(
    root
      .querySelector('[data-chart-action="connect-mode"]')
      .getAttribute("aria-pressed"),
    "false",
  );
  click(dom, root.querySelector('[data-org-agent="agent-1"]'));
  assert.equal(
    root.querySelector(".inspector-heading h3").textContent,
    "Coordinator",
  );
  assert.deepEqual(editor.snapshot().agents[0].collaborators, []);
});

test("peer helper deduplicates and disconnects both directions without mutating input", () => {
  const agents = guidedDraft().agents;
  const connected = connectAgents(agents, "agent-2", "agent-3");
  const again = connectAgents(connected, "agent-3", "agent-2");
  assert.deepEqual(again[1].collaborators, ["agent-3"]);
  assert.deepEqual(agents[1].collaborators, []);
  const removed = connectAgents(again, "agent-2", "agent-3", false);
  assert.deepEqual(removed[1].collaborators, []);
  assert.deepEqual(removed[2].collaborators, []);
  assert.throws(() => connectAgents(agents, "agent-2", "agent-2"), /different/);
});

test("links spanning an intervening peer route below the cards", () => {
  const agents = guidedDraft("research").agents;
  const { nodes } = organizationLayout(agents);
  const first = nodes.find((node) => node.key === "agent-2"),
    last = nodes.find((node) => node.key === "agent-4");
  assert.ok(
    collaborationPath(first, last).includes(`V ${first.y + first.height + 20}`),
  );
});

test("a second pointer cannot hijack or cancel an active connection drag", (t) => {
  const { dom, root, editor } = fixture(t);
  const source = root.querySelector('[data-org-agent="agent-2"]');
  const target = root.querySelector('[data-org-agent="agent-3"]');
  dom.window.document.elementFromPoint = () => target;
  pointer(
    dom,
    source.querySelector("[data-connect-handle]"),
    "pointerdown",
    0,
    0,
    1,
  );
  pointer(dom, target, "pointerdown", 10, 10, 2);
  pointer(dom, target, "pointermove", 30, 30, 2);
  pointer(dom, target, "pointerup", 30, 30, 2);
  pointer(dom, target, "pointercancel", 30, 30, 2);
  assert.deepEqual(editor.snapshot().agents[1].collaborators, []);
  pointer(dom, target, "pointermove", 30, 30, 1);
  pointer(dom, target, "pointerup", 30, 30, 1);
  assert.deepEqual(editor.snapshot().agents[1].collaborators, ["agent-3"]);
});

test("several peer links can form a triangle without affecting managers", (t) => {
  const dom = new JSDOM('<div id="editor"></div>');
  const root = dom.window.document.querySelector("#editor");
  const editor = mountOrganizationEditor({
    root,
    agents: guidedDraft("research").agents,
    maxConcurrency: 2,
  });
  t.after(() => {
    editor.destroy();
    dom.window.close();
  });
  const managers = editor.snapshot().agents.map((agent) => agent.reports_to);
  click(dom, root.querySelector('[data-chart-action="connect-mode"]'));
  for (const [a, b] of [
    ["agent-2", "agent-3"],
    ["agent-3", "agent-4"],
    ["agent-4", "agent-2"],
  ]) {
    click(dom, root.querySelector(`[data-org-agent="${a}"]`));
    click(dom, root.querySelector(`[data-org-agent="${b}"]`));
  }
  assert.equal(root.querySelectorAll(".org-collaborations path").length, 3);
  assert.deepEqual(
    editor.snapshot().agents.map((agent) => agent.reports_to),
    managers,
  );
  assert.ok(
    editor
      .snapshot()
      .agents.slice(1)
      .every((agent) => agent.collaborators.length === 2),
  );
});
