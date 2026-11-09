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
  const controller = mountDialog(document, "Local CLI sign-in", "Your accounts");
  const { dialog } = controller;
  mountProviderPanel(dialog.querySelector("[data-content]"), controller, api);
  return dialog;
}

/** Mount CLI controls and watch native sign-in state while this panel is visible. */
export function mountProviderPanel(
  content,
  controller,
  api,
  onChange = async () => {},
  pollInterval = 3000,
) {
  content.innerHTML = `<div class="dialog-body"><p>Your CLI tools keep their sign-in on this computer and reuse it when you reopen the app. Sign in through the provider’s tool only when needed.</p><div class="notice">Assign Local CLI in an agent’s settings to run tasks with Codex, Claude Code, or Antigravity. Credentials stay with each tool.</div><div data-providers aria-live="polite">Checking saved sign-ins…</div><p data-provider-message role="status"></p><div class="notice danger hidden" data-error role="alert"></div></div><footer class="dialog-footer"><span>Sign-in status updates automatically while this panel is open.</span><button class="button" data-refresh-providers>Refresh status</button></footer>`;
  const list = content.querySelector("[data-providers]");
  const window = content.ownerDocument.defaultView;
  let timer = null;
  let stopped = false;
  let inFlight = null;
  let lastSignature = null;
  let previous = new Map();

  /** Release polling immediately when the dialog closes or its tab is replaced. */
  function stop() {
    stopped = true;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    observer.disconnect();
  }
  const observer = new window.MutationObserver(() => {
    if (!content.isConnected) stop();
  });
  observer.observe(content.ownerDocument.body, { childList: true, subtree: true });

  /** Render changed states without moving keyboard focus away from a provider button. */
  function render(providers) {
    const focused = content.ownerDocument.activeElement;
    const focusedProvider = focused?.dataset.provider;
    list.innerHTML = providers
      .map(
        (provider) =>
          `<section class="provider-card"><div><h3>${e(provider.name)}</h3><p>${e(provider.tool)} · ${e(states[provider.state] || "Sign-in not verified")}</p></div><button class="button ${provider.state === "signed_in" ? "" : "primary"}" data-provider="${e(provider.id)}" ${provider.can_launch ? "" : "disabled"}>${provider.state === "signed_in" ? "Switch account" : provider.id === "gemini" && provider.installed ? "Open Antigravity" : `Sign in with ${e(provider.name)}`}</button><p class="provider-help">${providerHelp(provider)}</p><div class="provider-help">${provider.installed ? "Manual sign-in:" : "Install the official tool first, then run:"} <code>${e(provider.command)}</code> · <a href="${e(provider.docs)}" target="_blank" rel="noopener noreferrer">Tool website ↗</a></div></section>`,
      )
      .join("");
    if (focusedProvider)
      [...list.querySelectorAll("[data-provider]")]
        .find((button) => button.dataset.provider === focusedProvider)?.focus();
  }

  /** Queue one refresh at a time, without keeping a detached panel alive. */
  function schedule() {
    if (stopped || !content.isConnected) return stop();
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = null;
      refresh();
    }, pollInterval);
  }

  /** Recheck native CLI sessions; a launch is never treated as authentication. */
  function refresh() {
    if (stopped || !content.isConnected) return Promise.resolve();
    if (inFlight) return inFlight;
    if (controller.pending) {
      schedule();
      return Promise.resolve();
    }
    inFlight = Promise.resolve().then(async () => {
      try {
        const result = await api.providers();
        if (stopped || !content.isConnected) return;
        const signature = JSON.stringify(
          result.providers.map((provider) => ({
            id: provider.id,
            state: provider.state,
            installed: provider.installed,
            can_launch: provider.can_launch,
            models: provider.models,
          })),
        );
        content.querySelector("[data-error]").classList.add("hidden");
        if (signature !== lastSignature) {
          for (const provider of result.providers) {
            const old = previous.get(provider.id);
            if (old && old !== "signed_in" && provider.state === "signed_in")
              content.querySelector("[data-provider-message]").textContent =
                `${provider.name} sign-in confirmed. Ready for CLI tasks.`;
          }
          previous = new Map(
            result.providers.map((provider) => [provider.id, provider.state]),
          );
          lastSignature = signature;
          render(result.providers);
          await onChange();
        }
      } catch (error) {
        if (!stopped && content.isConnected) showError(content, error);
      } finally {
        inFlight = null;
        schedule();
      }
    });
    return inFlight;
  }

  /** Launch only the selected provider, then continue watching for confirmed login. */
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
      refresh();
    }
  }
  content.addEventListener("click", click);
  return refresh();
}

/** Explain verified native credentials and how to recover an unavailable session. */
function providerHelp(provider) {
  if (provider.state === "signed_in")
    return "Your saved sign-in is ready for Local CLI agents. Reopening this app does not require signing in again.";
  if (provider.id === "gemini")
    return "Antigravity reuses its saved Google sign-in. Open it with agy and sign in if prompted; this screen checks again automatically.";
  return "Complete sign-in in Terminal. This screen updates automatically when the CLI confirms your session.";
}
