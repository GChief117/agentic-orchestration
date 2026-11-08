import {
  ACTIONS,
  escapeHtml as e,
  goalPayload,
  projectPayload,
  starterProject,
  starterTasks,
} from "./domain.js";
import { icon } from "./views.js";

/**
 * Render an escaped, labeled text, numeric, or multiline form field.
 * @returns {string} Markup; options control required state, rows, and helper text.
 */
function input(label, field, value = "", options = {}) {
  const attrs = `data-field="${field}" aria-label="${e(label)}" ${options.required ? "required" : ""}`;
  const control = options.textarea
    ? `<textarea ${attrs} rows="${options.rows || 3}" ${options.required ? "maxlength=4000" : ""}>${e(value)}</textarea>`
    : `<input ${attrs} type="${options.number ? "number" : "text"}" value="${e(value)}" ${options.number ? 'min="1" step="1"' : 'maxlength="120"'}>`;
  return `<label class="field"><span>${e(label)}</span>${control}${options.hint ? `<small>${e(options.hint)}</small>` : ""}</label>`;
}
/**
 * Read one named field inside the supplied DOM scope, returning empty text if absent.
 */
const value = (root, field) =>
  root.querySelector(`[data-field="${field}"]`)?.value ?? "";
/**
 * Display a validation or API error inside the active dialog using plain text.
 */
const showError = (dialog, error) => {
  const box = dialog.querySelector("[data-error]");
  box.textContent = error.message;
  box.classList.remove("hidden");
};

/**
 * Create an accessible dialog shell with cleanup and pending-request protection.
 * @param {Document} document Document in which to mount the editor.
 * @param {string} title Dialog heading and accessible label.
 * @param {string} subtitle Short context label.
 * @returns {object} Dialog element and busy-state controls; opening mutates the DOM.
 */
function mountDialog(document, title, subtitle) {
  const dialog = document.createElement("dialog");
  dialog.className = "editor-dialog";
  dialog.setAttribute("aria-label", title);
  dialog.innerHTML = `<header class="dialog-header"><div><span class="eyebrow">${e(subtitle)}</span><h2>${e(title)}</h2></div><button class="icon-button" data-close aria-label="Close dialog">×</button></header><div data-content></div>`;
  document.body.append(dialog);
  let pending = false;
  dialog.querySelector("[data-close]").onclick = () => {
    if (!pending) dialog.close();
  };
  dialog.addEventListener("cancel", (event) => {
    if (pending) event.preventDefault();
  });
  dialog.addEventListener("close", () => dialog.remove());
  if (typeof dialog.showModal === "function") dialog.showModal();
  else {
    dialog.setAttribute("open", "");
    dialog.close = () => dialog.remove();
  }
  return {
    dialog,
    /**
     * Report whether the dialog is waiting for a submission to finish.
     */
    get pending() {
      return pending;
    },
    /**
     * Disable editor controls and update the submit label during a request.
     */
    busy(flag) {
      pending = flag;
      dialog
        .querySelectorAll("button, input, textarea, select")
        .forEach((element) => {
          element.disabled = flag;
        });
      const button = dialog.querySelector('[type="submit"]');
      if (button) button.textContent = flag ? "Saving…" : button.dataset.label;
    },
  };
}

/**
 * Open an editable charter and agent-roster form.
 * Validate a draft before awaiting onSubmit; retain the form when submission fails.
 * @param {Document} document Host document.
 * @param {function(object): Promise<*>} onSubmit Persists the project payload.
 * @returns {HTMLDialogElement} Mounted project editor.
 */
export function openProjectForm(document, onSubmit) {
  const controller = mountDialog(
    document,
    "Start a new project",
    "Greenfield workspace",
  );
  const { dialog } = controller;
  /**
   * Create a fresh empty roster row; its concurrency remains editable.
   */
  const blankAgent = () => ({
    key: "",
    name: "",
    role: "",
    instructions: "",
    responsibilities: "",
    capabilities: [],
    reports_to: "",
    max_concurrency: "1",
  });
  let draft = {
    name: "",
    objective: "",
    scope: "",
    constraints: "",
    success_criteria: "",
    max_concurrency: "",
    agents: [blankAgent()],
  };
  const content = dialog.querySelector("[data-content]");
  /**
   * Collect the project editor values and roster rows without validating or saving them.
   */
  function read() {
    return {
      ...Object.fromEntries(
        [
          "name",
          "objective",
          "scope",
          "constraints",
          "success_criteria",
          "max_concurrency",
        ].map((field) => [field, value(content, field)]),
      ),
      agents: [...content.querySelectorAll("[data-agent]")].map((row) => ({
        ...Object.fromEntries(
          [
            "key",
            "name",
            "role",
            "instructions",
            "responsibilities",
            "reports_to",
            "max_concurrency",
          ].map((field) => [field, value(row, field)]),
        ),
        capabilities: [
          ...row.querySelectorAll("[data-capability]:checked"),
        ].map((checkbox) => checkbox.value),
      })),
    };
  }
  /**
   * Rebuild the project editor from its draft and bind add/remove/submit actions.
   */
  function render() {
    content.innerHTML = `<form data-project-form><div class="dialog-body"><div class="notice info">Your charter defines the work. Your roster defines who owns it. Execution limits control how much can run together.</div><div class="form-section-heading"><h3>01 · Project charter</h3><button type="button" class="text-button" data-example>Use example</button></div>${input("Project name", "name", draft.name, { required: true })}${input("Objective", "objective", draft.objective, { required: true, textarea: true })}<div class="form-grid">${input("In scope", "scope", draft.scope, { required: true, textarea: true, hint: "One item per line" })}${input("Success criteria", "success_criteria", draft.success_criteria, { required: true, textarea: true, hint: "One item per line" })}</div><div class="form-grid">${input("Constraints", "constraints", draft.constraints, { textarea: true })}${input("Project concurrency", "max_concurrency", draft.max_concurrency, { number: true, required: true, hint: "Maximum simultaneous executions for this project" })}</div><div class="form-section-heading"><div><h3>02 · Assigned team</h3><p>${draft.agents.length} agent${draft.agents.length === 1 ? "" : "s"} · team size is derived from this roster</p></div><button type="button" class="button" data-add-agent>${icon("plus")} Add agent</button></div><div class="team-editors">${draft.agents
      .map(
        (agent, index) =>
          `<fieldset class="agent-editor" data-agent="${index}"><legend>Agent ${index + 1}${agent.name ? ` · ${e(agent.name)}` : ""}</legend><div class="form-grid three">${input("Agent name", "name", agent.name, { required: true })}${input("Unique key", "key", agent.key, { required: true, hint: "Used to assign tasks" })}${input("Role", "role", agent.role, { required: true })}</div>${input("Instructions", "instructions", agent.instructions, { required: true, textarea: true, rows: 2 })}${input("Responsibilities", "responsibilities", agent.responsibilities, { required: true, textarea: true, rows: 2, hint: "One responsibility per line" })}<div class="form-grid"><label class="field"><span>Reports to</span><select data-field="reports_to" aria-label="Reports to"><option value="">Project owner</option>${draft.agents
            .filter((_, other) => other !== index)
            .filter((other) => other.key)
            .map(
              (other) =>
                `<option value="${e(other.key)}" ${agent.reports_to === other.key ? "selected" : ""}>${e(other.name || other.key)}</option>`,
            )
            .join(
              "",
            )}</select></label>${input("Agent concurrency", "max_concurrency", agent.max_concurrency, { number: true, required: true })}</div><div class="capability-picker"><span class="label">Permitted capabilities</span>${Object.entries(
            ACTIONS,
          )
            .map(
              ([key, action]) =>
                `<label><input type="checkbox" data-capability value="${key}" ${agent.capabilities.includes(key) ? "checked" : ""}> ${e(action.label)}</label>`,
            )
            .join(
              "",
            )}</div>${draft.agents.length > 1 ? `<button type="button" class="text-button danger-text" data-remove-agent="${index}">Remove agent</button>` : ""}</fieldset>`,
      )
      .join(
        "",
      )}</div><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Agent roles are persistent. Live model adapters are not connected.</span><button class="button primary" type="submit" data-label="Create project">Create project</button></footer></form>`;
    content.querySelector("[data-example]").onclick = () => {
      draft = starterProject();
      render();
    };
    content.querySelector("[data-add-agent]").onclick = () => {
      draft = read();
      draft.agents.push(blankAgent());
      render();
    };
    content.querySelectorAll("[data-remove-agent]").forEach((button) => {
      button.onclick = () => {
        draft = read();
        const [removed] = draft.agents.splice(
          Number(button.dataset.removeAgent),
          1,
        );
        draft.agents.forEach((agent) => {
          if (agent.reports_to === removed.key) agent.reports_to = "";
        });
        render();
      };
    });
    content
      .querySelectorAll('[data-agent] [data-field="key"]')
      .forEach((field) => {
        const original = field.value;
        field.onchange = () => {
          draft = read();
          if (original)
            draft.agents.forEach((agent) => {
              if (agent.reports_to === original) agent.reports_to = field.value;
            });
          render();
        };
      });
    content.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      if (controller.pending) return;
      try {
        const payload = projectPayload(read());
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
        ["title", "agent", "capability", "path", "content", "expected"].map(
          (field) => [field, value(row, field)],
        ),
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
    content.innerHTML = `<form data-goal-form><div class="dialog-body"><div class="notice info">Tasks run through the connected local backend. Each task needs an assigned agent with the right capability.</div>${input("Goal title", "goal-title", title, { required: true })}<div class="form-section-heading"><h3>Tasks & dependencies</h3><button class="text-button" type="button" data-example>Use release example</button></div>${tasks
      .map((task, index) => {
        const agent =
          view.agents.find((item) => item.key === task.agent) || view.agents[0];
        const capabilities = agent.capabilities;
        if (!capabilities.includes(task.capability))
          task.capability = capabilities[0];
        return `<fieldset class="task-editor" data-task="${e(task.id)}"><legend>Step ${index + 1} · ${e(task.id)}</legend>${input("Task title", "title", task.title, { required: true })}<div class="form-grid"><label class="field"><span>Assigned agent</span><select aria-label="Assigned agent" data-field="agent">${view.agents.map((candidate) => `<option value="${e(candidate.key)}" ${agent.key === candidate.key ? "selected" : ""}>${e(candidate.name)} · ${e(candidate.role)}</option>`).join("")}</select></label><label class="field"><span>Action</span><select aria-label="Action" data-field="capability">${capabilities.map((cap) => `<option value="${e(cap)}" ${task.capability === cap ? "selected" : ""}>${e(ACTIONS[cap]?.label || cap)}</option>`).join("")}</select></label></div>${task.capability.startsWith("files.") ? input("Project-relative file path", "path", task.path, { required: true, hint: "For example: release/checklist.txt" }) : '<p class="muted">Checks the configured Python runtime on the local target.</p>'}${task.capability === "files.write" ? input("File contents", "content", task.content, { textarea: true }) : ""}${task.capability === "files.inspect" ? `<label class="checkbox-line"><input type="checkbox" data-field="compare" ${task.compare ? "checked" : ""}> Compare exact contents</label>${task.compare ? input("Expected contents", "expected", task.expected, { textarea: true }) : '<p class="muted">Checks that the file is readable and records its digest.</p>'}` : ""}<div class="dependency-picker"><span class="label">Wait for these tasks to be verified</span>${
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
