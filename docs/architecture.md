# Three-part architecture

Implementation status: a limited local backend now exists; see [backend implementation](backend.md) for the exact supported subset. The broader contracts below remain proposed.

Status: public-facing design proposal. The repository implements a static plan validator and a limited local backend, not the full execution and connection layers described here. The design is independent of any existing private installation, organization, or agent provider.

## 1. Orchestration

Orchestration turns human goals into accountable work across many concurrent tasks. It owns task graphs, priority, dependencies, assignment, budgets, resource reservations, acceptance, and replanning.

Its primary records are goals, tasks, attempts, artifacts, evidence, and events. A task survives worker replacement; an attempt identifies one execution. See the [coordination specification](specification.md).

## 2. Execution

Execution adapters translate bounded work into operations on a selected environment. The proposed capability families cover agent sessions, terminal jobs, files, browsers, desktop interaction, and artifact transfer. Adapters report what they actually support. A remote terminal adapter need not offer desktop control; a browser adapter need not offer a shell.

Adapters report job handles, observations, outputs, errors, and uncertain effects. They do not decide whether the human's goal is satisfied. See the [execution contract](execution.md) and [capability catalog](capabilities.md).

## 3. Connection and permissions

This layer identifies the target environment, establishes an authenticated transport, resolves private credentials, scopes operations, manages session ownership, and revokes access. Policies are checked where operations execute, not only in an agent's instructions.

It keeps connection liveness separate from job state. A lost connection cannot establish whether a remote operation completed. See [connection and permissions](connections.md).

## Relationship

```text
Human goals, constraints, and corrections
                    |
          1. ORCHESTRATION
   goals -> tasks -> bounded attempts -> acceptance
                    |
    3. CONNECTION AND PERMISSIONS
   target identity + access checks + scoped session
                    |
             2. EXECUTION
   agent | terminal | files | browser | desktop
                    |
          target environment
                    |
      receipts, artifacts, observations
                    |
          orchestration verifies
```

The numbered parts name responsibilities, not deployment order. Access checks occur on every applicable execution path. All three may initially live in one process; their responsibilities remain distinguishable.

## Interfaces between the parts

| Boundary | Information exchanged | Required behavior |
| --- | --- | --- |
| Orchestration to connection | Target ID, task/attempt identity, requested capabilities, policy revision. | Resolve and verify the intended environment; deny unsupported or unauthorized requests. |
| Orchestration to execution | Operation ID, inputs, limits, input revision, scoped session, resource claims. | Validate at execution time and return an operation receipt. |
| Execution to orchestration | Job state, artifact references, observations, effects, errors. | Distinguish dispatched, running, completed, failed, cancelled, and unknown outcomes. |
| Connection to orchestration | Available capabilities, identity, expiry, revocation, connectivity. | Do not represent connectivity as task success or absence of effects. |

## Neutral environment profiles

The public design begins with two profiles, described by capability rather than the names of existing installations:

- **Local service:** operations exposed by an authenticated service on a user-controlled machine; may advertise agent sessions, terminal jobs, files, and optional browser or desktop capabilities.
- **Remote SSH:** command execution and file transfer through an authenticated SSH connection; agent or browser helpers are additional advertised capabilities.

The [illustrative profiles](../examples/environment_profiles.json) contain no live hostnames, credentials, or installation paths. They are design examples, not deployable configuration or verified integrations.

## Scope and independence

This repository provides a neutral design and examples. It does not bundle, rename, replace, or configure existing private bridges. It claims no sponsorship, affiliation, or endorsement by organizations or projects whose products could be used to implement an adapter. Research citations identify sources, not affiliations.

Publishing the repository later would not publish any private environment configuration. Third-party code, if added, requires a provenance and license review; neutral naming does not remove attribution obligations.
