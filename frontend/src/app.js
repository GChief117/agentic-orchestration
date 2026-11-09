import { initializeAppearance, openAppearanceForm } from "./appearance.js";
import { openImportForm } from "./import-form.js";
import { openModelConnections } from "./model-connections.js";
import { openTeamForm } from "./team-form.js";
import { projectView } from "./domain.js";
import { openGoalForm, openProjectForm, openTaskDetails } from "./forms.js";
import { renderWorkspace } from "./views.js";

/**
 * Mount the project workspace and connect UI actions to the supplied API.
 * Polling updates task state without storing credentials or replacing open forms.
 * @param {object} options DOM document, API client, and polling interval (0 disables it).
 * @returns {object} Initial ready promise, refresh function, snapshot getter, and cleanup.
 */
export function mountApp({ document, api, pollInterval = 2000 }) {
  initializeAppearance(document);
  const root = document.getElementById("app");
  const feedback = document.getElementById("feedback");
  let snapshot = null,
    selectedProject = null,
    tab = "overview",
    workspaceTab = "projects";
  let pending = null,
    stopped = false,
    dialog = null;
  const window = document.defaultView;
  let notificationTimer;

  /**
   * Display a temporary accessible confirmation, replacing the prior timer.
   */
  function notify(message) {
    feedback.textContent = message;
    feedback.classList.remove("hidden");
    window.clearTimeout(notificationTimer);
    notificationTimer = window.setTimeout(
      () => feedback.classList.add("hidden"),
      5000,
    );
  }
  /**
   * Render the latest snapshot while preserving focus on equivalent controls.
   * Uses the selected project/tab only; performs no network request.
   */
  function render() {
    if (!snapshot || stopped) return;
    const focused = root.contains(document.activeElement)
      ? document.activeElement
      : null;
    const focusKey =
      focused &&
      ["project", "inspect", "cancel", "tab", "action"].find(
        (key) => focused.dataset[key],
      );
    const focusValue = focusKey && focused.dataset[focusKey];
    root.innerHTML = renderWorkspace(snapshot, {
      selectedProject,
      tab,
      workspaceTab,
      brandStyle: document.documentElement.dataset.brandStyle,
    });
    document.getElementById("connection-status").textContent =
      `${snapshot.features?.local_tools === false ? "Hosted" : "Local"} · connected`;
    document.getElementById("last-updated").textContent =
      `Updated ${new Date().toLocaleTimeString()}`;
    if (focusKey)
      [...root.querySelectorAll(`[data-${focusKey}]`)]
        .find((element) => element.dataset[focusKey] === focusValue)
        ?.focus({ preventScroll: true });
  }
  /**
   * Show a recoverable error, including a startup fallback before data loads.
   */
  function showError(error) {
    const box = document.getElementById("workspace-error");
    if (box) {
      box.textContent = error.message;
      box.classList.remove("hidden");
    } else {
      root.innerHTML =
        '<main class="startup"><h1>Connect to your workspace</h1><p data-startup-error role="alert"></p><button class="button primary" data-action="refresh">Try again</button></main>';
      root.querySelector("[data-startup-error]").textContent = error.message;
    }
    const connection = document.getElementById("connection-status");
    if (connection) connection.textContent = "Connection interrupted";
  }
  /**
   * Coalesce concurrent refreshes into one state request and render its result.
   * @returns {Promise<void>} Resolves after the fetch/render or displayed error.
   */
  async function refresh() {
    if (pending) return pending;
    pending = (async () => {
      try {
        const next = await api.state();
        if (stopped) return;
        snapshot = next;
        if (
          selectedProject &&
          !snapshot.projects.some((project) => project.id === selectedProject)
        )
          selectedProject = null;
        render();
      } catch (error) {
        if (!stopped) showError(error);
      } finally {
        pending = null;
      }
    })();
    return pending;
  }
  /**
   * Wait out any pre-mutation fetch, then request a fresh backend snapshot.
   */
  async function afterMutation() {
    if (pending) await pending;
    await refresh();
  }
  /**
   * Open the charter/team editor and select the project after it is persisted.
   */
  function createProject() {
    dialog = openProjectForm(
      document,
      async (payload) => {
        const result = await api.createProject(payload);
        selectedProject = result.id;
        tab = "overview";
        await afterMutation();
        notify("Project created. Its charter and assigned team are saved.");
      },
      snapshot.model_providers || [],
    );
  }
  /**
   * Open task assignment for the selected project and show its work after saving.
   */
  function createGoal() {
    const view = projectView(snapshot, selectedProject);
    if (!view) return;
    dialog = openGoalForm(
      document,
      view,
      async (payload) => {
        await api.submitGoal(payload);
        tab = "work";
        await afterMutation();
        notify("Goal submitted. Eligible tasks can now run.");
      },
      () => api.projectFiles(view.project.id),
    );
  }
  /** Open local discovery without importing anything until the user selects projects. */
  function importProjects(scanOnOpen = false) {
    dialog = openImportForm(
      document,
      api,
      async (projects) => {
        selectedProject = projects[0]?.id || null;
        tab = "overview";
        await afterMutation();
        notify(`${projects.length} local projects imported.`);
      },
      { scanOnOpen },
    );
  }
  /** Open chart editing for an idle project, optionally focused on a clicked role. */
  function editTeam(selectedKey = null) {
    const view = projectView(snapshot, selectedProject);
    if (!view) return;
    if (
      view.tasks.some((task) =>
        ["queued", "running", "cancelling"].includes(task.state),
      )
    ) {
      showError(
        new Error(
          "Wait for queued and running project work to finish before editing the team.",
        ),
      );
      return;
    }
    dialog = openTeamForm(
      document,
      view,
      async (payload) => {
        await api.updateTeam(view.project.id, payload);
        await afterMutation();
        notify("Team organization saved.");
      },
      selectedKey,
      snapshot.model_providers || [],
    );
  }

  /**
   * Delegate workspace button actions to navigation, editors, and API mutations.
   * @param {MouseEvent} event Click bubbling from a supported workspace control.
   */
  async function click(event) {
    const button = event.target.closest(
      "[data-action], [data-project], [data-cancel], [data-inspect], [data-org-agent]",
    );
    if (!button || !root.contains(button)) return;
    event.preventDefault();
    try {
      if (button.dataset.orgAgent) {
        editTeam(button.dataset.orgAgent);
      } else if (button.dataset.project) {
        selectedProject = button.dataset.project;
        tab = "overview";
        render();
      } else if (button.dataset.inspect) {
        const task = snapshot.tasks.find(
          (item) => item.id === button.dataset.inspect,
        );
        if (task)
          dialog = await openTaskDetails(
            document,
            task,
            snapshot.agents.find((agent) => agent.id === task.agent_id),
            api,
          );
      } else if (button.dataset.cancel) {
        button.disabled = true;
        await api.cancelGoal(button.dataset.cancel);
        await afterMutation();
        notify("Cancellation requested. Completed results are retained.");
      } else if (button.dataset.action === "projects") {
        selectedProject = null;
        workspaceTab = "projects";
        render();
      } else if (button.dataset.action === "activity") {
        selectedProject = null;
        workspaceTab = "activity";
        render();
      } else if (button.dataset.action === "tab") {
        tab = button.dataset.tab;
        render();
      } else if (button.dataset.action === "new-project") createProject();
      else if (button.dataset.action === "new-goal") createGoal();
      else if (button.dataset.action === "edit-team") editTeam();
      else if (button.dataset.action === "import-projects") importProjects();
      else if (button.dataset.action === "appearance")
        dialog = openAppearanceForm(document);
      else if (button.dataset.action === "providers")
        dialog = openModelConnections(
          document,
          api,
          snapshot.features?.local_tools !== false,
          afterMutation,
        );
      else if (button.dataset.action === "refresh") await refresh();
    } catch (error) {
      showError(error);
      if (button.isConnected) button.disabled = false;
    }
  }
  /** Let keyboard users open the same editor from a focusable chart node. */
  function chartKey(event) {
    const node = event.target.closest("[data-org-agent]");
    if (node && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      editTeam(node.dataset.orgAgent);
    }
  }
  root.addEventListener("click", click);
  root.addEventListener("keydown", chartKey);
  root.innerHTML =
    '<main class="startup"><span class="eyebrow">Orchestration</span><h1>Opening your workspace…</h1><p>Loading projects, teams, and task state.</p></main>';
  const ready = refresh().then(() => {
    const url = new URL(window.location.href);
    if (
      snapshot &&
      url.searchParams.get("import") === "1" &&
      snapshot.features?.local_tools !== false
    ) {
      importProjects(true);
      url.searchParams.delete("import");
      window.history.replaceState(null, "", url);
    }
  });
  const interval =
    pollInterval > 0
      ? window.setInterval(() => {
          if (!document.hidden) refresh();
        }, pollInterval)
      : null;
  return {
    ready,
    refresh,
    /**
     * Expose the latest backend snapshot for inspection and integration tests.
     */
    get snapshot() {
      return snapshot;
    },
    /**
     * Stop timers, detach listeners, and remove the active dialog.
     * Does not cancel work already submitted to the backend.
     */
    destroy() {
      stopped = true;
      if (interval) window.clearInterval(interval);
      window.clearTimeout(notificationTimer);
      root.removeEventListener("click", click);
      root.removeEventListener("keydown", chartKey);
      if (dialog?.isConnected) dialog.close();
    },
  };
}
