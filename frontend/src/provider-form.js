import { mountDialog, showError } from "./form-controls.js";
import { escapeHtml as e } from "./domain.js";

const states = {
  signed_in: "Saved sign-in available",
  signed_out: "Sign-in required",
  not_installed: "Tool not installed",
  unverified: "Sign-in not verified",
};

/** Open provider sign-in controls without receiving or storing account credentials. */
export function openProviderForm(document, api) {
  const controller = mountDialog(
    document,
    "Local CLI sign-in",
    "Your accounts",
  );
  const { dialog } = controller;
  mountProviderPanel(dialog.querySelector("[data-content]"), controller, api);
  return dialog;
}

/** Mount local sign-in controls inside an existing dialog or connection tab. */
export function mountProviderPanel(content, controller, api) {
  content.innerHTML = `<div class="dialog-body"><p>Your CLI tools keep their sign-in on this computer and reuse it when you reopen the app. Sign in through the provider’s tool only when needed.</p><div class="notice">Credentials stay in each CLI’s own credential store. This app does not copy them. Model tasks currently use separate API-key connections.</div><div data-providers aria-live="polite">Checking saved sign-ins…</div><p data-provider-message role="status"></p><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Saved CLI sign-ins survive app restarts. Sign in again if a provider expires or revokes the session.</span><button class="button" data-refresh-providers>Refresh status</button></footer>`;
  const list = content.querySelector("[data-providers]");

  /** Render truthful provider states and commands returned by the local backend. */
  function render(providers) {
    list.innerHTML = providers
      .map(
        (provider) =>
          `<section class="provider-card"><div><h3>${e(provider.name)}</h3><p>${e(provider.tool)} · ${e(states[provider.state] || "Sign-in not verified")}</p></div><button class="button ${provider.state === "signed_in" ? "" : "primary"}" data-provider="${e(provider.id)}" ${provider.can_launch ? "" : "disabled"}>${provider.state === "signed_in" ? "Switch account" : provider.id === "gemini" && provider.installed ? "Open Antigravity" : `Sign in with ${e(provider.name)}`}</button><p class="provider-help">${providerHelp(provider)}</p><div class="provider-help">${provider.installed ? "Manual sign-in:" : "Install the official tool first, then run:"} <code>${e(provider.command)}</code> · <a href="${e(provider.docs)}" target="_blank" rel="noopener noreferrer">Tool website ↗</a></div></section>`,
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
      if (content.isConnected) render(result.providers);
    } catch (error) {
      if (content.isConnected) showError(content, error);
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
      if (content.isConnected)
        content.querySelector("[data-provider-message]").textContent =
          result.message;
    } catch (error) {
      if (content.isConnected) showError(content, error);
    } finally {
      controller.busy(false);
    }
  }
  content.addEventListener("click", click);
  return refresh();
}

/** Explain whether a saved session was verified without treating a launch as login. */
function providerHelp(provider) {
  if (provider.state === "signed_in")
    return "Your saved sign-in is available. Reopening this app does not require signing in again.";
  if (provider.id === "gemini")
    return "Antigravity reuses its saved Google sign-in on this computer. Open it with agy and sign in only if prompted. This app cannot verify its login status yet.";
  return "Complete sign-in in Terminal, then refresh status. The CLI saves the session on this computer.";
}
