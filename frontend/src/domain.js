export const ACTIONS = {
  "files.write": { label: "Write a file", short: "File write" },
  "files.inspect": { label: "Inspect a file", short: "File check" },
  "terminal.python_version": {
    label: "Check Python runtime",
    short: "Runtime check",
  },
};

export const STATUSES = {
  accepted: { label: "Verified", tone: "success" },
  queued: { label: "Waiting", tone: "neutral" },
  running: { label: "Running", tone: "active" },
  cancelling: { label: "Cancelling", tone: "warning" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
  blocked: { label: "Blocked", tone: "warning" },
};

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

export function projectView(snapshot, id) {
  const project = snapshot.projects.find((item) => item.id === id);
  if (!project) return null;
  const agents = snapshot.agents.filter((item) => item.project_id === id);
  const goals = snapshot.goals.filter((item) => item.project_id === id);
  const tasks = snapshot.tasks.filter((item) => item.project_id === id);
  return { project, agents, goals, tasks };
}

export function metrics(snapshot) {
  return {
    projects: snapshot.projects.length,
    agents: snapshot.agents.length,
    active: snapshot.active_executions,
    capacity: snapshot.capacity.execution_limit,
    verified: snapshot.tasks.filter((task) => task.state === "accepted").length,
  };
}

function required(value, label) {
  const result = String(value ?? "").trim();
  if (!result) throw new Error(`${label} is required.`);
  return result;
}

function positive(value, label) {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < 1)
    throw new Error(`${label} must be a positive whole number.`);
  return result;
}

function lines(value) {
  return String(value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

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
  };
}

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
    if (draft.capability.startsWith("files.")) {
      parameters.path = required(draft.path, "File path");
      if (
        parameters.path.startsWith("/") ||
        parameters.path.split(/[\\/]/).includes("..")
      )
        throw new Error("Use a relative path inside the project workspace.");
    }
    if (draft.capability === "files.write")
      parameters.content = draft.content ?? "";
    if (draft.capability === "files.inspect" && draft.compare)
      parameters.expected = draft.expected ?? "";
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

export function starterProject() {
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

export function starterTasks(agents) {
  const owner = (capability) =>
    agents.find((agent) => agent.capabilities.includes(capability))?.key;
  if (!Object.keys(ACTIONS).every(owner))
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
