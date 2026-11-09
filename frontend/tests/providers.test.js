import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openProviderForm } from "../src/provider-form.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("provider dialog launches only on click and never treats launch as authentication", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  const calls = [];
  const providers = [
    {
      id: "openai",
      name: "OpenAI",
      tool: "Codex CLI",
      state: "signed_out",
      installed: true,
      can_launch: true,
      command: "codex login",
      docs: "https://developers.openai.com/codex/auth",
    },
    {
      id: "gemini",
      name: "Gemini",
      tool: "Antigravity CLI",
      state: "not_installed",
      installed: false,
      can_launch: false,
      command: "agy",
      docs: "https://antigravity.google/",
    },
  ];
  const api = {
    async providers() {
      return { providers };
    },
    async signInProvider(id) {
      calls.push(id);
      return { state: "launched", message: "Finish in Terminal" };
    },
  };
  try {
    const dialog = openProviderForm(dom.window.document, api);
    await tick();
    assert.deepEqual(calls, []);
    assert.equal(
      dialog.querySelector('[data-provider="gemini"]').disabled,
      true,
    );
    dialog.querySelector('[data-provider="openai"]').click();
    await tick();
    assert.deepEqual(calls, ["openai"]);
    assert.match(dialog.textContent, /Finish in Terminal/);
    assert.match(dialog.textContent, /Sign-in required/);
    assert.equal(dialog.querySelectorAll("input").length, 0);
    providers[0].state = "signed_in";
    dialog.querySelector("[data-refresh-providers]").click();
    await tick();
    assert.match(dialog.textContent, /Saved sign-in available/);
    assert.equal(
      dialog.querySelector('[data-provider="openai"]').textContent,
      "Switch account",
    );
    dialog.close();
  } finally {
    dom.window.close();
  }
});

test("provider errors remain visible and allow retry", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  try {
    const dialog = openProviderForm(dom.window.document, {
      async providers() {
        throw new Error("Connection interrupted");
      },
    });
    await tick();
    assert.match(
      dialog.querySelector("[data-error]").textContent,
      /Connection interrupted/,
    );
    assert.equal(
      dialog.querySelector("[data-refresh-providers]").disabled,
      false,
    );
    dialog.close();
  } finally {
    dom.window.close();
  }
});
