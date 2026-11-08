# Research and prior art

Sources consulted November 8, 2026. This is a selective starting bibliography, not a systematic literature review. Papers, engineering reports, and protocol documentation provide different kinds of evidence.

| Source | Contribution | Limits and relationship to our proposal |
| --- | --- | --- |
| [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents) (2024) | Distinguishes predefined workflows from model-directed agents; describes composable orchestration patterns and starting simple. | Engineering guidance; the page notes that its tooling landscape has changed. It does not establish a universal scheduling standard. |
| [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system) (2025) | Describes parallel research agents, context separation, and evaluation of a deployed system. | Reported gains are specific to its internal evaluation and configuration; not evidence that more agents always help. |
| [Microsoft Research: Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/) (2024) | Uses task and progress ledgers with an orchestrator that can replan when progress stalls. | A particular architecture and benchmark evaluation, not a universal optimum. |
| [Cemri et al.: Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) (2025, v3) | Analyzes 1,600+ annotated traces across seven frameworks and identifies 14 failure modes in design, alignment, and verification. | Studied systems, models, and tasks bound the conclusions; does not exhaust future failure modes. |
| [A2A protocol documentation](https://a2a-protocol.org/latest/) | Describes interoperability between independent agent applications and its relationship to MCP. | Communication compatibility does not establish scheduling, verification, or exactly-once effects. Documentation is mutable. |

## Our synthesis

This repository proposes a common task/attempt model, explicit ownership and acceptance, and coordination across concurrent goals. The concrete contracts, example format, scheduling default, and evaluation suite are design proposals assembled here. They are not endorsed by the authors above.

## Open questions

- When should a coordinator be a model, deterministic code, or a combination?
- How much context should agents share without duplicating noise or losing evidence?
- What is the right ownership and lease granularity for browser sessions and documents?
- How can acceptance remain robust when the evaluator is itself an imperfect model?
- Which scheduling policies balance user priorities, fairness, and cost across unrelated goals?
- What is a portable contract between heterogeneous runtimes without forcing identical internals?
- How should privacy, retention, and authorization propagate through delegated work?

Add primary sources and describe what they actually support. Avoid claims that no prior art exists or that this draft is the first standard.
