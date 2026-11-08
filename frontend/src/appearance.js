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
    detail: "Ivory surfaces, terracotta accents, and adjustable typography.",
  },
};

export const WARM_TYPOGRAPHY = {
  editorial: "Editorial serif headings",
  clean: "Clean sans-serif",
  soft: "Soft sans-serif",
};

/** Change only Warm minimal's type family, retaining its colors and surfaces. */
export function applyWarmTypography(document, name, persist = true) {
  if (!Object.hasOwn(WARM_TYPOGRAPHY, name)) name = "editorial";
  document.documentElement.dataset.warmTypography = name;
  if (persist) {
    try {
      document.defaultView.localStorage.setItem(
        "orchestration-warm-typography",
        name,
      );
    } catch {
      /* Typography still updates when preference storage is unavailable. */
    }
  }
}

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
  let typography = "editorial";
  try {
    typography =
      document.defaultView.localStorage.getItem(
        "orchestration-warm-typography",
      ) || typography;
  } catch {
    /* Retain the original type style without local storage. */
  }
  applyWarmTypography(document, typography, false);
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
    .join(
      "",
    )}</div><div class="warm-typography" data-warm-type-panel hidden><label class="field"><span>Warm minimal typography</span><select data-warm-type aria-label="Warm minimal typography">${Object.entries(
    WARM_TYPOGRAPHY,
  )
    .map(([key, label]) => `<option value="${key}">${label}</option>`)
    .join(
      "",
    )}</select></label><div class="typography-sample"><h2>A clear plan, a focused team.</h2><p>Organize projects, coordinate your agents, and follow the work as it happens.</p></div></div><p data-appearance-status role="status"></p></div>`;
  /** Reflect the selected palette without recreating controls or losing focus. */
  function update() {
    const selected = document.documentElement.dataset.theme || "original";
    content.querySelector("[data-warm-type-panel]").hidden =
      selected !== "warm";
    content.querySelector("[data-warm-type]").value =
      document.documentElement.dataset.warmTypography || "editorial";
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
  content
    .querySelector("[data-warm-type]")
    .addEventListener("change", (event) => {
      applyWarmTypography(document, event.target.value);
      update();
    });
  update();
  return dialog;
}
