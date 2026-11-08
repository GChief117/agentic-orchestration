# Coordination specification — draft v0.1

Implementation status: a limited local backend now exists; see [backend implementation](backend.md) for the exact supported subset. The broader contracts below remain proposed.

This document proposes observable semantics. It does not mandate an agent framework, storage system, model, or network protocol. The accompanying JSON format covers only a small static planning subset of this document; runtime semantics are not implemented.

This is the orchestration part of the [three-part architecture](architecture.md). Execution and connection/permissions have separate proposed contracts.

## Entities

| Entity | Meaning |
| --- | --- |
| Goal | Desired outcome, acceptance criteria, constraints, priority, budget, and revision. |
| Task | Durable work item belonging to a goal, with dependencies, owner, output contract, and acceptance criteria. |
| Attempt | One bounded execution of a task by a worker, with an identity, input revision, and outcome. |
| Agent | Worker capability and availability; identity alone does not confer permissions. |
| Artifact | Versioned output referenced by identity and revision or content digest. |
| Evidence | Observations or checks supporting a claim, with provenance and time. |
| Resource | Something requiring coordinated access, such as a file, browser session, or service quota. |
| Event | Recorded state change, with event ID, subject, revision, timestamp, and cause. |

## Task lifecycle

```text
draft -> ready -> running -> verifying -> accepted
                     |          |
                     v          v
                   blocked    ready (another attempt required)
                     |
                     v
                   ready
```

Additional transitions:

- `draft -> cancelled`; `ready -> cancelled`; `blocked -> cancelled`.
- `running -> ready` after a recoverable attempt failure and safe resource reconciliation.
- `running -> failed` or `verifying -> failed` when the retry policy is exhausted.
- `running -> cancelling -> cancelled`; `verifying -> cancelling -> cancelled`.
- `verifying -> blocked` when acceptance requires unavailable evidence or a human decision.
- `accepted -> ready` after explicit invalidation by a changed input or goal revision; old acceptance remains in history.

`failed` and `cancelled` terminate this task instance. Retrying them requires an explicit reopen event or a replacement task, not silent execution.

`ready` means dependencies are accepted at the required revisions and inputs are available. It does not mean a worker is currently free. Resource contention belongs in scheduler status, not automatically in `blocked`.

`blocked` requires a reason and an unblock condition. `accepted` requires acceptance evidence. A completed agent response is an attempt outcome, not an acceptance event.

## Scheduling across goals

A scheduling decision considers dependency readiness, capability, worker capacity, resource access, remaining budget, priority, deadline, and time waiting. Implementations should state their policy and expose why a ready task is waiting.

Start with bounded priority scheduling plus aging: tasks waiting longer gain scheduling weight. Apply a per-goal concurrency cap so one large goal cannot consume every worker. These are proposals; workload-specific alternatives should be compared experimentally.

A budget reservation precedes dispatch. Settle actual usage afterward and release unused reservations. Cancellation stops new reservations and requests cooperative termination of active attempts. A reservation limits admission; hard spending limits also require enforcement at the executor or provider boundary.

## Ownership and resources

Task ownership and execution are distinct. A coordinator may own a task while a worker executes an attempt. Ownership transfer is recorded atomically with a revision check.

Concurrent resource claims conflict if they address the same declared resource and at least one is a write. Concurrent reads are permitted. Resource names must cover the actual effect: overlapping paths, aliases, and shared browser state cannot be hidden behind different names.

Runtime leases should expire and carry monotonically increasing fencing tokens. A stale worker must be rejected at the write boundary after a lease expires or ownership changes. Merely telling the model that its lease expired is insufficient. Acquire required leases atomically or in a consistent order; release them on completion and reconcile uncertain effects before reassignment.

## Retries, cancellation, and recovery

Persist attempt identity and intended operation before performing an external effect. Use a stable operation ID across retries of the same effect when the destination supports idempotency. A timeout means the outcome may be unknown; query or reconcile before repeating the operation. Do not promise exactly-once execution across arbitrary external systems.

Cancellation is a request until acknowledged or enforced. Fence writes and reconcile in-flight actions before declaring the task cancelled. Record residual effects that cannot be undone.

On restart, recover durable task and attempt state, inspect active leases, reconcile uncertain side effects, and dispatch only eligible work. Recovery must not assume every interrupted attempt failed before acting.

## Replanning and invalidation

A plan change increments its revision. Dispatch records which revision the attempt used. New evidence, changed user intent, or invalidated inputs can require rechecking downstream artifacts. Dependents cannot use stale acceptance silently. Preserve completed outputs where they remain valid.

Dynamic task insertion must validate references and reject cycles before making new work schedulable. Detect lack of progress using repeated failures, unchanged artifacts, or exhausted budgets; do not rely only on an agent declaring itself stuck.

## Acceptance and integration

Task acceptance records criteria checked, evidence, checker identity, artifact revisions, and input revision. Goal acceptance additionally checks integration: individually valid outputs can still contradict each other.

Use deterministic checks where possible and independent review where useful. Separate review context from the producing agent's unsupported conclusions. If evidence is insufficient, record uncertainty rather than manufacture a pass.

## Minimum observable record

Expose goal and task IDs, ownership, state, dependencies, active attempt, input revision, output references, budget usage, blocking reason, next action, and acceptance evidence. Record state changes as durable events. Audit records should describe actions and decisions; they need not contain private model reasoning or secrets.

## Static example format

Top-level keys: `version` (currently `0.1`), `goals`, `agents`, `tasks`, `batches`. Every goal and agent has an `id`; goals include `acceptance`; agents include positive integer `capacity`. Each task has `id`, `goal`, `owner`, `depends_on`, nonempty `acceptance`, and `resources` containing `{name, mode}` with mode `read` or `write`.

Every task appears exactly once in `batches`, a list of nonempty task-ID lists. Tasks in one batch are declared concurrent; later batches assume all earlier tasks have been accepted. Dependencies must therefore be in strictly earlier batches. Capacity and resource claims are checked per batch. This format cannot represent dynamic retries, partial completion, or cancellation; it is an illustrative plan, not a runtime protocol.
