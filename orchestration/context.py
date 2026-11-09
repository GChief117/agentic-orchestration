"""Private, durable project context shared by the assigned team."""

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

MAX_CONTEXT_CHARACTERS = 48000
MAX_CONTEXT_ENTRIES = 32
MAX_CONTEXT_BYTES = 256 * 1024


class ProjectContext:
    """Store bounded notes separately from source repositories and public assets."""

    def __init__(self, directory):
        """Create the private context directory without touching project source files."""
        self.directory = Path(directory) / "context"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id):
        """Resolve a validated project identity inside the private context directory."""
        if not isinstance(project_id, str) or not project_id.isalnum():
            raise ValueError("Invalid project identity")
        return self.directory / (project_id + ".json")

    def read(self, project_id):
        """Return persisted notes and a revision that detects edits after planning."""
        path = self._path(project_id)
        if path.is_symlink():
            raise ValueError("The project's saved context cannot be a symbolic link")
        if not path.exists():
            entries = []
        else:
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    metadata = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_size > MAX_CONTEXT_BYTES
                    ):
                        raise ValueError(
                            "The project's saved context exceeds its file limit"
                        )
                    raw = stream.read(MAX_CONTEXT_BYTES + 1)
                if len(raw) > MAX_CONTEXT_BYTES:
                    raise ValueError(
                        "The project's saved context exceeds its file limit"
                    )
                entries = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                raise ValueError("The project's saved context is invalid") from exc
        if not isinstance(entries, list) or len(entries) > MAX_CONTEXT_ENTRIES:
            raise ValueError("The project's saved context is invalid")
        identities, total = set(), 0
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"id", "title", "content", "created"}
                or not isinstance(entry["id"], str)
                or not entry["id"]
                or len(entry["id"]) > 80
                or entry["id"] in identities
                or not isinstance(entry["title"], str)
                or not entry["title"].strip()
                or len(entry["title"]) > 200
                or not isinstance(entry["content"], str)
                or not entry["content"].strip()
                or type(entry["created"]) not in (int, float)
                or (
                    type(entry["created"]) is float
                    and not math.isfinite(entry["created"])
                )
                or entry["created"] < 0
            ):
                raise ValueError("The project's saved context has an invalid entry")
            identities.add(entry["id"])
            total += len(entry["content"])
        if total > MAX_CONTEXT_CHARACTERS:
            raise ValueError(
                "Shared context is limited to 48,000 characters per project"
            )
        encoded = json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()
        return {"entries": entries, "revision": hashlib.sha256(encoded).hexdigest()}

    def add(self, project_id, title, content):
        """Atomically save an explicit note; reject overflow instead of omitting context."""
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Context title must contain 1–200 characters")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Add the context you want the team to share")
        entries = self.read(project_id)["entries"]
        if len(entries) >= MAX_CONTEXT_ENTRIES:
            raise ValueError("A project can hold at most 32 shared context entries")
        if (
            sum(len(item["content"]) for item in entries) + len(content)
            > MAX_CONTEXT_CHARACTERS
        ):
            raise ValueError(
                "Shared context is limited to 48,000 characters per project"
            )
        entries.append(
            {
                "id": uuid.uuid4().hex,
                "title": title.strip(),
                "content": content.strip(),
                "created": time.time(),
            }
        )
        path = self._path(project_id)
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".pending-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(entries, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.read(project_id)
