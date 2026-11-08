# Multi-goal example

`multi_goal.json` describes two simultaneous goals: research orchestration approaches and prepare a release. Three workers share a task board; each has capacity one.

1. `research_sources` and `implement` run concurrently with separate resources.
2. `synthesize` and `test` consume accepted outputs from the first batch.
3. `release_notes` combines research and implementation evidence and writes the shared release document.
4. `verify_release` reads that document after the write and checks integration.

Each task has an owner and acceptance criteria. Read and write claims describe concurrency constraints, not operating-system permissions. Earlier batches are assumed fully accepted before the next batch begins; this simple representation intentionally leaves scheduling opportunities on the table.

Try moving `verify_release` into the third batch: validation should reject both the unsatisfied dependency and the overlapping write/read claim on `release-document`.

Passing validation proves only the documented structural properties. It does not establish source accuracy, test quality, successful execution, or goal acceptance.
