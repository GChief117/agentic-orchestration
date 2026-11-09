import { mountDialog, showError } from "./form-controls.js";
import { escapeHtml as e } from "./domain.js";
import { openProviderForm } from "./provider-form.js";

/** Connect portable provider APIs without saving credentials in browser storage. */
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
  let child = null;
  content.innerHTML = `<div class="dialog-body"><p>Connect an API key for the providers your agents will use. Model tasks use the provider and model assigned to each agent.</p><div class="notice">API usage can incur provider charges. Keys entered here stay in server memory until restart; they are not saved in project files, browser storage, or GitHub. CLI subscription sign-in is separate from API access.</div><div data-model-providers>Loading connections…</div><div class="notice danger hidden" data-error role="alert"></div>${localTools ? '<button class="text-button" data-local-cli>Manage local CLI sign-in</button>' : ""}</div>`;
  dialog.addEventListener("close", () => {
    if (child?.isConnected) child.close();
  });

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
      if (dialog.isConnected) render(result.providers);
      await onChange();
    } catch (error) {
      if (dialog.isConnected) showError(dialog, error);
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
    if (event.target.closest("[data-local-cli]")) {
      child = openProviderForm(document, api);
      return;
    }
    const form = event.target.closest("[data-model-provider]");
    if (!form) return;
    if (event.target.closest("[data-refresh-models]"))
      mutate(form.dataset.modelProvider);
    if (event.target.closest("[data-disconnect]"))
      mutate(form.dataset.modelProvider, undefined, true);
  });
  api
    .models()
    .then((result) => {
      if (dialog.isConnected) render(result.providers);
    })
    .catch((error) => {
      if (dialog.isConnected) showError(dialog, error);
    });
  return dialog;
}
