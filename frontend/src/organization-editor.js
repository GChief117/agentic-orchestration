import { ACTIONS, escapeHtml as e } from "./domain.js";
import { ROLES, makeAgent, changeRole } from "./presets.js";
import {
  canReparent,
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
}) {
  const document = root.ownerDocument;
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
    ignoreClick = false;
  let nextId =
    Math.max(
      0,
      ...agents.map(
        (agent) => Number(agent.key.match(/^agent-(\d+)$/)?.[1]) || 0,
      ),
    ) + 1;
  const locked = new Set(lockedKeys);

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
      });
    root.querySelector("[data-zoom-label]").textContent =
      `${Math.round(zoom * 100)}%`;
  }
  /** Return the currently selected editable role, or null for the project owner. */
  function current() {
    return agents.find((agent) => agent.key === selected);
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
      )}</select></label>${input("Display name", "name", agent.name)}${input("Instructions", "instructions", agent.instructions, { textarea: true, rows: 5 })}<button type="button" class="text-button" data-chart-action="reset-instructions">Restore suggested instructions</button><label class="field"><span>Reports to</span><select data-agent-field="reports_to" aria-label="Reports to"><option value="">You · project owner</option>${agents
      .filter((other) => other.key !== agent.key)
      .map(
        (other) =>
          `<option value="${e(other.key)}" ${agent.reports_to === other.key ? "selected" : ""} ${canReparent(agents, agent.key, other.key) ? "" : "disabled"}>${e(other.name)}</option>`,
      )
      .join(
        "",
      )}</select></label><details class="agent-settings"><summary>More settings</summary>${input("Role description", "role", agent.role)}${input("Responsibilities", "responsibilities", agent.responsibilities, { textarea: true, rows: 3 })}${input("Tasks this agent can run at once", "max_concurrency", agent.max_concurrency, { number: true })}<div class="capability-picker"><span class="label">Allowed actions</span>${Object.entries(
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
    const capacityOptions = [
      ...new Set([
        ...Array.from(
          { length: Math.min(capacity, 8) },
          (_, index) => index + 1,
        ),
        ...[capacity, Number(concurrency)].filter(Number.isSafeInteger),
      ]),
    ].sort((a, b) => a - b);
    root.innerHTML = `<div class="organization-editor"><div class="chart-toolbar"><div><strong>${agents.length} assigned agents</strong><span>Select a card to edit · drag it onto a new manager</span></div><div class="chart-add-controls"><select aria-label="New agent role" data-new-role>${Object.entries(
      ROLES,
    )
      .map(
        ([key, role]) =>
          `<option value="${key}" ${key === "builder" ? "selected" : ""}>${e(role.name)}</option>`,
      )
      .join(
        "",
      )}</select><button class="button primary" type="button" data-chart-action="add" title="Add a role reporting to the selected agent, or to you when Project owner is selected">+ Add role</button></div></div><div class="chart-editor-body"><section class="chart-workspace"><div class="chart-view-controls"><button type="button" class="button" data-chart-action="zoom-out" aria-label="Zoom out">−</button><span data-zoom-label></span><button type="button" class="button" data-chart-action="zoom-in" aria-label="Zoom in">+</button><button type="button" class="button" data-chart-action="fit">Fit team</button></div><div data-chart-canvas></div><p class="chart-message" data-chart-message role="status"></p><label class="field chart-capacity"><span>How much should run at once?</span><select aria-label="Project concurrency" data-project-concurrency>${capacityOptions
      .map(
        (count) =>
          `<option value="${count}" ${Number(concurrency) === count ? "selected" : ""}>${count === 1 ? "One task at a time" : `Up to ${count} tasks at once`}</option>`,
      )
      .join(
        "",
      )}</select><small>Team membership is separate from simultaneous execution.</small></label></section><aside class="node-inspector" data-node-inspector>${inspector()}</aside></div></div>`;
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
  /** Dispatch toolbar/node actions; keep draft deletion separate from persistence. */
  function click(event) {
    if (ignoreClick) {
      ignoreClick = false;
      return;
    }
    const node = event.target.closest("[data-org-agent]");
    if (node) {
      selected = node.dataset.orgAgent;
      render();
      return;
    }
    const action = event.target.closest("[data-chart-action]")?.dataset
      .chartAction;
    try {
      if (action === "add") add();
      else if (action === "remove") {
        if (locked.has(selected))
          throw new Error(
            "This agent has recorded tasks and cannot be removed.",
          );
        agents = removeAgent(agents, selected);
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
        concurrency = event.target.value;
        publish();
        return;
      }
      const agent = current();
      if (!agent) return;
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
    const node = event.target.closest("[data-org-node]")?.dataset.orgNode;
    gesture = {
      svg,
      node: node === "__owner__" ? null : node,
      pressedNode: node,
      startX: event.clientX,
      startY: event.clientY,
      last: point(event, svg),
      moved: false,
      candidate: null,
    };
  }
  /** Preview a valid drop target, or shift the viewBox when dragging the background. */
  function pointerMove(event) {
    if (!gesture) return;
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
    const source = organizationLayout(agents).nodes.find(
      (node) => node.key === gesture.node,
    );
    const cursor = point(event, gesture.svg);
    const preview = root.querySelector("[data-drag-link]");
    if (source && preview) {
      const x = source.x + source.width / 2,
        y = source.y + source.height;
      preview.setAttribute(
        "d",
        `M ${x} ${y} C ${x} ${y + 35} ${cursor.x} ${cursor.y - 35} ${cursor.x} ${cursor.y}`,
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
    error(
      gesture.candidate
        ? "Release to change this agent’s manager."
        : "Drop on a manager card. Descendants cannot become managers.",
    );
  }
  /** Commit only valid drops; invalid gestures leave the original hierarchy intact. */
  function pointerUp(event) {
    if (!gesture) return;
    root.querySelector("[data-drag-link]")?.setAttribute("d", "");
    const ended = gesture;
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
    if (ended.moved && ended.node && ended.candidate) {
      agents = reparentAgent(
        agents,
        ended.node,
        ended.candidate === "__owner__" ? "" : ended.candidate,
      );
      selected = ended.node;
      center = null;
      zoom = 1;
      publish();
      render();
    } else
      root
        .querySelectorAll(".drop-target")
        .forEach((element) => element.classList.remove("drop-target"));
  }
  /** Abort a cancelled gesture without changing any reporting relationship. */
  function pointerCancel() {
    root.querySelector("[data-drag-link]")?.setAttribute("d", "");
    gesture = null;
    ignoreClick = false;
    root
      .querySelectorAll(".drop-target")
      .forEach((element) => element.classList.remove("drop-target"));
  }
  /** Provide keyboard node selection; the inspector offers keyboard reparenting. */
  function keydown(event) {
    const node = event.target.closest("[data-org-agent]");
    if (node && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      selected = node.dataset.orgAgent;
      render();
      [...root.querySelectorAll("[data-org-agent]")]
        .find((element) => element.dataset.orgAgent === selected)
        ?.focus?.();
    }
  }
  root.addEventListener("click", click);
  root.addEventListener("input", inputChange);
  root.addEventListener("change", change);
  root.addEventListener("pointerdown", pointerDown);
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
      root.removeEventListener("pointermove", pointerMove);
      root.removeEventListener("pointerup", pointerUp);
      root.removeEventListener("pointercancel", pointerCancel);
      root.removeEventListener("keydown", keydown);
    },
  };
}
