"""Real, bounded local operations. This adapter does not call an AI model."""
import hashlib
import os
import subprocess
import sys
import tempfile
import time


class ExecutionCancelled(Exception):
    pass


class LocalExecutor:
    def __init__(self, connection):
        """Attach the local target policy used to authorize and scope operations."""
        self.connection = connection

    def execute(self, task, cancelled):
        """Perform one supported operation and return an evidence-bearing receipt.

        The task includes project/agent identity and normalized parameters. Check
        cooperative cancellation at operation boundaries; file writes are atomic,
        inspections compare bytes, and the fixed Python command has a timeout.
        No language model or remote bridge is invoked by this adapter.
        """
        capability = task["capability"]
        self.connection.authorize(task["target"], capability)
        params = task["parameters"]
        if cancelled.is_set():
            raise ExecutionCancelled("Cancelled before dispatch")
        started = time.time()
        if capability == "files.write":
            content = params.get("content", "")
            if not isinstance(content, str) or len(content.encode()) > 65536:
                raise ValueError("Artifact content must be text of at most 64 KiB")
            path = self.connection.path(params["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                if cancelled.is_set():
                    raise ExecutionCancelled("Cancelled before write")
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            data = path.read_bytes()
            result = {
                "artifact": params["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "check": "Written bytes match requested content",
                "accepted": data == content.encode(),
                "effect": "confirmed",
            }
        elif capability == "files.inspect":
            path = self.connection.path(params["path"])
            if path.stat().st_size > 65536:
                raise ValueError("Artifact exceeds inspection limit")
            data = path.read_bytes()
            expected = params.get("expected")
            result = {
                "artifact": params["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "check": "Artifact exists and matches expected content"
                if expected is not None
                else "Artifact exists and is readable",
                "accepted": expected is None or data == expected.encode(),
                "effect": "none",
            }
        elif capability == "terminal.python_version":
            # Fixed argument vector: no user command, shell interpolation, or arbitrary program.
            completed = subprocess.run(
                [sys.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self.connection.workspace,
            )
            result = {
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
                "exit_code": completed.returncode,
                "check": "Python exits successfully",
                "accepted": completed.returncode == 0,
                "effect": "none",
            }
        else:
            raise PermissionError("Unsupported operation")
        result.update(
            operation_id=task.get("attempt_id", task["id"]),
            target=task["target"],
            project_id=task["project_id"],
            agent_id=task["agent_id"],
            goal_id=task["goal_id"],
            capability=capability,
            duration_ms=round((time.time() - started) * 1000),
        )
        return result
