import { ACTIONS, escapeHtml as e } from "./domain.js";
import { ROLES, makeAgent, changeRole } from "./presets.js";
import {
  canReparent,
  connectAgents,
  collaborationPath,
  reparentAgent,
  removeAgent,
  organizationLayout,
  renderOrganizationChart,
} from "./organization.js";
import { input, value } from "./form-controls.js";

/** Infer an editable preset for a persisted role, keeping its instructions untouched. */
export function editableAgents(agents) {
  const byId = new Map(agents.map((agent) => [agent.id, agent.key]));
  return agents.map((agent) => ({
    ...agent,
    role_template:
      agent.role_template ||
      Object.entries(ROLES).find(
        ([, preset]) => preset.role.toLowerCase() === agent.role.toLowerCase(),
      )?.[0] ||
      "custom",
    responsibilities: Array.isArray(agent.responsibilities)
      ? agent.responsibilities.join("\n")
      : agent.responsibilities,
    capabilities: [...agent.capabilities],
    execution_mode: agent.execution_mode || "api",
    execution_speed: agent.execution_speed || "standard",
    collaborators: [...(agent.collaborators || [])],
    reports_to: byId.get(agent.reports_to) || agent.reports_to || "",
    max_concurrency: String(agent.max_concurrency),
  }));
}

/**
 * Mount a chart-first roster editor with a selected-node inspector and drag reparenting.
 * Changes stay in the draft until the owner explicitly saves the containing form.
 */
export function mountOrganizationEditor({
  root,
  agents: initial,
  maxConcurrency,
  onChange,
  lockedKeys = [],
  selectedKey = null,
  modelProviders = [],
}) {
  const document = root.ownerDocument;
  const localTools = modelProviders.some((provider) => provider.cli);
  let agents = editableAgents(initial),
    concurrency = String(maxConcurrency),
    selected =
      selectedKey &&
      (selectedKey === "__owner__" ||
        agents.some((agent) => agent.key === selectedKey))
        ? selectedKey
        : agents[0]?.key || "__owner__";
  let zoom = 1,
    center = null,
    gesture = null,
    ignoreClick = false,
    connectMode = false,
    connectionSource = null;
  let nextId =
    Math.max(
      0,
      ...agents.map(
        (agent) => Number(agent.key.match(/^agent-(\d+)$/)?.[1]) || 0,
      ),
    ) + 1;
  const locked = new Set(lockedKeys);
  agents = agents.map((agent) => {
    if (!localTools)
      return { ...agent, execution_mode: "api", execution_speed: "standard" };
    if (!agent.provider)
      return { ...agent, execution_mode: "cli", execution_speed: "standard" };
    if (agent.execution_mode === "cli" && !agent.model)
      return { ...agent, model: "default" };
    return agent;
  });
  agents.forEach((agent) => {
    const cli = modelProviders.find((item) => item.id === agent.provider)?.cli;
    if (
      agent.execution_mode !== "cli" ||
      Array.isArray(cli?.model_speed_modes?.[agent.model])
    ) normalizeSpeed(agent);
  });
  let appliedTeamModel = commonTeamModel();
  let teamModel = { ...(appliedTeamModel || suggestedTeamModel()) };

  /** Publish an immutable draft snapshot without committing it to the backend. */
  function publish() {
    onChange?.(snapshot());
  }
  /** Return current roster and concurrency settings for validation or saving. */
  function snapshot() {
    return {
      agents: agents.map((agent) => ({
        ...agent,
        capabilities: [...agent.capabilities],
        collaborators: [...(agent.collaborators || [])],
      })),
      max_concurrency: concurrency,
    };
  }
  /** Show actionable validation feedback beside the chart. */
  function error(message) {
    const box = root.querySelector("[data-chart-message]");
    if (box) box.textContent = message;
  }
  /** Compute the zoomed SVG viewBox while retaining the canvas center. */
  function viewport() {
    const layout = organizationLayout(agents);
    const position = center || { x: layout.width / 2, y: layout.height / 2 };
    return {
      x: position.x - layout.width / zoom / 2,
      y: position.y - layout.height / zoom / 2,
      width: layout.width / zoom,
      height: layout.height / zoom,
    };
  }
  /** Update only the canvas, preserving focus and unsaved typing in the inspector. */
  function draw() {
    root.querySelector("[data-chart-canvas]").innerHTML =
      renderOrganizationChart(agents, {
        interactive: true,
        selected,
        viewport: viewport(),
        connectMode,
        connectSource: connectionSource,
      });
    root.querySelector("[data-zoom-label]").textContent =
      `${Math.round(zoom * 100)}%`;
  }
  /** Return the currently selected editable role, or null for the project owner. */
  function current() {
    return agents.find((agent) => agent.key === selected);
  }
  /** Copy only model settings so shared choices cannot overwrite an agent's role. */
  function modelSettings(agent) {
    return {
      execution_mode: agent.execution_mode || (localTools ? "cli" : "api"),
      provider: agent.provider || "",
      model: agent.model || "",
      execution_speed: agent.execution_speed || "standard",
    };
  }
  /** Infer an existing common model without changing any per-agent settings. */
  function commonTeamModel() {
    if (!agents.length) return null;
    const first = modelSettings(agents[0]);
    if (!first.provider || !first.model) return null;
    return agents.every((agent) => {
      const settings = modelSettings(agent);
      return Object.keys(first).every((key) => first[key] === settings[key]);
    }) ? first : null;
  }
  /** Suggest a connected CLI first; the suggestion is not applied automatically. */
  function suggestedTeamModel() {
    for (const mode of localTools ? ["cli", "api"] : ["api"]) {
      const provider = modelProviders.find((item) => {
        const connection = mode === "cli" ? item.cli : item;
        return connection?.configured && (mode === "cli" || connection.models?.length);
      });
      if (provider) return {
        execution_mode: mode, provider: provider.id,
        model: mode === "cli" ? "default" : provider.models[0],
        execution_speed: "standard",
      };
    }
    return {
      execution_mode: localTools ? "cli" : "api", provider: "", model: "",
      execution_speed: "standard",
    };
  }
  /** Apply an explicit shared choice while preserving identities and organization. */
  function applyTeamModel() {
    if (!teamModel.provider || !teamModel.model)
      throw new Error("Choose a provider and model for the team first.");
    if (!speedModes(teamModel).includes(teamModel.execution_speed))
      throw new Error("Verify this model's speed availability before applying it to the team.");
    appliedTeamModel = modelSettings(teamModel);
    agents = agents.map((agent) => ({
      ...agent, ...appliedTeamModel,
      capabilities: [...new Set([...agent.capabilities, "model.generate"])],
    }));
    publish();
    render();
    error(`Applied the shared model to all ${agents.length} agents. Individual overrides remain available below.`);
  }
  /** Connect a pair reciprocally; collaboration never changes either manager. */
  function connectPair(first, second) {
    const source = agents.find((agent) => agent.key === first);
    const target = agents.find((agent) => agent.key === second);
    if (!source || !target || first === second) {
      render();
      error("Choose two different agent cards to connect.");
      return;
    }
    const exists = (source.collaborators || []).includes(second);
    if (!exists) {
      agents = connectAgents(agents, first, second);
      publish();
    }
    connectionSource = null;
    selected = second;
    render();
    error(
      exists
        ? `${source.name} and ${target.name} are already connected.`
        : `${source.name} now works with ${target.name}. Reporting lines are unchanged.`,
    );
  }
  /** Support click/tap and keyboard connection creation as an alternative to dragging. */
  function choosePeer(key) {
    if (!agents.some((agent) => agent.key === key)) {
      error(
        "Connect agent cards to each other. The project owner is not an agent.",
      );
      return;
    }
    connectMode = true;
    if (connectionSource) {
      if (connectionSource === key) {
        error("Choose a different agent to complete the connection.");
        return;
      }
      connectPair(connectionSource, key);
      return;
    }
    connectionSource = key;
    selected = key;
    render();
    error(`Choose another agent to connect with ${current().name}.`);
  }
  /** Prevent the browser's synthetic click from repeating a completed pointer action. */
  function suppressClick() {
    ignoreClick = true;
    document.defaultView.setTimeout(() => {
      ignoreClick = false;
    }, 0);
  }
  /** Return only speed modes advertised for the current CLI and selected model. */
  function speedModes(agent) {
    if (agent.execution_mode !== "cli") return ["standard"];
    const cli = modelProviders.find((item) => item.id === agent.provider)?.cli;
    const advertised = cli?.model_speed_modes?.[agent.model] || cli?.speed_modes;
    const supported = Array.isArray(advertised) ? advertised : [];
    return [...new Set(["standard", ...supported])].filter((mode) =>
      ["standard", "fast", "ultrafast"].includes(mode),
    );
  }
  /** Reset an unavailable speed after a provider, model, or execution change. */
  function normalizeSpeed(agent) {
    if (speedModes(agent).includes(agent.execution_speed)) return false;
    agent.execution_speed = "standard";
    return true;
  }
  /** Show supported CLI speed choices without inventing options for other providers. */
  function speedField(agent, scope = "agent") {
    const modes = speedModes(agent);
    const saved = agent.execution_speed;
    const unverified =
      ["fast", "ultrafast"].includes(saved) && !modes.includes(saved);
    if (unverified) modes.push(saved);
    if (modes.length < 2) return "";
    const labels = { standard: "Standard", fast: "Fast", ultrafast: "Ultrafast" };
    return `<label class="field"><span>Execution speed</span><select data-${scope}-speed aria-label="${scope === "team" ? "Team execution speed" : "Execution speed"}">${modes.map((mode) => `<option value="${mode}" ${saved === mode ? "selected" : ""}>${labels[mode]}${unverified && saved === mode ? " (saved; verify connection)" : ""}</option>`).join("")}</select><small>${unverified ? "The saved speed is retained until model availability can be verified." : "Requested service tier; actual response time depends on the provider."}</small></label>`;
  }
  /** Render provider-supplied model labels, grouping additional Antigravity families. */
  function modelOptions(models, details, selected) {
    const groups = new Map();
    for (const model of models) {
      const group = details?.[model]?.group || "";
      if (!groups.has(group)) groups.set(group, []);
      const label = details?.[model]?.label || (model === "default" ? "CLI default model" : model);
      groups.get(group).push(`<option value="${e(model)}" ${model === selected ? "selected" : ""}>${e(label)}</option>`);
    }
    return [...groups].map(([group, options]) => group
      ? `<optgroup label="${e(group)}">${options.join("")}</optgroup>`
      : options.join("")
    ).join("");
  }
  /** Explain the selected model using native metadata without inventing capability limits. */
  function modelDescription(connection, agent) {
    const detail = connection?.model_details?.[agent.model];
    const help = detail?.description || (connection?.configured
      ? agent.execution_mode === "cli"
        ? "Uses this CLI's saved sign-in and selected model."
        : "Models loaded from the provider's API."
      : "Connect this provider in Model connections, then reopen the team editor.");
    const resolved = detail?.resolved_model && detail.resolved_model !== agent.model
      ? `<small>Current model: ${e(detail.resolved_model)}</small>` : "";
    return `<small>${e(help)}</small>${resolved}`;
  }
  /** Reuse model controls for the whole team and individual agent overrides. */
  function modelControls(agent, scope = "agent") {
    const provider = modelProviders.find((item) => item.id === agent.provider);
    const mode = agent.execution_mode || "api";
    const connection = mode === "cli" ? provider?.cli : provider;
    const models = [
      ...new Set([
        ...(mode === "cli" ? ["default"] : []),
        ...(connection?.models || []),
        ...(agent.model ? [agent.model] : []),
      ]),
    ];
    return `${localTools ? `<label class="field"><span>${scope === "team" ? "Run the team with" : "Run this agent with"}</span><select data-${scope}-execution-mode aria-label="${scope === "team" ? "Team execution mode" : "Execution mode"}"><option value="cli" ${mode === "cli" ? "selected" : ""}>Local CLI · saved sign-in</option><option value="api" ${mode === "api" ? "selected" : ""}>API key</option></select></label>` : ""}<label class="field"><span>Model provider</span><select data-${scope}-provider aria-label="${scope === "team" ? "Team model provider" : "Model provider"}"><option value="">${scope === "team" ? "Choose a provider" : "No model assigned"}</option>${[
      ["openai", "OpenAI"],
      ["claude", "Claude"],
      ["gemini", "Gemini"],
    ]
      .map(
        ([key, name]) =>
          `<option value="${key}" ${agent.provider === key ? "selected" : ""}>${name}</option>`,
      )
      .join(
        "",
      )}</select></label>${agent.provider ? `<label class="field"><span>Model</span><select data-${scope}-model aria-label="${scope === "team" ? "Team model" : "Model"}"><option value="">Choose a model</option>${modelOptions(models, connection?.model_details, agent.model)}</select>${modelDescription(connection, agent)}</label>${speedField(agent, scope)}` : ""}`;
  }
  /** Show the shared model draft and whether the saved agent settings currently agree. */
  function teamModelSection() {
    const common = commonTeamModel();
    const status = common
      ? `All ${agents.length} agents currently share one model.`
      : "Mixed or unassigned models. Individual agent settings are preserved until you apply a team choice.";
    return `<section class="agent-editor" aria-label="Shared team model"><div class="form-section-heading"><div><h3>Team model</h3><p>Choose one model for the whole chart, then adjust any agent individually.</p></div><button class="button primary" type="button" data-chart-action="apply-team-model">Apply to entire team</button></div><div class="form-grid">${modelControls(teamModel, "team")}</div><p class="muted" data-team-model-status role="status">${status}</p></section>`;
  }
  /** Show each agent's provider/model assignment and reciprocal peer links. */
  function modelAndPeers(agent) {
    return `${modelControls(agent)}<fieldset class="collaborator-picker"><legend>Works with</legend>${agents
      .filter((peer) => peer.key !== agent.key)
      .map(
        (peer) =>
          `<label><input type="checkbox" data-collaborator value="${e(peer.key)}" ${(agent.collaborators || []).includes(peer.key) ? "checked" : ""}> ${e(peer.name)}</label>`,
      )
      .join(
        "",
      )}<p>Shares completed results. Uncheck to disconnect. Set task dependencies to control handoff order.</p></fieldset>`;
  }
  /** Render editable settings for the selected node, including an accessible reporting selector. */
  function inspector() {
    const agent = current();
    if (!agent)
      return '<div class="inspector-empty"><h3>Project owner</h3><p>Select an agent to edit its role and instructions, or add a role reporting directly to you.</p></div>';
    return `<div class="inspector-heading"><span class="eyebrow">Selected agent</span><h3>${e(agent.name)}</h3></div><label class="field"><span>Role</span><select data-agent-field="role_template" aria-label="Role">${Object.entries(
      ROLES,
    )
      .map(
        ([key, role]) =>
          `<option value="${key}" ${agent.role_template === key ? "selected" : ""}>${e(role.name)}</option>`,
      )
      .join(
        "",
      )}</select></label>${input("Display name", "name", agent.name)}${modelAndPeers(agent)}${input("Instructions", "instructions", agent.instructions, { textarea: true, rows: 5 })}<button type="button" class="text-button" data-chart-action="reset-instructions">Restore suggested instructions</button><label class="field"><span>Reports to</span><select data-agent-field="reports_to" aria-label="Reports to"><option value="">You · project owner</option>${agents
      .filter((other) => other.key !== agent.key)
      .map(
        (other) =>
          `<option value="${e(other.key)}" ${agent.reports_to === other.key ? "selected" : ""} ${canReparent(agents, agent.key, other.key) ? "" : "disabled"}>${e(other.name)}</option>`,
      )
      .join(
        "",
      )}</select></label>${input("Tasks this agent can run at once", "max_concurrency", agent.max_concurrency, { number: true })}<details class="agent-settings"><summary>More settings</summary>${input("Role description", "role", agent.role)}${input("Responsibilities", "responsibilities", agent.responsibilities, { textarea: true, rows: 3 })}<div class="capability-picker"><span class="label">Allowed actions</span>${Object.entries(
      ACTIONS,
    )
      .map(
        ([key, action]) =>
          `<label><input type="checkbox" data-agent-capability value="${key}" ${agent.capabilities.includes(key) ? "checked" : ""}> ${e(action.label)}</label>`,
      )
      .join(
        "",
      )}</div></details><button type="button" class="text-button danger-text" data-chart-action="remove" ${agents.length <= 1 || locked.has(agent.key) ? "disabled" : ""}>Remove this agent</button>${locked.has(agent.key) ? '<p class="muted">This agent has recorded work and must remain in the project history.</p>' : ""}`;
  }
  /** Rebuild chart controls and selected-node settings after a structural edit. */
  function render() {
    const capacity = agents.reduce(
      (sum, agent) => sum + (Number(agent.max_concurrency) || 1),
      0,
    );
    concurrency = String(
      Math.max(1, Math.min(Number(concurrency) || 1, capacity)),
    );
    root.innerHTML = `${teamModelSection()}<div class="organization-editor"><div class="chart-toolbar"><div><strong>${agents.length} assigned agents</strong><span>${connectMode ? "Choose two agents to connect · Escape to finish" : "Select a card to edit · drag the card to change its manager"}</span></div><div class="chart-add-controls"><select aria-label="New agent role" data-new-role>${Object.entries(
      ROLES,
    )
      .map(
        ([key, role]) =>
          `<option value="${key}" ${key === "builder" ? "selected" : ""}>${e(role.name)}</option>`,
      )
      .join(
        "",
      )}</select><button class="button primary" type="button" data-chart-action="add" title="Add a role reporting to the selected agent, or to you when Project owner is selected">+ Add role</button></div></div><div class="chart-editor-body"><section class="chart-workspace"><div class="chart-view-controls"><button type="button" class="button" data-chart-action="zoom-out" aria-label="Zoom out">−</button><span data-zoom-label></span><button type="button" class="button" data-chart-action="zoom-in" aria-label="Zoom in">+</button><button type="button" class="button" data-chart-action="fit">Fit team</button><button type="button" class="button" data-chart-action="connect-mode" aria-pressed="${connectMode}">${connectMode ? "Done connecting" : "Connect agents"}</button></div><div data-chart-canvas></div><p class="chart-link-legend">Drag a + handle to another agent to connect them. Solid: reports to · Dashed: works with</p><p class="chart-message" data-chart-message role="status"></p><div class="chart-capacity"><label class="field"><span>How much should run at once?</span><input type="number" min="1" step="1" aria-label="Project concurrency" data-project-concurrency value="${e(concurrency)}"><small>Enter a task limit or use the button below.</small></label><button type="button" class="button" data-chart-action="increase-tasks">+ Increase task limit</button><p class="muted">Team capacity: ${capacity} parallel tasks. Increasing above this adds slots to ${e((current() || agents[0]).name)}. Workspace limits still apply.</p></div></section><aside class="node-inspector" data-node-inspector>${inspector()}</aside></div></div>`;
    draw();
  }
  /** Add a role under the current selection and generate an unused internal key. */
  function add() {
    const role = root.querySelector("[data-new-role]").value;
    let key;
    do {
      key = `agent-${nextId++}`;
    } while (agents.some((agent) => agent.key === key));
    const agent = makeAgent(role, key, current()?.key || "");
    if (!localTools) agent.execution_mode = "api";
    if (appliedTeamModel) {
      Object.assign(agent, appliedTeamModel);
      agent.capabilities = [...new Set([...agent.capabilities, "model.generate"])];
    }
    const duplicateCount = agents.filter(
      (other) => other.role_template === role,
    ).length;
    if (duplicateCount) agent.name += ` ${duplicateCount + 1}`;
    agents.push(agent);
    selected = key;
    zoom = 1;
    center = null;
    publish();
    render();
  }
  /** Set the requested parallel task limit and explicitly allocate any extra slots. */
  function setConcurrency(count) {
    if (!Number.isSafeInteger(count) || count < 1)
      throw new Error("Task limit must be a positive whole number.");
    const capacity = agents.reduce(
      (sum, agent) => sum + Number(agent.max_concurrency),
      0,
    );
    if (count > capacity) {
      const agent = current() || agents[0];
      agent.max_concurrency = String(
        Number(agent.max_concurrency) + count - capacity,
      );
    }
    concurrency = String(count);
    publish();
    render();
  }
  /** Dispatch toolbar/node actions; keep draft deletion separate from persistence. */
  function click(event) {
    if (ignoreClick) {
      ignoreClick = false;
      return;
    }
    const node = event.target.closest("[data-org-agent]");
    if (node) {
      if (connectMode || event.target.closest("[data-connect-handle]")) {
        choosePeer(node.dataset.orgAgent);
        return;
      }
      selected = node.dataset.orgAgent;
      render();
      return;
    }
    const action = event.target.closest("[data-chart-action]")?.dataset
      .chartAction;
    try {
      if (action === "apply-team-model") applyTeamModel();
      else if (action === "connect-mode") {
        connectMode = !connectMode;
        connectionSource = null;
        render();
      } else if (action === "increase-tasks")
        setConcurrency(Number(concurrency) + 1);
      else if (action === "add") add();
      else if (action === "remove") {
        if (locked.has(selected))
          throw new Error(
            "This agent has recorded tasks and cannot be removed.",
          );
        agents = removeAgent(agents, selected);
        connectionSource = null;
        selected = agents[0].key;
        zoom = 1;
        center = null;
        publish();
        render();
      } else if (action === "fit") {
        zoom = 1;
        center = null;
        draw();
      } else if (action === "zoom-in" || action === "zoom-out") {
        zoom = Math.max(
          0.4,
          Math.min(3, zoom * (action === "zoom-in" ? 1.25 : 0.8)),
        );
        draw();
      } else if (action === "reset-instructions" && current()) {
        const agent = current();
        agent.instructions = ROLES[agent.role_template].instructions;
        agent.responsibilities = ROLES[agent.role_template].responsibilities;
        publish();
        render();
      }
    } catch (issue) {
      error(issue.message);
    }
  }
  /** Apply text edits without replacing the focused inspector fields. */
  function inputChange(event) {
    const field = event.target.dataset.field;
    if (!field || !current() || field === "max_concurrency") return;
    current()[field] = event.target.value;
    publish();
    if (field === "name" || field === "role") draw();
  }
  /** Validate discrete role, reporting, permission, and capacity changes. */
  function change(event) {
    try {
      if (event.target.matches("[data-project-concurrency]")) {
        setConcurrency(Number(event.target.value));
        return;
      }
      if (event.target.matches("[data-team-execution-mode]")) {
        teamModel.execution_mode = event.target.value;
        teamModel.model = teamModel.provider && teamModel.execution_mode === "cli" ? "default" : "";
        normalizeSpeed(teamModel);
        render();
        return;
      }
      if (event.target.matches("[data-team-provider]")) {
        teamModel.provider = event.target.value;
        teamModel.model = teamModel.provider && teamModel.execution_mode === "cli" ? "default" : "";
        normalizeSpeed(teamModel);
        render();
        return;
      }
      if (event.target.matches("[data-team-model]")) {
        teamModel.model = event.target.value;
        normalizeSpeed(teamModel);
        render();
        return;
      }
      if (event.target.matches("[data-team-speed]")) {
        teamModel.execution_speed = event.target.value;
        normalizeSpeed(teamModel);
        render();
        return;
      }
      const agent = current();
      if (!agent) return;
      if (event.target.matches("[data-agent-execution-mode]")) {
        agent.execution_mode = event.target.value;
        agent.model =
          agent.provider && agent.execution_mode === "cli" ? "default" : "";
        const resetSpeed = normalizeSpeed(agent);
        publish();
        render();
        if (resetSpeed)
          error("Execution speed reset to Standard for this execution method.");
        return;
      }
      if (event.target.matches("[data-agent-provider]")) {
        agent.provider = event.target.value;
        agent.model =
          agent.provider && agent.execution_mode === "cli" ? "default" : "";
        agent.capabilities = agent.capabilities.filter(
          (cap) => cap !== "model.generate",
        );
        if (agent.provider) agent.capabilities.unshift("model.generate");
        const resetSpeed = normalizeSpeed(agent);
        publish();
        render();
        if (resetSpeed)
          error("Execution speed reset to Standard for this provider.");
        return;
      }
      if (event.target.matches("[data-agent-model]")) {
        agent.model = event.target.value;
        const resetSpeed = normalizeSpeed(agent);
        publish();
        render();
        if (resetSpeed)
          error("Execution speed reset to Standard for this model.");
        return;
      }
      if (event.target.matches("[data-agent-speed]")) {
        const speed = event.target.value;
        if (speedModes(agent).includes(speed) || speed === agent.execution_speed)
          agent.execution_speed = speed;
        publish();
        render();
        return;
      }
      if (event.target.matches("[data-collaborator]")) {
        const peer = agents.find((item) => item.key === event.target.value);
        agents = connectAgents(
          agents,
          agent.key,
          peer.key,
          event.target.checked,
        );
        publish();
        draw();
        return;
      }
      const field = event.target.dataset.agentField;
      if (field === "role_template") {
        agents = agents.map((item) =>
          item.key === selected ? changeRole(item, event.target.value) : item,
        );
        publish();
        render();
      } else if (field === "reports_to") {
        agents = reparentAgent(agents, selected, event.target.value);
        zoom = 1;
        center = null;
        publish();
        render();
      } else if (event.target.matches("[data-agent-capability]")) {
        agent.capabilities = [
          ...root.querySelectorAll("[data-agent-capability]:checked"),
        ].map((element) => element.value);
        publish();
      } else if (event.target.dataset.field === "max_concurrency") {
        const count = Number(event.target.value);
        if (!Number.isSafeInteger(count) || count < 1)
          throw new Error("Concurrency must be a positive whole number.");
        agent.max_concurrency = String(count);
        publish();
        render();
      }
    } catch (issue) {
      error(issue.message);
    }
  }
  /** Map pointer coordinates into the current SVG viewBox for reliable pan/drag. */
  function point(event, svg) {
    if (svg.createSVGPoint && svg.getScreenCTM?.()) {
      const p = svg.createSVGPoint();
      p.x = event.clientX;
      p.y = event.clientY;
      return p.matrixTransform(svg.getScreenCTM().inverse());
    }
    const rect = svg.getBoundingClientRect();
    const box = viewport();
    return {
      x: box.x + ((event.clientX - rect.left) * box.width) / (rect.width || 1),
      y: box.y + ((event.clientY - rect.top) * box.height) / (rect.height || 1),
    };
  }
  /** Begin node reparenting or background panning; clicks remain selection actions. */
  function pointerDown(event) {
    const svg = event.target.closest("[data-org-svg]");
    if (!svg || (event.button ?? 0) !== 0) return;
    if (gesture && gesture.pointerId !== event.pointerId) return;
    const node = event.target.closest("[data-org-node]")?.dataset.orgNode;
    gesture = {
      svg,
      pointerId: event.pointerId,
      kind:
        connectMode || event.target.closest("[data-connect-handle]")
          ? "connect"
          : "move",
      node: node === "__owner__" ? null : node,
      pressedNode: node,
      startX: event.clientX,
      startY: event.clientY,
      last: point(event, svg),
      moved: false,
      candidate: null,
      sourceElement: node ? event.target.closest("[data-org-node]") : null,
      sourcePosition: node
        ? organizationLayout(agents).nodes.find((item) => item.key === node)
        : null,
    };
  }
  /** Move the actual card with the pointer and preview valid manager targets.
   * Background drags pan the viewBox without changing reporting relationships. */
  function pointerMove(event) {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (
      Math.hypot(
        event.clientX - gesture.startX,
        event.clientY - gesture.startY,
      ) < 6 &&
      !gesture.moved
    )
      return;
    if (!gesture.moved) {
      // Capture only real drags. Early capture retargets card clicks to the SVG.
      gesture.svg.setPointerCapture?.(event.pointerId);
    }
    gesture.moved = true;
    event.preventDefault();
    if (gesture.kind === "connect") {
      if (!gesture.node) return;
      const source = gesture.sourcePosition;
      const cursor = point(event, gesture.svg);
      const hit = document
        .elementFromPoint?.(event.clientX, event.clientY)
        ?.closest("[data-org-node]");
      const candidate = hit?.dataset.orgNode;
      gesture.candidate =
        candidate !== gesture.node &&
        agents.some((agent) => agent.key === candidate)
          ? candidate
          : null;
      root
        .querySelectorAll(".peer-target")
        .forEach((element) => element.classList.remove("peer-target"));
      gesture.sourceElement?.classList.add("connection-source");
      gesture.svg.classList.add("is-connecting");
      if (gesture.candidate) hit.classList.add("peer-target");
      const target =
        gesture.candidate &&
        organizationLayout(agents).nodes.find(
          (node) => node.key === gesture.candidate,
        );
      root
        .querySelector("[data-peer-preview]")
        ?.setAttribute(
          "d",
          target
            ? collaborationPath(source, target)
            : `M ${source.x + source.width} ${source.y + source.height / 2} L ${cursor.x} ${cursor.y}`,
        );
      error(
        gesture.candidate
          ? "Release to connect these agents as collaborators."
          : "Drag onto another agent. This will not change its manager.",
      );
      return;
    }
    if (!gesture.node) {
      const next = point(event, gesture.svg);
      const box = viewport();
      center = {
        x: box.x + box.width / 2 - (next.x - gesture.last.x),
        y: box.y + box.height / 2 - (next.y - gesture.last.y),
      };
      const updated = viewport();
      gesture.svg.setAttribute(
        "viewBox",
        `${updated.x} ${updated.y} ${updated.width} ${updated.height}`,
      );
      return;
    }
    const source = gesture.sourcePosition;
    const cursor = point(event, gesture.svg);
    if (source && gesture.sourceElement) {
      if (!gesture.sourceElement.classList.contains("dragging")) {
        selected = gesture.node;
        root
          .querySelectorAll(".org-node.selected")
          .forEach((element) => element.classList.remove("selected"));
        gesture.sourceElement.classList.add("selected", "dragging");
        gesture.sourceElement.querySelector("title")?.remove();
        gesture.svg.classList.add("is-dragging");
        const placeholder = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "rect",
        );
        for (const [name, value] of Object.entries({
          x: source.x,
          y: source.y,
          width: source.width,
          height: source.height,
          rx: 12,
          class: "org-drag-origin",
        }))
          placeholder.setAttribute(name, String(value));
        gesture.svg.append(placeholder);
        gesture.svg.append(gesture.sourceElement);
        root.querySelector("[data-node-inspector]").innerHTML = inspector();
      }
      gesture.position = {
        x: source.x + cursor.x - gesture.last.x,
        y: source.y + cursor.y - gesture.last.y,
      };
      gesture.sourceElement.setAttribute(
        "transform",
        `translate(${gesture.position.x} ${gesture.position.y})`,
      );
    }
    const hit = document
      .elementFromPoint?.(event.clientX, event.clientY)
      ?.closest("[data-org-node]");
    const candidate = hit?.dataset.orgNode;
    gesture.candidate =
      candidate &&
      canReparent(
        agents,
        gesture.node,
        candidate === "__owner__" ? "" : candidate,
      )
        ? candidate
        : null;
    root
      .querySelectorAll(".drop-target")
      .forEach((element) => element.classList.remove("drop-target"));
    if (gesture.candidate) hit.classList.add("drop-target");
    const preview = root.querySelector("[data-drag-link]");
    const manager =
      gesture.candidate &&
      organizationLayout(agents).nodes.find(
        (node) => node.key === gesture.candidate,
      );
    if (manager && gesture.position && source) {
      const x = manager.x + manager.width / 2,
        y = manager.y + manager.height;
      const childX = gesture.position.x + source.width / 2,
        childY = gesture.position.y;
      const middle = (y + childY) / 2;
      preview?.setAttribute(
        "d",
        `M ${x} ${y} C ${x} ${middle} ${childX} ${middle} ${childX} ${childY}`,
      );
    } else preview?.setAttribute("d", "");
    error(
      gesture.candidate
        ? "Release to change this agent’s manager."
        : "Drop on a manager card. Descendants cannot become managers.",
    );
  }
  /** Commit only valid drops; invalid gestures leave the original hierarchy intact. */
  function pointerUp(event) {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    root.querySelector("[data-drag-link]")?.setAttribute("d", "");
    const ended = gesture;
    if (ended.kind === "connect") {
      gesture = null;
      suppressClick();
      if (!ended.moved) {
        choosePeer(ended.pressedNode);
        return;
      }
      const target = document
        .elementFromPoint?.(event.clientX, event.clientY)
        ?.closest("[data-org-node]")?.dataset.orgNode;
      if (
        ended.node &&
        target &&
        target !== ended.node &&
        agents.some((agent) => agent.key === target)
      )
        connectPair(ended.node, target);
      else {
        render();
        error("No connection added. Drop onto a different agent card.");
      }
      return;
    }
    if (ended.node && ended.moved && event) {
      const target = document
        .elementFromPoint?.(event.clientX, event.clientY)
        ?.closest("[data-org-node]")?.dataset.orgNode;
      ended.candidate =
        target &&
        canReparent(agents, ended.node, target === "__owner__" ? "" : target)
          ? target
          : null;
    }
    gesture = null;
    if (!ended.moved && ended.pressedNode) {
      // Use the pressed card even if a browser retargets pointerup to the canvas.
      selected = ended.pressedNode;
      render();
      return;
    }
    ignoreClick = ended.moved;
    if (ended.moved)
      document.defaultView.setTimeout(() => {
        ignoreClick = false;
      }, 0);
    if (ended.moved && ended.node) {
      if (ended.candidate) {
        agents = reparentAgent(
          agents,
          ended.node,
          ended.candidate === "__owner__" ? "" : ended.candidate,
        );
        selected = ended.node;
        center = null;
        zoom = 1;
        publish();
      }
      render();
      if (!ended.candidate)
        error(
          "No reporting change. Drop the card on its new manager to move it.",
        );
    }
  }

  /** Abort a cancelled gesture without changing any reporting relationship. */
  function pointerCancel(event) {
    if (gesture && event && gesture.pointerId !== event.pointerId) return;
    const movedCard = gesture?.moved && gesture.node;
    const connecting = gesture?.kind === "connect";
    gesture = null;
    ignoreClick = false;
    if (movedCard || connecting) render();
    else {
      root.querySelector("[data-drag-link]")?.setAttribute("d", "");
      root
        .querySelectorAll(".drop-target")
        .forEach((element) => element.classList.remove("drop-target"));
    }
  }
  /** Keep browser-native image/text dragging from stealing chart pointer gestures. */
  function nativeDrag(event) {
    if (event.target.closest("[data-org-node]")) event.preventDefault();
  }
  /** Provide keyboard node selection; the inspector offers keyboard reparenting. */
  function keydown(event) {
    if (
      event.key === "Escape" &&
      (connectMode || gesture?.kind === "connect")
    ) {
      event.preventDefault();
      event.stopPropagation();
      gesture = null;
      connectMode = false;
      connectionSource = null;
      render();
      return;
    }
    const node = event.target.closest("[data-org-agent]");
    if (node && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (connectMode || event.target.closest("[data-connect-handle]"))
        choosePeer(node.dataset.orgAgent);
      else {
        selected = node.dataset.orgAgent;
        render();
      }
      [...root.querySelectorAll("[data-org-agent]")]
        .find((element) => element.dataset.orgAgent === selected)
        ?.focus?.();
    }
  }
  root.addEventListener("click", click);
  root.addEventListener("input", inputChange);
  root.addEventListener("change", change);
  root.addEventListener("pointerdown", pointerDown);
  root.addEventListener("dragstart", nativeDrag);
  root.addEventListener("pointermove", pointerMove);
  root.addEventListener("pointerup", pointerUp);
  root.addEventListener("pointercancel", pointerCancel);
  root.addEventListener("keydown", keydown);
  render();
  return {
    snapshot,
    /** Detach interaction listeners when the containing editor is closed or rebuilt. */
    destroy() {
      root.removeEventListener("click", click);
      root.removeEventListener("input", inputChange);
      root.removeEventListener("change", change);
      root.removeEventListener("pointerdown", pointerDown);
      root.removeEventListener("dragstart", nativeDrag);
      root.removeEventListener("pointermove", pointerMove);
      root.removeEventListener("pointerup", pointerUp);
      root.removeEventListener("pointercancel", pointerCancel);
      root.removeEventListener("keydown", keydown);
    },
  };
}
