# Orchestration patterns

These patterns can be combined. Start with the least coordination that meets the workload.

| Pattern | Useful when | Failure to watch |
| --- | --- | --- |
| Single worker with tools | Work is tightly coupled and fits one execution context. | Long context or excessive tool surface. |
| Fixed pipeline | Stages and acceptance checks are predictable. | An early error propagates unchecked. |
| Coordinator and specialists | Work can be decomposed into distinct bounded contributions. | Coordinator overload or underspecified briefs. |
| Parallel exploration and synthesis | Independent searches or alternatives can improve coverage. | Duplicated effort and unsupported merged conclusions. |
| Producer and verifier | Outputs admit meaningful independent checks. | Reviewer repeats the producer's assumptions. |
| Shared task board | Workers pull eligible tasks from multiple goals. | Races, starvation, and abandoned ownership. |
| Hierarchical coordination | Large goals have independently managed subgraphs. | Summaries lose constraints or responsibilities become unclear. |

## Proposed default for a multi-goal workspace

Use a portfolio scheduler above per-goal coordination. Keep a durable task graph and artifact store shared at the system level; give workers only the context and permissions their tasks need.

The portfolio scheduler handles fairness, resource conflicts, and global budgets. A goal coordinator handles decomposition, changing plans, integration, and goal acceptance. A worker handles a bounded attempt. One process or agent may fill several roles initially; separate responsibilities before adding infrastructure.

## Independence test before parallel dispatch

Can the tasks run against stable inputs, produce separately reviewable outputs, and avoid conflicting effects? If not, add dependencies, isolate outputs, or serialize the contested operations. Parallel brainstorming does not imply parallel writes to the same artifact are safe.

## Interoperability

A2A addresses communication between independent agent applications; MCP addresses access to tools and resources. Neither protocol, by itself, supplies this repository's proposed fairness policy, acceptance process, or resource ownership guarantees. Integrations should map capabilities and task status explicitly rather than assume protocol compatibility proves coordination correctness. See [research](research.md).
