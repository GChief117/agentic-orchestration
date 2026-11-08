# Backend implementation

The repository includes a small working backend foundation. A separate JavaScript frontend is served by the local application. It initializes greenfield projects with explicit charters and agent teams, runs assigned task graphs, persists their state, and performs bounded local file and terminal operations. It does not yet connect remote bridges, invoke model providers, or manage interactive agent sessions.

## Code responsibilities

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `orchestration/projects.py` | Validate project charters, fixed team rosters, agent roles, and reporting hierarchies. | Connection policy |
| `orchestration/plans.py` | Validate task IDs, dependencies, capabilities, inputs, and canonical resource claims before submission. | Connection policy |
| `orchestration/storage.py` | Store goals, tasks, attempts, receipts, and events in SQLite; hold a process-level data-directory lock. | SQLite and filesystem |
| `orchestration/scheduling.py` | Pure functions for dependency readiness, fair dispatch among goals, resource conflicts, and goal state. | Task records only |
| `orchestration/engine.py` | Coordinate submission, scheduling, worker lifecycle, cancellation, and persistence. | Plans, store, scheduler, executor |
| `orchestration/execution.py` | Perform local operations and return acceptance evidence and effect status. | Connection policy |
| `orchestration/connections.py` | Identify the local target, check supported capabilities, and constrain artifact paths. | Filesystem |
| `app.py` | Authenticated, loopback-only JSON API. | Engine |
| `orchestration/__main__.py` | Run a plan from the command line and print its result. | Engine |

## Trace one task

1. Create a project through `Engine.create_project` or `POST /api/projects`, supplying its charter, roster, and concurrency limit. Submit a goal with that project ID and explicit agent assignments through `Engine.submit` or `POST /api/goals`.
2. `parse_tasks` validates the graph and each assigned agent's capabilities. File paths are scoped to the project. File resources derive from canonical paths, so two aliases cannot bypass coordination by using different labels.
3. The store persists queued tasks and their dependencies.
4. `select_work` returns eligible tasks within worker capacity. It balances projects and goals while respecting overall, project, agent, and target limits. Writers exclude other readers and writers of the same resource.
5. The engine persists an attempt record **before** starting its worker.
6. The executor receives the project charter and assigned agent identity/role in its operation context, checks target capability, and performs the operation. Independent task groups can proceed while dependents wait.
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
python3 -m orchestration examples/backend_goal.json --project examples/greenfield_project.json --execution-limit 4 --data-dir .runtime/example
python3 -m unittest discover -s tests -v
python3 tools/local_smoke.py
```

The smoke test starts a real local API process, creates two projects with different team sizes, checks task execution and capacity against recorded intervals, verifies artifacts, and restarts the service to verify persistence. It saves a report under `.runtime/local-smoke-*/report.json` and stops its own process afterward. It uses the implemented local adapters; it does not test model providers or remote bridges.

The plan command returns JSON with goals, tasks, attempts, events, and receipts. Outputs remain under the selected directory's `artifacts/` folder. Using `--project` creates a new project and goal on each run; use `--project-id ID` to submit another goal to an existing team. Submission deduplication is not implemented.

For the JSON service:

```sh
python3 app.py --port 8765 --execution-limit 4
```

The service writes a private bearer credential to `.runtime/api-token` with mode `0600`, replacing it on restart. Clients provide it in `Authorization: Bearer …`. Do not commit this file or place the credential in a URL. API routes require the local credential. The local frontend shell bootstraps that credential into memory through its HTML response; it is not saved in browser storage. Static assets are served from an explicit allowlist.

| Method | Route | Body or result |
| --- | --- | --- |
| GET | `/` | Local JavaScript workspace shell, with a per-run session credential. |
| GET | `/assets/...` | Allowlisted frontend modules and styles. |
| POST | `/api/projects` | Body shaped like `examples/greenfield_project.json`; initializes a charter and assigned team. |
| GET | `/api/projects` | Persistent project definitions and assigned agent records. |
| POST | `/api/goals` | Body shaped like `examples/backend_goal.json`, plus `project_id`; returns the created goal ID. |
| GET | `/api/state` | Projects, agents, goals, tasks, attempts, events, and execution capacity. |
| POST | `/api/cancel` | `{ "id": "goal-id" }`; requests cancellation. |
| GET | `/api/artifact/task-id` | Text artifact referenced by a completed task receipt. |

The API is for a trusted local client, not multi-user network hosting. Authorization does not sandbox the Python process against a local account that already controls it.

## Team size and execution capacity

Team size is the persistent roster length and must match `team_size` on creation. Every task names a project-local agent key. Agent records include role, instructions, responsibilities, capabilities, reporting relationship, and per-agent `max_concurrency`.

The overall `execution_limit` is mandatory; there is no baked-in worker count. Project and agent concurrency limits are explicit in the project definition. `target_limits` can further constrain a machine; when omitted, the local target inherits the supplied overall limit.

A project with three agents can allow one, two, or three simultaneous tasks according to its limits. Idle agents remain assigned. These are logical agent identities and work assignments; creation does not launch three model processes. Instructions and reporting hierarchy are provided as execution context, but autonomous delegation and model-driven planning are not implemented.

Files live under `artifacts/projects/<project-id>/`. Identical relative filenames in separate projects do not collide. The artifact directory remains a local filesystem boundary, not a separate operating-system sandbox.

## Recovery and limitations

- Project charters, agent rosters, reporting relationships, and task ownership persist.
- Existing pre-project records remain inspectable; queued legacy tasks are blocked until resubmitted to an explicit project, never silently assigned to invented agents.
- Queued work persists. Accepted work remains accepted on restart.
- Interrupted attempts become failed and are **not automatically replayed**; a prior effect may already have occurred. Dependents become blocked.
- Cancellation reaches active worker signals and prevents queued dispatch. A completed file write cannot necessarily be undone; retain its receipt.
- One engine owns a data directory at a time. Multiple independent directories are allowed.
- There is no retry policy, distributed scheduler, priority/deadline enforcement, remote authentication, or long-running interactive agent-session adapter yet.
- File operations defend ordinary path traversal and known symlink escapes. The local artifact directory must remain controlled by the service account; this is not a sandbox against a hostile process modifying paths concurrently.

The larger [three-part design](architecture.md) remains a roadmap. Implemented behavior is limited to the operations and tests described here.
