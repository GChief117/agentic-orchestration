import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  applyAppearance,
  initializeAppearance,
  openAppearanceForm,
} from "../src/appearance.js";

test("appearance options apply immediately, persist only the theme, and restore original", () => {
  const dom = new JSDOM('<div id="app">Existing project</div>', {
    url: "http://localhost",
  });
  try {
    const document = dom.window.document;
    const dialog = openAppearanceForm(document);
    for (const theme of ["neutral", "graphite", "warm"]) {
      dialog.querySelector(`[data-appearance="${theme}"]`).click();
      assert.equal(document.documentElement.dataset.theme, theme);
      assert.equal(
        dom.window.localStorage.getItem("orchestration-appearance"),
        theme,
      );
      assert.equal(
        document.querySelector("#app").textContent,
        "Existing project",
      );
      assert.equal(dialog.querySelectorAll('[aria-pressed="true"]').length, 1);
    }
    assert.equal(
      dialog.querySelectorAll("[style]").length,
      0,
      "swatches must comply with the app CSP",
    );
    initializeAppearance(document);
    assert.equal(document.documentElement.dataset.theme, "warm");
    applyAppearance(document, "not-a-theme");
    assert.equal(document.documentElement.dataset.theme, undefined);
    assert.equal(dom.window.localStorage.length, 1);
    dialog.close();
  } finally {
    dom.window.close();
  }
});

test("appearance works when browser preference storage is unavailable", () => {
  const dom = new JSDOM("<div></div>");
  try {
    initializeAppearance(dom.window.document);
    applyAppearance(dom.window.document, "graphite");
    assert.equal(dom.window.document.documentElement.dataset.theme, "graphite");
  } finally {
    dom.window.close();
  }
});
