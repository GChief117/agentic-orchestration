/**
 * Human-readable labels for capabilities supported by the local backend.
 */
export const ACTIONS = {
  "bridge.setup.run": { label: "Run an approved workspace setup step", short: "Workspace setup" },
  "bridge.aws.login": { label: "Sign in to AWS on connected Mac", short: "AWS sign-in" },
  "bridge.aws.remote_login": { label: "Sign in to AWS using Terminal authorization", short: "AWS alternate sign-in" },
  "bridge.aws.status": { label: "Check saved AWS sign-in", short: "AWS status" },
  "bridge.postgres.prepare": { label: "Prepare local development databases", short: "Local databases" },
  "bridge.pgadmin.register": { label: "Add local PostgreSQL to pgAdmin", short: "pgAdmin connection" },
  "bridge.workspace.open": { label: "Show VS Code and pgAdmin on connected Mac", short: "Open workbench" },
  "bridge.notes.create": { label: "Create a Notes draft on connected Mac", short: "Connected Notes" },
  "bridge.demo.launch": { label: "Launch the connected workspace demo", short: "Connected demo" },
  "model.generate": { label: "Run model task", short: "Model task" },
  "files.write": { label: "Write a file", short: "File write" },
  "files.inspect": { label: "Inspect a file", short: "File check" },
  "terminal.python_version": {
    label: "Check Python runtime",
    short: "Runtime check",
  },
};

/** Supported final delivery formats; working task outputs remain editable text. */
export const DELIVERABLE_FORMATS = {
  pdf: "Designed PDF",
  html: "Web report (HTML)",
  markdown: "Editable Markdown",
};

/** Color and typography presets used by the document renderer. */
export const DELIVERABLE_STYLES = {
  editorial: "Warm editorial",
  ocean: "Ocean blue",
  neon: "Neon blue",
};

/** Presentation structure; evidence references stay available in either layout. */
export const DELIVERABLE_LAYOUTS = {
  visual_story: "Visual story with icons",
  reference: "Reference report",
};

/**
 * Display labels and fixed CSS tones for backend task and goal states.
 */
export const STATUSES = {
  accepted: { label: "Finished", tone: "success" },
  queued: { label: "Waiting", tone: "neutral" },
  running: { label: "Running", tone: "active" },
  cancelling: { label: "Cancelling", tone: "warning" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
  blocked: { label: "Blocked", tone: "warning" },
};

/**
 * Escape text before inserting it into generated HTML or attribute values.
 * @param {*} value Value to render as plain text.
 * @returns {string} HTML-escaped text; this is not an HTML sanitizer.
 */
export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );
}

/**
 * Select one project and its associated agents, goals, and tasks.
 * @param {object} snapshot Workspace state from the backend.
 * @param {string} id Project identity.
 * @returns {object|null} Project-scoped records, or null when absent.
 */
export function projectView(snapshot, id) {
  const project = snapshot.projects.find((item) => item.id === id);
  if (!project) return null;
  const agents = snapshot.agents.filter((item) => item.project_id === id);
  const goals = snapshot.goals.filter((item) => item.project_id === id);
  const tasks = snapshot.tasks.filter((item) => item.project_id === id);
  return { project, agents, goals, tasks };
}

/**
 * Derive workspace counters without treating assigned agents as running work.
 * @param {object} snapshot Backend state containing explicit execution counts.
 * @returns {object} Project, agent, active-execution, capacity, and verified totals.
 */
export function metrics(snapshot) {
  return {
    projects: snapshot.projects.length,
    agents: snapshot.agents.length,
    active: snapshot.active_executions,
    capacity: snapshot.capacity.execution_limit,
    verified: snapshot.tasks.filter((task) => task.state === "accepted").length,
  };
}

/**
 * Trim a required form value or throw an error naming the missing field.
 * @returns {string} Nonempty field text.
 */
function required(value, label) {
  const result = String(value ?? "").trim();
  if (!result) throw new Error(`${label} is required.`);
  return result;
}

/**
 * Parse a positive safe integer, rejecting fractional and empty form values.
 * @returns {number} Validated whole-number capacity.
 */
function positive(value, label) {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < 1)
    throw new Error(`${label} must be a positive whole number.`);
  return result;
}

/**
 * Split multiline form input into trimmed, nonempty list entries.
 * @returns {string[]} Values ready for JSON payloads.
 */
function lines(value) {
  return String(value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Validate and copy a shared model default without including unrelated agent fields. */
function teamModelPayload(value) {
  if (value == null) return value;
  const executionMode = value.execution_mode || "api";
  const executionSpeed = value.execution_speed || "standard";
  if (!["cli", "api"].includes(executionMode))
    throw new Error("Choose Local CLI or API for the team model.");
  if (!["openai", "claude", "gemini"].includes(value.provider))
    throw new Error("Choose a provider for the team model.");
  const model = required(value.model, "Team model");
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(model))
    throw new Error("Choose a valid team model.");
  if (!["standard", "fast", "ultrafast"].includes(executionSpeed))
    throw new Error("Choose a supported team execution speed.");
  if (executionMode === "api" && executionSpeed !== "standard")
    throw new Error("Accelerated team speeds require Local CLI execution.");
  return {
    execution_mode: executionMode,
    provider: value.provider,
    model,
    execution_speed: executionSpeed,
  };
}

/**
 * Build a project document from editable form values.
 * Validate required fields, roster keys, reporting cycles, and team capacity.
 * The backend still enforces all authoritative checks.
 * @param {object} draft Form values, including string-valued capacity fields.
 * @returns {object} Project creation payload; throws for invalid input.
 */
export function projectPayload(draft) {
  const agents = draft.agents.map((agent, index) => ({
    key: required(agent.key, `Agent ${index + 1} key`),
    name: required(agent.name, `Agent ${index + 1} name`),
    role: required(agent.role, `Agent ${index + 1} role`),
    instructions: required(
      agent.instructions,
      `Instructions for agent ${index + 1}`,
    ),
    responsibilities: lines(agent.responsibilities),
    capabilities: [...agent.capabilities],
    reports_to: agent.reports_to || null,
    provider: agent.provider || null,
    execution_mode: agent.execution_mode || "api",
    execution_speed: agent.execution_speed || "standard",
    model: agent.model || null,
    collaborators: [...(agent.collaborators || [])],
    max_concurrency: positive(
      agent.max_concurrency,
      `Concurrency for agent ${index + 1}`,
    ),
  }));
  if (!agents.length)
    throw new Error("Add at least one agent to this project.");
  if (new Set(agents.map((agent) => agent.key)).size !== agents.length)
    throw new Error("Every agent must have a unique key.");
  const byKey = new Map(agents.map((agent) => [agent.key, agent]));
  for (const agent of agents) {
    if (!agent.responsibilities.length || !agent.capabilities.length)
      throw new Error(
        `${agent.name} needs responsibilities and at least one capability.`,
      );
    const seen = new Set();
    let current = agent;
    while (current) {
      if (seen.has(current.key))
        throw new Error("The reporting hierarchy contains a cycle.");
      seen.add(current.key);
      if (current.reports_to && !byKey.has(current.reports_to))
        throw new Error(`${current.name} reports to an unknown agent.`);
      current = byKey.get(current.reports_to);
    }
  }
  const maxConcurrency = positive(draft.max_concurrency, "Project concurrency");
  if (
    maxConcurrency >
    agents.reduce((total, agent) => total + agent.max_concurrency, 0)
  )
    throw new Error(
      "Project concurrency exceeds the team's combined capacity.",
    );
  const scope = lines(draft.scope);
  const criteria = lines(draft.success_criteria);
  if (!scope.length || !criteria.length)
    throw new Error(
      "Include at least one scope item and one success criterion.",
    );
  return {
    name: required(draft.name, "Project name"),
    mode: "greenfield",
    charter: {
      objective: required(draft.objective, "Objective"),
      scope,
      constraints: lines(draft.constraints),
      success_criteria: criteria,
    },
    team_size: agents.length,
    max_concurrency: maxConcurrency,
    agents,
    ...(draft.team_model !== undefined
      ? { team_model: teamModelPayload(draft.team_model) }
      : {}),
  };
}

/**
 * Build an assigned task graph and validate its client-side constraints.
 * Check capabilities, relative paths, and dependency cycles without executing work.
 * @param {string} projectId Destination project.
 * @param {string} title Goal title.
 * @param {object[]} drafts Editable task rows.
 * @param {object[]} agents Roster whose keys and capabilities may be assigned.
 * @returns {object} Goal submission payload; throws for invalid input.
 */
export function goalPayload(projectId, title, drafts, agents) {
  if (!drafts.length) throw new Error("Add at least one task.");
  const team = new Map(agents.map((agent) => [agent.key, agent]));
  const tasks = drafts.map((draft) => {
    const agent = team.get(draft.agent);
    if (!agent || !agent.capabilities.includes(draft.capability))
      throw new Error(
        "Assign each task to an agent with the required capability.",
      );
    const parameters = {};
    if (
      draft.capability.startsWith("files.") ||
      draft.capability === "model.generate"
    ) {
      parameters.path = required(draft.path, "File path");
      if (
        parameters.path.startsWith("/") ||
        parameters.path.split(/[\\/]/).includes("..")
      )
        throw new Error("Use a relative path inside the project workspace.");
    }
    if (draft.capability === "model.generate") {
      if (!agent.provider || !agent.model)
        throw new Error("Choose a provider and model for this agent first.");
      parameters.prompt = required(draft.prompt, "Task prompt");
      if (draft.source_files?.length) {
        if (draft.source_files.length > 8)
          throw new Error("Choose at most eight source files per task.");
        parameters.source_files = [...draft.source_files];
      }
      const requestedTokens = String(draft.max_tokens ?? "").trim();
      if (agent.execution_mode !== "cli" || requestedTokens) {
        parameters.max_tokens = positive(
          requestedTokens || "2048",
          "Output token limit",
        );
        if (parameters.max_tokens > 8192)
          throw new Error("Use an output token limit of 8192 or less.");
      }
    }
    if (draft.capability === "files.write")
      parameters.content = draft.content ?? "";
    if (draft.capability === "files.inspect" && draft.compare)
      parameters.expected = draft.expected ?? "";
    if (draft.capability === "bridge.notes.create") {
      parameters.title = required(draft.note_title, "Note title");
      if (draft.content_task) {
        if (!draft.depends_on.includes(draft.content_task)) throw new Error("The note's content task must be one of its dependencies.");
        parameters.content_task = draft.content_task;
      }
      else parameters.content = required(draft.content, "Note content");
    }
    if (draft.capability === "bridge.demo.launch") {
      parameters.port = positive(draft.port || "8765", "Demo port");
      if (parameters.port < 1024 || parameters.port > 65535) throw new Error("Use a demo port from 1024 to 65535.");
    }
    if (draft.capability === "bridge.setup.run") {
      parameters.step = required(draft.setup_step, "Setup step");
    }
    return {
      id: draft.id,
      title: required(draft.title, "Task title"),
      agent: draft.agent,
      capability: draft.capability,
      parameters,
      depends_on: [...draft.depends_on],
    };
  });
  const byId = new Map(tasks.map((task) => [task.id, task]));
  if (byId.size !== tasks.length)
    throw new Error("Task identifiers must be unique.");
  const visiting = new Set(),
    complete = new Set();
  /**
   * Traverse task dependencies to detect unknown references and cycles.
   * Mutates only the local visiting/complete sets used by goalPayload.
   */
  function visit(id) {
    if (!byId.has(id))
      throw new Error("A dependency refers to a removed task.");
    if (visiting.has(id)) throw new Error("Task dependencies contain a cycle.");
    if (complete.has(id)) return;
    visiting.add(id);
    byId.get(id).depends_on.forEach(visit);
    visiting.delete(id);
    complete.add(id);
  }
  tasks.forEach((task) => visit(task.id));
  return { project_id: projectId, title: required(title, "Goal title"), tasks };
}

/**
 * Return an editable example charter and three-role team.
 * These values are form suggestions, not backend defaults or saved records.
 * @returns {object} A fresh form draft.
 */
export function starterProject() {
  /**
   * Build one example agent row with a single capability and optional manager.
   */
  const agent = (key, name, role, capability, instructions, parent) => ({
    key,
    name,
    role,
    capabilities: [capability],
    instructions,
    responsibilities: instructions,
    reports_to: parent,
    max_concurrency: "1",
  });
  return {
    name: "Release engineering",
    objective:
      "Prepare and verify release artifacts through an accountable team.",
    scope: "Artifact preparation\nIndependent verification\nRuntime inspection",
    constraints: "Keep artifacts inside the project workspace",
    success_criteria:
      "Artifacts pass their checks\nEach task has an owner and recorded evidence",
    max_concurrency: "2",
    agents: [
      agent(
        "coordinator",
        "Coordinator",
        "Coordination",
        "terminal.python_version",
        "Inspect prerequisites and coordinate the agreed scope.",
        "",
      ),
      agent(
        "builder",
        "Builder",
        "Implementation",
        "files.write",
        "Write artifacts according to the task specification.",
        "coordinator",
      ),
      agent(
        "reviewer",
        "Reviewer",
        "Verification",
        "files.inspect",
        "Verify artifacts against the expected contents.",
        "coordinator",
      ),
    ],
  };
}

/**
 * Create editable release tasks using capable agents from the current roster.
 * @param {object[]} agents Project team; every required action needs a match.
 * @returns {object[]} Fresh task drafts; throws when the example cannot be assigned.
 */
export function starterTasks(agents) {
  /**
   * Find the first roster key permitted to perform the example capability.
   */
  const owner = (capability) =>
    agents.find((agent) => agent.capabilities.includes(capability))?.key;
  if (!["files.write", "files.inspect", "terminal.python_version"].every(owner))
    throw new Error(
      "This example needs agents for file writing, file inspection, and runtime checks.",
    );
  return [
    {
      id: "task-1",
      title: "Check runtime",
      agent: owner("terminal.python_version"),
      capability: "terminal.python_version",
      depends_on: [],
    },
    {
      id: "task-2",
      title: "Prepare release checklist",
      agent: owner("files.write"),
      capability: "files.write",
      path: "release/checklist.txt",
      content: "Runtime checked. Artifact verified.\n",
      depends_on: [],
    },
    {
      id: "task-3",
      title: "Verify release checklist",
      agent: owner("files.inspect"),
      capability: "files.inspect",
      path: "release/checklist.txt",
      compare: true,
      expected: "Runtime checked. Artifact verified.\n",
      depends_on: ["task-1", "task-2"],
    },
  ];
}
