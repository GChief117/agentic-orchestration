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

Two further optional settings grant bounded actions to agents assigned those capabilities:

```json
{
  "agent_command": "/absolute/path/to/stdin-capable-wrapper",
  "demo_directory": "/Users/developer/Projects/Orchestration-Demo",
  "notes_enabled": true
}
```

`bridge.demo.launch` installs and opens an isolated loopback demo containing the saved project team and shared context. It copies only application runtime files, uses its own virtual environment/data directory, and refuses to overwrite unmanaged files or stop a demo with active work. `bridge.notes.create` creates a draft in Notes on the connected Mac; it never sends the draft. Updates are limited to matching notes previously created by the app. Both actions verify remote identity, run as assigned tasks, and return receipts. The demo directory must be a separate folder directly inside the configured root.

These settings are operator-controlled private configuration. A model cannot provide executable paths, SSH identities, or arbitrary shell commands as task parameters. All connected capabilities are disabled in hosted mode.
