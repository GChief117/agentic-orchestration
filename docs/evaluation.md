# Evaluation plan

Practices remain provisional until tested. Publish unsuccessful experiments and workload limitations as well as improvements.

## Baselines

Compare a single capable agent, a fixed workflow where appropriate, and the proposed orchestration strategy. Record model versions, prompts, tools, environment, concurrency, budget, and acceptance checks. Compare both matched total budget and matched wall-clock deadline; extra computation is a confounding variable.

Repeat runs, publish sample counts and variability, and distinguish automated checks from human ratings. Use held-out tasks when tuning policies. Do not interpret one provider's internal benchmark gain as a general multi-agent advantage.

## Measures

- Accepted goal rate and independently checked artifact quality.
- End-to-end latency and time spent waiting for dependencies or resources.
- Total model/tool cost and coordinator share of that cost.
- Rework, duplicated effects, invalidated artifacts, and human interventions.
- Maximum and percentile waiting time by goal or priority class.
- Recovery success and cancellation latency, including residual effects.

## Initial scenario suite

| Scenario | Required observation |
| --- | --- |
| Independent research tasks | Coverage and quality versus equal-budget single-agent work. |
| Two goals editing one shared artifact | Conflicting writes cannot commit concurrently; integrated result preserves both goals. |
| Worker crashes after an external write | Recovery reconciles the operation instead of blindly duplicating it. |
| User changes a requirement mid-run | Affected tasks are invalidated; outdated attempts cannot commit accepted results. |
| High-priority work arrives continuously | Lower-priority work still progresses under the declared fairness policy. |
| Dependency evidence becomes invalid | Downstream acceptance is rechecked or withdrawn explicitly. |
| Agent reports success with a wrong artifact | Acceptance checks reject the result. |
| Resource lease expires during execution | A stale worker's write is rejected by the resource boundary. |
| Cancellation races with dispatch | No new attempt starts after cancellation is committed; in-flight effects are reported. |

These are proposed runtime experiments. The included validator tests only static plan consistency; passing its tests is not evidence that these runtime scenarios pass.

## Experiment report template

State the hypothesis, workload, baseline, policy, permissions, budget, number of runs, acceptance checks, measured results, failure examples, and limits to generalization. Include sanitized inputs and artifacts sufficient to reproduce results.
