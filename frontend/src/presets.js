/** Editable role suggestions, not model-provider or runtime configuration. */
export const ROLES = {
  coordinator: {
    name: "Coordinator",
    role: "Coordination",
    instructions:
      "Keep the project aligned with its objective. Break work into clear tasks, assign ownership, and check that results meet the success criteria.",
    responsibilities:
      "Plan the work\nCoordinate handoffs\nCheck readiness and progress",
    capabilities: ["terminal.python_version", "files.inspect"],
  },
  builder: {
    name: "Builder",
    role: "Implementation",
    instructions:
      "Produce the requested deliverables within the project scope. Follow the assigned task, preserve existing work, and return a clear result for review.",
    responsibilities:
      "Create deliverables\nImplement assigned changes\nExplain what was produced",
    capabilities: ["files.write", "terminal.python_version"],
  },
  reviewer: {
    name: "Reviewer",
    role: "Verification",
    instructions:
      "Review deliverables against the project's success criteria. Check the evidence independently and report any mismatch or unresolved issue clearly.",
    responsibilities: "Verify results\nCheck evidence\nReport issues",
    capabilities: ["files.inspect"],
  },
  researcher: {
    name: "Researcher",
    role: "Research",
    instructions:
      "Organize the available evidence around the project question. Separate observations from assumptions and write useful research notes for the team.",
    responsibilities:
      "Review available evidence\nOrganize research notes\nIdentify unanswered questions",
    capabilities: ["files.inspect", "files.write"],
  },
  writer: {
    name: "Writer",
    role: "Writing",
    instructions:
      "Turn the team's findings into clear, accurate documents. Match the requested format, explain conclusions, and keep source material traceable.",
    responsibilities: "Draft documents\nOrganize findings\nImprove clarity",
    capabilities: ["files.write", "files.inspect"],
  },
  analyst: {
    name: "Analyst",
    role: "Analysis",
    instructions:
      "Examine available outputs and identify patterns, gaps, and tradeoffs. State assumptions and explain the evidence behind each conclusion.",
    responsibilities:
      "Analyze outputs\nCompare alternatives\nExplain tradeoffs",
    capabilities: ["files.inspect", "terminal.python_version"],
  },
  custom: {
    name: "Specialist",
    role: "Custom role",
    instructions:
      "Complete assigned work within the project scope and return an evidence-backed result. Ask for clarification when the task is ambiguous.",
    responsibilities: "Complete assigned work\nReport results",
    capabilities: ["files.inspect"],
  },
};

/** Project categories provide editable answers and suggested team shapes. */
export const PROJECT_PRESETS = {
  software: {
    name: "Build software",
    description: "Create, test, and review a deliverable.",
    projectName: "Software project",
    objective: "Build and verify a working software deliverable.",
    scope: "Implementation\nTesting\nDocumentation",
    success_criteria:
      "The core workflow works\nResults pass their checks\nThe deliverable is documented",
    roles: ["coordinator", "builder", "reviewer"],
  },
  research: {
    name: "Research & report",
    description: "Investigate a question and explain the findings.",
    projectName: "Research project",
    objective: "Investigate a question and produce an evidence-backed report.",
    scope: "Research notes\nAnalysis\nFinal report",
    success_criteria:
      "Findings are traceable to evidence\nAssumptions are identified\nThe report answers the project question",
    roles: ["coordinator", "researcher", "writer", "reviewer"],
  },
  operations: {
    name: "Organize a workflow",
    description: "Coordinate a repeatable process.",
    projectName: "Operations project",
    objective: "Organize and verify a repeatable workflow.",
    scope: "Process definition\nTask coordination\nResult verification",
    success_criteria:
      "Steps and owners are clear\nOutputs are verified\nExceptions are recorded",
    roles: ["coordinator", "analyst", "reviewer"],
  },
  custom: {
    name: "Something else",
    description: "Start with your own goal and team.",
    projectName: "",
    objective: "",
    scope: "Planning\nDeliverable creation\nReview",
    success_criteria:
      "Deliverables match the project goal\nResults are reviewed",
    roles: ["coordinator", "builder", "reviewer"],
  },
};

/** Create one suggested role with a stable internal key hidden from the editor. */
export function makeAgent(roleKey, key, parent = "") {
  const preset = ROLES[roleKey] || ROLES.custom;
  return {
    ...preset,
    capabilities: [...preset.capabilities],
    key,
    role_template: roleKey,
    reports_to: parent,
    max_concurrency: "1",
    provider: "",
    model: "",
    collaborators: [],
  };
}

/** Apply a selected role, retaining instruction/name edits made by the user. */
export function changeRole(agent, roleKey) {
  const previous = ROLES[agent.role_template] || ROLES.custom;
  const next = makeAgent(roleKey, agent.key, agent.reports_to);
  const suffix = agent.name.startsWith(previous.name)
    ? agent.name.slice(previous.name.length)
    : null;
  const suggestedName = suffix === "" || /^ \d+$/.test(suffix ?? "");
  return {
    ...next,
    name: suggestedName ? next.name + suffix : agent.name,
    instructions:
      agent.instructions === previous.instructions
        ? next.instructions
        : agent.instructions,
    responsibilities:
      agent.responsibilities === previous.responsibilities
        ? next.responsibilities
        : agent.responsibilities,
    max_concurrency: agent.max_concurrency,
    provider: agent.provider,
    model: agent.model,
    collaborators: [...(agent.collaborators || [])],
    capabilities: agent.provider
      ? [...new Set([...next.capabilities, "model.generate"])]
      : next.capabilities,
  };
}

/** Build a fresh guided draft with editable defaults and no required technical IDs. */
export function guidedDraft(presetKey = "custom") {
  const preset = PROJECT_PRESETS[presetKey];
  const agents = preset.roles.map((role, index) =>
    makeAgent(role, `agent-${index + 1}`, index ? "agent-1" : ""),
  );
  return {
    name: preset.projectName,
    objective: preset.objective,
    scope: preset.scope,
    constraints: "Keep work inside the project workspace",
    success_criteria: preset.success_criteria,
    max_concurrency: String(Math.min(2, agents.length)),
    agents,
    preset: presetKey,
  };
}

/** Suggest a short editable name when the user describes a project without naming it. */
export function suggestedName(objective) {
  return (
    objective
      .trim()
      .split(/\s+/)
      .slice(0, 7)
      .join(" ")
      .replace(/[.!?]+$/, "")
      .slice(0, 120) || "New project"
  );
}
