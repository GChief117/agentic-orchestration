import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { JSDOM, VirtualConsole } from "jsdom";

/** Verify modular CSS remains local, loadable, and parseable without a browser. */
test("CSS entry imports parseable local modules in cascade order", async () => {
  const root = new URL("../", import.meta.url);
  const entry = await readFile(new URL("styles.css", root), "utf8");
  const paths = [...entry.matchAll(/@import url\("(.+?)"\);/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    paths,
    [
      "base",
      "layout",
      "projects",
      "work",
      "forms",
      "organization",
      "project-setup",
      "responsive",
    ].map((name) => `./styles/${name}.css`),
  );
  const errors = [];
  const console = new VirtualConsole();
  console.on("jsdomError", (error) => errors.push(error.message));
  const dom = new JSDOM("<!doctype html><head></head>", {
    virtualConsole: console,
  });
  try {
    for (const path of paths) {
      const element = dom.window.document.createElement("style");
      element.textContent = await readFile(new URL(path, root), "utf8");
      dom.window.document.head.append(element);
      assert.ok(
        element.sheet?.cssRules.length > 0,
        `${path} must contain valid CSS rules`,
      );
    }
    assert.deepEqual(errors, []);
  } finally {
    dom.window.close();
  }
});
