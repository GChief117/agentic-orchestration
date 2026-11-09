# Connected workspaces

A source bridge connects an existing project's team to repositories reached by an operator's SSH/CLI wrapper. It verifies the configured remote host and user before listing or reading files. Agents receive explicit source contents and digests; they cannot issue shell commands through this adapter.

Create `source-bridges.json` inside the application's private data directory:

```json
{
  "PROJECT_ID_FROM_THE_APP": {
    "name": "Development repository bridge",
    "command": "/absolute/path/to/existing-ssh-wrapper",
    "hostname": "development.local",
    "username": "developer",
    "root": "/Users/developer/Projects",
    "repositories": ["backend", "client"]
  }
}
```

The executable must accept one remote command as its argument and preserve existing SSH identity/host-key checks. The application supplies a fixed Python 3 reader program with validated data. Keep this file private; do not commit host settings or credentials. Hosted mode disables source bridges.

The project's **Shared context** panel reports the verified connection. Planning receives repository names, current checkout revisions, and candidate documentation/manifests. The coordinator can select different source files for each task. Those files are fetched through the bridge at execution and recorded by path and SHA-256 in the receipt. Up to eight text files and 48,000 characters are allowed per task; unavailable or oversized files produce an explicit failure.

Only configured repository folders are readable. Symlinks, hidden paths, common credential names, unsupported file types, and path traversal are rejected. Requests have a 30-second deadline and bounded output. An unavailable bridge never falls back to a local folder. This adapter does not pull repositories, install dependencies, run their code, change remote files, or verify production behavior.

Shared notes and source contents are sent to the model provider selected for the task. The bridge uses the operator's existing authentication, and the app does not copy its credentials.

## Native research and explicit workspace actions

An optional `agent_command` names a stdin-capable wrapper on the controlling machine. A project with this configured can select **Native workspace research**. That mode runs the selected Codex model on the connected host with a read-only sandbox, reads the installed `pio-map-codebase` guidance and existing mapping documents, and inspects implementation files. It records actual inspection commands and the verified host in task receipts. Hidden mapping documents can be read by this native session, unlike the explicit source-attachment picker. Claude and other providers remain available for ordinary text tasks; native workspace research currently requires Codex.

The host must have its own Codex installation, saved sign-in, and Pio mapping skill. The app does not redistribute those tools or credentials. Native research does not refresh maps, create ticket branches, stash changes, run project code, or send messages. Ticket-specific Pio workflows that make those changes remain separate, explicitly requested work.

Optional settings grant bounded actions to agents assigned those capabilities:

```json
{
  "agent_command": "/absolute/path/to/stdin-capable-wrapper",
  "demo_directory": "/Users/developer/Projects/Orchestration-Demo",
  "notes_enabled": true,
  "editor_workspace": "/Users/developer/Projects/Development.code-workspace",
  "editor_profile": "Development",
  "postgres_enabled": false,
  "aws_login_enabled": false
}
```

`bridge.demo.launch` installs and opens an isolated loopback demo containing the saved project team and shared context. It copies only application runtime files, uses its own virtual environment/data directory, and refuses to overwrite unmanaged files or stop a demo with active work. `bridge.notes.create` creates a draft in Notes on the connected Mac; it never sends the draft. Updates are limited to matching notes previously created by the app. Both actions verify remote identity, run as assigned tasks, and return receipts. The demo directory must be a separate folder directly inside the configured root.

`bridge.workspace.open` opens the configured VS Code workspace/profile and pgAdmin on the connected Mac. The workspace file must be inside the configured source root. Its receipt separates successful launches from observed window visibility and positioning; macOS Accessibility permissions can prevent the latter.

Setting `postgres_enabled` to `true` enables the current macOS/Homebrew PostgreSQL 18 setup adapter. `bridge.postgres.prepare` creates only missing `datalake`, `accelerate`, `eab_dev`, and `scoring` databases at `127.0.0.1:5432`, using the connected OS user's password-free local connection. It does not migrate, seed, or replace databases. `bridge.pgadmin.register` adds that local server through pgAdmin's official importer, preserving existing connections and storing no password. Launch pgAdmin once first to initialize its desktop registry; refresh its server tree after registration. Both actions accept empty parameters and fail if the configured local service or desktop registry is unavailable.

Setting `aws_login_enabled` to `true` permits `bridge.aws.login` and `bridge.aws.status`. Login starts the official AWS CLI Console sign-in for the local `default` profile with Safari selected. The user completes any password, MFA, and consent prompts. Status checks authentication without returning credentials or identity details. The CLI owns its credential storage; the app does not copy it. A successful launch receipt means that the sign-in process started, not that authentication finished. These actions accept empty parameters and do not grant package, database, or cloud administration access beyond the user's existing AWS permissions.

`bridge.aws.remote_login` opens the official `aws login --remote` alternative in Terminal. This avoids the localhost browser callback. Open the displayed link in Safari, complete AWS authentication, and paste the returned authorization code directly into Terminal. Never paste credentials or authorization codes into the orchestration app or chat.

## Reviewed setup steps

`bridge.setup.run` lets an assigned team member install dependencies, configure a local workspace, or run migrations using an operator-reviewed step. Add a private `setup_steps` map to the bridge configuration. Each entry contains a display `label`, configured `repository`, absolute `argv` and `verify_argv` vectors, `timeout_seconds` (up to 1,800), `input_hashes` mapping repository-relative files to SHA-256 digests, and the expected `aws_account_id` and `aws_profile`. Optional `env` settings must be bounded, non-secret values. Keep credentials in the provider's credential store.

The task selects only a step ID, for example `{"step":"backend.install"}`. The planner and manual task form expose IDs and labels, while executable definitions remain private. Every run verifies the connected host, reviewed input hashes, and AWS account before starting. Commands must finish within their limit, and their separate verification command must succeed before the task is accepted. Native command output is suppressed; receipts report the step, exit status, verification, and duration.

Setup takes an exclusive workspace lock and records operation ownership on the connected host. Completed operations return their stored receipt instead of running twice. Failed or interrupted operations require inspection before retrying; an unresolved running record blocks new work. Use task dependencies to order setup steps. Persistent services require a reviewed launcher that finishes after checking service health, rather than a command that runs indefinitely.

These settings are operator-controlled private configuration. A model cannot provide executable paths, SSH identities, or arbitrary shell commands as task parameters. All connected capabilities are disabled in hosted mode.
