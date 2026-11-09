# Model execution

Each agent stores an `execution_mode` (`cli` or `api`), provider, and model.
Existing records migrate to `api`; new local teams can select `cli`. The API
runtime in `orchestration/models.py` uses HTTPS and the Python standard library.
The local runtime in `orchestration/cli_models.py` invokes installed Codex CLI,
Claude Code, or Antigravity CLI. CLI credentials remain with those tools; API
credentials remain in process memory or the server environment. Neither is
included in agent records, state snapshots, or project files.

In a local workspace, **Model connections** opens on the **Local CLI** tab, with
**API keys** second. The CLI tab opens Codex CLI, Claude Code, or Antigravity CLI
(`agy`) for Gemini. An agent configured for CLI execution uses that tool's saved
sign-in directly. Hosted workspaces expose only API connections and reject CLI
tasks before dispatch.

CLI credentials stay in the provider's persistent credential store on the same
computer. The app never copies them into its database, project files, or browser
storage. While the CLI tab is open it checks sign-in about every three seconds;
it does not launch login automatically. A verified session is shown as **Saved
sign-in available**, with **Switch account** as an explicit action. Codex and
Claude use native status commands; Antigravity uses its authenticated model list.
Transient check failures show an unverified state. A provider can still expire
or revoke a saved session, and signing out from the CLI also signs it out for
this app. Model metadata is cached outside the polling requests so ordinary
workspace snapshots do not start CLI processes.

A `model.generate` task requires a prompt, a project-relative output path, and
an output token value of 1–8192 (default 2048). APIs enforce that token limit;
CLIs treat it as a requested target and may use more tokens. Each call uses the selected
agent's instructions, project charter, roster, and completed handoffs. Explicit
dependencies take priority over recent collaborating-agent outputs; at most 20
handoffs and 24,000 result characters are included. Individual results are
excerpted to 8,000 characters. The prompt reports omitted handoff counts.
Artifacts are verified against their recorded digest before inclusion. A changed
artifact fails the attempt rather than silently supplying different evidence.

The model produces text only. Generated instructions are not executed as shell
commands. Output is written atomically to the isolated project artifact directory,
with a 64 KiB limit. The receipt records provider, model, response identity, and
usage when the provider returns them. Incomplete provider responses fail the task
and block dependents, while retaining the returned artifact for inspection.
Mechanical completion does not establish the output's correctness.

CLI execution passes the context through stdin, never as shell arguments. Each
turn uses a temporary working directory; source files are attached as explicit
text context rather than exposing the original source tree to the CLI. Native
tool calls are disabled or denied, and CLI processes inherit the native login
without the API-key environment variables used by the separate API runtime.
Process output is limited to 4 MiB and final text to 64 KiB, with a 120-second
timeout. Cancellation terminates the CLI process group. Only a completed native
result is accepted. `default` delegates model selection to the CLI; Codex and
Antigravity also expose native model choices. Claude currently exposes its native
default. The receipt includes `execution_mode` and `token_limit_enforced` so
CLI token targets are not represented as hard limits.
Antigravity may also apply instructions from its native global configuration;
this adapter does not rewrite that configuration or claim exclusive control of
those instructions. Its model tool access is still denied for these text tasks.

HTTP requests have a 30-second timeout, a 4 MiB response limit, TLS verification,
and no redirects, automatic retries, or provider fallback. Network errors are
redacted before becoming task errors. Cancellation is checked before and after
the request; it cannot retract a request already received or billed by a provider.
Model lists are loaded when connecting or refreshing an API key. A provider may
restrict which listed models work with text generation; a rejected model fails
explicitly. Keys entered in the UI are lost on restart. Environment keys can be
used for persistent server configuration.

Create peer links directly on the chart by dragging a card’s **+ handle** onto
another agent. **Connect agents** mode also supports selecting two cards with
clicks, taps, or Enter/Space. Escape exits connection mode. Uncheck a peer under
**Works with** to remove the link on both sides. Connecting peers never changes
their managers.

`Works with` links are reciprocal information-sharing relationships, not execution
dependencies. They expose completed peer results within the same project; future
or still-running results are never guessed. Add a task dependency to require a
specific handoff before a downstream task starts.

Official API references:

- [OpenAI text generation](https://developers.openai.com/api/docs/guides/text)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create)
- [Gemini content generation](https://ai.google.dev/api/generate-content)


## Selected source context

For imported local projects, a model task can attach up to eight source files.
The goal editor lists safe text-file metadata; only checked files are read when
the task runs, and their contents are sent to the selected provider. Each file is
limited to 64 KiB and the combined context to 48,000 characters. Reads revalidate
canonical paths and use directory descriptors to reject symlinks. Hidden files,
common key/credential files, unsupported suffixes, and binary content are rejected.

File listings are bounded to eight levels and 3,000 entries; dependency and build
folders are skipped. Filtering in the picker preserves already-selected files.
The receipt records selected relative paths and content hashes, not source copies.
This feature is disabled in hosted mode and never edits the imported source files.
