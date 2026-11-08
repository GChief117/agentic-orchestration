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
  editorial: "Georgia · Familiar and sturdy",
  iowan: "Iowan Old Style · Open and literary",
  baskerville: "Baskerville · Classic and refined",
  literary: "Palatino · Warm and spacious",
  didot: "Didot · Sharp and elegant",
  times: "Times · Compact and traditional",
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
    )} </div><div class="warm-typography" data-warm-type-panel hidden><h3>Compare editorial serif headings</h3><p>Six heading styles with the same body font and warm palette. Click a sample to apply it.</p><div class="typography-options">${Object.entries(
    WARM_TYPOGRAPHY,
  )
    .map(
      ([key, label]) =>
        `<button type="button" class="typography-option" data-warm-type="${key}" aria-pressed="false"><span class="typography-label">${label}</span><span class="typography-preview-title">A clear plan.<br>A focused team.</span><span class="typography-preview-body">Plan, build, review.<br>Projects 12 · Agents 06</span></button>`,
    )
    .join("")}</div></div><p data-appearance-status role="status"></p></div>`;
  /** Reflect the selected palette without recreating controls or losing focus. */
  function update() {
    const selected = document.documentElement.dataset.theme || "original";
    content.querySelector("[data-warm-type-panel]").hidden =
      selected !== "warm";
    const typography =
      document.documentElement.dataset.warmTypography || "editorial";
    for (const button of content.querySelectorAll("[data-warm-type]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.warmType === typography),
      );
    for (const button of content.querySelectorAll("[data-appearance]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.appearance === selected),
      );
    content.querySelector("[data-appearance-status]").textContent =
      `${APPEARANCES[selected].name} applied. Close this panel to explore the app.`;
  }
  content.addEventListener("click", (event) => {
    const font = event.target.closest("[data-warm-type]");
    if (font) {
      applyWarmTypography(document, font.dataset.warmType);
      update();
      return;
    }
    const button = event.target.closest("[data-appearance]");
    if (!button) return;
    applyAppearance(document, button.dataset.appearance);
    update();
  });
  update();
  return dialog;
}
