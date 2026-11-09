import { mountDialog, showError } from "./form-controls.js";
import { escapeHtml as e } from "./domain.js";

/** Scan a chosen local folder and import only projects explicitly checked by the user. */
export function openImportForm(document, api, onImported) {
  const controller = mountDialog(
    document,
    "Import local projects",
    "Project discovery",
  );
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
  let scan = null;
  const selected = new Set();
  content.innerHTML = `<div class="dialog-body"><p>Choose a folder to search. The scanner checks project filenames and skips hidden folders, dependencies, and symbolic links.</p><label class="field"><span>Search folder</span><input data-scan-root aria-label="Search folder" placeholder="~/Projects" list="scan-folders"><datalist id="scan-folders"></datalist></label><label class="scan-project"><input type="checkbox" data-include-root><span>Also list this folder even if it has no standard project files</span></label><button type="button" class="button" data-scan>Scan folder</button><p data-scan-summary role="status"></p><div data-scan-results></div><div class="notice">Import saves a link to each original folder and creates an editable starter team. It does not copy files, install dependencies, or run code. Tasks still use the app’s isolated workspace.</div><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span data-selection-count>No projects selected</span><button type="button" class="button primary" data-import disabled>Import selected</button></footer>`;
  const root = content.querySelector("[data-scan-root]");

  /** Keep the import button synchronized with explicit checkbox selections. */
  function selectionChanged() {
    content.querySelector("[data-selection-count]").textContent =
      `${selected.size} project${selected.size === 1 ? "" : "s"} selected`;
    content.querySelector("[data-import]").disabled =
      !selected.size || selected.size > 100;
  }

  /** Render detected paths with imported projects disabled and nothing preselected. */
  function render() {
    const results = content.querySelector("[data-scan-results]");
    results.innerHTML = scan.projects.length
      ? `<label class="field"><span>Filter results</span><input data-filter aria-label="Filter projects" placeholder="Name, path, or project type"></label><button type="button" class="text-button" data-select-all>Select up to 100 visible projects</button><button type="button" class="text-button" data-clear>Clear selection</button><div class="scan-results">${scan.projects.map((project) => `<label class="scan-project" data-result data-search="${e(`${project.name} ${project.path} ${project.kinds.join(" ")}`.toLowerCase())}"><input type="checkbox" data-candidate="${e(project.id)}" ${project.imported ? "disabled" : ""}><span><strong>${e(project.name)}</strong><small>${e(project.path)}</small><small>${e(project.kinds.join(" · "))}${project.imported ? " · Already imported" : ""}</small></span></label>`).join("")}</div>`
      : "<p>No recognized projects found. Try a project’s parent folder or another search location.</p>";
    content.querySelector("[data-scan-summary]").textContent =
      `${scan.projects.length} projects found in ${scan.visited} folders.${scan.truncated ? " Scan reached a depth, size, or time limit; scan a smaller folder for more results." : ""}${scan.unreadable ? ` ${scan.unreadable} folders could not be read.` : ""}`;
    selectionChanged();
  }

  /** Scan or import on explicit clicks; keep failures reviewable without losing selections. */
  async function click(event) {
    if (controller.pending) return;
    const button = event.target.closest("button");
    if (!button) return;
    if (
      button.hasAttribute("data-select-all") ||
      button.hasAttribute("data-clear")
    ) {
      if (button.hasAttribute("data-clear")) selected.clear();
      else
        for (const input of content.querySelectorAll(
          "[data-result]:not([hidden]) [data-candidate]:not(:disabled)",
        )) {
          if (selected.size >= 100) break;
          selected.add(input.dataset.candidate);
        }
      for (const input of content.querySelectorAll("[data-candidate]"))
        input.checked = selected.has(input.dataset.candidate);
      selectionChanged();
      return;
    }
    if (!button.matches("[data-scan], [data-import]")) return;
    controller.busy(true);
    content.querySelector("[data-error]").classList.add("hidden");
    try {
      if (button.hasAttribute("data-scan")) {
        content.querySelector("[data-scan-summary]").textContent =
          "Scanning project folders…";
        scan = await api.scanProjects(
          root.value,
          content.querySelector("[data-include-root]").checked,
        );
        selected.clear();
        render();
      } else {
        const result = await api.importProjects(scan.scan_id, [...selected]);
        await onImported(result.projects);
        dialog.close();
      }
    } catch (error) {
      if (dialog.isConnected) showError(dialog, error);
    } finally {
      controller.busy(false);
      selectionChanged();
    }
  }

  /** Filter discovery results locally without changing prior checkbox selections. */
  function input(event) {
    if (event.target.matches("[data-filter]")) {
      const query = event.target.value.toLowerCase();
      for (const row of content.querySelectorAll("[data-result]"))
        row.hidden = !row.dataset.search.includes(query);
    }
  }

  /** Store checked candidate IDs; paths are resolved by the backend scan cache. */
  function change(event) {
    const id = event.target.dataset.candidate;
    if (!id) return;
    if (event.target.checked) selected.add(id);
    else selected.delete(id);
    selectionChanged();
  }
  content.addEventListener("click", click);
  content.addEventListener("input", input);
  content.addEventListener("change", change);
  api
    .scanRoots()
    .then(({ roots }) => {
      if (!dialog.isConnected) return;
      content.querySelector("datalist").innerHTML = roots
        .map((path) => `<option value="${e(path)}"></option>`)
        .join("");
      if (!root.value) root.value = roots[0] || "";
    })
    .catch((error) => {
      if (dialog.isConnected) showError(dialog, error);
    });
  return dialog;
}
