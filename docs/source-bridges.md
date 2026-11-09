# Read-only source bridges

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
