"""Bounded, read-only discovery of local projects with explicit import selection."""

import os
from pathlib import Path
import secrets
import threading
import time

MARKERS = {
    ".git": "Git",
    "package.json": "JavaScript",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "setup.py": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "build.gradle.kts": "Kotlin",
    "Package.swift": "Swift",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "CMakeLists.txt": "C/C++",
    "Makefile": "Make",
    "Dockerfile": "Docker",
    "pubspec.yaml": "Dart",
    "mix.exs": "Elixir",
}
SKIP = {
    "node_modules",
    "vendor",
    "venv",
    "env",
    "build",
    "dist",
    "target",
    "coverage",
    "Library",
    "Applications",
    "__pycache__",
}


class ProjectDiscovery:
    """Keep short-lived scan selections server-side; never execute project content."""

    def __init__(self):
        """Initialize a single bounded scan cache protected across HTTP threads."""
        self.lock = threading.Lock()
        self.latest = None

    def roots(self):
        """Suggest existing conventional project folders without scanning them."""
        home = Path.home()
        return [
            str(path)
            for path in (
                home / "Developer",
                home / "Projects",
                home / "Documents",
                home,
            )
            if path.is_dir()
        ]

    def scan(self, root, include_root=False):
        """Inspect names to depth five, skipping hidden/dependency and symlink folders.

        Bound work to 10,000 directories, 200 candidates, and ten seconds. Surface
        truncation and unreadable-folder counts rather than implying a full scan.
        """
        if not isinstance(include_root, bool):
            raise ValueError("Include-folder option must be true or false")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("Choose a folder to scan")
        try:
            base = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                "The scan folder does not exist or is unavailable"
            ) from exc
        if not base.is_dir():
            raise ValueError("Choose a folder, not a file")
        deadline = time.monotonic() + 10
        pending, candidates, visited, unreadable = [(base, 0)], [], 0, 0
        truncated = False
        while pending:
            if (
                visited >= 10000
                or len(candidates) >= 200
                or time.monotonic() >= deadline
            ):
                truncated = True
                break
            folder, depth = pending.pop()
            visited += 1
            try:
                with os.scandir(folder) as iterator:
                    entries = []
                    for entry in iterator:
                        entries.append(entry)
                        if len(entries) >= 10000 or time.monotonic() >= deadline:
                            truncated = True
                            break
                names = {entry.name for entry in entries}
                kinds = {kind for name, kind in MARKERS.items() if name in names}
                for name in names:
                    if name.endswith((".csproj", ".sln", ".fsproj")):
                        kinds.add(".NET")
                    if name.endswith((".xcodeproj", ".xcworkspace")):
                        kinds.add("Swift / Xcode")
                if folder == base and include_root and not kinds:
                    kinds.add("Folder")
                if kinds:
                    candidates.append(
                        {
                            "id": secrets.token_urlsafe(16),
                            "name": folder.name,
                            "path": str(folder),
                            "kinds": sorted(kinds),
                        }
                    )
                # A detected project owns its subtree; scan it directly to find
                # nested projects without listing every dependency/example here.
                if kinds and folder != base:
                    continue
                if depth >= 5:
                    truncated |= any(
                        entry.is_dir(follow_symlinks=False)
                        and not entry.name.startswith(".")
                        and entry.name not in SKIP
                        for entry in entries
                    )
                    continue
                children = [
                    Path(entry.path)
                    for entry in entries
                    if not entry.name.startswith(".")
                    and entry.name not in SKIP
                    and not entry.name.endswith((".xcodeproj", ".xcworkspace"))
                    and entry.is_dir(follow_symlinks=False)
                ]
                remaining = max(0, 10000 - visited - len(pending))
                if len(children) > remaining:
                    truncated = True
                pending.extend(
                    (child, depth + 1) for child in sorted(children)[:remaining][::-1]
                )
            except (PermissionError, OSError):
                unreadable += 1
        result = {
            "scan_id": secrets.token_urlsafe(24),
            "root": str(base),
            "projects": sorted(candidates, key=lambda item: item["path"].casefold()),
            "visited": visited,
            "unreadable": unreadable,
            "truncated": truncated,
        }
        with self.lock:
            self.latest = (time.monotonic(), result)
        return result

    def selected(self, scan_id, identifiers):
        """Resolve explicit candidate IDs and reject expired, missing, or changed paths."""
        if (
            not isinstance(identifiers, list)
            or not 1 <= len(identifiers) <= 100
            or any(not isinstance(item, str) for item in identifiers)
        ):
            raise ValueError("Select between 1 and 100 projects")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Select each project only once")
        with self.lock:
            if (
                not self.latest
                or self.latest[1]["scan_id"] != scan_id
                or time.monotonic() - self.latest[0] > 1800
            ):
                raise ValueError("This scan expired; scan the folder again")
            candidates = {item["id"]: item for item in self.latest[1]["projects"]}
            result = []
            for identifier in identifiers:
                if identifier not in candidates:
                    raise ValueError("A selected project was not found in this scan")
                item = candidates[identifier]
                path = Path(item["path"])
                if not path.is_dir() or str(path.resolve()) != item["path"]:
                    raise ValueError("A selected folder moved or changed; scan again")
                result.append(dict(item))
            return result


def imported_definition(candidate):
    """Prepare an editable charter and three named roles without assuming project goals."""
    roles = [
        (
            "coordinator",
            "Coordinator",
            "Coordination",
            "Plan work and coordinate the team. Confirm the objective before assigning changes.",
            ["terminal.python_version", "files.inspect"],
        ),
        (
            "builder",
            "Builder",
            "Implementation",
            "Implement assigned work after reviewing the existing project. Preserve existing files.",
            ["files.write", "terminal.python_version"],
        ),
        (
            "reviewer",
            "Reviewer",
            "Verification",
            "Review results against agreed requirements and report evidence and unresolved issues.",
            ["files.inspect"],
        ),
    ]
    return {
        "name": candidate["name"][:120],
        "mode": "existing",
        "team_size": 3,
        "max_concurrency": 3,
        "charter": {
            "objective": f"Review and plan the next steps for {candidate['name']}.",
            "scope": ["Review the existing project and agree on requested work."],
            "constraints": [
                "Keep the original source folder unchanged until source execution is explicitly supported."
            ],
            "success_criteria": ["The project owner has reviewed the plan and team."],
        },
        "agents": [
            {
                "key": key,
                "name": name,
                "role": role,
                "instructions": instructions,
                "responsibilities": [role],
                "capabilities": caps,
                "reports_to": None if index == 0 else "coordinator",
                "max_concurrency": 1,
            }
            for index, (key, name, role, instructions, caps) in enumerate(roles)
        ],
    }
