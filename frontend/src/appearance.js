import { mountDialog } from "./form-controls.js";

export const APPEARANCES = {
  original: {
    name: "Original green",
    detail: "The current palette and typography.",
  },
  neutral: {
    name: "Crisp neutral",
    detail: "White surfaces, charcoal text, blue accents, and sharper corners.",
  },
  graphite: {
    name: "Dark graphite",
    detail: "Dark surfaces, quiet borders, and cool blue accents.",
  },
  warm: {
    name: "Warm minimal",
    detail: "Ivory surfaces, terracotta accents, and serif headings.",
  },
};

/** Apply a whitelisted palette; save only this appearance preference locally. */
export function applyAppearance(document, name, persist = true) {
  if (!Object.hasOwn(APPEARANCES, name)) name = "original";
  if (name === "original") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = name;
  if (persist) {
    try {
      document.defaultView.localStorage.setItem(
        "orchestration-appearance",
        name,
      );
    } catch {
      /* Storage may be unavailable in private contexts. */
    }
  }
}

/** Restore the visual preference without changing project data or app behavior. */
export function initializeAppearance(document) {
  let name = "original";
  try {
    name =
      document.defaultView.localStorage.getItem("orchestration-appearance") ||
      name;
  } catch {
    /* Use the existing appearance when storage is unavailable. */
  }
  applyAppearance(document, name, false);
}

/** Preview app-wide styles immediately while retaining every existing layout. */
export function openAppearanceForm(document) {
  const { dialog } = mountDialog(
    document,
    "Choose an appearance",
    "Visual styles",
  );
  const content = dialog.querySelector("[data-content]");
  content.innerHTML = `<div class="dialog-body"><p>Try a style on the whole app. Layout, projects, and behavior stay the same.</p><div class="appearance-options">${Object.entries(
    APPEARANCES,
  )
    .map(
      ([key, theme]) =>
        `<button type="button" class="appearance-option" data-appearance="${key}" aria-pressed="false"><span class="appearance-swatches" data-palette="${key}" aria-hidden="true"><i></i><i></i><i></i></span><strong>${theme.name}</strong><small>${theme.detail}</small></button>`,
    )
    .join("")}</div><p data-appearance-status role="status"></p></div>`;
  /** Reflect the selected palette without recreating controls or losing focus. */
  function update() {
    const selected = document.documentElement.dataset.theme || "original";
    for (const button of content.querySelectorAll("[data-appearance]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.appearance === selected),
      );
    content.querySelector("[data-appearance-status]").textContent =
      `${APPEARANCES[selected].name} applied. Close this panel to explore the app.`;
  }
  content.addEventListener("click", (event) => {
    const button = event.target.closest("[data-appearance]");
    if (!button) return;
    applyAppearance(document, button.dataset.appearance);
    update();
  });
  update();
  return dialog;
}
