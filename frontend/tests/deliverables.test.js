import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderWorkspace, selectGoalArtifacts } from "../src/views.js";
import { downloadTaskArtifact, openTaskDetails } from "../src/forms.js";

/** Make a saved task output with enough context for the normal goal view. */
function output(id, dependencies = [], artifact = `${id}.md`) {
  return {
    id, project_id: "p", goal_id: "g", agent_id: "a", title: `Result ${id}`,
    state: "accepted", capability: "model.generate", dependencies,
    receipt: artifact ? { artifact, accepted: true, sha256: `digest-${id}` } : null,
  };
}

/** Build a completed project fixture without equating execution with goal achievement. */
function workspace(tasks) {
  return {
    projects: [{ id: "p", name: "Example", charter: { objective: "Investigate evidence" } }],
    agents: [{ id: "a", name: "Researcher", project_id: "p" }], tasks,
    goals: [{ id: "g", project_id: "p", title: "Investigate evidence", state: "accepted", total: tasks.length, accepted: tasks.length }],
  };
}

test("the final artifact stays primary through non-artifact validation and earlier outputs stay accessible", () => {
  const research = output("research");
  const handoff = output("handoff", ["research"], null);
  const final = output("final", ["handoff"]);
  const verification = output("verification", ["final"], null);
  const independent = output("independent");
  const tasks = [research, handoff, final, verification, independent];
  const selection = selectGoalArtifacts(tasks);
  assert.deepEqual(selection.primary.map((task) => task.id).sort(), ["final", "independent"]);
  assert.deepEqual(selection.supporting.map((task) => task.id), ["research"]);
  assert.deepEqual(
    [...selection.primary, ...selection.supporting].map((task) => task.id).sort(),
    ["final", "independent", "research"],
  );
});

test("completed goals surface open and download controls without claiming the objective is verified", () => {
  const unsafe = '<img src=x onerror="alert(1)">';
  const tasks = [output("research", [], "notes.md"), output("final", ["research"], `${unsafe}.md`)];
  tasks[1].title = unsafe;
  const dom = new JSDOM(renderWorkspace(workspace(tasks), { selectedProject: "p", tab: "work" }));
  try {
    const goal = dom.window.document.querySelector(".goal-panel");
    assert.match(goal.textContent, /Results to review/);
    assert.match(goal.textContent, /Supporting results/);
    assert.match(goal.querySelector(".panel-heading .status").textContent, /Execution finished/);
    assert.doesNotMatch(goal.querySelector(".panel-heading .status").textContent, /Verified|Achieved/);
    assert.match(goal.textContent, /Open result/);
    assert.match(goal.textContent, /Download/);
    for (const id of ["research", "final"]) {
      assert.ok(goal.querySelector(`[data-inspect="${id}"]`));
      assert.ok(goal.querySelector(`[data-download-artifact="${id}"]`));
    }
    assert.equal(goal.querySelector("img"), null);
    assert.ok(goal.textContent.includes(unsafe));
  } finally { dom.window.close(); }
});

test("result dialogs lead with safe readable content and reuse it for download", async () => {
  const dom = new JSDOM("<body></body>");
  const task = output("final", [], "reports/final.md");
  const content = "# Findings\n<script>visible text, never executed</script>";
  const downloads = [];
  let fetches = 0;
  dom.window.HTMLAnchorElement.prototype.click = function () {
    downloads.push({ href: this.href, filename: this.download });
  };
  try {
    const dialog = await openTaskDetails(dom.window.document, task, { name: "Researcher" }, {
      artifact: async (id) => {
        assert.equal(id, "final");
        fetches += 1;
        return { name: "reports/final.md", content };
      },
    });
    const result = dialog.querySelector("[data-artifact-content]");
    const receipt = dialog.querySelector("[data-receipt]");
    assert.equal(result.textContent, content);
    assert.equal(dialog.querySelector("script"), null);
    assert.match(dialog.textContent, /final\.md/);
    assert.ok(result.compareDocumentPosition(receipt) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.ok(receipt.closest("details"));
    assert.equal(receipt.closest("details").open, false);
    dialog.querySelector("[data-download-artifact]").click();
    await Promise.resolve();
    assert.equal(fetches, 1);
    assert.equal(downloads.length, 1);
    assert.equal(downloads[0].filename, "final.md");
    assert.equal(decodeURIComponent(downloads[0].href.split(",").slice(1).join(",")), content);
    dialog.close();
  } finally { dom.window.close(); }
});

test("download preserves result text while reducing server paths to a safe filename", async () => {
  const dom = new JSDOM("<body></body>");
  const downloads = [];
  const content = "Unicode café\nA comma, a percent %, and <markup> remain text.";
  dom.window.HTMLAnchorElement.prototype.click = function () {
    downloads.push({ href: this.href, filename: this.download });
  };
  try {
    await downloadTaskArtifact(dom.window.document, output("final"), {
      artifact: async (id) => {
        assert.equal(id, "final");
        return { name: "../../reports/final.md", content };
      },
    });
    assert.equal(downloads.length, 1);
    assert.equal(downloads[0].filename, "final.md");
    assert.match(downloads[0].href, /^data:text\/plain/);
    assert.equal(decodeURIComponent(downloads[0].href.split(",").slice(1).join(",")), content);
    assert.equal(dom.window.document.querySelector("a[download]"), null);
  } finally { dom.window.close(); }
});

test("a missing artifact reports the error and never starts an empty download", async () => {
  const dom = new JSDOM("<body></body>");
  let clicks = 0;
  dom.window.HTMLAnchorElement.prototype.click = () => { clicks += 1; };
  try {
    await assert.rejects(
      downloadTaskArtifact(dom.window.document, output("missing"), {
        artifact: async () => { throw new Error("Artifact no longer exists"); },
      }),
      /Artifact no longer exists/,
    );
    assert.equal(clicks, 0);
    const dialog = await openTaskDetails(dom.window.document, output("missing"), { name: "Researcher" }, {
      artifact: async () => { throw new Error("Artifact no longer exists"); },
    });
    const error = dialog.querySelector("[data-error]");
    assert.match(error.textContent, /Artifact no longer exists/);
    assert.equal(error.classList.contains("hidden"), false);
    assert.equal(clicks, 0);
    dialog.close();
  } finally { dom.window.close(); }
});
