import { mountDialog, showError } from "./form-controls.js";
import { escapeHtml as e } from "./domain.js";
import { mountProviderPanel } from "./provider-form.js";

let connectionDialogId = 0;

/** Open local CLI connections first, with API keys available in a second tab. */
export function openModelConnections(
  document,
  api,
  localTools = true,
  onChange = async () => {},
) {
  const controller = mountDialog(
    document,
    "Model connections",
    "Agent execution",
  );
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
  const panelId = `connection-panel-${++connectionDialogId}`;
  content.innerHTML = `${localTools ? `<nav class="tabs connection-tabs" role="tablist" aria-label="Connection type"><button type="button" id="${panelId}-cli" data-connection-mode="cli" role="tab" aria-controls="${panelId}">Local CLI</button><button type="button" id="${panelId}-api" data-connection-mode="api" role="tab" aria-controls="${panelId}">API keys</button></nav>` : ""}<section id="${panelId}" data-connection-panel${localTools ? ' role="tabpanel"' : ""}></section>`;
  const panel = content.querySelector("[data-connection-panel]");
  let selectedMode;

  /** Clear unsubmitted secrets before replacing a panel or closing the dialog. */
  function clearKeys() {
    for (const field of panel.querySelectorAll("[data-api-key]")) field.value = "";
  }

  /** Mount only the selected connection type and discard stale panel responses. */
  function selectMode(mode) {
    if (controller.pending || mode === selectedMode) return;
    if (mode !== "api" && (mode !== "cli" || !localTools)) return;
    clearKeys();
    selectedMode = mode;
    for (const tab of content.querySelectorAll("[data-connection-mode]")) {
      const selected = tab.dataset.connectionMode === mode;
      tab.classList.toggle("selected", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) panel.setAttribute("aria-labelledby", tab.id);
    }
    const root = document.createElement("div");
    panel.replaceChildren(root);
    if (mode === "cli") return mountProviderPanel(root, controller, api, onChange);
    return mountApiPanel(root, controller, api, onChange);
  }

  content.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-connection-mode]");
    if (tab) selectMode(tab.dataset.connectionMode);
  });
  content.addEventListener("keydown", (event) => {
    const tab = event.target.closest("[data-connection-mode]");
    if (!tab || controller.pending) return;
    const alternate = selectedMode === "cli" ? "api" : "cli";
    const mode = {
      ArrowLeft: alternate,
      ArrowRight: alternate,
      Home: "cli",
      End: "api",
    }[event.key];
    if (!mode) return;
    event.preventDefault();
    const previousFocus = document.activeElement;
    const ready = selectMode(mode);
    Promise.resolve(ready).then(() => {
      if (
        dialog.isConnected &&
        selectedMode === mode &&
        [previousFocus, document.body].includes(document.activeElement)
      ) {
        content.querySelector(`[data-connection-mode="${mode}"]`).focus();
      }
    });
  });
  dialog.addEventListener("close", () => {
    clearKeys();
  });
  selectMode(localTools ? "cli" : "api");
  return dialog;
}

/** Connect provider APIs without saving credentials in browser storage. */
function mountApiPanel(content, controller, api, onChange) {
  content.innerHTML = `<div class="dialog-body"><p>Connect an API key for agents assigned to API execution. Local CLI agents use the saved sign-in from the first tab.</p><div class="notice">API usage can incur provider charges. Keys entered here stay in server memory until restart; they are not saved in project files, browser storage, or GitHub.</div><div data-model-providers>Loading connections…</div><div class="notice danger hidden" data-error role="alert"></div></div>`;

  /** Render readiness and an empty password field for each provider. */
  function render(providers) {
    content.querySelector("[data-model-providers]").innerHTML = providers
      .map(
        (provider) =>
          `<form class="provider-card" data-model-provider="${e(provider.id)}"><div><h3>${e(provider.name)}</h3><p>${provider.configured ? `API configured · ${provider.models.length} models loaded` : "API not connected"}</p></div><button class="button primary" type="submit" data-label="Connect API">Connect API</button><label class="field provider-help"><span>API key</span><input type="password" autocomplete="off" maxlength="4096" data-api-key aria-label="${e(provider.name)} API key" placeholder="Enter a key to connect or replace it"></label>${provider.configured ? '<div class="provider-help"><button type="button" class="text-button" data-refresh-models>Refresh model list</button> · <button type="button" class="text-button danger-text" data-disconnect>Disconnect for this session</button></div>' : ""}</form>`,
      )
      .join("");
  }

  /** Serialize provider changes and remove the submitted secret from the DOM. */
  async function mutate(provider, key, disconnect = false) {
    if (controller.pending) return;
    controller.busy(true);
    content.querySelector("[data-error]").classList.add("hidden");
    try {
      const result = disconnect
        ? await api.disconnectModel(provider)
        : await api.connectModel(provider, key);
      if (content.isConnected) render(result.providers);
      await onChange();
    } catch (error) {
      if (content.isConnected) showError(content, error);
    } finally {
      for (const field of content.querySelectorAll("[data-api-key]"))
        field.value = "";
      controller.busy(false);
    }
  }
  content.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.target.closest("[data-model-provider]");
    if (form)
      mutate(
        form.dataset.modelProvider,
        form.querySelector("[data-api-key]").value,
      );
  });
  content.addEventListener("click", (event) => {
    const form = event.target.closest("[data-model-provider]");
    if (!form) return;
    if (event.target.closest("[data-refresh-models]"))
      mutate(form.dataset.modelProvider);
    if (event.target.closest("[data-disconnect]"))
      mutate(form.dataset.modelProvider, undefined, true);
  });
  return api
    .models()
    .then((result) => {
      if (content.isConnected) render(result.providers);
    })
    .catch((error) => {
      if (content.isConnected) showError(content, error);
    });
}
