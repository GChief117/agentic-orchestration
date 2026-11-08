# Proposed capability catalog

Implementation status: a limited local backend now exists; see [backend implementation](backend.md) for the exact supported subset. The broader contracts below remain proposed.

This catalog describes intended coverage for a neutral public design. Every broad capability family is **proposed**; a small local subset is implemented and tested as documented separately. An adapter advertises only the subset it actually supports and tests.

| Capability family | Proposed operations | Required distinction |
| --- | --- | --- |
| Target discovery and health | Inspect target identity, transport state, capabilities, and limits. | Healthy connection is not proof of job completion. |
| Agent sessions | Create/list sessions, send input, retrieve output, close owned sessions. | Provider support and credentials are independently configured. |
| Terminal jobs | Start a command, inspect status, retrieve bounded logs, provide input, request cancellation. | Explicit shell authority versus structured process arguments. |
| Files | List, read, write, edit, inspect revision, and verify integrity. | Requested write versus confirmed content at the expected revision. |
| Artifact transfer | Upload/download artifacts with size limits and integrity checks. | Transferred bytes versus application acceptance. |
| Browser operations | Inspect pages, navigate, interact, capture observations, manage supported contexts. | Dedicated context versus an existing authenticated user session. |
| Desktop operations | Observe screen, send bounded input, claim/release exclusive control. | Shared native input versus isolated desktop environment. |
| Isolated workspaces | Create/inspect/close temporary environments and export outputs. | Advertised isolation and persistence versus assumptions about sessions. |
| Worker coordination | Session heartbeats, task-linked messages, ownership, cancellation signals. | Liveness and communication versus accepted task progress. |
| Provider routing | Select an explicitly configured model/provider endpoint. | Routing does not grant account access or imply provider affiliation. |
| Service lifecycle | Inspect version, stage/validate an upgrade, activate or roll back where supported. | Administrative authority versus ordinary execution permission. |
| Audit and recovery | Correlated receipts, reconnect, reconcile operation outcomes, bounded event history. | Known failure versus unknown effect after interruption. |

## Initial implementation priorities

1. Define a shared receipt and capability-discovery contract.
2. Implement target verification, terminal jobs, file operations, and artifact transfer for one neutral adapter.
3. Add agent-session lifecycle and browser operations against controlled fixtures.
4. Evaluate desktop isolation, provider routing, and administrative lifecycle separately before expanding authority.

These are future implementation priorities. Existing private tools are not dependencies of this repository and their features are not automatically available here.
