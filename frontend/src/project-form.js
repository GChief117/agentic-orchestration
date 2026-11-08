import { ACTIONS, escapeHtml as e, projectPayload } from "./domain.js";
import { input, value, showError, mountDialog } from "./form-controls.js";
import {
  PROJECT_PRESETS,
  ROLES,
  changeRole,
  guidedDraft,
  makeAgent,
  suggestedName,
} from "./presets.js";
import { renderOrganizationChart } from "./organization.js";
import { createDictation } from "./speech.js";
import { icon } from "./views.js";

/**
 * Guide project creation through purpose, team organization, and review.
 * Internal keys are generated; role suggestions and dictated text remain editable.
 * @returns {HTMLDialogElement} Mounted editor; no project is saved before confirmation.
 */
export function openProjectForm(document, onSubmit) {
  const controller = mountDialog(
    document,
    "Let’s set up your project",
    "Project setup",
  );
  const { dialog } = controller;
  dialog.classList.add("guided-project-dialog");
  const content = dialog.querySelector("[data-content]");
  const dictation = createDictation(document.defaultView);
  const expandedAgents = new Set();
  let draft = guidedDraft(),
    step = 0,
    nextAgent = 4;
  dialog.addEventListener("close", () => dictation.destroy());

  /** Save visible inputs before changing a step or rebuilding the organization chart. */
  function capture() {
    if (step === 0) {
      for (const field of [
        "name",
        "objective",
        "scope",
        "constraints",
        "success_criteria",
      ]) {
        const control = content.querySelector(`[data-field="${field}"]`);
        if (control) draft[field] = control.value;
      }
    }
    const concurrency = content.querySelector(
      '[data-field="project_concurrency"]',
    );
    if (concurrency) draft.max_concurrency = concurrency.value;
    const rows = [...content.querySelectorAll("[data-agent]")];
    if (rows.length)
      draft.agents = rows.map((row) => {
        if (row.querySelector("details")?.open)
          expandedAgents.add(row.dataset.agent);
        else expandedAgents.delete(row.dataset.agent);
        const previous = draft.agents.find(
          (agent) => agent.key === row.dataset.agent,
        );
        return {
          ...previous,
          ...Object.fromEntries(
            [
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
        };
      });
  }

  /** Apply a starter category while retaining a name or objective written by the user. */
  function usePreset(key) {
    capture();
    const previous = PROJECT_PRESETS[draft.preset];
    const next = guidedDraft(key);
    if (draft.name && draft.name !== previous.projectName)
      next.name = draft.name;
    if (draft.objective && draft.objective !== previous.objective)
      next.objective = draft.objective;
    draft = next;
    nextAgent = draft.agents.length + 1;
    render();
  }

  /** Describe the project in plain language, with suggested answers and optional dictation. */
  function purposeStep() {
    return `<div class="step-intro"><h3>What would you like to accomplish?</h3><p>Describe it in your own words, or choose a starting point below.</p></div><div class="preset-grid">${Object.entries(
      PROJECT_PRESETS,
    )
      .map(
        ([key, preset]) =>
          `<button type="button" data-preset="${key}" class="preset-card ${draft.preset === key ? "chosen" : ""}" aria-pressed="${draft.preset === key}"><strong>${e(preset.name)}</strong><span>${e(preset.description)}</span></button>`,
      )
      .join(
        "",
      )}</div><div class="voice-field">${input("Your goal", "objective", draft.objective, { required: true, textarea: true, rows: 4, hint: "For example: help me research an idea, build a prototype, and review the result." })}${dictation.supported ? '<button class="button dictate-button" type="button" data-dictate>◉ Dictate</button><p class="voice-privacy">Dictation uses your browser’s speech service. This app does not store audio.</p>' : '<p class="muted">You can type here or use your device’s keyboard dictation.</p>'}<p class="speech-status" data-speech-status role="status"></p></div>${input("Project name", "name", draft.name, { hint: "Optional for now—we can suggest a name from your goal." })}<details class="advanced-settings"><summary>Refine scope and success criteria</summary><p class="muted">Suggested answers are already filled in. Adjust anything that matters to your project.</p><div class="form-grid">${input("What’s included", "scope", draft.scope, { textarea: true, hint: "One item per line" })}${input("What does success look like?", "success_criteria", draft.success_criteria, { textarea: true, hint: "One item per line" })}</div>${input("Anything to avoid?", "constraints", draft.constraints, { textarea: true, rows: 2 })}</details>`;
  }

  /** Render role selectors and editable defaults next to a live reporting chart. */
  function teamStep() {
    const capacity = draft.agents.reduce(
      (sum, agent) => sum + Number(agent.max_concurrency || 1),
      0,
    );
    draft.max_concurrency = String(
      Math.max(1, Math.min(Number(draft.max_concurrency) || 1, capacity)),
    );
    return `<div class="step-intro"><h3>Choose the team and how it works together.</h3><p>Pick a role. We’ll fill in its instructions, and you can edit them.</p></div><div class="team-setup-layout"><div class="role-editors"><div class="form-section-heading"><h3>${draft.agents.length} assigned agents</h3><button class="button" type="button" data-add-agent>${icon("plus")} Add role</button></div>${draft.agents
      .map(
        (agent, index) =>
          `<fieldset class="role-editor" data-agent="${e(agent.key)}"><legend>Team member ${index + 1}</legend><div class="role-editor-header"><label class="field"><span>Role</span><select aria-label="Role" data-role>${Object.entries(
            ROLES,
          )
            .map(
              ([key, role]) =>
                `<option value="${key}" ${agent.role_template === key ? "selected" : ""}>${e(role.name)}</option>`,
            )
            .join(
              "",
            )}</select></label>${draft.agents.length > 1 ? `<button type="button" class="icon-button" data-remove-agent="${e(agent.key)}" aria-label="Remove ${e(agent.name)}">×</button>` : ""}</div><p class="instruction-preview">${e(agent.instructions)}</p><label class="field"><span>Reports to</span><select aria-label="Reports to" data-field="reports_to"><option value="">You · project owner</option>${draft.agents
            .filter((other) => other.key !== agent.key)
            .map(
              (other) =>
                `<option value="${e(other.key)}" ${agent.reports_to === other.key ? "selected" : ""}>${e(other.name)}</option>`,
            )
            .join(
              "",
            )}</select></label><details class="agent-settings" ${expandedAgents.has(agent.key) ? "open" : ""}><summary>Edit instructions and permissions</summary>${input("Display name", "name", agent.name)}${input("Role description", "role", agent.role)}${input("Instructions", "instructions", agent.instructions, { textarea: true, rows: 4 })}<button class="text-button" type="button" data-reset-role="${e(agent.key)}">Restore suggested instructions</button>${input("Responsibilities", "responsibilities", agent.responsibilities, { textarea: true, rows: 2, hint: "One per line" })}${input("Tasks this agent can run at once", "max_concurrency", agent.max_concurrency, { number: true })}<div class="capability-picker"><span class="label">Allowed actions</span>${Object.entries(
            ACTIONS,
          )
            .map(
              ([key, action]) =>
                `<label><input type="checkbox" data-capability value="${key}" ${agent.capabilities.includes(key) ? "checked" : ""}> ${e(action.label)}</label>`,
            )
            .join("")}</div></details></fieldset>`,
      )
      .join(
        "",
      )}</div><aside class="team-chart-panel"><div class="panel-heading"><div><h3>Team organization</h3><p class="muted">Select a role card to edit that team member.</p></div></div>${renderOrganizationChart(draft.agents, { interactive: true })}<label class="field"><span>How much should run at once?</span><select data-field="project_concurrency" aria-label="Project concurrency">${Array.from(
      { length: capacity },
      (_, index) => index + 1,
    )
      .map(
        (count) =>
          `<option value="${count}" ${Number(draft.max_concurrency) === count ? "selected" : ""}>${count === 1 ? "One task at a time" : `Up to ${count} tasks at once`}</option>`,
      )
      .join(
        "",
      )}</select><small>Your ${draft.agents.length}-agent team stays assigned even when some agents are waiting.</small></label></aside></div>`;
  }

  /** Show the exact charter and organization that will be saved on confirmation. */
  function reviewStep() {
    return `<div class="step-intro"><h3>Ready to create ${e(draft.name)}?</h3><p>Review the plan. You can go back and change any answer.</p></div><section class="setup-review"><div><span class="label">Your goal</span><p class="review-objective">${e(draft.objective)}</p><span class="label">In scope</span><ul>${draft.scope
      .split("\n")
      .filter(Boolean)
      .map((line) => `<li>${e(line)}</li>`)
      .join(
        "",
      )}</ul><span class="label">Success criteria</span><ul>${draft.success_criteria
      .split("\n")
      .filter(Boolean)
      .map((line) => `<li>${e(line)}</li>`)
      .join(
        "",
      )}</ul><p class="muted">${draft.agents.length} assigned agents · up to ${draft.max_concurrency} concurrent tasks</p></div><div><span class="label">Team organization</span>${renderOrganizationChart(draft.agents)}</div></section><div class="notice info">This saves your project and team configuration. It does not start work or connect a model provider yet.</div>`;
  }

  /** Rebuild the current step and bind its plain-language setup controls. */
  function render() {
    dictation.destroy();
    const labels = ["Your project", "Your team", "Review"];
    content.innerHTML = `<form data-project-form><div class="setup-progress">${labels.map((label, index) => `<button type="button" data-step="${index}" ${index > step ? "disabled" : ""} class="${index === step ? "current" : index < step ? "complete" : ""}" aria-current="${index === step ? "step" : "false"}"><span>${index + 1}</span>${label}</button>`).join("")}</div><div class="dialog-body">${[purposeStep, teamStep, reviewStep][step]()}<div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><div>${step ? '<button type="button" class="button" data-back>← Back</button>' : '<button type="button" class="text-button" data-example>Try a filled-in example</button>'}</div><button type="submit" class="button primary" data-label="${step === 2 ? "Create project" : "Continue"}">${step === 2 ? "Create project" : "Continue →"}</button></footer></form>`;
    content
      .querySelector("[data-example]")
      ?.addEventListener("click", () => usePreset("software"));
    content
      .querySelectorAll("[data-preset]")
      .forEach((button) =>
        button.addEventListener("click", () =>
          usePreset(button.dataset.preset),
        ),
      );
    content.querySelectorAll("[data-step]").forEach((button) =>
      button.addEventListener("click", () => {
        capture();
        step = Number(button.dataset.step);
        render();
      }),
    );
    content.querySelector("[data-back]")?.addEventListener("click", () => {
      capture();
      step -= 1;
      render();
    });
    content.querySelector("[data-add-agent]")?.addEventListener("click", () => {
      capture();
      const parent =
        draft.agents.find((agent) => agent.role_template === "coordinator")
          ?.key || "";
      const agent = makeAgent("builder", `agent-${nextAgent++}`, parent);
      const count = draft.agents.filter(
        (item) => item.role_template === "builder",
      ).length;
      if (count) agent.name += ` ${count + 1}`;
      draft.agents.push(agent);
      render();
    });
    content.querySelectorAll("[data-remove-agent]").forEach((button) =>
      button.addEventListener("click", () => {
        capture();
        draft.agents = draft.agents.filter(
          (agent) => agent.key !== button.dataset.removeAgent,
        );
        const parent =
          draft.agents.find((agent) => agent.role_template === "coordinator")
            ?.key || "";
        draft.agents.forEach((agent) => {
          if (agent.reports_to === button.dataset.removeAgent)
            agent.reports_to = agent.key === parent ? "" : parent;
        });
        render();
      }),
    );
    content.querySelectorAll("[data-role]").forEach((select) =>
      select.addEventListener("change", () => {
        const key = select.closest("[data-agent]").dataset.agent;
        capture();
        draft.agents = draft.agents.map((agent) =>
          agent.key === key ? changeRole(agent, select.value) : agent,
        );
        render();
      }),
    );
    content
      .querySelectorAll(
        '[data-field="reports_to"], [data-agent] [data-field="max_concurrency"], [data-agent] [data-field="name"], [data-agent] [data-field="role"]',
      )
      .forEach((select) =>
        select.addEventListener("change", () => {
          capture();
          render();
        }),
      );
    content.querySelectorAll("[data-reset-role]").forEach((button) =>
      button.addEventListener("click", () => {
        capture();
        draft.agents = draft.agents.map((agent) =>
          agent.key === button.dataset.resetRole
            ? {
                ...agent,
                instructions: ROLES[agent.role_template].instructions,
                responsibilities: ROLES[agent.role_template].responsibilities,
              }
            : agent,
        );
        render();
      }),
    );
    content.querySelectorAll("[data-org-agent]").forEach((button) =>
      button.addEventListener("click", () => {
        const row = [...content.querySelectorAll("[data-agent]")].find(
          (item) => item.dataset.agent === button.dataset.orgAgent,
        );
        row?.querySelector("details")?.setAttribute("open", "");
        row?.scrollIntoView?.({ block: "center", behavior: "smooth" });
        row?.querySelector("select")?.focus();
      }),
    );
    content.querySelector("[data-dictate]")?.addEventListener("click", () => {
      dictation.start(
        content.querySelector('[data-field="objective"]'),
        (message, listening) => {
          if (!dialog.isConnected) return;
          const status = content.querySelector("[data-speech-status]");
          if (status) status.textContent = message;
          const button = content.querySelector("[data-dictate]");
          if (button)
            button.textContent = listening ? "■ Stop dictation" : "◉ Dictate";
        },
      );
    });
    content.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (controller.pending) return;
      try {
        capture();
        if (!draft.name.trim()) draft.name = suggestedName(draft.objective);
        const payload = projectPayload(draft);
        if (step < 2) {
          step += 1;
          render();
          return;
        }
        controller.busy(true);
        await onSubmit(payload);
        dialog.close();
      } catch (error) {
        showError(dialog, error);
      } finally {
        if (dialog.isConnected) controller.busy(false);
      }
    });
  }
  render();
  return dialog;
}
