"""Local target identity and capability enforcement for the prototype."""
from pathlib import Path
import platform


class LocalConnection:
    CAPABILITIES = ("files.write", "files.inspect", "terminal.python_version")

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def describe(self):
        return {
            "id": "local-workspace",
            "name": "Local workspace",
            "transport": "local",
            "status": "connected",
            "platform": platform.system(),
            "capabilities": list(self.CAPABILITIES),
            "scope": "Application artifact directory only",
        }

    def authorize(self, target, capability):
        if target != "local-workspace":
            raise PermissionError("Target is unavailable; no fallback is permitted")
        if capability not in self.CAPABILITIES:
            raise PermissionError("Capability is not supported by this target")

    def path(self, name):
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise PermissionError("An artifact-relative path is required")
        candidate = (self.workspace / name).resolve()
        if not candidate.is_relative_to(self.workspace) or candidate == self.workspace:
            raise PermissionError("Artifact path escapes the workspace")
        return candidate

    def project_path(self, project_id, name):
        """Constrain paths within a project, not merely within the service workspace."""
        if not isinstance(project_id, str) or not project_id.isalnum():
            raise PermissionError("Invalid project identity")
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise PermissionError("A project-relative artifact path is required")
        root = self.path("projects/" + project_id)
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise PermissionError("Artifact path escapes this project")
        return candidate
