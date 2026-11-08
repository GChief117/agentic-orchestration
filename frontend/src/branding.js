/** Sidebar identity treatments; labels stay fixed while their visual style changes. */
export const BRAND_STYLES = {
  arrow: "Arrow · current",
  editorial: "Editorial wordmark",
  monogram: "Framed monogram",
  connected: "Connected nodes",
  minimal: "Minimal accent",
  seal: "Square seal",
};

/** Render one trusted brand lockup for both the sidebar and its exact preview. */
export function renderBrand(style = "arrow") {
  if (!Object.hasOwn(BRAND_STYLES, style)) style = "arrow";
  const marks = {
    arrow: '<span class="brand-icon" aria-hidden="true">↗</span>',
    editorial: "",
    monogram: '<span class="brand-icon" aria-hidden="true">O</span>',
    connected:
      '<span class="brand-icon" aria-hidden="true"><svg viewBox="0 0 32 32" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M16 10v6M8 23v-7h16v7"/><circle cx="16" cy="7" r="3"/><circle cx="8" cy="25" r="3"/><circle cx="24" cy="25" r="3"/></svg></span>',
    minimal: '<span class="brand-accent" aria-hidden="true"></span>',
    seal: '<span class="brand-icon" aria-hidden="true">O</span>',
  };
  return `<span class="brand-lockup brand-variant-${style}">${marks[style]}<span class="brand-copy"><span class="brand-title">Orchestration</span><small>Project workspace</small></span></span>`;
}

/** Apply and optionally remember a visual lockup without changing navigation. */
export function applyBrandStyle(document, style, persist = true) {
  if (!Object.hasOwn(BRAND_STYLES, style)) style = "arrow";
  document.documentElement.dataset.brandStyle = style;
  for (const brand of document.querySelectorAll("[data-brand]"))
    brand.innerHTML = renderBrand(style);
  if (persist) {
    try {
      document.defaultView.localStorage.setItem(
        "orchestration-brand-style",
        style,
      );
    } catch {
      /* The visual option remains usable when storage is unavailable. */
    }
  }
}

/** Restore only a whitelisted brand choice from local visual preferences. */
export function initializeBrandStyle(document) {
  let style = "arrow";
  try {
    style =
      document.defaultView.localStorage.getItem("orchestration-brand-style") ||
      style;
  } catch {
    /* Keep the original lockup in private storage contexts. */
  }
  applyBrandStyle(document, style, false);
}
