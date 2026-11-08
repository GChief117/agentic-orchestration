# One workflow through three parts

This walkthrough is a design example, not executable instructions for a live bridge.

Suppose a user wants a research report and a tested code change at the same time. A local service and a remote SSH target have been configured privately. Their public profile shapes are illustrated in [environment_profiles.json](environment_profiles.json).

1. **Orchestration** creates two goals, decomposes them into tasks, and assigns one owner per task. It can reuse the dependency pattern in [multi_goal.json](multi_goal.json).
2. **Connection and permissions** verifies each target and determines which capabilities the current principal may use. The remote target's shell access does not grant access to local files.
3. **Execution** starts a research agent session on one target and a terminal job on the other. Each receives a task-linked attempt ID, an operation ID, and bounded inputs.
4. **Orchestration** tracks independent progress. It serializes edits to a shared final document and avoids treating agent output as automatic acceptance.
5. **Execution** returns artifacts and receipts. Transfer verifies artifact identity; checks verify research provenance and code behavior.
6. **Orchestration** accepts individual tasks after checking evidence, then verifies that the combined deliverable satisfies both goals.

## If the remote connection drops

Connection state changes to unavailable. Orchestration pauses dispatch to that target but independent local work can continue. After the target identity and permissions are revalidated, execution reconciles the existing operation ID. The coordinator must not infer failure and start a duplicate job elsewhere.

## If the human changes direction

Orchestration records a new goal revision and invalidates affected work. Connection and permissions rejects revoked authority; execution stops or fences outdated operations where supported. Unaffected artifacts remain usable after their inputs are checked.

## What is implemented today

The static plan validator checks the example task graph, ownership references, capacity, and declared batch conflicts. It does not connect to either environment, launch these jobs, enforce permissions, or test this recovery behavior.
