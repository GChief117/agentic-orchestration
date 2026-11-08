# Agentic Orchestration

**A backend foundation and open design for coordinating multiple agents across concurrent tasks.**

Agents can execute work. Orchestration decides which work should happen, who owns it, what can run together, and whether the result actually satisfies the goal.

This repository develops a shared vocabulary, practical patterns, and testable contracts for that coordination. It covers a portfolio of goals and tasks—not only a single prompt delegated to several agents.

**Status: experimental backend and draft design v0.1.** This is not an official standard, production-ready service, or claim of consensus. Existing research and frameworks already address parts of this problem; our contribution is a coherent, falsifiable starting point for multi-task coordination. The design is independent of any provider, organization, private bridge installation, or framework. Research citations identify sources and do not imply affiliation or endorsement.

## Run the backend

Python 3.10+ on macOS or Linux; no third-party runtime dependencies.

```sh
python3 -m orchestration examples/backend_goal.json --project examples/greenfield_project.json --execution-limit 4 --data-dir .runtime/example
python3 app.py --port 8765 --execution-limit 4
```

The first command runs an explicit task graph and prints its result. The second starts the JavaScript project workspace and its authenticated JSON API at `http://127.0.0.1:8765`. The backend initializes projects with charters and assigned agent teams, and persists goals, tasks, attempts, and receipts, enforces dependencies and resource conflicts, and executes bounded local operations. Remote bridges and model execution adapters are not connected yet. The Model connections dialog can launch sign-in through installed official provider tools; those accounts are not yet used to execute tasks. See the [backend implementation](docs/backend.md) for the module map, request flow, API, and limitations.

## JavaScript frontend

The frontend lives separately in [`frontend/`](frontend/README.md) and uses plain JavaScript modules and CSS. It supports project charters, editable agent rosters, goal and dependency creation, task inspection, artifacts, cancellation, and live state refresh. It connects to the same local backend; no bundler or JavaScript runtime is required to serve it.

For development checks:

```sh
npm ci --prefix frontend
npm test --prefix frontend
```

## Provider sign-in

Open **Model connections** in the workspace header. On macOS, the sign-in buttons open installed Codex CLI, Claude Code, or Gemini CLI in Terminal, where you finish the provider’s own browser flow. The app does not receive, copy, or store OAuth tokens. Install missing tools using the setup guides linked in the dialog. Other operating systems show the manual sign-in commands.

Refresh status after signing in. OpenAI and Claude status comes from their CLI status commands. Gemini remains **unverified** because this integration has no reliable status command for it. A successful launch is not proof of authentication, and authenticated tools are not yet wired into the task executor. Existing API-key authentication in a tool may also appear as signed in; this is not an account identity check.

The routes `GET /api/providers` and `POST /api/providers/sign-in` require the same local session and origin protections as the rest of the API. Sign-in launches only after an explicit request. Generated launcher scripts contain only fixed commands and live in the ignored runtime directory.

Official references: [Codex authentication](https://developers.openai.com/codex/auth), [Claude Code authentication](https://code.claude.com/docs/en/authentication), [Gemini CLI authentication](https://geminicli.com/docs/get-started/authentication/).

## Three parts, one repository

| Part | Responsibility | Design |
| --- | --- | --- |
| **Orchestration** | Coordinate agents and tasks, dependencies, priorities, ownership, budgets, and outcomes. | [Coordination specification](docs/specification.md) |
| **Execution** | Describe agent sessions, terminal jobs, files, browsers, desktops, and artifact transfer through capability-based adapters. | [Execution contract](docs/execution.md) |
| **Connection and permissions** | Identify targets, authenticate connections, scope access, isolate sessions, and recover safely. | [Connection design](docs/connections.md) |

The [architecture](docs/architecture.md) explains how these parts cooperate. The [capability catalog](docs/capabilities.md) describes intended coverage; these adapter capabilities are proposals, not working integrations. The initial neutral profiles are **local service** and **remote SSH**. Existing private bridges remain outside this repository and unchanged.

This is a public-facing design prepared in a private repository. Repository visibility and the MIT license are separate: the license does not make a private GitHub repository public.

## Start here

- [Working backend](docs/backend.md): code organization and how modules coordinate.

- [Three-part walkthrough](examples/three_part_walkthrough.md): concurrent work across neutral environments.
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
   Project charter + assigned agent team
            |
   Portfolio scheduler ---- budgets, priority, fairness
            |
       Task graph --------- dependencies, owners, acceptance criteria
            |
    Agent attempts -------- bounded execution, scoped resources
            |
    Evidence + artifacts -- verification, integration, recovery
```

Each greenfield project begins with a charter and an explicit agent roster. Team size stays separate from execution concurrency; every task names an assigned project agent.

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
