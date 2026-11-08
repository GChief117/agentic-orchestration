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

/** Validate a proposed reporting move, including all descendant-cycle constraints. */
export function canReparent(agents, key, parent) {
  if (!agents.some((agent) => agent.key === key)) return false;
  if (parent && !agents.some((agent) => agent.key === parent)) return false;
  return !organizationTree(
    agents.map((agent) =>
      agent.key === key ? { ...agent, reports_to: parent || "" } : agent,
    ),
  ).error;
}

/** Return a new roster with a valid reporting change; never mutate the caller's data. */
export function reparentAgent(agents, key, parent) {
  if (!canReparent(agents, key, parent))
    throw new Error(
      "An agent cannot report to itself or one of its descendants.",
    );
  return agents.map((agent) =>
    agent.key === key ? { ...agent, reports_to: parent || "" } : agent,
  );
}

/** Remove a draft agent and preserve its children by promoting them to its manager. */
export function removeAgent(agents, key) {
  if (agents.length <= 1)
    throw new Error("Keep at least one agent in the team.");
  const removed = agents.find((agent) => agent.key === key);
  if (!removed) throw new Error("Select an agent to remove.");
  return agents
    .filter((agent) => agent.key !== key)
    .map((agent) =>
      agent.reports_to === key
        ? { ...agent, reports_to: removed.reports_to || "" }
        : agent,
    );
}

/** Compute a centered, deterministic tree layout for SVG cards and connector paths. */
export function organizationLayout(agents) {
  const tree = organizationTree(agents);
  if (tree.error)
    return { ...tree, nodes: [], edges: [], width: 600, height: 350 };
  const cardWidth = 164,
    cardHeight = 104,
    gap = 28,
    level = 170,
    padding = 34;
  const owner = {
    key: "__owner__",
    name: "Project owner",
    role: "You",
    children: tree.roots,
  };
  const widths = new Map();
  /** Measure each subtree so its manager can be centered over all descendants. */
  function measure(node) {
    const children = node.children.map(measure);
    const width = Math.max(
      cardWidth,
      children.reduce((sum, value) => sum + value, 0) +
        Math.max(0, children.length - 1) * gap,
    );
    widths.set(node.key, width);
    return width;
  }
  const width = measure(owner) + padding * 2;
  const nodes = [],
    edges = [];
  /** Assign card coordinates and connector paths within the measured subtree. */
  function place(node, left, depth) {
    const x = left + widths.get(node.key) / 2 - cardWidth / 2,
      y = padding + depth * level;
    nodes.push({
      ...node,
      x,
      y,
      width: cardWidth,
      height: cardHeight,
      owner: node.key === "__owner__",
    });
    let childLeft = left;
    for (const child of node.children) {
      const childX = childLeft + widths.get(child.key) / 2;
      const fromX = x + cardWidth / 2,
        fromY = y + cardHeight,
        toY = padding + (depth + 1) * level;
      const middle = (fromY + toY) / 2;
      edges.push(`M ${fromX} ${fromY} V ${middle} H ${childX} V ${toY}`);
      place(child, childLeft, depth + 1);
      childLeft += widths.get(child.key) + gap;
    }
  }
  place(owner, padding, 0);
  return {
    nodes,
    edges,
    width,
    height: Math.max(...nodes.map((node) => node.y + cardHeight)) + padding,
    error: null,
  };
}

/** Render a responsive SVG chart; viewBox fitting keeps the owner and every branch aligned. */
export function renderOrganizationChart(
  agents,
  { interactive = false, selected = null, viewport = null } = {},
) {
  const layout = organizationLayout(agents);
  if (layout.error)
    return `<div class="notice danger" role="status">${e(layout.error)}</div>`;
  const box = viewport || {
    x: 0,
    y: 0,
    width: layout.width,
    height: layout.height,
  };
  const clip = (text, limit) =>
    text.length > limit ? text.slice(0, limit - 1) + "…" : text;
  return `<div class="organization-chart ${interactive ? "editable-chart" : ""}"><svg data-org-svg viewBox="${box.x} ${box.y} ${box.width} ${box.height}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Agent reporting organization"><g class="org-connectors">${layout.edges.map((path) => `<path d="${path}"/>`).join("")}</g>${layout.nodes.map((node) => `<g class="org-node ${node.owner ? "owner-node" : "org-card"} ${node.key === selected ? "selected" : ""}" data-org-node="${e(node.key)}" ${interactive ? `data-org-agent="${e(node.key)}" role="button" tabindex="0" aria-label="${node.owner ? "Select project owner" : `Edit ${e(node.name)}`}"` : ""} transform="translate(${node.x} ${node.y})"><title>${e(node.name)} · ${e(node.role)}</title><rect width="${node.width}" height="${node.height}" rx="12"/><circle cx="82" cy="27" r="15"/><text class="node-initial" x="82" y="31">${e(node.owner ? "You" : node.name.slice(0, 1).toUpperCase())}</text><text class="node-name" x="82" y="63">${e(clip(node.name, 20))}</text><text class="node-role" x="82" y="83">${e(clip(node.role, 26))}</text>${node.state === "busy" ? '<circle class="node-busy" cx="149" cy="15" r="4"/>' : ""}</g>`).join("")}<path class="org-drag-link" data-drag-link d=""/></svg></div>`;
}
