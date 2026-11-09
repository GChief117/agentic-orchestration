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
export function openGoalForm(document, view, onSubmit) {
  const controller = mountDialog(document, "Define a goal", view.project.name);
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
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
      compare: Boolean(row.querySelector('[data-field="compare"]')?.checked),
      depends_on: [...row.querySelectorAll("[data-dependency]:checked")].map(
        (checkbox) => checkbox.value,
      ),
    }));
  }
  /**
   * Render task editors, constrain action choices by agent, and bind graph editing.
   */
  function render() {
    content.innerHTML = `<form data-goal-form><div class="dialog-body"><div class="notice info">Tasks follow their dependencies and assigned agent settings. Model tasks call the chosen provider and may incur API charges.</div>${input("Goal title", "goal-title", title, { required: true })}<div class="form-section-heading"><h3>Tasks & dependencies</h3><button class="text-button" type="button" data-example>Use release example</button></div>${tasks
      .map((task, index) => {
        const agent =
          view.agents.find((item) => item.key === task.agent) || view.agents[0];
        const capabilities = agent.capabilities;
        if (!capabilities.includes(task.capability))
          task.capability = capabilities[0];
        return `<fieldset class="task-editor" data-task="${e(task.id)}"><legend>Step ${index + 1} · ${e(task.id)}</legend>${input("Task title", "title", task.title, { required: true })}<div class="form-grid"><label class="field"><span>Assigned agent</span><select aria-label="Assigned agent" data-field="agent">${view.agents.map((candidate) => `<option value="${e(candidate.key)}" ${agent.key === candidate.key ? "selected" : ""}>${e(candidate.name)} · ${e(candidate.role)}</option>`).join("")}</select></label><label class="field"><span>Action</span><select aria-label="Action" data-field="capability">${capabilities.map((cap) => `<option value="${e(cap)}" ${task.capability === cap ? "selected" : ""}>${e(ACTIONS[cap]?.label || cap)}</option>`).join("")}</select></label></div>${task.capability.startsWith("files.") || task.capability === "model.generate" ? input("Project-relative file path", "path", task.path, { required: true, hint: "For example: release/checklist.txt" }) : '<p class="muted">Checks the configured Python runtime on the local target.</p>'}${task.capability === "model.generate" ? `<p class="muted">${e(agent.provider || "No provider")} · ${e(agent.model || "No model selected")}</p>${input("Task prompt", "prompt", task.prompt || "", { required: true, textarea: true, rows: 5 })}${input("Maximum output tokens", "max_tokens", task.max_tokens || "2048", { number: true })}` : ""}${task.capability === "files.write" ? input("File contents", "content", task.content, { textarea: true }) : ""}${task.capability === "files.inspect" ? `<label class="checkbox-line"><input type="checkbox" data-field="compare" ${task.compare ? "checked" : ""}> Compare exact contents</label>${task.compare ? input("Expected contents", "expected", task.expected, { textarea: true }) : '<p class="muted">Checks that the file is readable and records its digest.</p>'}` : ""}<div class="dependency-picker"><span class="label">Wait for these tasks to be verified</span>${
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
  render();
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
