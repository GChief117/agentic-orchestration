"""Validate explicit task graphs before any task becomes eligible to execute."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskDefinition:
    title: str
    agent_id: str
    capability: str
    parameters: dict
    resources: list[tuple[str, str]]
    dependencies: list[int]


def parse_tasks(tasks, connection, project_id, agents):
    if not isinstance(tasks, list) or not tasks or len(tasks) > 1000:
        raise ValueError("A goal requires 1–1000 task definitions")
    identifiers = {}
    for index, task in enumerate(tasks):
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not task["id"].strip()
        ):
            raise ValueError("Every task needs a nonempty string id")
        if task["id"] in identifiers:
            raise ValueError("Duplicate task id: " + task["id"])
        identifiers[task["id"]] = index
    team = {agent["key"]: agent for agent in agents}
    definitions = []
    for task in tasks:
        title = task.get("title", task["id"])
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Task title must contain 1–200 characters")
        agent_key = task.get("agent")
        if not isinstance(agent_key, str) or agent_key not in team:
            raise ValueError("Every task must name an agent assigned to this project")
        agent = team[agent_key]
        capability = task.get("capability")
        if capability not in agent["capabilities"]:
            raise PermissionError("Assigned agent is not permitted this capability")
        connection.authorize(task.get("target", "local-workspace"), capability)
        parameters = task.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("Task parameters must be an object")
        parameters = dict(parameters)
        dependencies = task.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(dep, str) and dep in identifiers for dep in dependencies
        ):
            raise ValueError("Dependencies must reference task ids in this goal")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("Duplicate dependency")
        # Infer canonical resource claims from actual operations, not user-supplied labels.
        resources = []
        if capability in ("files.write", "files.inspect"):
            path = connection.project_path(project_id, parameters.get("path"))
            parameters["path"] = str(path.relative_to(connection.workspace))
            mode = "write" if capability == "files.write" else "read"
            resources = [("file:" + str(path), mode)]
        if capability == "files.write":
            content = parameters.get("content")
            if not isinstance(content, str) or len(content.encode()) > 65536:
                raise ValueError("File content must be text of at most 64 KiB")
        if (
            capability == "files.inspect"
            and "expected" in parameters
            and not isinstance(parameters["expected"], str)
        ):
            raise ValueError("Expected file content must be text")
        # A version-check subprocess has no shared terminal input or file writes.
        definitions.append(
            TaskDefinition(
                title,
                agent["id"],
                capability,
                parameters,
                resources,
                [identifiers[dep] for dep in dependencies],
            )
        )
    # Iterative topological validation avoids recursion limits on long plans.
    remaining = {
        index: set(task.dependencies) for index, task in enumerate(definitions)
    }
    resolved = set()
    while remaining:
        ready = {index for index, deps in remaining.items() if deps <= resolved}
        if not ready:
            raise ValueError("Dependency cycle detected")
        resolved.update(ready)
        for index in ready:
            del remaining[index]
    return definitions
