import { renderBrand } from "./branding.js";
import { renderOrganizationChart } from "./organization.js";
import {
  ACTIONS,
  DELIVERABLE_FORMATS,
  DELIVERABLE_LAYOUTS,
  DELIVERABLE_STYLES,
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
export function badge(state, label = null) {
  const status = STATUSES[state] || { label: state, tone: "neutral" };
  return `<span class="status ${status.tone}"><span></span>${e(label || status.label)}</span>`;
}
/**
 * Render an unordered list with every supplied item escaped as text.
 */
function bulletList(items) {
  return `<ul class="plain-list">${items.map((item) => `<li>${e(item)}</li>`).join("")}</ul>`;
}

/** Format approximate durations without implying second-by-second precision. */
export function durationLabel(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)
    return null;
  if (seconds < 60) return "under 1 min";
  const minutes = Math.ceil(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (!hours) return `${minutes} min`;
  return `${hours} hr${remainder ? ` ${remainder} min` : ""}`;
}

/** Format recorded execution time at second precision, independent of forecast rounding. */
export function elapsedLabel(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)
    return null;
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  const parts = [];
  if (hours) parts.push(`${hours} hr`);
  if (minutes) parts.push(`${minutes} min`);
  if (remainder || !parts.length) parts.push(`${remainder} sec`);
  return parts.join(" ");
}

/** Format one estimated completion window, preserving an unknown estimate as unknown. */
function remainingWindow(timing) {
  const low = durationLabel(timing.remaining_low_seconds);
  const high = durationLabel(timing.remaining_high_seconds);
  const typical = durationLabel(timing.remaining_seconds);
  const upperBound = high === "under 1 min" ? "Under 1 min" : `Up to ${high}`;
  if (timing.basis === "execution_limit" && high) return upperBound;
  if (low && high && timing.remaining_low_seconds <= timing.remaining_high_seconds) {
    if (low === high) return low === "under 1 min" ? "Under 1 min" : `About ${high}`;
    if (timing.remaining_low_seconds < 60) return `Up to ${high}`;
    return `About ${low}–${high}`;
  }
  if (typical) return typical === "under 1 min" ? "Under 1 min" : `About ${typical}`;
  return high ? upperBound : "ETA unavailable";
}

/** Show per-task elapsed time and expected finish/start without hiding uncertainty. */
export function renderTaskTiming(timing, { state = "queued", preview = false } = {}) {
  if (!timing || typeof timing !== "object") return "";
  const elapsed = elapsedLabel(timing.elapsed_seconds);
  const terminal = ["accepted", "failed", "cancelled", "blocked"].includes(state);
  if (terminal || state === "cancelling")
    return elapsed ? `<span class="task-timing">${terminal ? "Duration" : "Elapsed"}: ${e(elapsed)}</span>` : "";
  const parts = [];
  if (!preview && state === "running" && elapsed) parts.push(`Elapsed: ${elapsed}`);
  const label = timing.basis === "execution_limit" ? "Initial allowance" : preview ? "Proposed task estimate" : "Estimated finish in";
  parts.push(`${label}: ${remainingWindow(timing)}`);
  if ((preview || state === "queued") && typeof timing.starts_in_seconds === "number" && timing.starts_in_seconds > 0) {
    const start = durationLabel(timing.starts_in_seconds);
    if (start) parts.push(`Estimated start in ${start}`);
  }
  return `<span class="task-timing">${e(parts.join(" · "))}</span>`;
}

/** Render a goal's measured elapsed time and clearly qualified remaining estimate. */
export function renderGoalTiming(timing, { state = "queued", preview = false } = {}) {
  if (!timing || typeof timing !== "object") return "";
  const finite = (number) => typeof number === "number" && Number.isFinite(number) && number >= 0;
  const terminal = ["accepted", "failed", "cancelled", "blocked"].includes(state);
  const stopping = state === "cancelling";
  const cells = [];
  const count = (number) => finite(number) ? Math.floor(number) : 0;
  const elapsed = elapsedLabel(timing.elapsed_seconds);
  if (!preview && elapsed)
    cells.push(`<div><span class="label">${terminal ? "Total elapsed" : "Elapsed"}</span><strong>${e(elapsed)}</strong></div>`);
  if (terminal || stopping) {
    return cells.length ? `<section class="goal-timing" aria-label="Goal timing">${cells.join("")}${stopping ? '<p class="goal-timing-detail">Stopping remaining work.</p>' : ""}</section>` : "";
  }

  const allowance = timing.basis === "execution_limit";
  const remaining = remainingWindow(timing);
  const label = allowance ? "Initial time allowance" : preview ? "Proposed-plan estimate" : "Estimated remaining";
  cells.push(`<div><span class="label">${label}</span><strong>${e(remaining)}</strong></div>`);

  if (!allowance && finite(timing.eta_at) && timing.eta_at > 0) {
    const finish = new Date(timing.eta_at * 1000);
    if (Number.isFinite(finish.getTime())) {
      const clock = finish.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      cells.push(`<div><span class="label">Estimated finish</span><time datetime="${finish.toISOString()}">Around ${e(clock)}</time></div>`);
    }
  }
  const confidence = { low: "Low confidence", medium: "Medium confidence", high: "High confidence" }[timing.confidence];
  const samples = count(timing.sample_count);
  let explanation = allowance
    ? "No duration history yet; allowance uses execution limits. Actual completion may be sooner."
    : samples ? `Based on ${samples} completed ${samples === 1 ? "task" : "tasks"}.` : "Timing will update as execution data becomes available.";
  if (confidence) explanation = `${confidence}. ${explanation}`;
  if (preview) explanation += " This estimate is for the proposed plan; edits may change its duration.";
  else explanation = `${count(timing.active_tasks)} running · ${count(timing.waiting_tasks)} waiting. ${explanation}`;
  return `<section class="goal-timing" aria-label="${preview ? "Plan timing" : "Goal timing"}">${cells.join("")}<p class="goal-timing-detail">${e(explanation)}</p></section>`;
}

/** Render visible progress reported by active executors, never hidden model reasoning. */
function liveActivity(tasks, agents) {
  const active = tasks.filter((task) => ["running", "cancelling"].includes(task.state));
  if (!active.length) return "";
  return `<section class="goal-activity" aria-label="Live agent activity"><h4>Live agent activity</h4>${active.map((task) => {
    const activity = task.activity || {};
    const agent = agents.get(task.agent_id);
    const message = activity.message || (task.state === "cancelling" ? "Stopping this task." : "Running the assigned task.");
    const model = activity.model || agent?.model;
    const provider = activity.provider || agent?.provider;
    const modelLabel = task.capability === "model.generate" && model
      ? [provider, model === "default" ? "CLI default model" : model].filter(Boolean).join(" · ")
      : "";
    return `<article class="agent-activity"><div><span class="activity-indicator" aria-hidden="true"></span><strong>${e(agent?.name || "Assigned agent")}</strong><span>${e(task.title)}</span></div>${modelLabel ? `<p class="activity-model">Model: ${e(modelLabel)}</p>` : ""}<p>${e(message)}</p>${activity.preview ? `<details data-activity-preview="${e(task.id)}"><summary>Current response</summary><pre>${e(activity.preview)}</pre></details>` : ""}</article>`;
  }).join("")}</section>`;
}

/** Render one task row with measured timing and a restrained running-state indicator. */
function taskRow(task, agents) {
  const running = ["running", "cancelling"].includes(task.state);
  return `<button class="task-row ${running ? "is-running" : ""}" data-inspect="${e(task.id)}"><span class="task-symbol ${task.state === "accepted" ? "done" : ""}">${task.state === "accepted" ? "✓" : "·"}</span><span class="task-title"><strong>${e(task.title)}</strong><small>${e(agents.get(task.agent_id)?.name || "Unassigned")} <b>·</b> ${e(ACTIONS[task.capability]?.short || task.capability)}${task.dependencies.length ? ` <b>·</b> ${task.dependencies.length} dependencies` : ""}</small>${renderTaskTiming(task.timing, { state: task.state })}</span>${badge(task.state)}<span class="row-arrow">↗</span></button>`;
}

/** Group saved outputs by dependency order while retaining every supporting artifact. */
export function selectGoalArtifacts(tasks) {
  const artifacts = tasks.filter((task) => typeof task.receipt?.artifact === "string" && task.receipt.artifact);
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const consumed = new Set();
  for (const artifact of artifacts) {
    const queue = [...(artifact.dependencies || [])];
    const seen = new Set([artifact.id]);
    while (queue.length) {
      const id = queue.pop();
      if (seen.has(id)) continue;
      seen.add(id);
      consumed.add(id);
      queue.push(...(byId.get(id)?.dependencies || []));
    }
  }
  return {
    primary: artifacts.filter((task) => !consumed.has(task.id)),
    supporting: artifacts.filter((task) => consumed.has(task.id)),
  };
}

/** Put saved goal outputs ahead of execution logs without claiming they satisfy the objective. */
function goalResults(goal, tasks, agents) {
  const { primary, supporting } = selectGoalArtifacts(tasks);
  if (!primary.length && !supporting.length) {
    return ["accepted", "failed", "cancelled", "blocked"].includes(goal.state)
      ? '<section class="goal-results"><h4>Results to review</h4><p>No saved file was produced for this goal. Task receipts remain available below.</p></section>'
      : "";
  }
  const result = (task) => `<article class="goal-result"><div><strong>${e(task.title)}</strong><span class="result-filename">${e(task.receipt.artifact)}</span><small>${e(agents.get(task.agent_id)?.name || "Assigned agent")}</small></div><div class="result-actions"><button class="button" data-inspect="${e(task.id)}">Open result</button><button class="text-button" data-download-artifact="${e(task.id)}">Download</button></div></article>`;
  return `<section class="goal-results" aria-label="Goal results"><h4>Results to review</h4><p>These are the editable source files behind your final deliverable. Review their contents against the goal; successful execution alone does not confirm the objective was met.</p>${primary.map(result).join("")}${supporting.length ? `<details class="supporting-results" data-goal-results="${e(goal.id)}"><summary>Supporting results (${supporting.length})</summary>${supporting.map(result).join("")}</details>` : ""}</section>`;
}

/** Make the requested final document prominent while retaining the working source text. */
function goalDelivery(goal, draft, generating = false) {
  const preferences = draft || goal.deliverable || { format: "pdf", style: "editorial" };
  const delivery = goal.delivery;
  const busy = generating || delivery?.state === "rendering";
  const options = (items, selected) => Object.entries(items).map(([key, label]) => `<option value="${key}" ${key === selected ? "selected" : ""}>${label}</option>`).join("");
  const status = delivery?.state === "ready"
    ? `<p class="delivery-ready">${e(delivery.name || "Your report is ready")}${Number.isFinite(delivery.bytes) ? ` <span>· ${Math.max(1, Math.round(delivery.bytes / 1024))} KB</span>` : ""}</p><button class="button primary" data-download-goal="${e(goal.id)}">Download ${e(delivery.format === "markdown" ? "Markdown" : (delivery.format || "file").toUpperCase())}</button>`
    : busy ? '<p class="delivery-status" role="status">Designing and preparing the final file…</p>'
      : delivery?.state === "failed" ? `<p class="delivery-error" role="alert">${e(delivery.error || "The final file could not be prepared. Try again.")}</p>`
        : `<p class="muted">${goal.state === "accepted" ? "Choose a format to turn the completed source into your final report." : "The final document will be prepared after the team completes its work."}</p>`;
  return `<section class="goal-delivery" aria-label="Final deliverable"><div class="delivery-heading"><div><span class="eyebrow">Final deliverable</span><h4>${e(DELIVERABLE_FORMATS[preferences.format] || "Report")}</h4></div>${status}</div><div class="delivery-options"><label class="field"><span>Desired deliverable</span><select aria-label="Desired deliverable" data-delivery-format="${e(goal.id)}" ${busy ? "disabled" : ""}>${options(DELIVERABLE_FORMATS, preferences.format)}</select></label><label class="field"><span>Visual style</span><select aria-label="Visual style" data-delivery-style="${e(goal.id)}" ${busy ? "disabled" : ""}>${options(DELIVERABLE_STYLES, preferences.style)}</select></label><label class="field" ${preferences.format === "markdown" ? "hidden" : ""}><span>Report structure</span><select aria-label="Report structure" data-delivery-layout="${e(goal.id)}" ${busy ? "disabled" : ""}>${options(DELIVERABLE_LAYOUTS, preferences.layout || "reference")}</select></label><button class="button" data-generate-deliverable="${e(goal.id)}" ${busy || goal.state !== "accepted" ? "disabled" : ""}>${busy ? "Preparing…" : delivery?.state === "ready" ? "Generate another format" : delivery?.state === "failed" ? "Retry delivery" : "Prepare deliverable"}</button></div></section>`;
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
    <div class="metric"><span>Finished tasks</span><strong>${m.verified}</strong><small>Execution checks passed</small></div>
  </div>`;
}

/**
 * Render selectable project summaries, or the first-project empty state.
 */
function projectCards(snapshot) {
  if (!snapshot.projects.length)
    return empty(
      "A place for your next project.",
      "Define a charter, assemble a team, and turn your goals into accountable work.",
    );
  return `<div class="project-grid">${snapshot.projects
    .map((project, index) => {
      const view = projectView(snapshot, project.id);
      const accepted = view.tasks.filter(
        (task) => task.state === "accepted",
      ).length;
      return `<button class="project-card" data-project="${e(project.id)}"><div class="card-top"><span class="project-mark tint-${index % 4}">${e(project.name.slice(0, 2).toUpperCase())}</span><span class="eyebrow">${project.mode === "existing" ? "Imported" : "Greenfield"}</span></div><h3>${e(project.name)}</h3><p>${e(project.charter.objective)}</p><div class="card-meta"><span>${icon("people")} ${project.team_size} agents</span><span>${project.active_executions} / ${project.max_concurrency} active</span></div><progress value="${accepted}" max="${Math.max(1, view.tasks.length)}" aria-label="${accepted} of ${view.tasks.length} tasks passed execution checks"></progress><div class="card-bottom"><span>${accepted} of ${view.tasks.length} tasks passed execution checks</span>${icon("arrow")}</div></button>`;
    })
    .join("")}</div>`;
}

/**
 * Render a project's goal/task rows with ownership, status, and cancellation controls.
 */
function goalsView(view, display = {}) {
  if (!view.goals.length)
    return `${empty("The team is ready for its first goal.", "Give this project a goal and assign each step to a team member.")}<div class="empty-action"><button class="button primary" data-action="new-goal">${icon("plus")} Add a goal</button></div>`;
  const agents = new Map(view.agents.map((agent) => [agent.id, agent]));
  const controls = view.goals.length > 1 ? '<div class="goal-list-controls"><button class="text-button" data-collapse-goals="all">Collapse all</button><button class="text-button" data-expand-goals="all">Expand all</button></div>' : "";
  return controls + view.goals
    .map((goal) => {
      const tasks = view.tasks.filter((task) => task.goal_id === goal.id);
      const collapsed = display.collapsedGoals?.has(goal.id) || false;
      const deleteBlocked = ["running", "cancelling"].includes(goal.state) || goal.delivery?.state === "rendering" || display.generatingGoals?.has(goal.id);
      const deleteControl = `<button class="text-button danger-text" data-delete-goal="${e(goal.id)}" ${deleteBlocked ? 'disabled title="Wait for active work and delivery to stop before deleting this goal"' : ""}>Delete goal</button>`;
      const quickDownload = collapsed && goal.delivery?.state === "ready" ? `<button class="button" data-download-goal="${e(goal.id)}">Download ${e(goal.delivery.format === "markdown" ? "Markdown" : (goal.delivery.format || "file").toUpperCase())}</button>` : "";
      return `<section class="panel goal-panel ${goal.state === "running" ? "is-running" : ""} ${collapsed ? "is-collapsed" : ""}" data-goal="${e(goal.id)}"><header class="panel-heading"><div class="goal-summary"><span class="eyebrow">Goal</span><h3>${e(goal.title)}</h3><small>${goal.accepted} of ${goal.total} tasks finished${goal.research_mode === "native_cli" ? " · Connected research tools" : ""}</small></div><div class="goal-heading-actions">${badge(goal.state, goal.state === "accepted" ? "Execution finished" : null)}${quickDownload}${deleteControl}<button class="button goal-collapse-toggle" data-toggle-goal="${e(goal.id)}" aria-expanded="${!collapsed}" aria-controls="goal-body-${e(goal.id)}">${collapsed ? "Expand" : "Collapse"}</button></div></header><div id="goal-body-${e(goal.id)}" data-goal-body="${e(goal.id)}" ${collapsed ? "hidden" : ""}>${renderGoalTiming(goal.timing, { state: goal.state })}${goalDelivery(goal, display.deliveryDrafts?.get(goal.id), display.generatingGoals?.has(goal.id))}${goalResults(goal, tasks, agents)}${liveActivity(tasks, agents)}<div class="task-list">${tasks.map((task) => taskRow(task, agents)).join("")}</div><footer class="goal-footer"><span>${goal.accepted} of ${goal.total} tasks passed execution checks</span>${["queued", "running", "cancelling"].includes(goal.state) ? `<button class="text-button danger-text" data-cancel="${e(goal.id)}" ${goal.state === "cancelling" ? "disabled" : ""}>${goal.state === "cancelling" ? "Cancelling…" : "Cancel remaining work"}</button>` : "<span>Source text and supporting results remain available above</span>"}</footer></div></section>`;
    })
    .join("");
}

/**
 * Render persistent agent roles, responsibilities, managers, and active capacity.
 */
function teamView(view) {
  const byId = new Map(view.agents.map((agent) => [agent.id, agent]));
  return `<div class="section-intro"><h2>Assigned team</h2><p>Roles stay with this project. Execution slots are allocated as work becomes ready.</p></div><section class="panel organization-panel"><div class="panel-heading"><h2>Team organization</h2><button class="text-button" data-action="edit-team">Edit organization</button></div>${renderOrganizationChart(view.agents, { interactive: true })}</section><div class="agent-grid">${view.agents.map((agent) => `<article class="panel agent-card"><div class="agent-top"><span class="avatar">${e(agent.name.slice(0, 2).toUpperCase())}</span><span class="status ${agent.state === "busy" ? "active" : "neutral"}"><span></span>${agent.state === "busy" ? "Working" : "Available"}</span></div><h3>${e(agent.name)}</h3><span class="role-label">${e(agent.role)}</span><p class="agent-instructions">${e(agent.instructions)}</p><div class="label">Responsibilities</div>${bulletList(agent.responsibilities)}<div class="capability-tags">${agent.capabilities.map((cap) => `<span>${e(ACTIONS[cap]?.short || cap)}</span>`).join("")}</div><footer><span>Reports to <strong>${e(byId.get(agent.reports_to)?.name || "Project owner")}</strong></span><span>${agent.active_executions} / ${agent.max_concurrency} active</span></footer></article>`).join("")}</div>`;
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

/** Describe available native research without treating configured tools as execution evidence. */
function researchCapability(project) {
  const research = project.research;
  if (!research) return "";
  const skills = Array.isArray(research.skills) ? research.skills.filter((skill) => typeof skill === "string") : [];
  return `<section class="panel research-capability"><div><h2>Research tools</h2><p>${research.available ? e(research.name || "Connected native research is available") : e(research.reason || "Native research is not connected")}</p>${research.available ? `<p class="muted">Agents can inspect the connected repositories through the native CLI. Completed tasks retain their recorded tool activity.</p>${skills.length ? `<div class="capability-tags">${skills.map((skill) => `<span>${e(skill)}</span>`).join("")}</div>` : ""}` : ""}</div></section>`;
}

/** Show only workspace actions registered by the backend's connected tool adapter. */
function workspaceCapabilities(project) {
  const capabilities = Array.isArray(project.workspace_capabilities)
    ? project.workspace_capabilities.filter((capability) => typeof capability === "string")
    : [];
  if (!capabilities.length) return "";
  return `<section class="panel research-capability"><div><h2>Connected workspace actions</h2><p>These actions are registered for this workspace. Assign each action to an agent with its matching permission.</p><div class="capability-tags">${capabilities.map((capability) => `<span>${e(ACTIONS[capability]?.label || capability)}</span>`).join("")}</div></div></section>`;
}

/**
 * Render the selected project charter, capacity summary, and goal overview.
 */
function overviewView(snapshot, view, display = {}) {
  const p = view.project;
  return `<div class="overview-grid"><section class="panel charter"><div class="panel-heading"><h2>Project charter</h2><span class="chip">${p.mode === "existing" ? "Imported" : "Greenfield"}</span></div><div class="charter-body">${p.has_source_bridge ? `<div class="notice"><strong>Connected source bridge</strong><p>Project sources are read through the configured bridge. Open Shared context to see its repositories.</p><small>The local output folder is separate from the connected source.</small></div>` : p.source_path ? `<div class="notice"><strong>Local source folder</strong><p>${e(p.source_path)}</p><small>Linked for reference. Tasks run in the isolated workspace.</small></div>` : ""}<span class="label">Objective</span><p class="objective">${e(p.charter.objective)}</p><div class="charter-columns"><div><span class="label">In scope</span>${bulletList(p.charter.scope)}</div><div><span class="label">Success criteria</span>${bulletList(p.charter.success_criteria)}</div></div>${p.charter.constraints.length ? `<span class="label">Constraints</span>${bulletList(p.charter.constraints)}` : ""}</div></section><section class="panel capacity-panel"><h2>Team & capacity</h2><p>Assigned agents and available execution slots are separate.</p><dl><div><dt>Assigned agents</dt><dd>${p.team_size}</dd></div><div><dt>Project concurrency</dt><dd>${p.max_concurrency}</dd></div><div><dt>Currently executing</dt><dd>${p.active_executions}</dd></div><div><dt>Workspace limit</dt><dd>${snapshot.capacity.execution_limit}</dd></div></dl><div class="small-note">Model tasks use each agent’s selected provider. Connect API access before starting model work.</div></section></div><section class="panel shared-context-panel"><div><h2>Shared context</h2><p>Save project notes, evidence, and decisions for the whole team. Saved context is included in new plans and agent tasks.</p></div><button class="button" data-action="shared-context">Open context</button></section>${researchCapability(p)}${workspaceCapabilities(p)}<section class="panel organization-panel overview-organization"><div class="panel-heading"><h2>Team organization</h2><button class="text-button" data-action="edit-team">Edit organization ${icon("arrow")}</button></div>${renderOrganizationChart(view.agents, { interactive: true })}</section><div class="section-heading"><h2>Project goals</h2><button class="text-button" data-action="tab" data-tab="work">View all ${icon("arrow")}</button></div>${goalsView(view, display)}`;
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
  {
    selectedProject = null,
    tab = "overview",
    workspaceTab = "projects",
    brandStyle = "arrow",
    collapsedGoals = new Set(),
    deliveryDrafts = new Map(),
    generatingGoals = new Set(),
  } = {},
) {
  const view = selectedProject && projectView(snapshot, selectedProject);
  const display = { collapsedGoals, deliveryDrafts, generatingGoals };
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
      )}</nav>${tab === "team" ? teamView(view) : tab === "work" ? goalsView(view, display) : tab === "activity" ? activityView(snapshot, view) : overviewView(snapshot, view, display)}`;
  return `<aside class="sidebar"><a class="brand" href="#" data-action="projects" data-brand aria-label="Orchestration home">${renderBrand(brandStyle)}</a><span class="nav-label">Workspace</span><nav><button class="nav-item ${!view && workspaceTab === "projects" ? "current" : ""}" data-action="projects">${icon("grid")} Projects <span>${snapshot.projects.length}</span></button><button class="nav-item ${!view && workspaceTab === "activity" ? "current" : ""}" data-action="activity">${icon("layers")} Activity</button><button class="nav-item" data-action="appearance">${icon("layers")} Appearance</button></nav><div class="nav-project-heading"><span class="nav-label">Your projects</span><button class="icon-button" data-action="new-project" aria-label="Create project">${icon("plus")}</button></div><nav class="project-nav">${snapshot.projects.map((project) => `<button class="nav-item ${view?.project.id === project.id ? "current" : ""}" data-project="${e(project.id)}"><span class="project-dot"></span><span class="truncate">${e(project.name)}</span></button>`).join("")}</nav><footer class="sidebar-footer"><span class="local-indicator"></span><div>${snapshot.features?.local_tools === false ? "Hosted workspace" : "Local workspace"}<small>Independent · experimental</small></div></footer></aside><div class="main-area"><header class="topbar"><span>Workspace <b>/</b> ${view ? "Project" : workspaceTab === "activity" ? "Activity" : "Overview"}</span><div class="topbar-actions">${snapshot.features?.local_tools === false ? "" : '<button class="button" data-action="import-projects">Import projects</button>'}<button class="button" data-action="providers">Model connections</button><span class="connection" id="connection-status" role="status">Connecting</span><button class="icon-button" data-action="refresh" aria-label="Refresh workspace">↻</button></div></header><main><div class="page-header"><div>${view ? `<button class="back-link" data-action="projects">← All projects</button>` : '<span class="eyebrow">A clearer way to coordinate</span>'}<h1>${e(heading)}</h1><p>${e(description)}</p></div><button class="button primary" data-action="${view ? "new-goal" : "new-project"}">${icon("plus")} ${view ? "New goal" : "New project"}</button></div><div id="workspace-error" class="notice danger hidden" role="alert"></div>${body}<footer class="page-footer"><span>Local operations · Results are backed by execution receipts</span><span id="last-updated"></span></footer></main></div>`;
}
