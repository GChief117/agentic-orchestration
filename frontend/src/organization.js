import { escapeHtml as e } from "./domain.js";

/** Build a tree from either editable role keys or persisted agent IDs. */
export function organizationTree(agents) {
  const nodes = new Map(
    agents.map((agent) => [agent.id || agent.key, { ...agent, children: [] }]),
  );
  const aliases = new Map(
    agents.flatMap((agent) => [
      [agent.id || agent.key, agent.id || agent.key],
      [agent.key, agent.id || agent.key],
    ]),
  );
  for (const agent of agents) {
    const visited = new Set();
    let current = agent;
    while (current?.reports_to) {
      const id = current.id || current.key;
      if (visited.has(id))
        return {
          roots: [],
          error:
            "Reporting relationships contain a cycle. Choose a different manager.",
        };
      visited.add(id);
      const parentId = aliases.get(current.reports_to);
      if (!parentId)
        return {
          roots: [],
          error: "An agent reports to someone outside this team.",
        };
      current = nodes.get(parentId);
    }
  }
  const roots = [];
  for (const node of nodes.values()) {
    const parent = node.reports_to && nodes.get(aliases.get(node.reports_to));
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return { roots, error: null };
}

/** Render the current organization as connected role cards without exposing internal keys. */
export function renderOrganizationChart(agents, { interactive = false } = {}) {
  const tree = organizationTree(agents);
  if (tree.error)
    return `<div class="notice danger" role="status">${e(tree.error)}</div>`;
  /** Render one role and its reporting descendants as semantic nested lists. */
  function branch(node) {
    const tag = interactive ? "button" : "div";
    const attrs = interactive
      ? ` type="button" data-org-agent="${e(node.key)}" aria-label="Edit ${e(node.name)}"`
      : "";
    return `<li><${tag}${attrs} class="org-card"><span class="org-avatar">${e(node.name.slice(0, 1).toUpperCase())}</span><strong>${e(node.name)}</strong><small>${e(node.role)}</small>${node.state === "busy" ? '<span class="org-working">Working</span>' : ""}</${tag}>${node.children.length ? `<ul>${node.children.map(branch).join("")}</ul>` : ""}</li>`;
  }
  return `<div class="organization-chart" aria-label="Agent reporting organization"><div class="org-owner"><span>You</span><strong>Project owner</strong></div>${tree.roots.length ? `<ul class="org-tree">${tree.roots.map(branch).join("")}</ul>` : '<p class="muted">Add a role to see the team organization.</p>'}</div>`;
}
