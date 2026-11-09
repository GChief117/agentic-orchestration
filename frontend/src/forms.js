import {
  ACTIONS,
  escapeHtml as e,
  goalPayload,
  starterTasks,
} from "./domain.js";
import { icon, renderGoalTiming, renderTaskTiming } from "./views.js";
import { input, value, showError, mountDialog } from "./form-controls.js";
export { openProjectForm } from "./project-form.js";

/**
 * Open a goal editor scoped to an existing project and its assigned roster.
 * @param {Document} document Host document.
 * @param {object} view Project and agents returned by projectView.
 * @param {function(object): Promise<*>} onSubmit Persists the validated task graph.
 * @returns {HTMLDialogElement} Mounted task editor.
 */
export function openGoalForm(
  document,
  view,
  onSubmit,
  loadSourceFiles = null,
  onPlan = null,
) {
  const controller = mountDialog(document, "Define a goal", view.project.name);
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
  const resultId = new Uint8Array(12);
  document.defaultView.crypto.getRandomValues(resultId);
  const resultNamespace = [...resultId]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  let sourceFiles = [],
    sourceError = "",
    sourceTruncated = false,
    sourceBridge = null;
  const hasSources = Boolean(view.project.source_path || view.project.has_source_bridge);
  let sourcesLoading = Boolean(hasSources && loadSourceFiles);
  let title = "",
    nextId = 1;
  const planners = view.agents.filter(
    (agent) => agent.provider && agent.model && agent.capabilities.includes("model.generate"),
  );
  let stage = onPlan ? "goal" : "tasks";
  let goal = "";
  let coordinator =
    (planners.find((agent) => !agent.reports_to) || planners[0])?.key || "";
  let planSummary = "";
  let planTiming = null;
  let teamRevision = null;
  const goalSources = { source_files: [], source_filter: "" };
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
      max_tokens: "",
      advanced_open: false,
      compare: false,
      depends_on: [],
    };
  }
  let tasks = [blankTask()];
  /** Save the goal and lead selection before loading files or leaving the first step. */
  function captureGoal() {
    goal = value(content, "goal");
    title = value(content, "goal-title");
    coordinator = value(content, "coordinator");
  }
  /** Use the first line as a concise title when the owner leaves it optional. */
  function suggestedTitle() {
    return goal.trim().split(/\r?\n/)[0].slice(0, 120);
  }
  /** Turn a validated coordinator plan into editable drafts without starting tasks. */
  function planDrafts(plan) {
    if (!Array.isArray(plan.tasks) || !plan.tasks.length)
      throw new Error(
        "The coordinator returned no tasks. Try planning the goal again.",
      );
    if (typeof plan.team_revision !== "string" || !plan.team_revision)
      throw new Error("The plan could not be linked to this team. Try planning again.");
    return plan.tasks.map((task) => ({
      id: task.id,
      title: task.title,
      agent: task.agent,
      capability: task.capability,
      path: task.parameters?.path || "",
      prompt: task.parameters?.prompt || "",
      content: task.parameters?.content || "",
      expected: task.parameters?.expected || "",
      max_tokens: task.parameters?.max_tokens == null
        ? ""
        : String(task.parameters.max_tokens),
      source_files: [...(task.parameters?.source_files || [])],
      source_filter: "",
      advanced_open: false,
      compare: task.parameters?.expected !== undefined,
      depends_on: [...(task.depends_on || [])],
    }));
  }
  /** Ask the selected coordinator for a plan, then let the owner review it. */
  async function requestPlan(event) {
    event.preventDefault();
    if (controller.pending) return;
    captureGoal();
    if (!goal.trim()) {
      showError(dialog, new Error("Describe the outcome you want to achieve."));
      return;
    }
    if (!coordinator) {
      showError(
        dialog,
        new Error("Assign a model to a team member before asking for a plan."),
      );
      return;
    }
    let planned = false;
    let planningError = null;
    controller.busy(true);
    try {
      const plan = await onPlan({
        goal: goal.trim(),
        coordinator,
        source_files: [...goalSources.source_files],
      });
      tasks = planDrafts(plan);
      nextId = Math.max(
        0,
        ...tasks.map((task) => Number(task.id.match(/^task-(\d+)$/)?.[1]) || 0),
      ) + 1;
      planSummary = typeof plan.summary === "string" ? plan.summary : "";
      planTiming = plan.timing || null;
      teamRevision = plan.team_revision;
      if (plan.coordinator_key) coordinator = plan.coordinator_key;
      title = title.trim() || suggestedTitle();
      stage = "tasks";
      planned = true;
    } catch (error) {
      planningError = error;
    } finally {
      controller.busy(false);
    }
    if (dialog.isConnected) {
      if (planned) render();
      else if (planningError) {
        renderGoal();
        showError(dialog, planningError);
      }
    }
  }
  /** Start with the outcome and chart, leaving task details for the review step. */
  function renderGoal() {
    content.innerHTML = `<form data-goal-intake><div class="dialog-body"><p>Describe the outcome you want. Your coordinator will propose tasks for the team in your organization chart. You review the plan before any task starts.</p>${input("What should your team accomplish?", "goal", goal, { required: true, textarea: true, rows: 6, hint: "Include the result you want and anything the team should consider." })}${input("Goal title (optional)", "goal-title", title)}<label class="field"><span>Planning coordinator</span><select data-field="coordinator" aria-label="Planning coordinator">${planners.map((agent) => `<option value="${e(agent.key)}" ${agent.key === coordinator ? "selected" : ""}>${e(agent.name)} · ${e(agent.role)}</option>`).join("")}</select></label>${!planners.length ? '<div class="notice info">Assign a provider and model to at least one team member in the organization chart before planning.</div>' : ""}<section data-goal-sources>${sourcePicker(goalSources, true)}</section><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><button class="text-button" type="button" data-manual-goal>Define tasks manually</button><button class="button primary" type="submit" data-label="Plan with coordinator" ${planners.length ? "" : "disabled"}>Plan with coordinator</button></footer></form>`;
    content.querySelector("form").onsubmit = requestPlan;
    content.querySelector("[data-manual-goal]").onclick = () => {
      captureGoal();
      title = title.trim() || suggestedTitle();
      teamRevision = null;
      planSummary = "";
      planTiming = null;
      const task = blankTask();
      const lead = view.agents.find((agent) => agent.key === coordinator);
      if (lead) {
        task.agent = lead.key;
        task.capability = lead.capabilities.includes("model.generate")
          ? "model.generate"
          : lead.capabilities[0];
      }
      task.title = title;
      task.prompt = goal;
      task.source_files = [...goalSources.source_files];
      task.source_filter = goalSources.source_filter;
      tasks = [task];
      stage = "tasks";
      render();
    };
  }
  /**
   * Collect task rows, comparison fields, and dependency selections from the goal editor.
   */
  function read() {
    title = value(content, "goal-title");
    return [...content.querySelectorAll("[data-task]")].map((row) => {
      const task = {
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
        advanced_open: Boolean(row.querySelector("[data-task-advanced]")?.open),
        compare: Boolean(row.querySelector('[data-field="compare"]')?.checked),
        depends_on: [...row.querySelectorAll("[data-dependency]:checked")].map(
          (checkbox) => checkbox.value,
        ),
      };
      if (task.capability === "model.generate" && !task.path.trim())
        task.path = resultPath(task);
      return task;
    });
  }
  /** Give each model task a stable result filename without requiring a manual path. */
  function resultPath(task) {
    return `results/${resultNamespace}/${task.id}.md`;
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
  /** Offer explicit source attachments from the project's connected bridge or local folder. */
  function sourcePicker(task, planning = false) {
    if (!hasSources) return "";
    const bridgeConnected = Boolean(sourceBridge || view.project.has_source_bridge);
    const origin = bridgeConnected
      ? `Connected source bridge: ${sourceBridge?.name || "configured project bridge"}${sourceBridge?.hostname ? ` · ${sourceBridge.hostname}` : ""}`
      : view.project.source_path;
    return `<details class="source-picker"><summary>Project source files · <span data-source-count>${(task.source_files || []).length}</span> selected</summary><p class="muted">${e(origin)}</p>${bridgeConnected ? '<p class="muted">Paths use repository/file names from the connected source. A local folder with the same name is not the remote source.</p>' : ""}<p>${planning ? "Choose up to 8 files to share with the planning coordinator. Review each proposed task’s own source selection before starting." : "Choose up to 8 files. Their contents are sent to this agent’s provider when you start the task."} Original files are not modified.</p>${sourcesLoading ? '<p class="muted">Loading filenames…</p>' : sourceError ? `<p role="alert">${e(sourceError)}</p>` : `<input aria-label="Filter project files" data-source-filter placeholder="Filter filenames" value="${e(task.source_filter || "")}"><div class="source-options" data-source-options>${sourceOptions(task)}</div><small>${sourceFiles.length} available files. Up to 100 matches shown.${sourceTruncated ? " Listing was limited; use the filter to find a source file." : ""}</small>`}</details>`;
  }
  /** Explain the difference between enforced API limits and a CLI output request. */
  function tokenLimitInput(agent, task) {
    const cli = agent.execution_mode === "cli";
    return input(
      cli ? "Requested output tokens" : "Maximum output tokens",
      "max_tokens",
      cli ? task.max_tokens || "" : task.max_tokens || "2048",
      {
        number: true,
        placeholder: cli ? "Use CLI default" : undefined,
        hint: cli
          ? "Optional. Leave blank for the CLI’s default output length. A requested value is not a billing cap."
          : undefined,
      },
    );
  }
  /** Keep optional result-file and output controls outside the main task-writing flow. */
  function modelSettings(agent, task) {
    return `<details class="advanced-settings" data-task-advanced ${task.advanced_open ? "open" : ""}><summary>Advanced settings</summary>${input("Save result as", "path", task.path || resultPath(task), { hint: "The generated result is saved here inside this project's output folder. This is not a source file to read." })}${tokenLimitInput(agent, task)}</details>`;
  }
  /**
   * Render task editors, constrain action choices by agent, and bind graph editing.
   */
  function render() {
    if (stage === "goal") return renderGoal();
    content.innerHTML = `<form data-goal-form><div class="dialog-body">${planSummary ? `<div class="notice info"><strong>Coordinator’s plan</strong><p>${e(planSummary)}</p><span>Review and adjust the tasks below before starting the goal.</span></div>` : '<div class="notice info">Define the tasks your team should run. Each task follows its dependencies and the settings saved for its assigned agent.</div>'}${renderGoalTiming(planTiming, { preview: true })}${input("Goal title", "goal-title", title, { required: true })}<div class="form-section-heading"><h3>Tasks & dependencies</h3><button class="text-button" type="button" data-example>Use release example</button></div>${tasks
      .map((task, index) => {
        const agent =
          view.agents.find((item) => item.key === task.agent) || view.agents[0];
        const capabilities = agent.capabilities;
        if (!capabilities.includes(task.capability))
          task.capability = capabilities[0];
        return `<fieldset class="task-editor" data-task="${e(task.id)}"><legend>Step ${index + 1} · ${e(task.id)}</legend>${renderTaskTiming(planTiming?.task_timings?.[task.id], { preview: true })}${input("Task title", "title", task.title, { required: true })}<div class="form-grid"><label class="field"><span>Assigned agent</span><select aria-label="Assigned agent" data-field="agent">${view.agents.map((candidate) => `<option value="${e(candidate.key)}" ${agent.key === candidate.key ? "selected" : ""}>${e(candidate.name)} · ${e(candidate.role)}</option>`).join("")}</select></label><label class="field"><span>Action</span><select aria-label="Action" data-field="capability">${capabilities.map((cap) => `<option value="${e(cap)}" ${task.capability === cap ? "selected" : ""}>${e(ACTIONS[cap]?.label || cap)}</option>`).join("")}</select></label></div>${task.capability.startsWith("files.") ? input("Project-relative file path", "path", task.path, { required: true, hint: "For example: release/checklist.txt" }) : task.capability === "terminal.python_version" ? '<p class="muted">Checks the configured Python runtime on the local target.</p>' : ""}${task.capability === "model.generate" ? `<p class="muted">${agent.execution_mode === "cli" ? "Local CLI" : "API key"} · ${e(agent.provider || "No provider")} · ${e(agent.model || "No model selected")}</p>${input("Task prompt", "prompt", task.prompt || "", { required: true, textarea: true, rows: 5 })}${sourcePicker(task)}${modelSettings(agent, task)}` : ""}${task.capability === "files.write" ? input("File contents", "content", task.content, { textarea: true }) : ""}${task.capability === "files.inspect" ? `<label class="checkbox-line"><input type="checkbox" data-field="compare" ${task.compare ? "checked" : ""}> Compare exact contents</label>${task.compare ? input("Expected contents", "expected", task.expected, { textarea: true }) : '<p class="muted">Checks that the file is readable and records its digest.</p>'}` : ""}<div class="dependency-picker"><span class="label">Wait for these tasks to be verified</span>${
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
      )}<button class="button" type="button" data-add-task>${icon("plus")} Add task</button><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer">${onPlan ? '<button class="button" type="button" data-edit-goal>← Edit goal</button>' : '<span>Dependencies, agent limits, and project capacity are enforced by the backend.</span>'}<button class="button primary" type="submit" data-label="Start goal">Start goal</button></footer></form>`;
    content.querySelector("[data-edit-goal]")?.addEventListener("click", () => {
      tasks = read();
      stage = "goal";
      render();
    });
    content.querySelector("[data-example]").onclick = () => {
      try {
        tasks = starterTasks(view.agents);
        planSummary = "";
        planTiming = null;
        teamRevision = null;
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
        if (teamRevision != null) payload.team_revision = teamRevision;
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
    const row = event.target.closest("[data-task], [data-goal-sources]");
    const task = row.hasAttribute("data-goal-sources")
      ? goalSources
      : tasks.find((item) => item.id === row.dataset.task);
    task.source_filter = event.target.value;
    row.querySelector("[data-source-options]").innerHTML = sourceOptions(task);
  });
  content.addEventListener("change", (event) => {
    if (!event.target.matches("[data-source-file]")) return;
    const row = event.target.closest("[data-task], [data-goal-sources]");
    const task = row.hasAttribute("data-goal-sources")
      ? goalSources
      : tasks.find((item) => item.id === row.dataset.task);
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
        sourceBridge = result.bridge || null;
      })
      .catch((error) => {
        sourceError = error.message;
      })
      .finally(() => {
        sourcesLoading = false;
        if (!dialog.isConnected || controller.pending) return;
        const active = document.activeElement;
        const field = active?.dataset.field;
        const rowId = active?.closest("[data-task]")?.dataset.task;
        if (stage === "goal") captureGoal();
        else tasks = read();
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

/** Return a download filename without local directory names or control characters. */
function resultFilename(name) {
  const filename = String(name || "result.txt").replaceAll("\\", "/").split("/").pop().replace(/[\u0000-\u001f\u007f]/g, "");
  return filename && ![".", ".."].includes(filename) ? filename : "result.txt";
}

/** Download bounded text through a temporary link without object URLs or persistent timers. */
function downloadArtifact(document, artifact, fallbackName) {
  if (typeof artifact?.content !== "string") throw new Error("This result has no downloadable text.");
  const link = document.createElement("a");
  link.href = `data:text/plain;charset=utf-8,${encodeURIComponent(artifact.content)}`;
  link.download = resultFilename(artifact.name || fallbackName);
  link.hidden = true;
  document.body.append(link);
  try { link.click(); }
  finally { link.remove(); }
}

/** Fetch a saved task result and request a local text download. */
export async function downloadTaskArtifact(document, task, api) {
  const artifact = await api.artifact(task.id);
  downloadArtifact(document, artifact, task.receipt?.artifact);
}

/**
 * Show a saved result first, with execution evidence available in collapsed details.
 * Render result and receipt text without interpreting either as HTML.
 * @param {Document} document Host document.
 * @param {object} task Selected backend task.
 * @param {object|undefined} agent Assigned agent record if available.
 * @param {object} api API client used for artifact retrieval.
 * @returns {Promise<HTMLDialogElement>} Mounted evidence dialog.
 */
export async function openTaskDetails(document, task, agent, api) {
  const hasArtifact = Boolean(task.receipt?.artifact);
  const { dialog } = mountDialog(document, task.title, hasArtifact ? "Task result" : "Task evidence");
  dialog.classList.add("evidence-dialog");
  const content = dialog.querySelector("[data-content]");
  content.innerHTML = `<div class="dialog-body">${hasArtifact ? `<section data-artifact><div class="result-heading"><h3 data-artifact-name>${e(task.receipt.artifact)}</h3><button class="button primary" type="button" data-download-artifact="${e(task.id)}" disabled>Download result</button></div><p class="muted">Review this saved output against the goal and its source evidence.</p><pre data-artifact-content aria-label="Result contents">Loading result…</pre></section>` : ""}<dl class="evidence-meta"><div><dt>Assigned agent</dt><dd>${e(agent?.name || "Unknown")}</dd></div><div><dt>Execution status</dt><dd>${e(task.state === "accepted" ? "Finished" : task.state)}</dd></div><div><dt>Action</dt><dd>${e(ACTIONS[task.capability]?.label || task.capability)}</dd></div></dl>${task.error ? `<div class="notice danger">${e(task.error)}</div>` : ""}<details class="execution-receipt" ${hasArtifact ? "" : "open"}><summary>Execution receipt</summary><pre data-receipt></pre></details><div class="notice danger hidden" data-error role="alert"></div></div>`;
  content.querySelector("[data-receipt]").textContent = task.receipt
    ? JSON.stringify(task.receipt, null, 2)
    : "No receipt yet. This task has not completed an execution.";
  if (hasArtifact) {
    try {
      const artifact = await api.artifact(task.id);
      if (typeof artifact?.content !== "string") throw new Error("This result has no readable text.");
      content.querySelector("[data-artifact-content]").textContent =
        artifact.content;
      content.querySelector("[data-artifact-name]").textContent = artifact.name || task.receipt.artifact;
      const download = content.querySelector("[data-download-artifact]");
      download.disabled = false;
      download.addEventListener("click", () => downloadArtifact(document, artifact, task.receipt.artifact));
    } catch (error) {
      if (dialog.isConnected) {
        content.querySelector("[data-artifact-content]").textContent = "The saved result could not be loaded.";
        showError(dialog, error);
      }
    }
  }
  return dialog;
}
