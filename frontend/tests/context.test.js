import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openContextForm } from "../src/context-form.js";
import { ApiClient } from "../src/api.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Submit the actual shared-context form without relying on browser validation. */
function submit(dialog, window) {
  dialog.querySelector("[data-context-form]").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }),
  );
}

/** Supply user-entered draft values through the public form controls. */
function draft(dialog, title, content) {
  dialog.querySelector("[data-context-title]").value = title;
  dialog.querySelector("[data-context-content]").value = content;
}

test("shared context loads persisted notes and source status as safe text", async () => {
  const dom = new JSDOM("<body></body>");
  const unsafe = '<img src=x onerror="alert(1)">';
  try {
    const dialog = openContextForm(dom.window.document, { id: "p", name: "Project" }, {
      projectContext: async (id) => {
        assert.equal(id, "p");
        return { entries: [
          { id: "n1", title: unsafe, content: "Line one\n<script>Untrusted note</script>", created: 1900000000 },
          { id: "n2", title: "No date", content: "Missing date is okay", created: "not-a-date" },
        ], revision: 1, source_bridge: { name: unsafe, status: "ready", repositories: [
          { name: unsafe, available: true, revision: "abc", candidates: [] },
          { name: "missing-repository", available: false, candidates: [] },
        ] } };
      },
    });
    assert.match(dialog.textContent, /Loading shared context/);
    assert.equal(dialog.querySelector("[data-context-fields]").disabled, true);
    await tick();
    assert.equal(dialog.querySelectorAll(".context-entry").length, 2);
    assert.equal(dialog.querySelector("[data-context-title]").maxLength, 200);
    assert.equal(dialog.querySelector("[data-context-content]").maxLength, 48000);
    assert.equal(dialog.querySelectorAll("img, script").length, 0);
    assert.ok(dialog.textContent.includes(unsafe));
    assert.match(dialog.querySelector("[data-context-bridge]").textContent, /ready/);
    assert.match(dialog.querySelector("[data-context-bridge]").textContent, /2 repositories/);
    assert.equal(dialog.querySelectorAll("[data-context-bridge] li").length, 2);
    assert.equal(dialog.querySelector("[data-context-bridge] li").textContent, unsafe);
    assert.match(dialog.querySelector("[data-context-bridge]").textContent, /missing-repository · Unavailable/);
    assert.match(dialog.querySelector("[data-context-bridge]").textContent, /Remote project files are read through this bridge/);
    assert.match(dialog.querySelector("[data-context-bridge]").textContent, /local checkout is not the remote source/);
    assert.equal(dialog.querySelectorAll("time").length, 1);
    assert.doesNotMatch(dialog.textContent, /Invalid Date|NaN/);
    assert.equal(dialog.querySelector("[data-context-fields]").disabled, false);
    dialog.close();
  } finally { dom.window.close(); }
});

test("saving waits for persisted context and blocks duplicate submissions", async () => {
  const dom = new JSDOM("<body></body>");
  let finish;
  let calls = 0;
  const saved = new Promise((resolve) => { finish = resolve; });
  try {
    const dialog = openContextForm(dom.window.document, { id: "p" }, {
      projectContext: async () => ({ entries: [], revision: 0 }),
      addProjectContext: async (id, value) => {
        calls += 1;
        assert.equal(id, "p");
        assert.deepEqual(value, { title: "Decision", content: "Use the evidence folder." });
        return saved;
      },
    });
    await tick();
    draft(dialog, "Decision", "Use the evidence folder.");
    submit(dialog, dom.window);
    submit(dialog, dom.window);
    assert.equal(calls, 1);
    assert.equal(dialog.querySelector("[data-save-context]").disabled, true);
    assert.equal(dialog.querySelector("[data-context-title]").disabled, true);
    assert.equal(dialog.querySelector("[data-close]").disabled, true);
    assert.equal(dialog.querySelectorAll(".context-entry").length, 0);
    assert.match(dialog.querySelector("[data-context-status]").textContent, /Saving/);
    assert.equal(dialog.querySelector("[data-context-title]").value, "Decision");
    finish({ entries: [{ id: "saved", title: "Decision", content: "Use the evidence folder.", created: 1900000000 }], revision: 1 });
    await tick();
    assert.equal(dialog.querySelectorAll(".context-entry").length, 1);
    assert.match(dialog.querySelector("[data-context-status]").textContent, /Shared context saved/);
    assert.equal(dialog.querySelector("[data-context-title]").value, "");
    assert.equal(dialog.querySelector("[data-context-content]").value, "");
    assert.equal(dialog.querySelector("[data-save-context]").disabled, false);
    dialog.close();
  } finally { dom.window.close(); }
});

test("save failures keep the unsaved draft and previous confirmed notes", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    const dialog = openContextForm(dom.window.document, { id: "p" }, {
      projectContext: async () => ({ entries: [{ id: "old", title: "Saved note", content: "Confirmed earlier" }] }),
      addProjectContext: async () => { throw new Error("Connection interrupted"); },
    });
    await tick();
    draft(dialog, "Unsaved decision", "Keep this draft.");
    submit(dialog, dom.window);
    await tick();
    assert.match(dialog.querySelector("[data-error]").textContent, /Connection interrupted/);
    assert.equal(dialog.querySelector("[data-error]").classList.contains("hidden"), false);
    assert.match(dialog.querySelector("[data-context-status]").textContent, /Save not confirmed/);
    assert.equal(dialog.querySelector("[data-context-title]").value, "Unsaved decision");
    assert.equal(dialog.querySelector("[data-context-content]").value, "Keep this draft.");
    assert.equal(dialog.querySelectorAll(".context-entry").length, 1);
    assert.equal(dialog.querySelector(".context-entry h3").textContent, "Saved note");
    assert.equal(dialog.querySelector("[data-save-context]").disabled, false);
    dialog.close();
  } finally { dom.window.close(); }
});

test("an incomplete save response cannot falsely mark a draft as saved", async () => {
  const dom = new JSDOM("<body></body>");
  try {
    const dialog = openContextForm(dom.window.document, { id: "p" }, {
      projectContext: async () => ({ entries: [] }),
      addProjectContext: async () => ({ revision: 2 }),
    });
    await tick();
    draft(dialog, "New fact", "Evidence required");
    submit(dialog, dom.window);
    await tick();
    assert.match(dialog.querySelector("[data-context-status]").textContent, /Save not confirmed/);
    assert.equal(dialog.querySelector("[data-context-title]").value, "New fact");
    assert.match(dialog.querySelector("[data-error]").textContent, /did not return the saved shared context/);
    dialog.close();
  } finally { dom.window.close(); }
});

test("load errors can retry without losing a draft", async () => {
  const dom = new JSDOM("<body></body>");
  let loads = 0;
  try {
    const dialog = openContextForm(dom.window.document, { id: "p" }, {
      projectContext: async () => {
        loads += 1;
        if (loads === 1) throw new Error("Temporary failure");
        return { entries: [], source_bridge: null };
      },
    });
    await tick();
    assert.match(dialog.querySelector("[data-error]").textContent, /Temporary failure/);
    assert.equal(dialog.querySelector("[data-refresh-context]").disabled, false);
    draft(dialog, "Keep title", "Keep content");
    dialog.querySelector("[data-refresh-context]").click();
    await tick();
    assert.equal(loads, 2);
    assert.equal(dialog.querySelector("[data-context-title]").value, "Keep title");
    assert.equal(dialog.querySelector("[data-context-content]").value, "Keep content");
    assert.match(dialog.querySelector("[data-context-entries]").textContent, /No shared context saved yet/);
    assert.equal(dialog.querySelector("[data-context-bridge]").textContent, "");
    assert.equal(dialog.querySelector("[data-error]").classList.contains("hidden"), true);
    dialog.close();
  } finally { dom.window.close(); }
});

test("context API helpers use an encoded project path and structured note body", async () => {
  const calls = [];
  const api = new ApiClient({ token: "local", fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ entries: [] }) };
  } });
  await api.projectContext("project/one");
  await api.addProjectContext("project/one", { title: "Decision", content: "Use evidence" });
  assert.equal(calls[0].url, "/api/projects/project%2Fone/context");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[1].url, "/api/projects/project%2Fone/context");
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].options.body), { title: "Decision", content: "Use evidence" });
});
