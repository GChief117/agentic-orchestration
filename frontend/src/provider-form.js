import { mountDialog, showError } from "./form-controls.js";
import { escapeHtml as e } from "./domain.js";

const states = {
  signed_in: "Signed in to local tool",
  signed_out: "Sign-in required",
  not_installed: "Tool not installed",
  unverified: "Sign-in not verified",
};

/** Open provider sign-in controls without receiving or storing account credentials. */
export function openProviderForm(document, api) {
  const controller = mountDialog(
    document,
    "Model connections",
    "Your accounts",
  );
  const { dialog } = controller;
  const content = dialog.querySelector("[data-content]");
  content.innerHTML = `<div class="dialog-body"><p>Choose the account you want to use. Sign-in opens the provider’s official tool in Terminal, where you complete its browser sign-in.</p><div class="notice">These connections use your local provider tools. Agent tasks currently run local operations; signing in does not yet enable model execution.</div><div data-providers aria-live="polite">Checking installed tools…</div><p data-provider-message role="status"></p><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Credentials stay with the provider’s tool.</span><button class="button" data-refresh-providers>Refresh status</button></footer>`;
  const list = content.querySelector("[data-providers]");

  /** Render truthful provider states and commands returned by the local backend. */
  function render(providers) {
    list.innerHTML = providers
      .map(
        (provider) =>
          `<section class="provider-card"><div><h3>${e(provider.name)}</h3><p>${e(provider.tool)} · ${e(states[provider.state] || "Sign-in not verified")}</p></div><button class="button primary" data-provider="${e(provider.id)}" ${provider.can_launch ? "" : "disabled"}>${provider.state === "signed_in" ? "Sign in again" : `Sign in with ${e(provider.name)}`}</button><p class="provider-help">${provider.id === "gemini" ? "In Gemini CLI, select Sign in with Google. Return here after completing the browser flow. This app cannot verify Gemini’s sign-in state yet." : "Finish the sign-in flow, then refresh status here."}</p><div class="provider-help">${provider.installed ? "Manual sign-in:" : "Install the official tool first, then run:"} <code>${e(provider.command)}</code> · <a href="${e(provider.docs)}" target="_blank" rel="noopener noreferrer">Setup guide ↗</a></div></section>`,
      )
      .join("");
  }

  /** Refresh status on demand, keeping checks out of the workspace polling loop. */
  async function refresh() {
    if (controller.pending) return;
    controller.busy(true);
    content.querySelector("[data-error]").classList.add("hidden");
    try {
      const result = await api.providers();
      if (dialog.isConnected) render(result.providers);
    } catch (error) {
      if (dialog.isConnected) showError(dialog, error);
    } finally {
      controller.busy(false);
    }
  }

  /** Launch only the clicked provider; a successful launch is never a login claim. */
  async function click(event) {
    const button = event.target.closest(
      "[data-provider], [data-refresh-providers]",
    );
    if (!button || controller.pending) return;
    if (button.hasAttribute("data-refresh-providers")) return refresh();
    controller.busy(true);
    content.querySelector("[data-error]").classList.add("hidden");
    try {
      const result = await api.signInProvider(button.dataset.provider);
      if (dialog.isConnected)
        content.querySelector("[data-provider-message]").textContent =
          result.message;
    } catch (error) {
      if (dialog.isConnected) showError(dialog, error);
    } finally {
      controller.busy(false);
    }
  }
  content.addEventListener("click", click);
  refresh();
  return dialog;
}
