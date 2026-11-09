import { escapeHtml as e } from "./domain.js";

/**
 * Render an escaped, labeled text, numeric, or multiline form field.
 * @returns {string} Markup; options control required state, rows, placeholder, and helper text.
 */
export function input(label, field, value = "", options = {}) {
  const attrs = `data-field="${field}" aria-label="${e(label)}" ${options.required ? "required" : ""}${options.placeholder ? ` placeholder="${e(options.placeholder)}"` : ""}`;
  const control = options.textarea
    ? `<textarea ${attrs} rows="${options.rows || 3}" ${options.required ? "maxlength=4000" : ""}>${e(value)}</textarea>`
    : `<input ${attrs} type="${options.number ? "number" : "text"}" value="${e(value)}" ${options.number ? 'min="1" step="1"' : 'maxlength="120"'}>`;
  return `<label class="field"><span>${e(label)}</span>${control}${options.hint ? `<small>${e(options.hint)}</small>` : ""}</label>`;
}
/**
 * Read one named field inside the supplied DOM scope, returning empty text if absent.
 */
export const value = (root, field) =>
  root.querySelector(`[data-field="${field}"]`)?.value ?? "";
/**
 * Display a validation or API error inside the active dialog using plain text.
 */
export const showError = (dialog, error) => {
  const box = dialog.querySelector("[data-error]");
  box.textContent = error.message;
  box.classList.remove("hidden");
};

/**
 * Create an accessible dialog shell with cleanup and pending-request protection.
 * @param {Document} document Document in which to mount the editor.
 * @param {string} title Dialog heading and accessible label.
 * @param {string} subtitle Short context label.
 * @returns {object} Dialog element and busy-state controls; opening mutates the DOM.
 */
export function mountDialog(document, title, subtitle) {
  const dialog = document.createElement("dialog");
  dialog.className = "editor-dialog";
  dialog.setAttribute("aria-label", title);
  dialog.innerHTML = `<header class="dialog-header"><div><span class="eyebrow">${e(subtitle)}</span><h2>${e(title)}</h2></div><button class="icon-button" data-close aria-label="Close dialog">×</button></header><div data-content></div>`;
  document.body.append(dialog);
  let pending = false;
  const disabledState = new Map();
  dialog.querySelector("[data-close]").onclick = () => {
    if (!pending) dialog.close();
  };
  dialog.addEventListener("cancel", (event) => {
    if (pending) event.preventDefault();
  });
  dialog.addEventListener("close", () => dialog.remove());
  if (typeof dialog.showModal === "function") dialog.showModal();
  else {
    dialog.setAttribute("open", "");
    dialog.close = () => {
      dialog.removeAttribute("open");
      dialog.dispatchEvent(new document.defaultView.Event("close"));
    };
  }
  return {
    dialog,
    /**
     * Report whether the dialog is waiting for a submission to finish.
     */
    get pending() {
      return pending;
    },
    /**
     * Disable editor controls and update the submit label during a request.
     */
    busy(flag) {
      if (flag === pending) return;
      pending = flag;
      if (flag) {
        dialog
          .querySelectorAll("button, input, textarea, select")
          .forEach((element) => {
            disabledState.set(element, element.disabled);
            element.disabled = true;
          });
      } else {
        for (const [element, wasDisabled] of disabledState)
          element.disabled = wasDisabled;
        disabledState.clear();
      }
      const button = dialog.querySelector('[type="submit"]');
      if (button) button.textContent = flag ? "Saving…" : button.dataset.label;
    },
  };
}
