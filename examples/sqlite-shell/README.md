# Standalone SQLite shell

A small local SQL shell and optional empty schema for context items, tasks and
execution receipts. This reference does not import the orchestration application,
connect to any service, start agents or include any work data.

Requirements: Python 3.10 or newer with its standard library `sqlite3` module.
There are no third party Python packages, provider SDKs, Node tools or database
servers to install. Python itself and its bundled SQLite library are required.

Copy `sqlite_shell.py` anywhere and run:

```sh
python3 sqlite_shell.py ./context.sqlite3 --init-agent-schema
python3 sqlite_shell.py ./context.sqlite3
```

The optional schema is embedded in the single script. Initialization is
idempotent and creates empty tables. It is not a migration system for an existing
application database. New database files are created with owner-only permissions
where the operating system supports them. Existing permissions are preserved.
Keep live databases and exports outside source control.

## Use the shell

Enter SQL ending with a semicolon. `.tables`, `.schema`, `.help` and `.quit` are
available on their own lines. Query results are JSON lines with separate columns
and rows, preserving duplicate column names. Binary values use `blob_hex`.

```sql
INSERT INTO context_items (id, source_uri, content)
VALUES ('note-1', 'example://design', 'Use a bounded work queue.');
INSERT INTO tasks (id, title, context_id)
VALUES ('task-1', 'Review the example design', 'note-1');
SELECT id, title, status FROM tasks WHERE status = 'planned';
```

For scripts, execute one batch and exit:

```sh
python3 sqlite_shell.py ./context.sqlite3 --execute "SELECT id, title FROM tasks;"
```

The shell runs SQL locally with the current user's permissions. It is a developer
tool, not a restricted agent tool or security boundary. Do not send untrusted SQL
to it. Batches commit automatically; explicit transaction commands, `VACUUM` and
other operations that require running outside a transaction are not supported.
Use the platform SQLite CLI or Python API for those operations. A SQL failure
rolls back the batch and returns a nonzero status in scripted mode. Explicit
transaction control and database attachment are rejected to preserve that batch
boundary.

The optional tables record metadata only. Status changes, evidence verification,
claim leases, access control, redaction, retention, backups and agent execution
remain the responsibility of the integrating application. In particular, writing
`done` or a receipt does not prove that a task succeeded.

## Check the reference

From this directory:

```sh
python3 -m unittest -v
```

Tests use temporary databases and synthetic records. No network access or model
calls are made.
