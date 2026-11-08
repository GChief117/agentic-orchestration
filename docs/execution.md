# Execution adapter contract

Implementation status: a limited local backend now exists; see [backend implementation](backend.md) for the exact supported subset. The broader contracts below remain proposed.

Status: proposed interface semantics; only the local subset documented in the backend implementation is available.

## Capability discovery

An adapter identifies itself, its protocol version, target environment, supported capability IDs, operation limits, and relevant isolation properties. Capabilities are explicit. Reachability does not imply support, and support does not imply permission to use a capability.

Capabilities unavailable on the selected target return `unsupported`; denied operations return `denied`. Neither condition permits automatic execution on another machine. Target substitution requires an explicit orchestration decision within the user's authority.

## Request envelope

Proposed requests carry:

| Field | Purpose |
| --- | --- |
| `operation_id` | Stable identity for one intended operation; reused only for reconciliation or supported idempotent retry of that same operation. |
| `goal_id`, `task_id`, `attempt_id` | Link execution to accountable work and its current attempt. |
| `target_id`, `capability` | State exactly where the operation belongs and what it requires. |
| `session_ref` | Reference an authenticated, scoped session without embedding its secret in the plan. |
| `input_revision`, `policy_revision` | Detect outdated plans or permissions before effects occur. |
| `resource_claims` | Declare contested resources and any required fencing tokens. |
| `parameters` | Capability-specific validated inputs. |
| `limits` | Time, output size, and other enforceable bounds. |

Do not pass serialized commands through extra shell evaluation layers. A terminal adapter should use an argument vector where possible; explicit shell execution is a distinct capability with the authority of the target account. Application-level resource locks cannot constrain an unrestricted shell by themselves.

## Operation receipt

Return operation identity, verified target identity, state, timestamps, optional job handle, structured error, output/artifact references, and effect status (`none`, `confirmed`, or `unknown`). A completed command should include its exit status. An artifact reference should include enough identity and integrity information to verify the retrieved bytes.

An accepted request means dispatch was accepted. It does not mean a page changed, an upload was accepted by a server, or a task's acceptance criteria passed. A browser operation should return a fresh observation when available; the coordinator checks the intended result separately.

## Long-running work

Start returns a job handle. Separate operations inspect state, retrieve bounded output, provide input when supported, and request cancellation. The adapter records whether jobs survive client disconnect, service restart, and session expiry. These are advertised properties, not universal guarantees.

Agent sessions similarly support create, inspect, send input, read output, and close where available. Provider-specific behavior stays inside the adapter. Session continuation is different from rerunning a task after failure.

## Concurrency and isolation

Independent browser contexts may run in parallel if the adapter supports them. A shared desktop's input stream requires exclusive ownership. Isolated desktop environments must declare their actual separation and artifact-export lifecycle; separate sessions alone do not establish isolation.

File writes should support expected-revision or digest checks where feasible. Transfer should verify bytes and keep partial outputs distinguishable from complete artifacts. Runtime resource enforcement must cover aliases and every supported write route.

## Failure and retry

A timeout or disconnect returns an unknown outcome when completion cannot be established. Reconcile the existing operation before repeating an effect. Deduplication support must specify retention and behavior when the same operation ID arrives with different parameters; conflicting reuse must be rejected.

Cancellation is acknowledged separately from its request. Report whether the process stopped, what effects occurred, and which resources were released. Preserve evidence needed for task recovery without recording credentials or private session contents unnecessarily.

## Conformance work still required

Future adapters need tests for capability rejection, target identity, permission expiry, job recovery, cancellation, concurrent resource use, artifact integrity, and ambiguous effects. The current static validator does not test these behaviors.
