# Model execution

The text-generation runtime in `orchestration/models.py` uses HTTPS and the Python
standard library. It does not depend on provider SDKs or locally installed CLIs.
The configured provider and model belong to each persistent agent record.
Credentials do not belong to agent records: they remain in process memory or the
server environment and are never included in state snapshots or project files.

A `model.generate` task requires a prompt, a project-relative output path, and
an output limit of 1–8192 tokens (default 2048). The API call uses the selected
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
