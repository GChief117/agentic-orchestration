import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openImportForm } from "../src/import-form.js";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("scan is explicit and import sends only selected candidate IDs", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  const calls = [],
    saved = [];
  const api = {
    async scanRoots() {
      return { roots: ["/local/projects"] };
    },
    async scanProjects(root, includeRoot) {
      calls.push([root, includeRoot]);
      return {
        scan_id: "scan-1",
        visited: 10,
        truncated: true,
        unreadable: 1,
        projects: [
          {
            id: "a",
            name: "<Project A>",
            path: "/local/projects/a",
            kinds: ["Python"],
          },
          {
            id: "b",
            name: "Project B",
            path: "/local/projects/b",
            kinds: ["Git"],
            imported: true,
          },
          {
            id: "c",
            name: "Project C",
            path: "/local/projects/c",
            kinds: ["JavaScript"],
          },
        ],
      };
    },
    async importProjects(scan, ids) {
      saved.push([scan, ids]);
      return { projects: [{ id: "imported" }] };
    },
  };
  let result;
  try {
    const dialog = openImportForm(
      dom.window.document,
      api,
      async (projects) => {
        result = projects;
      },
    );
    await tick();
    assert.equal(calls.length, 0);
    dialog.querySelector("[data-scan]").click();
    await tick();
    assert.deepEqual(calls, [["/local/projects", false]]);
    assert.equal(dialog.querySelectorAll("[data-candidate]:checked").length, 0);
    assert.equal(dialog.querySelector('[data-candidate="b"]').disabled, true);
    assert.match(dialog.textContent, /Scan reached/);
    assert.equal(dialog.querySelector("[data-import]").disabled, true);
    const filter = dialog.querySelector("[data-filter]");
    filter.value = "Python";
    filter.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    dialog.querySelector("[data-select-all]").click();
    assert.equal(dialog.querySelector('[data-candidate="a"]').checked, true);
    assert.equal(dialog.querySelector('[data-candidate="c"]').checked, false);
    dialog.querySelector("[data-import]").click();
    await tick();
    assert.deepEqual(saved, [["scan-1", ["a"]]]);
    assert.deepEqual(result, [{ id: "imported" }]);
    assert.equal(dom.window.document.querySelector("dialog"), null);
  } finally {
    dom.window.close();
  }
});

test("failed scan leaves retry controls available", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  try {
    const dialog = openImportForm(
      dom.window.document,
      {
        async scanRoots() {
          return { roots: [] };
        },
        async scanProjects() {
          throw new Error("Folder not accessible");
        },
      },
      async () => {},
    );
    dialog.querySelector("[data-scan]").click();
    await tick();
    assert.match(
      dialog.querySelector("[data-error]").textContent,
      /Folder not accessible/,
    );
    assert.equal(dialog.querySelector("[data-scan]").disabled, false);
    assert.equal(dialog.querySelector("[data-import]").disabled, true);
  } finally {
    dom.window.close();
  }
});
