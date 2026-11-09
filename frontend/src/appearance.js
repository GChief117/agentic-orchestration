import {
  BRAND_STYLES,
  renderBrand,
  applyBrandStyle,
  initializeBrandStyle,
} from "./branding.js";
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
  ocean: {
    name: "Ocean & clay",
    detail: "Deep teal, soft clay surfaces, and a light coastal background.",
  },
  plum: {
    name: "Plum & honey",
    detail: "Rich plum, warm honey surfaces, and creamy neutrals.",
  },
  warm: {
    name: "Warm minimal",
    detail: "Ivory surfaces, terracotta accents, and adjustable typography.",
  },
};

export const TYPOGRAPHY = {
  editorial: "Georgia · Familiar and sturdy",
  iowan: "Iowan Old Style · Open and literary",
  baskerville: "Baskerville · Classic and refined",
  literary: "Palatino · Warm and spacious",
  didot: "Didot · Sharp and elegant",
  times: "Times · Compact and traditional",
};

/** Apply a heading font independently of the current color palette. */
export function applyTypography(document, name, persist = true) {
  if (!Object.hasOwn(TYPOGRAPHY, name)) name = "default";
  if (name === "default") delete document.documentElement.dataset.typography;
  else document.documentElement.dataset.typography = name;
  if (persist) {
    try {
      document.defaultView.localStorage.setItem(
        "orchestration-typography",
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
  initializeBrandStyle(document);
  let name = "original";
  try {
    name =
      document.defaultView.localStorage.getItem("orchestration-appearance") ||
      name;
  } catch {
    /* Use the existing appearance when storage is unavailable. */
  }
  applyAppearance(document, name, false);
  let typography = "default";
  try {
    typography =
      document.defaultView.localStorage.getItem("orchestration-typography") ||
      document.defaultView.localStorage.getItem(
        "orchestration-warm-typography",
      ) ||
      typography;
  } catch {
    /* Retain the original type style without local storage. */
  }
  applyTypography(document, typography, false);
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
    )} </div><section class="brand-options-section"><h3>Sidebar branding</h3><p>Compare the name and icon treatments. Click one to apply it.</p><div class="brand-options">${Object.entries(
    BRAND_STYLES,
  )
    .map(
      ([key, name]) =>
        `<button type="button" class="brand-option" data-brand-option="${key}" aria-pressed="false"><span class="brand-option-name">${name}</span><span class="brand-preview">${renderBrand(key)}</span></button>`,
    )
    .join(
      "",
    )}</div></section><div class="typography-section" data-typography-panel><h3>Compare editorial serif headings</h3><p>Six heading styles for any color palette. Click a sample to apply it.</p><div class="typography-options">${Object.entries(
    TYPOGRAPHY,
  )
    .map(
      ([key, label]) =>
        `<button type="button" class="typography-option" data-typography-option="${key}" aria-pressed="false"><span class="typography-label">${label}</span><span class="typography-preview-title">A clear plan.<br>A focused team.</span><span class="typography-preview-body">Plan, build, review.<br>Projects 12 · Agents 06</span></button>`,
    )
    .join(
      "",
    )}</div><button type="button" class="text-button" data-typography-reset>Use theme default typography</button></div><p data-appearance-status role="status"></p></div>`;
  /** Reflect the selected palette without recreating controls or losing focus. */
  function update() {
    const selected = document.documentElement.dataset.theme || "original";
    const typography = document.documentElement.dataset.typography || "default";
    for (const button of content.querySelectorAll("[data-typography-option]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.typographyOption === typography),
      );
    for (const button of content.querySelectorAll("[data-brand-option]"))
      button.setAttribute(
        "aria-pressed",
        String(
          button.dataset.brandOption ===
            (document.documentElement.dataset.brandStyle || "arrow"),
        ),
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
    if (event.target.closest("[data-typography-reset]")) {
      applyTypography(document, "default");
      update();
      return;
    }
    const brand = event.target.closest("[data-brand-option]");
    if (brand) {
      applyBrandStyle(document, brand.dataset.brandOption);
      update();
      return;
    }
    const font = event.target.closest("[data-typography-option]");
    if (font) {
      applyTypography(document, font.dataset.typographyOption);
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
