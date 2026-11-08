"""Pure scheduling decisions, independent of threads, storage, or adapters."""

from collections import Counter
from dataclasses import dataclass


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


def select_work(tasks, active_ids, capacity):
    """Prefer goals with fewer active tasks, using insertion order to break ties."""
    by_id = {task["id"]: task for task in tasks}
    active = [task for task in tasks if task["id"] in active_ids]
    held = [claim for task in active for claim in task["resources"]]
    load = Counter(task["goal_id"] for task in active)
    queued = [task for task in tasks if task["state"] == "queued"]
    dispatch, blocked = [], []

    while queued:
        task = min(queued, key=lambda item: load[item["goal_id"]])
        queued.remove(task)
        dependencies = [
            by_id[identifier]["state"] for identifier in task["dependencies"]
        ]
        if any(state in ("failed", "cancelled", "blocked") for state in dependencies):
            blocked.append(task["id"])
        elif (
            len(active) + len(dispatch) < capacity
            and all(state == "accepted" for state in dependencies)
            and not conflicts(task["resources"], held)
        ):
            dispatch.append(task)
            held.extend(task["resources"])
            load[task["goal_id"]] += 1
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
