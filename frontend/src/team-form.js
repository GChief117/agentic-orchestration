import { mountDialog, showError } from "./form-controls.js";
import { mountOrganizationEditor } from "./organization-editor.js";
import { projectPayload } from "./domain.js";

/** Open the same chart editor for an existing project's persisted team. */
export function openTeamForm(document, view, onSubmit, selectedKey = null) {
  const controller = mountDialog(
    document,
    "Edit team organization",
    view.project.name,
  );
  const { dialog } = controller;
  dialog.classList.add("guided-project-dialog", "team-organization-dialog");
  const lockedKeys = view.agents
    .filter((agent) => view.tasks.some((task) => task.agent_id === agent.id))
    .map((agent) => agent.key);
  const content = dialog.querySelector("[data-content]");
  content.innerHTML =
    '<form data-team-form><div class="dialog-body"><p class="muted">Select a card to edit its role. Drag it onto another card to change its manager. Edits are saved only when you choose Save organization.</p><div data-team-editor></div><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Existing agent identities and task history are preserved.</span><button class="button primary" type="submit" data-label="Save organization">Save organization</button></footer></form>';
  const editor = mountOrganizationEditor({
    root: content.querySelector("[data-team-editor]"),
    agents: view.agents,
    maxConcurrency: view.project.max_concurrency,
    lockedKeys,
    selectedKey,
  });
  dialog.addEventListener("close", () => editor.destroy());
  content.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (controller.pending) return;
    try {
      const team = editor.snapshot();
      const project = projectPayload({
        name: view.project.name,
        objective: view.project.charter.objective,
        scope: view.project.charter.scope.join("\n"),
        constraints: view.project.charter.constraints.join("\n"),
        success_criteria: view.project.charter.success_criteria.join("\n"),
        agents: team.agents,
        max_concurrency: team.max_concurrency,
      });
      controller.busy(true);
      await onSubmit({
        agents: project.agents,
        team_size: project.team_size,
        max_concurrency: project.max_concurrency,
      });
      dialog.close();
    } catch (error) {
      showError(dialog, error);
    } finally {
      if (dialog.isConnected) controller.busy(false);
    }
  });
  return dialog;
}
