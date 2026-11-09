"""Project charters and persistent agent-team definitions for new work."""

from dataclasses import dataclass
from .models import PROVIDERS, model_name


def text(value, label, maximum=4000):
    """Validate and trim a bounded, nonempty string; raise ValueError on failure."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must contain 1–{maximum} characters")
    return value.strip()


def text_list(value, label, required=False):
    """Validate a list of text values and return trimmed strings.

    required controls whether an empty list is allowed.
    """
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{label} must be {'a nonempty' if required else 'a'} list")
    return [text(item, label) for item in value]


def positive_integer(value, label):
    """Return a positive integer, rejecting booleans and noninteger values."""
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
    provider: str | None
    model: str | None
    collaborators: list[str]
    execution_mode: str
    execution_speed: str


@dataclass(frozen=True)
class ProjectDefinition:
    name: str
    mode: str
    charter: dict
    agents: list[AgentDefinition]
    max_concurrency: int
    team_model: dict | None


def parse_team_model(value):
    """Validate a persistent shared model preference without storing credentials."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "provider",
        "model",
        "execution_mode",
        "execution_speed",
    }:
        raise ValueError("Team model must contain only provider and execution settings")
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Choose a provider for the team model")
    model = model_name(value.get("model"))
    mode = value.get("execution_mode", "cli")
    speed = value.get("execution_speed", "standard")
    if mode not in ("cli", "api") or speed not in ("standard", "fast", "ultrafast"):
        raise ValueError("Choose valid team model execution settings")
    if mode != "cli" and speed != "standard":
        raise ValueError("Team speed choices require local CLI execution")
    return {
        "provider": provider,
        "model": model,
        "execution_mode": mode,
        "execution_speed": speed,
    }


def parse_project(document, connection):
    """Convert a project document into validated charter and agent definitions.

    Validate roster size, supported capabilities, reporting hierarchy, and
    concurrency before any database writes. This does not launch agent processes.
    """
    if not isinstance(document, dict):
        raise ValueError("Project must be an object")
    name = text(document.get("name"), "Project name", 120)
    if document.get("mode") not in ("greenfield", "existing"):
        raise ValueError("Choose greenfield or existing project initialization")
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
        provider = item.get("provider") or None
        model = item.get("model") or None
        if provider is not None and provider not in PROVIDERS:
            raise ValueError("Unknown model provider")
        if model is not None:
            model = model_name(model)
        if model is not None and provider is None:
            raise ValueError("A model requires a provider")
        execution_mode = item.get("execution_mode", "api")
        if execution_mode not in ("api", "cli"):
            raise ValueError("Choose local CLI or API execution")
        execution_speed = item.get("execution_speed", "standard")
        if execution_speed not in ("standard", "fast", "ultrafast"):
            raise ValueError("Choose standard, fast, or ultrafast execution")
        if execution_mode != "cli" and execution_speed != "standard":
            raise ValueError("Execution speed choices require local CLI execution")
        collaborators = text_list(item.get("collaborators", []), "Collaborators")
        if len(set(collaborators)) != len(collaborators) or key in collaborators:
            raise ValueError("Collaborators must be distinct other agents")
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
                provider,
                model,
                collaborators,
                execution_mode,
                execution_speed,
            )
        )
    by_key = {agent.key: agent for agent in agents}
    for agent in agents:
        for peer in list(agent.collaborators):
            if peer not in by_key:
                raise ValueError("Collaboration points to an unknown agent")
            if agent.key not in by_key[peer].collaborators:
                by_key[peer].collaborators.append(agent.key)
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
    return ProjectDefinition(
        name,
        document["mode"],
        charter,
        agents,
        concurrency,
        parse_team_model(document.get("team_model")),
    )
