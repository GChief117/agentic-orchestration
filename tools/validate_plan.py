#!/usr/bin/env python3
"""Validate draft static plans; no agent execution or runtime enforcement."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def validate(plan):
    """Return human-readable errors. Never execute any content from a plan."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    if plan.get("version") != "0.1":
        errors.append("version must be 0.1")

    def nonempty_text(value):
        return isinstance(value, str) and bool(value.strip())

    def text_list(value, nonempty=False):
        return (
            isinstance(value, list)
            and (bool(value) or not nonempty)
            and all(nonempty_text(item) for item in value)
        )

    def records(key):
        values = plan.get(key)
        if not isinstance(values, list) or not values:
            errors.append(f"{key} must be a nonempty list")
            return {}
        result = {}
        for i, row in enumerate(values):
            if not isinstance(row, dict) or not nonempty_text(row.get("id")):
                errors.append(f"{key}[{i}] must be an object with a nonempty id")
                continue
            identifier = row["id"]
            if identifier in result:
                errors.append(f"duplicate {key} id: {identifier}")
            result[identifier] = row
        return result

    goals, agents, tasks = (records(key) for key in ("goals", "agents", "tasks"))
    for goal_id, goal in goals.items():
        if not text_list(goal.get("acceptance"), nonempty=True):
            errors.append(f"{goal_id}: goal acceptance must contain nonempty text")
    for agent_id, agent in agents.items():
        capacity = agent.get("capacity")
        if type(capacity) is not int or capacity < 1:
            errors.append(f"{agent_id}: capacity must be a positive integer")

    dependencies = {}
    claims = {}
    for task_id, task in tasks.items():
        for field, lookup in [("goal", goals), ("owner", agents)]:
            value = task.get(field)
            if not isinstance(value, str) or value not in lookup:
                errors.append(f"{task_id}: unknown {field}: {value!r}")
        if not text_list(task.get("acceptance"), nonempty=True):
            errors.append(f"{task_id}: acceptance must contain nonempty text")
        deps = task.get("depends_on")
        if not text_list(deps):
            errors.append(f"{task_id}: depends_on must be a list of ids")
            deps = []
        if len(set(deps)) != len(deps):
            errors.append(f"{task_id}: duplicate dependency")
        dependencies[task_id] = deps
        for dep in deps:
            if dep not in tasks:
                errors.append(f"{task_id}: unknown dependency: {dep}")
        resources = task.get("resources")
        claims[task_id] = []
        if not isinstance(resources, list):
            errors.append(f"{task_id}: resources must be a list")
            continue
        seen = set()
        for resource in resources:
            if (
                not isinstance(resource, dict)
                or not nonempty_text(resource.get("name"))
                or resource.get("mode") not in ("read", "write")
            ):
                errors.append(f"{task_id}: invalid resource claim")
                continue
            name = resource["name"]
            if name in seen:
                errors.append(f"{task_id}: duplicate resource claim: {name}")
            seen.add(name)
            claims[task_id].append(resource)

    # Iterative topological check also handles deeply nested plans.
    indegree = {task_id: 0 for task_id in tasks}
    children = defaultdict(list)
    for task_id, deps in dependencies.items():
        for dep in set(deps):
            if dep in tasks:
                indegree[task_id] += 1
                children[dep].append(task_id)
    ready = [key for key, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(tasks):
        errors.append("dependency cycle detected")

    batches = plan.get("batches")
    if not isinstance(batches, list) or not batches:
        errors.append("batches must be a nonempty list")
        batches = []
    positions = {}
    for index, batch in enumerate(batches):
        if not text_list(batch, nonempty=True):
            errors.append(f"batch {index}: must be a nonempty list of task ids")
            continue
        load = Counter()
        resources = defaultdict(list)
        for task_id in batch:
            if task_id not in tasks:
                errors.append(f"batch {index}: unknown task: {task_id}")
                continue
            if task_id in positions:
                errors.append(f"{task_id}: appears in multiple batch positions")
            positions[task_id] = index
            owner = tasks[task_id].get("owner")
            if isinstance(owner, str) and owner in agents:
                load[owner] += 1
            for resource in claims[task_id]:
                resources[resource["name"]].append((task_id, resource["mode"]))
        for owner, count in load.items():
            capacity = agents[owner].get("capacity")
            if type(capacity) is int and count > capacity:
                errors.append(f"batch {index}: {owner} exceeds capacity")
        for name, users in resources.items():
            if len(users) > 1 and any(mode == "write" for _, mode in users):
                errors.append(f"batch {index}: conflicting access to {name}")
    for task_id, deps in dependencies.items():
        if task_id not in positions:
            errors.append(f"{task_id}: missing from batches")
        for dep in deps:
            if (
                task_id in positions
                and dep in positions
                and positions[dep] >= positions[task_id]
            ):
                errors.append(
                    f"{task_id}: dependency {dep} must be in an earlier batch"
                )
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot read plan: {exc}\n")
    errors = validate(plan)
    if errors:
        parser.exit(1, "\n".join(f"ERROR: {message}" for message in errors) + "\n")
    print(
        f'Valid static plan: {len(plan["goals"])} goals, {len(plan["tasks"])} tasks, '
        f'{len(plan["batches"])} batches. Runtime behavior is not checked.'
    )


if __name__ == "__main__":
    main()
