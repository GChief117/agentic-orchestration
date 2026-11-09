import {
  ACTIONS,
  escapeHtml as e,
  goalPayload,
  starterTasks,
} from "./domain.js";
import { icon } from "./views.js";
import { input, value, showError, mountDialog } from "./form-controls.js";
export { openProjectForm } from "./project-form.js";

/**
 * Open a goal editor scoped to an existing project and its assigned roster.
 * @param {Document} document Host document.
 * @param {object} view Project and agents returned by projectView.
 * @param {function(object): Promise<*>} onSubmit Persists the validated task graph.
 * @returns {HTMLDialogElement} Mounted task editor.
 */
export function openGoalForm(document, view, onSubmit, loadSourceFiles = null) {
  const controller = mountDialog(document, "Define a goal", view.project.name);
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
  let sourceFiles = [],
    sourceError = "",
    sourceTruncated = false;
  let sourcesLoading = Boolean(view.project.source_path && loadSourceFiles);
  let title = "",
    nextId = 1;
  /**
   * Create a task draft with a stable local ID and the first available agent/action.
   */
  function blankTask() {
    const agent = view.agents[0];
    return {
      id: `task-${nextId++}`,
      title: "",
      agent: agent.key,
      capability: agent.capabilities[0],
      path: "",
      content: "",
      expected: "",
      prompt: "",
      source_files: [],
      source_filter: "",
      max_tokens: "2048",
      compare: false,
      depends_on: [],
    };
  }
  let tasks = [blankTask()];
  /**
   * Collect task rows, comparison fields, and dependency selections from the goal editor.
   */
  function read() {
    title = value(content, "goal-title");
    return [...content.querySelectorAll("[data-task]")].map((row) => ({
      id: row.dataset.task,
      ...Object.fromEntries(
        [
          "title",
          "agent",
          "capability",
          "path",
          "content",
          "expected",
          "prompt",
          "max_tokens",
        ].map((field) => [field, value(row, field)]),
      ),
      source_files: [
        ...(tasks.find((task) => task.id === row.dataset.task)?.source_files ||
          []),
      ],
      source_filter:
        tasks.find((task) => task.id === row.dataset.task)?.source_filter || "",
      compare: Boolean(row.querySelector('[data-field="compare"]')?.checked),
      depends_on: [...row.querySelectorAll("[data-dependency]:checked")].map(
        (checkbox) => checkbox.value,
      ),
    }));
  }
  /** Render only matching filenames; file contents are not fetched by this picker. */
  function sourceOptions(task) {
    return (
      sourceFiles
        .filter((file) =>
          file.path
            .toLowerCase()
            .includes((task.source_filter || "").toLowerCase()),
        )
        .slice(0, 100)
        .map(
          (file) =>
            `<label class="source-file"><input type="checkbox" data-source-file value="${e(file.path)}" ${(task.source_files || []).includes(file.path) ? "checked" : ""}><span>${e(file.path)}</span></label>`,
        )
        .join("") || '<p class="muted">No matching text files.</p>'
    );
  }
  /** Offer explicit read-only attachments from the selected project's source folder. */
  function sourcePicker(task) {
    if (!view.project.source_path) return "";
    return `<details class="source-picker"><summary>Project source files · <span data-source-count>${(task.source_files || []).length}</span> selected</summary><p class="muted">${e(view.project.source_path)}</p><p>Choose up to 8 files. Their contents are sent to this agent’s provider when you start the task. Original files are not modified.</p>${sourcesLoading ? '<p class="muted">Loading filenames…</p>' : sourceError ? `<p role="alert">${e(sourceError)}</p>` : `<input aria-label="Filter project files" data-source-filter placeholder="Filter filenames" value="${e(task.source_filter || "")}"><div class="source-options" data-source-options>${sourceOptions(task)}</div><small>${sourceFiles.length} available files. Up to 100 matches shown.${sourceTruncated ? " Listing was limited; narrow the project folder if needed." : ""}</small>`}</details>`;
  }
  /** Explain the difference between enforced API limits and a CLI output request. */
  function tokenLimitInput(agent, task) {
    const cli = agent.execution_mode === "cli";
    return input(
      cli ? "Requested output tokens" : "Maximum output tokens",
      "max_tokens",
      task.max_tokens || "2048",
      {
        number: true,
        hint: cli
          ? "The CLI may use a different number of tokens. Output is limited to 64 KiB and execution to 120 seconds; this is not a billing cap."
          : undefined,
      },
    );
  }
  /**
   * Render task editors, constrain action choices by agent, and bind graph editing.
   */
  function render() {
    content.innerHTML = `<form data-goal-form><div class="dialog-body"><div class="notice info">Tasks follow their dependencies and assigned agent settings. Model tasks use the assigned Local CLI or API connection and may consume your provider usage allowance.</div>${input("Goal title", "goal-title", title, { required: true })}<div class="form-section-heading"><h3>Tasks & dependencies</h3><button class="text-button" type="button" data-example>Use release example</button></div>${tasks
      .map((task, index) => {
        const agent =
          view.agents.find((item) => item.key === task.agent) || view.agents[0];
        const capabilities = agent.capabilities;
        if (!capabilities.includes(task.capability))
          task.capability = capabilities[0];
        return `<fieldset class="task-editor" data-task="${e(task.id)}"><legend>Step ${index + 1} · ${e(task.id)}</legend>${input("Task title", "title", task.title, { required: true })}<div class="form-grid"><label class="field"><span>Assigned agent</span><select aria-label="Assigned agent" data-field="agent">${view.agents.map((candidate) => `<option value="${e(candidate.key)}" ${agent.key === candidate.key ? "selected" : ""}>${e(candidate.name)} · ${e(candidate.role)}</option>`).join("")}</select></label><label class="field"><span>Action</span><select aria-label="Action" data-field="capability">${capabilities.map((cap) => `<option value="${e(cap)}" ${task.capability === cap ? "selected" : ""}>${e(ACTIONS[cap]?.label || cap)}</option>`).join("")}</select></label></div>${task.capability.startsWith("files.") || task.capability === "model.generate" ? input("Project-relative file path", "path", task.path, { required: true, hint: "For example: release/checklist.txt" }) : '<p class="muted">Checks the configured Python runtime on the local target.</p>'}${task.capability === "model.generate" ? `<p class="muted">${agent.execution_mode === "cli" ? "Local CLI" : "API key"} · ${e(agent.provider || "No provider")} · ${e(agent.model || "No model selected")}</p>${input("Task prompt", "prompt", task.prompt || "", { required: true, textarea: true, rows: 5 })}${tokenLimitInput(agent, task)}${sourcePicker(task)}` : ""}${task.capability === "files.write" ? input("File contents", "content", task.content, { textarea: true }) : ""}${task.capability === "files.inspect" ? `<label class="checkbox-line"><input type="checkbox" data-field="compare" ${task.compare ? "checked" : ""}> Compare exact contents</label>${task.compare ? input("Expected contents", "expected", task.expected, { textarea: true }) : '<p class="muted">Checks that the file is readable and records its digest.</p>'}` : ""}<div class="dependency-picker"><span class="label">Wait for these tasks to be verified</span>${
          tasks
            .filter((other) => other.id !== task.id)
            .map(
              (other) =>
                `<label><input type="checkbox" data-dependency value="${e(other.id)}" ${task.depends_on.includes(other.id) ? "checked" : ""}> ${e(other.title || other.id)}</label>`,
            )
            .join("") || '<span class="muted">No other tasks yet.</span>'
        }</div>${tasks.length > 1 ? `<button type="button" class="text-button danger-text" data-remove-task="${e(task.id)}">Remove task</button>` : ""}</fieldset>`;
      })
      .join(
        "",
      )}<button class="button" type="button" data-add-task>${icon("plus")} Add task</button><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Dependencies, agent limits, and project capacity are enforced by the backend.</span><button class="button primary" type="submit" data-label="Start goal">Start goal</button></footer></form>`;
    content.querySelector("[data-example]").onclick = () => {
      try {
        tasks = starterTasks(view.agents);
        title = "Prepare a verified release";
        nextId = 4;
        render();
      } catch (error) {
        showError(dialog, error);
      }
    };
    content.querySelector("[data-add-task]").onclick = () => {
      tasks = read();
      tasks.push(blankTask());
      render();
    };
    content.querySelectorAll("[data-remove-task]").forEach((button) => {
      button.onclick = () => {
        tasks = read().filter((task) => task.id !== button.dataset.removeTask);
        tasks.forEach((task) => {
          task.depends_on = task.depends_on.filter(
            (id) => id !== button.dataset.removeTask,
          );
        });
        render();
      };
    });
    content
      .querySelectorAll(
        '[data-field="agent"], [data-field="capability"], [data-field="compare"]',
      )
      .forEach((field) => {
        field.onchange = () => {
          tasks = read();
          render();
        };
      });
    content.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      if (controller.pending) return;
      try {
        tasks = read();
        const payload = goalPayload(view.project.id, title, tasks, view.agents);
        controller.busy(true);
        await onSubmit(payload);
        dialog.close();
      } catch (error) {
        showError(dialog, error);
      } finally {
        if (dialog.isConnected) controller.busy(false);
      }
    };
  }
  content.addEventListener("input", (event) => {
    if (!event.target.matches("[data-source-filter]")) return;
    const row = event.target.closest("[data-task]");
    const task = tasks.find((item) => item.id === row.dataset.task);
    task.source_filter = event.target.value;
    row.querySelector("[data-source-options]").innerHTML = sourceOptions(task);
  });
  content.addEventListener("change", (event) => {
    if (!event.target.matches("[data-source-file]")) return;
    const row = event.target.closest("[data-task]");
    const task = tasks.find((item) => item.id === row.dataset.task);
    const paths = new Set(task.source_files || []);
    if (event.target.checked) paths.add(event.target.value);
    else paths.delete(event.target.value);
    if (paths.size > 8) {
      event.target.checked = false;
      showError(
        dialog,
        new Error("Choose at most eight source files per task."),
      );
      return;
    }
    task.source_files = [...paths];
    row.querySelector("[data-source-count]").textContent = paths.size;
  });
  render();
  if (sourcesLoading) {
    loadSourceFiles()
      .then((result) => {
        sourceFiles = result.files;
        sourceTruncated = result.truncated;
      })
      .catch((error) => {
        sourceError = error.message;
      })
      .finally(() => {
        sourcesLoading = false;
        if (!dialog.isConnected) return;
        const active = document.activeElement;
        const field = active?.dataset.field;
        const rowId = active?.closest("[data-task]")?.dataset.task;
        tasks = read();
        render();
        const scope = rowId
          ? [...content.querySelectorAll("[data-task]")].find(
              (row) => row.dataset.task === rowId,
            )
          : content;
        if (field) scope?.querySelector(`[data-field="${field}"]`)?.focus();
      });
  }
  return dialog;
}

/**
 * Open execution evidence and fetch its artifact when a receipt references one.
 * Render receipt and artifact text without interpreting it as HTML.
 * @param {Document} document Host document.
 * @param {object} task Selected backend task.
 * @param {object|undefined} agent Assigned agent record if available.
 * @param {object} api API client used for artifact retrieval.
 * @returns {Promise<HTMLDialogElement>} Mounted evidence dialog.
 */
export async function openTaskDetails(document, task, agent, api) {
  const { dialog } = mountDialog(document, task.title, "Task evidence");
  dialog.classList.add("evidence-dialog");
  const content = dialog.querySelector("[data-content]");
  content.innerHTML = `<div class="dialog-body"><dl class="evidence-meta"><div><dt>Assigned agent</dt><dd>${e(agent?.name || "Unknown")}</dd></div><div><dt>Status</dt><dd>${e(task.state)}</dd></div><div><dt>Action</dt><dd>${e(ACTIONS[task.capability]?.label || task.capability)}</dd></div></dl>${task.error ? `<div class="notice danger">${e(task.error)}</div>` : ""}<h3>Execution receipt</h3><pre data-receipt></pre><section data-artifact class="hidden"><h3>Artifact contents</h3><pre data-artifact-content></pre></section><div class="notice danger hidden" data-error role="alert"></div></div>`;
  content.querySelector("[data-receipt]").textContent = task.receipt
    ? JSON.stringify(task.receipt, null, 2)
    : "No receipt yet. This task has not completed an execution.";
  if (task.receipt?.artifact) {
    try {
      const artifact = await api.artifact(task.id);
      content.querySelector("[data-artifact-content]").textContent =
        artifact.content;
      content.querySelector("[data-artifact]").classList.remove("hidden");
    } catch (error) {
      if (dialog.isConnected) showError(dialog, error);
    }
  }
  return dialog;
}
