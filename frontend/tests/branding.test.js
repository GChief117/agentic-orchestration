import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  BRAND_STYLES,
  applyBrandStyle,
  initializeBrandStyle,
  renderBrand,
} from "../src/branding.js";
import { openAppearanceForm } from "../src/appearance.js";

test("six branding samples apply to the existing navigation link and persist", () => {
  const dom = new JSDOM('<a data-brand data-action="projects" href="#"></a>', {
    url: "http://localhost",
  });
  try {
    const document = dom.window.document;
    document.documentElement.dataset.theme = "warm";
    const link = document.querySelector("[data-brand]");
    const dialog = openAppearanceForm(document);
    assert.equal(dialog.querySelectorAll("[data-brand-option]").length, 6);
    for (const style of Object.keys(BRAND_STYLES)) {
      dialog
        .querySelector(`[data-brand-option="${style}"] .brand-title`)
        .click();
      assert.equal(document.querySelector("[data-brand]"), link);
      assert.equal(
        link.innerHTML,
        dialog.querySelector(`[data-brand-option="${style}"] .brand-preview`)
          .innerHTML,
      );
      assert.equal(link.dataset.action, "projects");
      assert.equal(document.documentElement.dataset.theme, "warm");
      assert.equal(document.documentElement.dataset.brandStyle, style);
      assert.equal(
        dialog.querySelectorAll('[data-brand-option][aria-pressed="true"]')
          .length,
        1,
      );
    }
    delete document.documentElement.dataset.brandStyle;
    initializeBrandStyle(document);
    assert.equal(document.documentElement.dataset.brandStyle, "seal");
    assert.equal(link.innerHTML, renderBrand("seal"));
    applyBrandStyle(document, "<script>");
    assert.equal(document.documentElement.dataset.brandStyle, "arrow");
    assert.equal(link.querySelector("script"), null);
    dialog.close();
  } finally {
    dom.window.close();
  }
});
