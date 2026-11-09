import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { APPEARANCES } from "../src/appearance.js";

/** Calculate relative luminance from an opaque CSS hex color. */
function luminance(hex) {
  const channels = [1, 3, 5].map(
    (index) => parseInt(hex.slice(index, index + 2), 16) / 255,
  );
  const linear = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

/** Compare two opaque colors, independent of light or dark palette ordering. */
function contrast(first, second) {
  const values = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (values[1] + 0.05) / (values[0] + 0.05);
}

test("all chart palettes separate cards from canvas and keep lines and labels readable", async () => {
  const organization = await readFile(
    new URL("../styles/organization.css", import.meta.url),
    "utf8",
  );
  const themes = await readFile(
    new URL("../styles/themes.css", import.meta.url),
    "utf8",
  );
  const palettes = {};
  for (const match of `${organization}\n${themes}`.matchAll(
    /:root(?:\[data-theme="([a-z]+)"\])?\s*\{([^}]+)\}/g,
  )) {
    const colors = Object.fromEntries(
      [...match[2].matchAll(/--org-([a-z]+):\s*(#[0-9a-f]+);/g)].map((item) => [
        item[1],
        item[2],
      ]),
    );
    const name = match[1] || "original";
    palettes[name] = { ...palettes[name], ...colors };
  }
  for (const name of Object.keys(APPEARANCES)) {
    const colors = { ...palettes.original, ...palettes[name] };
    assert.notEqual(
      colors.card,
      colors.canvas,
      `${name}: card must differ from canvas`,
    );
    assert.notEqual(
      colors.selected,
      colors.canvas,
      `${name}: selection must differ from canvas`,
    );
    for (const surface of ["canvas", "card", "owner"])
      assert.ok(
        contrast(colors.border, colors[surface]) >= 3,
        `${name}: border on ${surface}`,
      );
    assert.ok(
      contrast(colors.link, colors.canvas) >= 3,
      `${name}: connector on canvas`,
    );
    assert.ok(
      contrast(colors.accent, colors.selected) >= 3,
      `${name}: selection outline`,
    );
    for (const text of ["text", "secondary"])
      for (const surface of ["card", "owner", "selected"])
        assert.ok(
          contrast(colors[text], colors[surface]) >= 4.5,
          `${name}: ${text} on ${surface}`,
        );
    assert.ok(
      contrast(colors.text, colors.badge) >= 4.5,
      `${name}: agent initial`,
    );
  }
});
