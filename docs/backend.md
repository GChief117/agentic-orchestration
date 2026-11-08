# Backend implementation

The repository includes a small working backend foundation. There is no frontend. It runs explicit task graphs, persists their state, and performs bounded local file and terminal operations. It does not yet connect remote bridges, invoke model providers, or manage interactive agent sessions.

## Code responsibilities

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `orchestration/plans.py` | Validate task IDs, dependencies, capabilities, inputs, and canonical resource claims before submission. | Connection policy |
| `orchestration/storage.py` | Store goals, tasks, attempts, receipts, and events in SQLite; hold a process-level data-directory lock. | SQLite and filesystem |
| `orchestration/scheduling.py` | Pure functions for dependency readiness, fair dispatch among goals, resource conflicts, and goal state. | Task records only |
| `orchestration/engine.py` | Coordinate submission, scheduling, worker lifecycle, cancellation, and persistence. | Plans, store, scheduler, executor |
| `orchestration/execution.py` | Perform local operations and return acceptance evidence and effect status. | Connection policy |
| `orchestration/connections.py` | Identify the local target, check supported capabilities, and constrain artifact paths. | Filesystem |
| `app.py` | Authenticated, loopback-only JSON API. | Engine |
| `orchestration/__main__.py` | Run a plan from the command line and print its result. | Engine |

## Trace one task

1. The caller submits a title and a list of tasks through `Engine.submit` or `POST /api/goals`.
2. `parse_tasks` validates the entire graph. File resources derive from canonical paths, so two aliases cannot bypass coordination by using different labels.
3. The store persists queued tasks and their dependencies.
4. `select_work` returns eligible tasks within worker capacity. It prefers goals with fewer active tasks; writers exclude other readers and writers of the same resource.
5. The engine persists an attempt record **before** starting its worker.
6. The executor checks target capability and performs the operation. Independent task groups can proceed while dependents wait.
7. The engine stores the receipt and terminal state under its coordination lock. Only accepted dependencies permit downstream dispatch.

Scheduler policy is testable without a database or thread. Adapter execution is testable without the scheduler. Their integration is exercised by the runtime tests.

## Supported operations

- `files.write`: atomically replace a bounded text artifact and verify the resulting bytes.
- `files.inspect`: inspect bounded artifact content, compute a digest, and optionally compare expected text.
- `terminal.python_version`: execute the current Python interpreter's version command using a fixed argument vector.

Task graphs can contain any combination of these operations. These are foundation capabilities, not a claim of complete bridge parity. There is no unrestricted shell or remote target fallback.

## Run it

Python 3.10+ on macOS or Linux; no runtime dependencies outside the standard library. The data-directory process lock currently uses Unix `flock`.

```sh
python3 -m orchestration examples/backend_goal.json --data-dir .runtime/example
python3 -m unittest discover -s tests -v
```

The command returns JSON with goals, tasks, attempts, events, and receipts. Outputs remain under the selected directory's `artifacts/` folder. Running the example again creates another goal; submission deduplication is not implemented.

For the JSON service:

```sh
python3 app.py --port 8765
```

The service writes a private bearer credential to `.runtime/api-token` with mode `0600`, replacing it on restart. Clients provide it in `Authorization: Bearer …`. Do not commit this file or place the credential in a URL. All routes require the local credential; no dashboard is served.

| Method | Route | Body or result |
| --- | --- | --- |
| POST | `/api/goals` | Body shaped like `examples/backend_goal.json`; returns the created goal ID. |
| GET | `/api/state` | Goals, tasks, attempts, events, target, and worker counts. |
| POST | `/api/cancel` | `{ "id": "goal-id" }`; requests cancellation. |
| GET | `/api/artifact/task-id` | Text artifact referenced by a completed task receipt. |

The API is for a trusted local client, not multi-user network hosting. Authorization does not sandbox the Python process against a local account that already controls it.

## Recovery and limitations

- Queued work persists. Accepted work remains accepted on restart.
- Interrupted attempts become failed and are **not automatically replayed**; a prior effect may already have occurred. Dependents become blocked.
- Cancellation reaches active worker signals and prevents queued dispatch. A completed file write cannot necessarily be undone; retain its receipt.
- One engine owns a data directory at a time. Multiple independent directories are allowed.
- There is no retry policy, distributed scheduler, priority/deadline enforcement, remote authentication, or long-running interactive agent-session adapter yet.
- File operations defend ordinary path traversal and known symlink escapes. The local artifact directory must remain controlled by the service account; this is not a sandbox against a hostile process modifying paths concurrently.

The larger [three-part design](architecture.md) remains a roadmap. Implemented behavior is limited to the operations and tests described here.
