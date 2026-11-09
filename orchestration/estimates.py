"""Read-only goal forecasts from measured durations and the actual scheduler policy.

Ranges are heuristics, not completion guarantees. Before duration history exists,
the upper result is an execution-limit allowance rather than a predicted ETA.
"""

from collections import defaultdict
from dataclasses import dataclass
import math
from statistics import median
import time

from .scheduling import CapacityLimits, goal_state, select_work


MAX_HISTORY = 1000
MAX_SAMPLES = 50
MAX_PENDING = 256
TERMINAL = {"accepted", "failed", "blocked", "cancelled"}
PENDING = {"queued", "running", "cancelling"}


@dataclass(frozen=True)
class _Duration:
    low: float | None
    middle: float | None
    high: float | None
    basis: str
    samples: int = 0
    exact: bool = False


@dataclass(frozen=True)
class _Simulation:
    goals: dict
    starts: dict
    finishes: dict


def _number(value):
    """Return finite nonnegative numeric metadata without accepting booleans."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _duration_seconds(task):
    """Read successful measured wall time, preferring persisted task timestamps."""
    start, finish = task.get("started"), task.get("finished")
    if _number(start) and _number(finish) and finish > start:
        return finish - start
    value = (task.get("receipt") or {}).get("duration_ms")
    return value / 1000 if _number(value) and value > 0 else None


def _keys(task, agents):
    """Build exact and progressively broader timing families for an assigned task."""
    agent = agents.get(task.get("agent_id"), {})
    receipt = task.get("receipt") or {}
    capability = task.get("capability")
    mode = (
        receipt.get("execution_mode", agent.get("execution_mode", "api"))
        if capability == "model.generate"
        else "local"
    )
    family = (capability, mode)
    provider = (*family, receipt.get("provider", agent.get("provider")))
    exact = (
        *provider,
        receipt.get("model", agent.get("model")),
        receipt.get("execution_speed", agent.get("execution_speed", "standard")),
    )
    return exact, provider, family


def _history(tasks, agents):
    """Keep bounded recent successful timing samples, excluding failed attempts."""
    records = [task for task in tasks if task.get("state") == "accepted"]
    records.sort(key=lambda task: task.get("finished") or 0, reverse=True)
    history = defaultdict(list)
    for task in records[:MAX_HISTORY]:
        duration = _duration_seconds(task)
        if duration is None:
            continue
        for key in _keys(task, agents):
            if len(history[key]) < MAX_SAMPLES:
                history[key].append(duration)
    return history


def _limit(task, agents, cli_timeout, api_timeout):
    """Use configured adapter deadlines only where an execution timeout exists."""
    if task.get("capability") == "model.generate":
        mode = agents.get(task.get("agent_id"), {}).get("execution_mode", "api")
        value = cli_timeout if mode == "cli" else api_timeout
        return float(value) if _number(value) and value > 0 else None
    if task.get("capability") == "terminal.python_version":
        return 10.0
    return None


def _quantile(values, fraction):
    """Linearly interpolate an empirical quantile from a nonempty sample."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _prediction(task, agents, history, now, cli_timeout, api_timeout):
    """Estimate a task's remaining duration, widening sparse empirical samples."""
    keys = _keys(task, agents)
    selected, exact = [], False
    # Prefer enough exact evidence; use a broader family before relying on one run.
    for index, key in enumerate(keys):
        if len(history.get(key, [])) >= 3:
            selected, exact = history[key], index == 0
            break
    if not selected:
        for index, key in enumerate(keys):
            if history.get(key):
                selected, exact = history[key], index == 0
                break
    limit = _limit(task, agents, cli_timeout, api_timeout)
    elapsed = 0.0
    if task.get("state") in ("running", "cancelling") and _number(task.get("started")):
        elapsed = max(0.0, now - task["started"])
    if not selected:
        return _Duration(
            0.0 if limit is not None else None,
            None,
            max(0.0, limit - elapsed)
            if limit is not None and elapsed < limit
            else None,
            "execution_limit" if limit is not None and elapsed < limit else "unknown",
        )
    observed_count = len(selected)
    # Conditional residuals avoid claiming an already-overdue task has zero left.
    residuals = [duration - elapsed for duration in selected if duration > elapsed]
    if not residuals:
        return _Duration(
            0.0,
            None,
            limit - elapsed if limit is not None and elapsed < limit else None,
            "execution_limit" if limit is not None and elapsed < limit else "unknown",
            observed_count,
            exact,
        )
    spread = 2.0 if observed_count < 5 or not exact else 1.5
    low = max(0.001, _quantile(residuals, 0.25) / spread)
    middle = max(0.001, median(residuals))
    high = max(middle, _quantile(residuals, 0.75) * spread)
    if limit is not None:
        remaining_limit = limit - elapsed
        if remaining_limit <= 0:
            return _Duration(None, None, None, "unknown", observed_count, exact)
        high = min(high, remaining_limit)
        middle = min(middle, high)
        low = min(low, middle)
    return _Duration(low, middle, high, "history", observed_count, exact)


def _limits(snapshot):
    """Mirror configured global, project, agent, and target capacities."""
    capacity = snapshot.get("capacity", {})
    execution = capacity.get("execution_limit", 0)
    if not isinstance(execution, int) or isinstance(execution, bool) or execution < 1:
        return None
    return CapacityLimits(
        execution,
        {
            item["id"]: item.get("max_concurrency", 0)
            for item in snapshot.get("projects", [])
        },
        {
            item["id"]: item.get("max_concurrency", 0)
            for item in snapshot.get("agents", [])
        },
        dict(capacity.get("target_limits", {})),
    )


def _simulate(tasks, durations, limits, scenario):
    """Advance scheduler completion events without modifying live tasks or state."""
    simulated = [
        {**task, "resources": list(task.get("resources") or [])} for task in tasks
    ]
    active, starts, finishes = {}, {}, {}
    by_id = {task["id"]: task for task in simulated}
    if any(
        dep not in by_id for task in simulated for dep in task.get("dependencies", [])
    ):
        return _Simulation({}, {}, {})
    for task in simulated:
        task.setdefault("dependencies", [])
        task.setdefault("target", "local-workspace")
        if task["state"] in ("running", "cancelling"):
            duration = getattr(durations[task["id"]], scenario)
            if duration is None:
                duration = durations[task["id"]].high
            if duration is None:
                return _Simulation({}, {}, {})
            active[task["id"]] = duration
            starts[task["id"]] = 0.0
    elapsed = 0.0
    for _ in range(len(simulated) * 2 + 2):
        decision = select_work(simulated, set(active), limits)
        for identifier in decision.blocked:
            by_id[identifier]["state"] = "blocked"
            finishes[identifier] = elapsed
        for task in decision.dispatch:
            duration = getattr(durations[task["id"]], scenario)
            if duration is None:
                duration = durations[task["id"]].high
            if duration is None:
                return _Simulation({}, {}, {})
            by_id[task["id"]]["state"] = "running"
            starts[task["id"]] = elapsed
            active[task["id"]] = elapsed + duration
        if not active:
            break
        elapsed = min(active.values())
        for identifier in [key for key, finish in active.items() if finish <= elapsed]:
            by_id[identifier]["state"] = "accepted"
            finishes[identifier] = elapsed
            del active[identifier]
    results = {}
    goals = {task["goal_id"] for task in simulated if task["id"] in durations}
    for goal_id in goals:
        pending = [
            task
            for task in simulated
            if task["goal_id"] == goal_id and task["id"] in durations
        ]
        if pending and all(task["id"] in finishes for task in pending):
            results[goal_id] = max(finishes[task["id"]] for task in pending)
    return _Simulation(results, starts, finishes)


def _rounded(value):
    """Avoid false subsecond precision in public timing metadata."""
    return math.ceil(max(0, value)) if value is not None else None


def estimate_goals(snapshot, *, now=None, cli_timeout=120, api_timeout=30):
    """Forecast every goal using current work, history, dependencies, and capacity.

    ``execution_limit`` means an initial allowance, not a measured ETA. Empirical
    ranges widen sparse or mixed-provider evidence; they are not confidence bounds.
    If data or scheduling is insufficient, future timing fields remain null.
    """
    now = time.time() if now is None else now
    tasks = snapshot.get("tasks", [])
    agents = {item["id"]: item for item in snapshot.get("agents", [])}
    history = _history(tasks, agents)
    pending = [task for task in tasks if task.get("state") in PENDING]
    durations = {
        task["id"]: _prediction(task, agents, history, now, cli_timeout, api_timeout)
        for task in pending
    }
    limits = _limits(snapshot)
    scenarios = {name: _Simulation({}, {}, {}) for name in ("low", "middle", "high")}
    if limits is not None and len(pending) <= MAX_PENDING:
        scenarios = {
            name: _simulate(tasks, durations, limits, name) for name in scenarios
        }
    any_uncertain_middle = any(item.middle is None for item in durations.values())
    any_limit = any(item.basis == "execution_limit" for item in durations.values())
    strong_history = all(
        item.exact and item.samples >= 5 and item.basis == "history"
        for item in durations.values()
    )
    results = {}
    for goal in snapshot.get("goals", []):
        members = [task for task in tasks if task.get("goal_id") == goal["id"]]
        remaining = [task for task in members if task.get("state") in PENDING]
        state = goal.get("state") or goal_state(task["state"] for task in members)
        terminal = bool(members) and (
            not remaining or state in ("accepted", "failed", "cancelled")
        )
        goal_start = goal.get("created")
        if not _number(goal_start):
            starts = [
                task["started"] for task in members if _number(task.get("started"))
            ]
            goal_start = min(starts) if starts else now
        goal_end = now
        if terminal:
            ends = [
                task["finished"] for task in members if _number(task.get("finished"))
            ]
            goal_end = max(ends) if ends else now
        selected = [durations[task["id"]] for task in remaining]
        observed = [item.samples for item in selected if item.samples]
        high = scenarios["high"].goals.get(goal["id"])
        low = scenarios["low"].goals.get(goal["id"])
        middle = scenarios["middle"].goals.get(goal["id"])
        if terminal:
            low = middle = high = None
            basis, confidence = "completed", "none"
        elif high is None or not selected or state == "cancelling":
            low = middle = high = None
            basis, confidence = "unknown", "none"
        else:
            history_used = any(item.basis == "history" for item in selected)
            basis = (
                "mixed"
                if history_used and any_limit
                else "history"
                if history_used
                else "execution_limit"
            )
            confidence = "medium" if basis == "history" and strong_history else "low"
            if any_uncertain_middle:
                middle = None
        task_timings = {}
        for task in members:
            identifier = task["id"]
            start, finish = task.get("started"), task.get("finished")
            task_elapsed = (
                max(0.0, (finish if _number(finish) else now) - start)
                if _number(start)
                else 0.0
            )
            task_low = scenarios["low"].finishes.get(identifier)
            task_middle = scenarios["middle"].finishes.get(identifier)
            task_high = scenarios["high"].finishes.get(identifier)
            predicted_start = scenarios["middle"].starts.get(identifier)
            if task.get("state") not in PENDING:
                task_low = task_middle = task_high = predicted_start = None
                task_basis, task_confidence = "completed", "none"
            elif (
                task.get("state") == "cancelling"
                or task_high is None
                or identifier not in scenarios["high"].starts
            ):
                task_low = task_middle = task_high = predicted_start = None
                task_basis, task_confidence = "unknown", "none"
            else:
                prediction = durations[identifier]
                task_basis = (
                    "mixed"
                    if prediction.basis == "history" and any_limit
                    else prediction.basis
                )
                task_confidence = (
                    "medium" if task_basis == "history" and strong_history else "low"
                )
                if any_uncertain_middle:
                    task_middle = None
                    if task.get("state") == "queued":
                        predicted_start = None
            task_timings[identifier] = {
                "elapsed_seconds": _rounded(task_elapsed),
                "remaining_seconds": _rounded(task_middle),
                "remaining_low_seconds": _rounded(task_low),
                "remaining_high_seconds": _rounded(task_high),
                "eta_at": now + math.ceil(task_middle)
                if task_middle is not None
                else None,
                "starts_in_seconds": _rounded(predicted_start),
                "start_at": start
                if _number(start)
                else now + math.ceil(predicted_start)
                if predicted_start is not None
                else None,
                "confidence": task_confidence,
                "basis": task_basis,
                "state": task.get("state"),
            }
        results[goal["id"]] = {
            "elapsed_seconds": _rounded(max(0, goal_end - goal_start)),
            "remaining_seconds": _rounded(middle),
            "remaining_low_seconds": _rounded(low),
            "remaining_high_seconds": _rounded(high),
            "eta_at": now + math.ceil(middle) if middle is not None else None,
            "confidence": confidence,
            "basis": basis,
            "sample_count": min(observed) if observed else 0,
            "active_tasks": sum(
                task.get("state") in ("running", "cancelling") for task in members
            ),
            "waiting_tasks": sum(task.get("state") == "queued" for task in members),
            "total_tasks": len(members),
            "completed_tasks": sum(task.get("state") == "accepted" for task in members),
            "task_timings": task_timings,
        }
    return results


def estimate_plan(
    tasks, agents, snapshot, project_id, *, now=None, cli_timeout=120, api_timeout=30
):
    """Forecast a proposed task graph alongside existing work, without queueing it."""
    now = time.time() if now is None else now
    prefix = "__eta_preview__"
    identifiers = {task["id"]: f"{prefix}:{index}" for index, task in enumerate(tasks)}
    team = {agent["key"]: agent for agent in agents}
    proposed = []
    for task in tasks:
        path = task.get("parameters", {}).get("path")
        resources = list(task.get("resources") or [])
        if (
            not resources
            and path
            and task.get("capability")
            in ("model.generate", "files.write", "files.inspect")
        ):
            suffix = f"/projects/{project_id}/{path}"
            resource = next(
                (
                    name
                    for existing in snapshot.get("tasks", [])
                    for name, _ in existing.get("resources", [])
                    if name.endswith(suffix)
                ),
                f"preview:{project_id}:{path}",
            )
            resources = [
                (resource, "read" if task["capability"] == "files.inspect" else "write")
            ]
        proposed.append(
            {
                **task,
                "id": identifiers[task["id"]],
                "goal_id": prefix,
                "project_id": project_id,
                "agent_id": team[task["agent"]]["id"],
                "target": task.get("target", "local-workspace"),
                "state": "queued",
                "resources": resources,
                "dependencies": [
                    identifiers[dep] for dep in task.get("depends_on", [])
                ],
            }
        )
    state = {
        **snapshot,
        "tasks": [*snapshot.get("tasks", []), *proposed],
        "goals": [
            *snapshot.get("goals", []),
            {"id": prefix, "project_id": project_id, "created": now, "state": "queued"},
        ],
    }
    result = estimate_goals(
        state, now=now, cli_timeout=cli_timeout, api_timeout=api_timeout
    )[prefix]
    reverse = {value: key for key, value in identifiers.items()}
    result["task_timings"] = {
        reverse[key]: value for key, value in result["task_timings"].items()
    }
    return result
