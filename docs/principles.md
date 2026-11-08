# Proposed design principles

Status: draft. These are hypotheses and engineering recommendations, not established universal best practices. Each should be evaluated against the workload.

| Principle | Operational rule | Tradeoff / test |
| --- | --- | --- |
| Define outcomes first | Goals declare acceptance criteria and constraints before execution. | Criteria may evolve; record revisions rather than silently moving the target. |
| Delegate bounded work | Give each task an objective, inputs, output contract, permissions, and budget. | Excessively small tasks make coordination more expensive than execution. |
| One accountable owner | Each task has one responsible owner at a time; attempts have separate execution identities. | Avoid making one owner a bottleneck for unrelated work. |
| Add agents deliberately | Delegate for parallelism, context separation, capability, or independent checking. | Compare with a single-agent baseline using matched budgets. |
| Share evidence selectively | Share durable artifacts and provenance; provide task-relevant context. | Over-compression can hide important uncertainty. |
| Separate execution from acceptance | Record a result before verifying task criteria. | Independent review is useful only when it adds a meaningful check. |
| Coordinate writes explicitly | Use isolated artifacts or resource leases, followed by deliberate integration. | A lock without expiry and fencing can create deadlock or stale writes. |
| Treat changes as events | Record corrections, cancellations, invalidations, and ownership changes. | Event histories need retention and privacy policies. |
| Recover without duplicating effects | Distinguish retries from new tasks; use operation IDs for external effects. | Not every external API supports safe retries; ambiguous outcomes need reconciliation. |
| Allocate fairly | Bound concurrency and spending; consider priority, deadlines, and starvation. | Fairness may reduce throughput for a high-value goal. |
| Expose actionable status | Show owner, state, next action, evidence, and required human input. | Showing every internal message can overwhelm users. |
| Measure useful work | Measure accepted outcomes, latency, cost, rework, and intervention. | Fast completion is meaningless if acceptance is weak. |

## A delegation brief

A useful brief answers:

- What result is needed, and how will it be checked?
- What inputs and decisions are authoritative, and at which revision?
- Which resources may be read or changed?
- What is the deadline or execution budget?
- Which artifacts and evidence must be returned?
- What should happen if the task cannot proceed?

## A handoff record

Record task and attempt IDs, output artifact references and revisions, evidence references, unresolved questions, side effects already performed, and the next responsible owner. Claims, assumptions, and verified observations should be distinguishable.

## Human authority

A user correction updates the relevant goal revision. The coordinator identifies affected tasks, stops or fences outdated writes, and decides which completed outputs need revalidation. Ask the human only for a specific missing decision that materially changes the outcome or authority to act.
