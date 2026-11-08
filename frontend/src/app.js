import { projectView } from "./domain.js";
import { openGoalForm, openProjectForm, openTaskDetails } from "./forms.js";
import { renderWorkspace } from "./views.js";

export function mountApp({ document, api, pollInterval = 2000 }) {
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

  function notify(message) {
    feedback.textContent = message;
    feedback.classList.remove("hidden");
    window.clearTimeout(notificationTimer);
    notificationTimer = window.setTimeout(
      () => feedback.classList.add("hidden"),
      5000,
    );
  }
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
    });
    document.getElementById("connection-status").textContent =
      "Local · connected";
    document.getElementById("last-updated").textContent =
      `Updated ${new Date().toLocaleTimeString()}`;
    if (focusKey)
      [...root.querySelectorAll(`[data-${focusKey}]`)]
        .find((element) => element.dataset[focusKey] === focusValue)
        ?.focus({ preventScroll: true });
  }
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
  async function afterMutation() {
    if (pending) await pending;
    await refresh();
  }
  function createProject() {
    dialog = openProjectForm(document, async (payload) => {
      const result = await api.createProject(payload);
      selectedProject = result.id;
      tab = "overview";
      await afterMutation();
      notify("Project created. Its charter and assigned team are saved.");
    });
  }
  function createGoal() {
    const view = projectView(snapshot, selectedProject);
    if (!view) return;
    dialog = openGoalForm(document, view, async (payload) => {
      await api.submitGoal(payload);
      tab = "work";
      await afterMutation();
      notify("Goal submitted. Eligible tasks can now run.");
    });
  }
  async function click(event) {
    const button = event.target.closest(
      "[data-action], [data-project], [data-cancel], [data-inspect]",
    );
    if (!button || !root.contains(button)) return;
    event.preventDefault();
    try {
      if (button.dataset.project) {
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
      else if (button.dataset.action === "refresh") await refresh();
    } catch (error) {
      showError(error);
      if (button.isConnected) button.disabled = false;
    }
  }
  root.addEventListener("click", click);
  root.innerHTML =
    '<main class="startup"><span class="eyebrow">Orchestration</span><h1>Opening your workspace…</h1><p>Loading projects, teams, and task state.</p></main>';
  const ready = refresh();
  const interval =
    pollInterval > 0
      ? window.setInterval(() => {
          if (!document.hidden) refresh();
        }, pollInterval)
      : null;
  return {
    ready,
    refresh,
    get snapshot() {
      return snapshot;
    },
    destroy() {
      stopped = true;
      if (interval) window.clearInterval(interval);
      window.clearTimeout(notificationTimer);
      root.removeEventListener("click", click);
      if (dialog?.isConnected) dialog.remove();
    },
  };
}
