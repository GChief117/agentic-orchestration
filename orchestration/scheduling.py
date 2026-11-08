"""Pure capacity and dependency decisions, independent of storage or adapters."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class CapacityLimits:
    execution: int
    projects: dict[str, int]
    agents: dict[str, int]
    targets: dict[str, int]


@dataclass(frozen=True)
class Schedule:
    dispatch: list[dict]
    blocked: list[str]


def conflicts(requested, held):
    """Readers may share a resource; any overlapping writer must wait."""
    return any(
        resource == existing and "write" in (mode, existing_mode)
        for resource, mode in requested
        for existing, existing_mode in held
    )


def select_work(tasks, active_ids, limits):
    """Balance projects, then goals, while respecting every configured capacity."""
    by_id = {task["id"]: task for task in tasks}
    active = [task for task in tasks if task["id"] in active_ids]
    held = [claim for task in active for claim in task["resources"]]
    project_load = Counter(task["project_id"] for task in active)
    goal_load = Counter(task["goal_id"] for task in active)
    agent_load = Counter(task["agent_id"] for task in active)
    target_load = Counter(task["target"] for task in active)
    queued = [task for task in tasks if task["state"] == "queued"]
    dispatch, blocked = [], []

    while queued:
        task = min(
            queued,
            key=lambda item: (
                project_load[item["project_id"]],
                goal_load[item["goal_id"]],
            ),
        )
        queued.remove(task)
        project, agent, target = task["project_id"], task["agent_id"], task["target"]
        dependencies = [
            by_id[identifier]["state"] for identifier in task["dependencies"]
        ]
        if any(state in ("failed", "cancelled", "blocked") for state in dependencies):
            blocked.append(task["id"])
        elif (
            len(active) + len(dispatch) < limits.execution
            and project_load[project] < limits.projects.get(project, 0)
            and agent_load[agent] < limits.agents.get(agent, 0)
            and target_load[target] < limits.targets.get(target, 0)
            and all(state == "accepted" for state in dependencies)
            and not conflicts(task["resources"], held)
        ):
            dispatch.append(task)
            held.extend(task["resources"])
            project_load[project] += 1
            goal_load[task["goal_id"]] += 1
            agent_load[agent] += 1
            target_load[target] += 1
    return Schedule(dispatch, blocked)


def goal_state(states):
    """Aggregate task state without hiding active cancellation or failed work."""
    states = set(states)
    if states == {"accepted"}:
        return "accepted"
    if "cancelling" in states:
        return "cancelling"
    if "running" in states:
        return "running"
    if "queued" in states:
        return "queued"
    if states & {"failed", "blocked"}:
        return "failed"
    return "cancelled"
