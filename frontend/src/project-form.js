import { escapeHtml as e, projectPayload } from "./domain.js";
import { input, showError, mountDialog } from "./form-controls.js";
import { PROJECT_PRESETS, guidedDraft, suggestedName } from "./presets.js";
import { renderOrganizationChart } from "./organization.js";
import { createDictation } from "./speech.js";
import { mountOrganizationEditor } from "./organization-editor.js";

/**
 * Guide project creation through purpose, team organization, and review.
 * Internal keys are generated; role suggestions and dictated text remain editable.
 * @returns {HTMLDialogElement} Mounted editor; no project is saved before confirmation.
 */
export function openProjectForm(document, onSubmit, modelProviders = []) {
  const controller = mountDialog(
    document,
    "Let’s set up your project",
    "Project setup",
  );
  const { dialog } = controller;
  dialog.classList.add("guided-project-dialog");
  const content = dialog.querySelector("[data-content]");
  const dictation = createDictation(document.defaultView);
  let organizationEditor = null;
  let draft = guidedDraft(),
    step = 0;
  dialog.addEventListener("close", () => {
    dictation.destroy();
    organizationEditor?.destroy();
  });

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
    if (step === 1 && organizationEditor) {
      const team = organizationEditor.snapshot();
      draft.agents = team.agents;
      draft.max_concurrency = team.max_concurrency;
    }
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

  /** Make the organization chart the primary surface for choosing and editing agents. */
  function teamStep() {
    return '<div class="step-intro"><h3>Build your team directly on the chart.</h3><p>Select a card to edit. Add roles under it, drag agents onto a new manager, or change reporting lines in the inspector.</p></div><div data-organization-editor></div>';
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
      )}</ul><p class="muted">${draft.agents.length} assigned agents · up to ${draft.max_concurrency} concurrent tasks</p></div><div><span class="label">Team organization</span>${renderOrganizationChart(draft.agents)}</div></section><div class="notice info">This saves your project and team configuration. Start a goal when you are ready to run its tasks.</div>`;
  }

  /** Rebuild the current step and bind its plain-language setup controls. */
  function render() {
    dictation.destroy();
    organizationEditor?.destroy();
    organizationEditor = null;
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
    if (step === 1) {
      organizationEditor = mountOrganizationEditor({
        modelProviders,
        root: content.querySelector("[data-organization-editor]"),
        agents: draft.agents,
        maxConcurrency: draft.max_concurrency,
        onChange(team) {
          draft.agents = team.agents;
          draft.max_concurrency = team.max_concurrency;
        },
      });
    }
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
