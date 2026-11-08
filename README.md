# Agentic Orchestration

**An open proposal for coordinating multiple agents across multiple concurrent tasks.**

Agents can execute work. Orchestration decides which work should happen, who owns it, what can run together, and whether the result actually satisfies the goal.

This repository develops a shared vocabulary, practical patterns, and testable contracts for that coordination. It covers a portfolio of goals and tasks—not only a single prompt delegated to several agents.

**Status: experimental draft v0.1.** This is a community proposal, not an official standard, production runtime, or claim of consensus. Existing research and frameworks already address parts of this problem; our contribution is a coherent, falsifiable starting point for multi-task coordination. The initial draft grew out of the Agent Spaces concept, but is independent of any provider or framework.

## Start here

- [Design principles](docs/principles.md): proposed practices and their tradeoffs.
- [Coordination specification](docs/specification.md): goals, tasks, attempts, ownership, scheduling, and completion.
- [Patterns](docs/patterns.md): when to use different orchestration structures.
- [Evaluation](docs/evaluation.md): how to test whether orchestration helps.
- [Research and prior art](docs/research.md): sources, limitations, and open questions.
- [Roadmap](ROADMAP.md) and [contributing](CONTRIBUTING.md).

## The core idea

```text
Human goals and corrections
            |
   Portfolio scheduler ---- budgets, priority, fairness
            |
       Task graph --------- dependencies, owners, acceptance criteria
            |
    Agent attempts -------- bounded execution, scoped resources
            |
    Evidence + artifacts -- verification, integration, recovery
```

One goal may create many tasks. Tasks from different goals may compete for the same agent, budget, file, browser session, or external service. Those conflicts need explicit coordination.

A task is a durable unit of work. An attempt is one execution of that task. A worker finishing an attempt does not, by itself, mean the task or goal is accepted.

## Try the examples

Requires Python 3.10+; no third-party packages or model credentials.

```sh
python3 tools/validate_plan.py examples/multi_goal.json
python3 -m unittest discover -s tests -v
```

The validator checks the small JSON plan format used here: unique identifiers, references, dependency cycles, acceptance criteria, and declared resource conflicts in explicit concurrent batches. It does **not** execute agents, schedule work, enforce runtime permissions, or prove that a plan is correct.

The [example](examples/multi_goal.json) coordinates a research goal and a release goal with shared resources. [Its walkthrough](examples/README.md) explains the batches and their limits.

## What we want to discover

1. When do additional agents improve accepted outcomes enough to justify their coordination cost?
2. What information must survive every delegation and handoff?
3. How should unrelated goals share scarce resources fairly?
4. How can a human change direction while work is running?
5. Which failure modes require runtime enforcement rather than better prompts?

Contribute a reproducible failure, a measured experiment, a counterexample, or an RFC. A pattern earns a stronger recommendation through evidence—not its inclusion in this repository.

## License

MIT. See [LICENSE](LICENSE).
