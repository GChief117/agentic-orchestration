"""Project charters and persistent agent-team definitions for new work."""

from dataclasses import dataclass


def text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must contain 1–{maximum} characters")
    return value.strip()


def text_list(value, label, required=False):
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{label} must be {'a nonempty' if required else 'a'} list")
    return [text(item, label) for item in value]


def positive_integer(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True)
class AgentDefinition:
    key: str
    name: str
    role: str
    instructions: str
    responsibilities: list[str]
    capabilities: list[str]
    reports_to: str | None
    max_concurrency: int


@dataclass(frozen=True)
class ProjectDefinition:
    name: str
    mode: str
    charter: dict
    agents: list[AgentDefinition]
    max_concurrency: int


def parse_project(document, connection):
    """Validate the whole charter and organization before storing any records."""
    if not isinstance(document, dict):
        raise ValueError("Project must be an object")
    name = text(document.get("name"), "Project name", 120)
    if document.get("mode") != "greenfield":
        raise ValueError("Only explicit greenfield project initialization is supported")
    raw_charter = document.get("charter")
    if not isinstance(raw_charter, dict):
        raise ValueError("A project charter is required")
    charter = {
        "objective": text(raw_charter.get("objective"), "Charter objective"),
        "scope": text_list(raw_charter.get("scope"), "Charter scope", required=True),
        "constraints": text_list(raw_charter.get("constraints"), "Charter constraints"),
        "success_criteria": text_list(
            raw_charter.get("success_criteria"),
            "Charter success criteria",
            required=True,
        ),
    }
    roster = document.get("agents")
    if not isinstance(roster, list) or not 1 <= len(roster) <= 1000:
        raise ValueError("A project needs 1–1000 explicit agent definitions")
    team_size = positive_integer(document.get("team_size"), "Team size")
    if team_size != len(roster):
        raise ValueError("Team size must equal the number of defined agents")
    agents, keys = [], set()
    for item in roster:
        if not isinstance(item, dict):
            raise ValueError("Agent definitions must be objects")
        key = text(item.get("key"), "Agent key", 80)
        if key in keys:
            raise ValueError("Duplicate agent key: " + key)
        keys.add(key)
        capabilities = text_list(
            item.get("capabilities"), "Agent capabilities", required=True
        )
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("Duplicate agent capability")
        for capability in capabilities:
            connection.authorize("local-workspace", capability)
        parent = item.get("reports_to")
        if parent is not None:
            parent = text(parent, "Reporting agent key", 80)
        agents.append(
            AgentDefinition(
                key,
                text(item.get("name"), "Agent name", 120),
                text(item.get("role"), "Agent role", 120),
                text(item.get("instructions"), "Agent instructions"),
                text_list(
                    item.get("responsibilities"),
                    "Agent responsibilities",
                    required=True,
                ),
                capabilities,
                parent,
                positive_integer(item.get("max_concurrency"), "Agent concurrency"),
            )
        )
    parents = {agent.key: agent.reports_to for agent in agents}
    for agent in agents:
        seen, current = set(), agent.key
        while current is not None:
            if current not in parents:
                raise ValueError("Reporting relationship points to an unknown agent")
            if current in seen:
                raise ValueError("Agent reporting hierarchy contains a cycle")
            seen.add(current)
            current = parents[current]
    concurrency = positive_integer(
        document.get("max_concurrency"), "Project concurrency"
    )
    if concurrency > sum(agent.max_concurrency for agent in agents):
        raise ValueError("Project concurrency exceeds the team's configured capacity")
    return ProjectDefinition(name, "greenfield", charter, agents, concurrency)
