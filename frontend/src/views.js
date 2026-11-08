import { renderOrganizationChart } from "./organization.js";
import {
  ACTIONS,
  STATUSES,
  escapeHtml as e,
  metrics,
  projectView,
} from "./domain.js";

const glyphs = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5"/>',
  people:
    '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',
};
/**
 * Return trusted inline SVG for a known icon name; it is hidden from screen readers.
 */
export const icon = (name) =>
  `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${glyphs[name] || glyphs.layers}</svg>`;
/**
 * Render an escaped empty-state heading and explanation.
 */
const empty = (title, body) =>
  `<div class="empty-state"><span class="empty-icon">${icon("layers")}</span><h3>${e(title)}</h3><p>${e(body)}</p></div>`;
/**
 * Map a backend state to escaped label markup and a fixed status style.
 * @returns {string} Accessible status badge markup.
 */
export function badge(state) {
  const status = STATUSES[state] || { label: state, tone: "neutral" };
  return `<span class="status ${status.tone}"><span></span>${e(status.label)}</span>`;
}
/**
 * Render an unordered list with every supplied item escaped as text.
 */
function bulletList(items) {
  return `<ul class="plain-list">${items.map((item) => `<li>${e(item)}</li>`).join("")}</ul>`;
}

/**
 * Render workspace counters derived from actual backend execution state.
 */
function metricCards(snapshot) {
  const m = metrics(snapshot);
  return `<div class="metrics">
    <div class="metric"><span>Projects</span><strong>${m.projects}</strong><small>Independent workspaces</small></div>
    <div class="metric"><span>Assigned agents</span><strong>${m.agents}</strong><small>Persistent team members</small></div>
    <div class="metric"><span>Active executions</span><strong>${m.active}<i> / ${m.capacity}</i></strong><small>Shared execution capacity</small></div>
    <div class="metric"><span>Verified tasks</span><strong>${m.verified}</strong><small>Acceptance checks passed</small></div>
  </div>`;
}

/**
 * Render selectable project summaries, or the first-project empty state.
 */
function projectCards(snapshot) {
  if (!snapshot.projects.length)
    return `${empty("A place for your next project.", "Define a charter, assemble a team, and turn your goals into accountable work.")}<div class="empty-action"><button class="button primary" data-action="new-project">${icon("plus")} Create a project</button></div>`;
  return `<div class="project-grid">${snapshot.projects
    .map((project, index) => {
      const view = projectView(snapshot, project.id);
      const accepted = view.tasks.filter(
        (task) => task.state === "accepted",
      ).length;
      return `<button class="project-card" data-project="${e(project.id)}"><div class="card-top"><span class="project-mark tint-${index % 4}">${e(project.name.slice(0, 2).toUpperCase())}</span><span class="eyebrow">Greenfield</span></div><h3>${e(project.name)}</h3><p>${e(project.charter.objective)}</p><div class="card-meta"><span>${icon("people")} ${project.team_size} agents</span><span>${project.active_executions} / ${project.max_concurrency} active</span></div><progress value="${accepted}" max="${Math.max(1, view.tasks.length)}" aria-label="${accepted} of ${view.tasks.length} tasks verified"></progress><div class="card-bottom"><span>${accepted} of ${view.tasks.length} tasks verified</span>${icon("arrow")}</div></button>`;
    })
    .join("")}</div>`;
}

/**
 * Render a project's goal/task rows with ownership, status, and cancellation controls.
 */
function goalsView(view) {
  if (!view.goals.length)
    return `${empty("The team is ready for its first goal.", "Give this project a goal and assign each step to a team member.")}<div class="empty-action"><button class="button primary" data-action="new-goal">${icon("plus")} Add a goal</button></div>`;
  const agents = new Map(view.agents.map((agent) => [agent.id, agent]));
  return view.goals
    .map((goal) => {
      const tasks = view.tasks.filter((task) => task.goal_id === goal.id);
      return `<section class="panel goal-panel"><header class="panel-heading"><div><span class="eyebrow">Goal</span><h3>${e(goal.title)}</h3></div>${badge(goal.state)}</header><div class="task-list">${tasks.map((task) => `<button class="task-row" data-inspect="${e(task.id)}"><span class="task-symbol ${task.state === "accepted" ? "done" : ""}">${task.state === "accepted" ? "✓" : "·"}</span><span class="task-title"><strong>${e(task.title)}</strong><small>${e(agents.get(task.agent_id)?.name || "Unassigned")} <b>·</b> ${e(ACTIONS[task.capability]?.short || task.capability)}${task.dependencies.length ? ` <b>·</b> ${task.dependencies.length} dependencies` : ""}</small></span>${badge(task.state)}<span class="row-arrow">↗</span></button>`).join("")}</div><footer class="goal-footer"><span>${goal.accepted} of ${goal.total} tasks verified</span>${["queued", "running", "cancelling"].includes(goal.state) ? `<button class="text-button danger-text" data-cancel="${e(goal.id)}" ${goal.state === "cancelling" ? "disabled" : ""}>${goal.state === "cancelling" ? "Cancelling…" : "Cancel remaining work"}</button>` : "<span>Results retained</span>"}</footer></section>`;
    })
    .join("");
}

/**
 * Render persistent agent roles, responsibilities, managers, and active capacity.
 */
function teamView(view) {
  const byId = new Map(view.agents.map((agent) => [agent.id, agent]));
  return `<div class="section-intro"><h2>Assigned team</h2><p>Roles stay with this project. Execution slots are allocated as work becomes ready.</p></div><section class="panel organization-panel"><div class="panel-heading"><h2>Team organization</h2></div>${renderOrganizationChart(view.agents)}</section><div class="agent-grid">${view.agents.map((agent) => `<article class="panel agent-card"><div class="agent-top"><span class="avatar">${e(agent.name.slice(0, 2).toUpperCase())}</span><span class="status ${agent.state === "busy" ? "active" : "neutral"}"><span></span>${agent.state === "busy" ? "Working" : "Available"}</span></div><h3>${e(agent.name)}</h3><span class="role-label">${e(agent.role)}</span><p class="agent-instructions">${e(agent.instructions)}</p><div class="label">Responsibilities</div>${bulletList(agent.responsibilities)}<div class="capability-tags">${agent.capabilities.map((cap) => `<span>${e(ACTIONS[cap]?.short || cap)}</span>`).join("")}</div><footer><span>Reports to <strong>${e(byId.get(agent.reports_to)?.name || "Project owner")}</strong></span><span>${agent.active_executions} / ${agent.max_concurrency} active</span></footer></article>`).join("")}</div>`;
}

/**
 * Render recent events, optionally restricted to task IDs from one project.
 * Project filtering omits unscoped workspace events.
 */
function activityView(snapshot, view = null) {
  const taskIds = view && new Set(view.tasks.map((task) => task.id));
  const events = taskIds
    ? snapshot.events.filter((event) => taskIds.has(event.task_id))
    : snapshot.events;
  if (!events.length)
    return empty(
      "No execution activity yet.",
      "Task starts, results, and cancellations will be recorded here.",
    );
  const tasks = new Map(snapshot.tasks.map((task) => [task.id, task]));
  return `<section class="panel"><div class="activity-list">${events.map((event) => `<div class="activity-row"><span class="activity-dot"></span><div><strong>${e(tasks.get(event.task_id)?.title || event.message)}</strong><p>${e(event.message)}</p></div><time datetime="${new Date(event.at * 1000).toISOString()}">${e(new Date(event.at * 1000).toLocaleTimeString())}</time></div>`).join("")}</div></section>`;
}

/**
 * Render the selected project charter, capacity summary, and goal overview.
 */
function overviewView(snapshot, view) {
  const p = view.project;
  return `<div class="overview-grid"><section class="panel charter"><div class="panel-heading"><h2>Project charter</h2><span class="chip">Greenfield</span></div><div class="charter-body"><span class="label">Objective</span><p class="objective">${e(p.charter.objective)}</p><div class="charter-columns"><div><span class="label">In scope</span>${bulletList(p.charter.scope)}</div><div><span class="label">Success criteria</span>${bulletList(p.charter.success_criteria)}</div></div>${p.charter.constraints.length ? `<span class="label">Constraints</span>${bulletList(p.charter.constraints)}` : ""}</div></section><section class="panel capacity-panel"><h2>Team & capacity</h2><p>Assigned agents and available execution slots are separate.</p><dl><div><dt>Assigned agents</dt><dd>${p.team_size}</dd></div><div><dt>Project concurrency</dt><dd>${p.max_concurrency}</dd></div><div><dt>Currently executing</dt><dd>${p.active_executions}</dd></div><div><dt>Workspace limit</dt><dd>${snapshot.capacity.execution_limit}</dd></div></dl><div class="small-note">Local execution is connected. Model providers and remote adapters are not connected.</div></section></div><section class="panel organization-panel overview-organization"><div class="panel-heading"><h2>Team organization</h2><button class="text-button" data-action="tab" data-tab="team">View team ${icon("arrow")}</button></div>${renderOrganizationChart(view.agents)}</section><div class="section-heading"><h2>Project goals</h2><button class="text-button" data-action="tab" data-tab="work">View all ${icon("arrow")}</button></div>${goalsView(view)}`;
}

/**
 * Render the complete navigation shell and selected workspace/project view.
 * All backend-provided text is escaped; this function neither mounts DOM nor fetches data.
 * @param {object} snapshot Current backend state.
 * @param {object} [selection] Selected project, project tab, and workspace tab.
 * @returns {string} HTML ready for the controller to mount.
 */
export function renderWorkspace(
  snapshot,
  { selectedProject = null, tab = "overview", workspaceTab = "projects" } = {},
) {
  const view = selectedProject && projectView(snapshot, selectedProject);
  const heading = view
    ? view.project.name
    : workspaceTab === "activity"
      ? "Workspace activity"
      : "Your projects";
  const description = view
    ? view.project.charter.objective
    : "Give every project a clear purpose, an assigned team, and a traceable path to completion.";
  let body;
  if (!view)
    body = `${metricCards(snapshot)}${workspaceTab === "activity" ? activityView(snapshot) : `<div class="section-heading"><h2>Project workspaces <span class="count">${snapshot.projects.length}</span></h2><span class="muted">Each team has its own charter and workspace</span></div>${projectCards(snapshot)}`}`;
  else
    body = `<nav class="tabs" aria-label="Project sections">${[
      ["overview", "Overview"],
      ["team", "Team"],
      ["work", "Goals & tasks"],
      ["activity", "Activity"],
    ]
      .map(
        ([key, label]) =>
          `<button class="${tab === key ? "selected" : ""}" data-action="tab" data-tab="${key}" aria-current="${tab === key ? "page" : "false"}">${label}${key === "team" ? ` <span>${view.agents.length}</span>` : ""}</button>`,
      )
      .join(
        "",
      )}</nav>${tab === "team" ? teamView(view) : tab === "work" ? goalsView(view) : tab === "activity" ? activityView(snapshot, view) : overviewView(snapshot, view)}`;
  return `<aside class="sidebar"><a class="brand" href="#" data-action="projects"><span class="brand-icon">↗</span><span>Orchestration<small>PROJECT WORKSPACE</small></span></a><span class="nav-label">Workspace</span><nav><button class="nav-item ${!view && workspaceTab === "projects" ? "current" : ""}" data-action="projects">${icon("grid")} Projects <span>${snapshot.projects.length}</span></button><button class="nav-item ${!view && workspaceTab === "activity" ? "current" : ""}" data-action="activity">${icon("layers")} Activity</button></nav><div class="nav-project-heading"><span class="nav-label">Your projects</span><button class="icon-button" data-action="new-project" aria-label="Create project">${icon("plus")}</button></div><nav class="project-nav">${snapshot.projects.map((project) => `<button class="nav-item ${view?.project.id === project.id ? "current" : ""}" data-project="${e(project.id)}"><span class="project-dot"></span><span class="truncate">${e(project.name)}</span></button>`).join("")}</nav><footer class="sidebar-footer"><span class="local-indicator"></span><div>Local workspace<small>Independent · experimental</small></div></footer></aside><div class="main-area"><header class="topbar"><span>Workspace <b>/</b> ${view ? "Project" : workspaceTab === "activity" ? "Activity" : "Overview"}</span><div class="topbar-actions"><span class="connection" id="connection-status" role="status">Connecting</span><button class="icon-button" data-action="refresh" aria-label="Refresh workspace">↻</button></div></header><main><div class="page-header"><div>${view ? `<button class="back-link" data-action="projects">← All projects</button>` : '<span class="eyebrow">A clearer way to coordinate</span>'}<h1>${e(heading)}</h1><p>${e(description)}</p></div><button class="button primary" data-action="${view ? "new-goal" : "new-project"}">${icon("plus")} ${view ? "New goal" : "New project"}</button></div><div id="workspace-error" class="notice danger hidden" role="alert"></div>${body}<footer class="page-footer"><span>Local operations · Results are backed by execution receipts</span><span id="last-updated"></span></footer></main></div>`;
}
