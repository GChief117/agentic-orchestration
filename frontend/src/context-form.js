import { escapeHtml as e } from "./domain.js";
import { mountDialog, showError } from "./form-controls.js";

/** Format a recorded creation time without displaying invalid or missing dates. */
function contextTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
  const date = new Date(value * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  return `<time datetime="${date.toISOString()}">${e(date.toLocaleString())}</time>`;
}

/** Require a confirmed saved-entry collection before replacing the visible list. */
function confirmedContext(result) {
  if (!result || !Array.isArray(result.entries) || result.entries.some((entry) =>
    !entry || typeof entry.title !== "string" || typeof entry.content !== "string")) {
    throw new Error("The server did not return the saved shared context. Refresh to check its status.");
  }
  return result;
}

/**
 * Open project notes that the backend shares with team plans and task context.
 * Changes appear saved only after the server returns the confirmed entry list.
 */
export function openContextForm(document, project, api) {
  const controller = mountDialog(document, "Shared context", project.name || "Project");
  const { dialog } = controller;
  dialog.classList.add("context-dialog");
  const content = dialog.querySelector("[data-content]");
  content.innerHTML = `<div class="dialog-body"><p>Save project facts, decisions, and instructions here. Shared context is included automatically in team plans and task context.</p><section data-context-bridge class="hidden"></section><section aria-label="Saved shared context" data-context-entries><p>Loading shared context…</p></section><p data-context-status role="status" aria-live="polite"></p><div class="notice danger hidden" data-error role="alert"></div><form data-context-form><fieldset data-context-fields disabled><legend>Add shared context</legend><label class="field"><span>Title</span><input data-context-title aria-label="Title" maxlength="200" required></label><label class="field"><span>Context</span><textarea data-context-content aria-label="Context" maxlength="48000" rows="7" required></textarea></label><button class="button primary" type="submit" data-save-context data-label="Save shared context">Save shared context</button></fieldset></form></div><footer class="dialog-footer"><span>Saved notes stay with this project.</span><button class="button" data-refresh-context disabled>Refresh context</button></footer>`;
  const fields = content.querySelector("[data-context-fields]");
  const form = content.querySelector("[data-context-form]");
  const status = content.querySelector("[data-context-status]");
  const refresh = content.querySelector("[data-refresh-context]");
  const title = content.querySelector("[data-context-title]");
  const text = content.querySelector("[data-context-content]");
  let loading = false;

  /** Render only server-confirmed content, treating every supplied string as text. */
  function render(result) {
    const confirmed = confirmedContext(result);
    content.querySelector("[data-context-entries]").innerHTML = confirmed.entries.length
      ? confirmed.entries.map((entry) => `<article class="context-entry"><header><h3>${e(entry.title)}</h3>${contextTime(entry.created)}</header><p class="context-entry-content">${e(entry.content)}</p></article>`).join("")
      : '<p class="muted">No shared context saved yet.</p>';
    const bridge = confirmed.source_bridge;
    const source = content.querySelector("[data-context-bridge]");
    const repositories = Array.isArray(bridge?.repositories)
      ? bridge.repositories.filter((repository) => repository && typeof repository === "object")
      : [];
    source.classList.toggle("hidden", !bridge);
    source.innerHTML = bridge ? `<h3>Source connection</h3><p>${e(bridge.name || "Project source")}${bridge.status ? ` · ${e(bridge.status)}` : ""}</p><p>${repositories.length} ${repositories.length === 1 ? "repository" : "repositories"}</p>${repositories.length ? `<ul class="plain-list">${repositories.map((repository) => `<li>${e(repository.name || "Unnamed repository")}${repository.available === false ? " · Unavailable" : ""}</li>`).join("")}</ul>` : ""}<p class="muted">Remote project files are read through this bridge. The local checkout is not the remote source.</p>` : "";
  }

  /** Load the durable project context, preserving any unsaved draft during refresh. */
  async function load() {
    if (loading || controller.pending) return;
    loading = true;
    fields.disabled = true;
    refresh.disabled = true;
    status.textContent = "Loading shared context…";
    content.querySelector("[data-error]").classList.add("hidden");
    try {
      const result = await api.projectContext(project.id);
      if (!dialog.isConnected) return;
      render(result);
      status.textContent = "";
    } catch (error) {
      if (dialog.isConnected) {
        status.textContent = "Shared context could not be loaded.";
        showError(dialog, error);
      }
    } finally {
      loading = false;
      if (dialog.isConnected) {
        fields.disabled = false;
        refresh.disabled = false;
      }
    }
  }

  /** Persist one note, leaving the draft intact if the save cannot be confirmed. */
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (loading || controller.pending) return;
    const draft = { title: title.value.trim(), content: text.value.trim() };
    content.querySelector("[data-error]").classList.add("hidden");
    if (!draft.title || !draft.content) {
      showError(dialog, new Error("Enter a title and context before saving."));
      return;
    }
    controller.busy(true);
    fields.disabled = true;
    status.textContent = "Saving shared context…";
    try {
      const result = await api.addProjectContext(project.id, draft);
      if (!dialog.isConnected) return;
      render(result);
      title.value = "";
      text.value = "";
      status.textContent = "Shared context saved. The team will receive it in subsequent plans and tasks.";
    } catch (error) {
      if (dialog.isConnected) {
        status.textContent = "Save not confirmed. Your draft is still here.";
        showError(dialog, error);
      }
    } finally {
      controller.busy(false);
      fields.disabled = false;
    }
  });
  refresh.addEventListener("click", load);
  load();
  return dialog;
}
