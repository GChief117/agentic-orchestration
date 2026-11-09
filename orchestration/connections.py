"""Local target identity and capability enforcement for the prototype."""
from pathlib import Path
import platform


class LocalConnection:
    CAPABILITIES = (
        "files.write",
        "files.inspect",
        "terminal.python_version",
        "model.generate",
        "bridge.notes.create",
        "bridge.demo.launch",
        "bridge.workspace.open",
        "bridge.postgres.prepare",
        "bridge.pgadmin.register",
        "bridge.aws.login",
        "bridge.aws.status",
    )

    def __init__(self, workspace):
        """Resolve and create the root directory for this local execution target."""
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def describe(self):
        """Return local target identity, supported capabilities, and its file scope.

        This is a local configuration description, not a remote connectivity probe.
        """
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
        """Raise PermissionError unless both target and capability are supported."""
        if target != "local-workspace":
            raise PermissionError("Target is unavailable; no fallback is permitted")
        if capability not in self.CAPABILITIES:
            raise PermissionError("Capability is not supported by this target")

    def path(self, name):
        """Resolve an artifact-relative name inside the target root.

        Reject absolute paths, root selection, and paths resolving outside the root.
        """
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise PermissionError("An artifact-relative path is required")
        candidate = (self.workspace / name).resolve()
        if not candidate.is_relative_to(self.workspace) or candidate == self.workspace:
            raise PermissionError("Artifact path escapes the workspace")
        return candidate

    def project_path(self, project_id, name):
        """Resolve an artifact path inside one project namespace.

        Validate the project identifier and reject traversal outside its directory.
        """
        if not isinstance(project_id, str) or not project_id.isalnum():
            raise PermissionError("Invalid project identity")
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise PermissionError("A project-relative artifact path is required")
        root = self.path("projects/" + project_id)
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise PermissionError("Artifact path escapes this project")
        return candidate
