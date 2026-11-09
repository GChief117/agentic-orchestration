"""Validate explicit task graphs before any task becomes eligible to execute."""

from dataclasses import dataclass
from .sources import source_file
from .postgres_setup import SETUP_CAPABILITIES, validate_setup_parameters


@dataclass(frozen=True)
class TaskDefinition:
    title: str
    agent_id: str
    capability: str
    parameters: dict
    resources: list[tuple[str, str]]
    dependencies: list[int]


def parse_tasks(
    tasks, connection, project_id, agents, *, source_root=None, source_validator=None
):
    """Validate an entire task graph and return normalized TaskDefinition records.

    Require project-owned agents, allowed capabilities, valid dependencies, and
    scoped file paths. Derive resource claims from operations rather than trusting
    client labels. No tasks are executed or persisted here.
    """
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
        if capability == "bridge.notes.create":
            if (
                set(parameters) - {"title", "content", "content_task", "note_id"}
                or not isinstance(parameters.get("title"), str)
                or not 1 <= len(parameters["title"]) <= 200
            ):
                raise ValueError(
                    "A Notes draft needs a title and content or a source task"
                )
            if parameters.get("content_task"):
                if parameters["content_task"] not in dependencies:
                    raise ValueError("The Notes source must be a task dependency")
            elif (
                not isinstance(parameters.get("content"), str)
                or not 1 <= len(parameters["content"]) <= 48000
            ):
                raise ValueError("A Notes draft needs bounded text content")
            resources = [("bridge-notes:" + project_id, "write")]
        elif capability in SETUP_CAPABILITIES:
            parameters = validate_setup_parameters(capability, parameters)
            resources = [("bridge-local-database:" + project_id, "write")]
        elif capability == "bridge.workspace.open":
            apps = parameters.get("apps", ["vscode", "pgadmin"])
            if (
                set(parameters) - {"apps"}
                or not isinstance(apps, list)
                or not 1 <= len(apps) <= 2
                or any(app not in ("vscode", "pgadmin") for app in apps)
                or len(set(apps)) != len(apps)
            ):
                raise ValueError(
                    "Choose VS Code, pgAdmin, or both workspace applications"
                )
            parameters["apps"] = apps
            resources = [("bridge-windows:" + project_id, "write")]
        elif capability == "bridge.demo.launch":
            if (
                set(parameters) - {"port"}
                or type(parameters.get("port", 8765)) is not int
                or not 1024 <= parameters.get("port", 8765) <= 65535
            ):
                raise ValueError("Choose a local demo port from 1024 to 65535")
            resources = [("bridge-demo:" + project_id, "write")]
        if capability in ("files.write", "files.inspect", "model.generate"):
            path = connection.project_path(project_id, parameters.get("path"))
            parameters["path"] = str(path.relative_to(connection.workspace))
            mode = (
                "write" if capability in ("files.write", "model.generate") else "read"
            )
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
        if capability == "model.generate":
            if not agent.get("provider") or not agent.get("model"):
                raise ValueError("Choose a provider and model for the assigned agent")
            prompt = parameters.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
                raise ValueError("Model task needs a prompt of 1–16000 characters")
            cli = agent.get("execution_mode", "api") == "cli"
            max_tokens = parameters.get("max_tokens", None if cli else 2048)
            if not (cli and max_tokens is None) and (
                type(max_tokens) is not int or not 1 <= max_tokens <= 8192
            ):
                raise ValueError(
                    "An output token limit, when supplied, must be between 1 and 8192"
                )
            parameters["max_tokens"] = max_tokens
            parameters["plan_task_id"] = task["id"]
            source_paths = parameters.get("source_files", [])
            if (
                not isinstance(source_paths, list)
                or len(source_paths) > 8
                or any(not isinstance(path, str) for path in source_paths)
                or len(set(source_paths)) != len(source_paths)
            ):
                raise ValueError("Choose at most eight distinct source files")
            if source_paths and not source_root and source_validator is None:
                raise PermissionError("Source files require an imported local project")
            if source_paths and source_validator:
                source_validator(source_paths)
            else:
                for relative in source_paths:
                    source_file(source_root, relative)
            if source_paths:
                parameters["source_files"] = source_paths
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
