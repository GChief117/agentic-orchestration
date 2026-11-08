import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { ApiClient } from "../src/api.js";
import { mountApp } from "../src/app.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
const delay = () => new Promise((resolve) => setTimeout(resolve, 20));
async function until(check) {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay();
  }
  throw new Error("Timed out waiting for the frontend workflow");
}

test(
  "JavaScript forms create a real project, execute a goal, and inspect its artifact",
  { timeout: 20000 },
  async (t) => {
    const directory = await mkdtemp(
      path.join(tmpdir(), "orchestration-frontend-"),
    );
    const process = spawn(
      globalThis.process.env.PYTHON || "python3",
      [
        "-u",
        "app.py",
        "--port",
        "0",
        "--execution-limit",
        "3",
        "--data-dir",
        directory,
      ],
      { cwd: root, stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "";
    process.stdout.on("data", (chunk) => {
      output += chunk;
    });
    let errors = "";
    process.stderr.on("data", (chunk) => {
      errors += chunk;
    });
    t.after(async () => {
      if (process.exitCode === null && process.signalCode === null) {
        const exited = once(process, "exit");
        process.kill("SIGINT");
        await exited;
      }
      await rm(directory, { recursive: true, force: true });
    });
    await until(() => {
      if (process.exitCode !== null) throw new Error(errors);
      return /Backend API: (http:\/\/127.0.0.1:\d+)/.test(output);
    });
    const baseUrl = output.match(/Backend API: (http:\/\/127.0.0.1:\d+)/)[1];
    const response = await fetch(baseUrl);
    assert.equal(response.status, 200);
    const dom = new JSDOM(await response.text(), { url: baseUrl });
    const token = dom.window.document.querySelector(
      'meta[name="orchestration-session"]',
    ).content;
    const api = new ApiClient({ token, baseUrl });
    const app = mountApp({
      document: dom.window.document,
      api,
      pollInterval: 0,
    });
    t.after(() => {
      app.destroy();
      dom.window.close();
    });
    await app.ready;
    assert.equal(app.snapshot.projects.length, 0);
    const document = dom.window.document;
    document.querySelector('[data-action="new-project"]').click();
    document.querySelector("dialog [data-example]").click();
    document.querySelector('dialog [data-field="name"]').value =
      "Frontend integration project";
    document
      .querySelector("dialog form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await until(() =>
      document.querySelector(
        "dialog [data-organization-editor] [data-org-svg]",
      ),
    );
    document
      .querySelector("dialog form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await until(() => document.querySelector("dialog .setup-review"));
    document
      .querySelector("dialog form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await until(
      () =>
        app.snapshot.projects.length === 1 && !document.querySelector("dialog"),
    );
    assert.equal(app.snapshot.projects[0].team_size, 3);
    assert.equal(app.snapshot.projects[0].name, "Frontend integration project");
    document.querySelector('[data-tab="team"]').click();
    assert.equal(document.querySelectorAll(".agent-card").length, 3);
    assert.equal(
      document.querySelectorAll(".organization-panel .org-card").length,
      3,
    );
    document.querySelector('[data-action="new-goal"]').click();
    document.querySelector("dialog [data-example]").click();
    document
      .querySelector("dialog form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await until(async () => {
      await app.refresh();
      return (
        app.snapshot.goals[0]?.state === "accepted" &&
        !document.querySelector("dialog")
      );
    });
    assert.equal(app.snapshot.tasks.length, 3);
    assert.equal(document.querySelectorAll(".task-row").length, 3);
    const writer = app.snapshot.tasks.find(
      (task) => task.capability === "files.write",
    );
    document.querySelector(`[data-inspect="${writer.id}"]`).click();
    await until(() =>
      document
        .querySelector("[data-artifact-content]")
        ?.textContent.includes("Runtime checked."),
    );
    assert.ok(
      document
        .querySelector("[data-receipt]")
        .textContent.includes(writer.receipt.sha256),
    );
    document.querySelector("dialog [data-close]").click();
    document.querySelector('[data-tab="team"]').click();
    document.querySelector('[data-action="edit-team"]').click();
    const originalBuilder = app.snapshot.agents.find(
      (agent) => agent.id === writer.agent_id,
    );
    document
      .querySelector(`dialog [data-org-agent="${originalBuilder.key}"]`)
      .dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    assert.ok(
      document.querySelector('dialog [data-chart-action="remove"]').disabled,
    );
    const name = document.querySelector('dialog [data-field="name"]');
    name.value = "Lead builder";
    name.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    document
      .querySelector("dialog form")
      .dispatchEvent(
        new dom.window.Event("submit", { bubbles: true, cancelable: true }),
      );
    await until(
      () =>
        !document.querySelector("dialog") &&
        app.snapshot.agents.some(
          (agent) =>
            agent.id === originalBuilder.id && agent.name === "Lead builder",
        ),
    );
    assert.equal(dom.window.localStorage.length, 0);
    assert.equal(dom.window.sessionStorage.length, 0);
    assert.equal(errors, "");
  },
);
