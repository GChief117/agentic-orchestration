"""Fixed, explicitly configured bridge actions executed by saved team tasks.

The adapter accepts task data, not shell commands. Its only mutations are creating
a Notes draft and installing/starting a separate local demo workspace. Remote
identity is checked inside the same process before either action takes effect.
"""

import base64
import io
import json
from pathlib import Path
import shlex
import zipfile

from .cli_models import CLIRuntime

MAX_ACTION_INPUT = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 3 * 1024 * 1024
MAX_NOTE_CHARACTERS = 48000
ACTION_TIMEOUT = 120

_REMOTE_ACTIONS = r'''
import base64
import getpass
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

MAX_INPUT = 4 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_UNPACKED = 12 * 1024 * 1024

def canonical_directory(value, create=False):
    """Reject redirected roots and create only a configured canonical directory."""
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Configured demo directory must be a canonical absolute path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError("Demo directory cannot cross a symbolic link")
        if current.exists() and not current.is_dir():
            raise ValueError("Demo directory parent must be a directory")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path

def atomic_json(path, value):
    """Write a private action record without exposing credentials or partial JSON."""
    if path.is_symlink():
        raise ValueError("Action record cannot be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def apple_string(value):
    """Quote plain task data as one AppleScript literal."""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'

def read_note_record(path):
    """Read a bounded regular ownership record, never a redirected file."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Invalid note ownership record")
    with path.open("rb") as stream:
        data = stream.read(8193)
    if len(data) > 8192:
        raise ValueError("Note ownership record exceeds its limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid note ownership record")
    return value

def owned_note(ledger, note_id, title):
    """Allow updates only to a note previously created by an app action."""
    for index, path in enumerate(ledger.glob("*.json")):
        if index >= 1000:
            raise ValueError("Note ownership lookup exceeded its limit")
        try:
            saved = read_note_record(path)
        except (ValueError, OSError):
            continue
        if saved.get("note_id") == note_id and saved.get("title") == title:
            return True
    return False

def notes_create(payload):
    """Create or update an app-owned draft; no messaging API is available here."""
    title, content = payload["parameters"]["title"], payload["parameters"]["content"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("A note title of 1–200 characters is required")
    if not isinstance(content, str) or not content.strip() or len(content) > 48000:
        raise ValueError("Note content must contain 1–48,000 characters")
    requested_note_id = payload["parameters"].get("note_id")
    if requested_note_id is not None and (not isinstance(requested_note_id, str) or not requested_note_id or len(requested_note_id) > 1000 or any(c in requested_note_id for c in "\x00\r\n")):
        raise ValueError("Choose a valid app-owned note identity")
    operation = payload["operation_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation):
        raise ValueError("Invalid action operation identity")
    ledger = canonical_directory(str(Path.home() / "Library/Application Support/Agentic Orchestration/notes-actions"), create=True)
    record = ledger / (operation + ".json")
    if record.is_symlink():
        raise ValueError("Note operation record cannot be a symbolic link")
    if record.exists():
        saved = read_note_record(record)
        if saved.get("title") != title or saved.get("content_sha256") != hashlib.sha256(content.encode()).hexdigest() or saved.get("requested_note_id") != requested_note_id:
            raise ValueError("This note operation was already used for different content")
        return {"note_id": saved["note_id"], "title": title, "reused": True, "updated": saved.get("updated", False)}
    if requested_note_id is not None and not owned_note(ledger, requested_note_id, title):
        raise ValueError("Only a note previously created by this app with the same title can be updated")
    body = "<h1>" + html.escape(title) + "</h1><div>" + html.escape(content).replace("\n", "<br>") + "</div>"
    script_lines = ['tell application "Notes"']
    if requested_note_id is None:
        script_lines.extend([
            'set targetAccount to default account',
            'set targetFolder to folder "Notes" of targetAccount',
            'set targetNote to make new note at targetFolder with properties {name:' + apple_string(title) + ', body:' + apple_string(body) + '}',
        ])
    else:
        script_lines.extend([
            'set targetNote to note id ' + apple_string(requested_note_id),
            'if name of targetNote is not ' + apple_string(title) + ' then error "The app-owned note title changed; update declined"',
            'set body of targetNote to ' + apple_string(body),
        ])
    script_lines.extend([
        'activate',
        'show targetNote',
        'return id of targetNote',
        'end tell',
    ])
    script = '\n'.join(script_lines)
    result = subprocess.run(["/usr/bin/osascript", "-"], input=script, text=True, capture_output=True, timeout=35)
    if result.returncode:
        raise ValueError("Notes could not save the draft. Check its title, Notes setup, and Automation permission on the target Mac.")
    note_id = result.stdout.strip()
    if not note_id or len(note_id) > 1000:
        raise ValueError("Notes did not confirm the saved draft")
    if requested_note_id is not None and note_id != requested_note_id:
        raise ValueError("Notes returned a different identity for the updated draft")
    updated = requested_note_id is not None
    atomic_json(record, {"note_id": note_id, "title": title, "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "requested_note_id": requested_note_id, "updated": updated})
    return {"note_id": note_id, "title": title, "reused": False, "updated": updated}

def archive_files(encoded):
    """Validate the trusted runtime package before touching an existing demo."""
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError("Demo package exceeds its size limit")
    output, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = Path(name).parts
            if (not parts or Path(name).is_absolute() or any(part in (".", "..") or part.startswith(".") for part in parts)
                    or "\\" in name or info.is_dir() or info.file_size > MAX_FILE
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Demo package contains an invalid path or file")
            if not (name in {"app.py", "requirements.txt"} or name.startswith("orchestration/") or name.startswith("frontend/")):
                raise ValueError("Demo package contains a file outside the runtime")
            total += info.file_size
            if total > MAX_UNPACKED or len(output) >= 300:
                raise ValueError("Demo package expands beyond its limit")
            if name in output:
                raise ValueError("Demo package has duplicate files")
            output[name] = archive.read(info)
    if not {"app.py", "requirements.txt", "orchestration/engine.py", "frontend/index.html"} <= set(output):
        raise ValueError("Demo package is missing required runtime files")
    return output

def live_owned_process(manifest, demo):
    """Recognize only the recorded demo process; never signal an unrelated PID."""
    pid = manifest.get("pid")
    if type(pid) is not int or pid <= 1:
        return None
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
    expected = [str(demo / "app.py"), str(demo / ".runtime/demo")]
    return pid if result.returncode == 0 and all(item in result.stdout for item in expected) else None

def ensure_demo_idle(manifest, demo):
    """Refuse to stop a running demo unless its authenticated state is idle."""
    port = manifest.get("port")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Cannot verify the current demo's port; it was left running")
    data = canonical_directory(str(demo / ".runtime/demo"))
    token_path = data / "api-token"
    if token_path.is_symlink() or not token_path.is_file():
        raise ValueError("Cannot verify the current demo's session; it was left running")
    with token_path.open("r") as stream:
        token = stream.read(513).strip()
    if not token or len(token) > 512:
        raise ValueError("Cannot verify the current demo's session; it was left running")
    request = urllib.request.Request("http://127.0.0.1:" + str(port) + "/api/state", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Demo state exceeds its verification limit")
        state = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot verify that the current demo is idle; it was left running") from exc
    if not isinstance(state, dict) or type(state.get("active_executions")) is not int or not isinstance(state.get("tasks"), list) or not isinstance(state.get("goals"), list) or not all(isinstance(task, dict) for task in state["tasks"]) or not all(isinstance(goal, dict) and (goal.get("delivery") is None or isinstance(goal.get("delivery"), dict)) for goal in state["goals"]):
        raise ValueError("Cannot verify that the current demo is idle; it was left running")
    if state["active_executions"] != 0 or any(task.get("state") in ("queued", "running", "cancelling") for task in state["tasks"] if isinstance(task, dict)) or any((goal.get("delivery") or {}).get("state") == "rendering" for goal in state["goals"] if isinstance(goal, dict)):
        raise ValueError("The current demo has active or queued work; wait for it to finish before updating")

def demo_launch(payload):
    """Install a separate runtime, seed its team, then verify its loopback service."""
    demo = canonical_directory(payload["demo_directory"])
    source_root = canonical_directory(payload["root"])
    if demo == source_root or any(demo == source_root / repo or (source_root / repo) in demo.parents for repo in payload["repositories"]):
        raise ValueError("Demo directory must be separate from connected source repositories")
    files = archive_files(payload["archive"])
    port = payload["parameters"].get("port", 8765)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Choose a local demo port between 1024 and 65535")
    demo = canonical_directory(str(demo), create=True)
    record = demo / ".agentic-demo-manifest.json"
    manifest = json.loads(record.read_text()) if record.exists() and not record.is_symlink() else {}
    if record.is_symlink():
        raise ValueError("Demo manifest cannot be a symbolic link")
    if not manifest and any(path.name not in {".venv"} for path in demo.iterdir()):
        raise ValueError("Demo directory contains unmanaged files; choose a separate configured directory")
    previous = manifest.get("files", {})
    for name, content in files.items():
        path = demo / name
        if path.exists():
            if path.is_symlink() or name not in previous:
                raise ValueError("Demo would overwrite an unmanaged file")
            if hashlib.sha256(path.read_bytes()).hexdigest() != previous[name]:
                raise ValueError("A managed demo file was edited; preserve it before updating")
        current = path.parent
        while current != demo:
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise ValueError("Demo package directory is redirected")
            current = current.parent
    old_pid = live_owned_process(manifest, demo)
    if old_pid:
        ensure_demo_idle(manifest, demo)
        os.kill(old_pid, signal.SIGTERM)
        for _ in range(30):
            if live_owned_process(manifest, demo) is None:
                break
            time.sleep(0.1)
        if live_owned_process(manifest, demo) is not None:
            raise ValueError("The previous demo did not stop; no other process was interrupted")
    with socket.socket() as reservation:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            reservation.bind(("127.0.0.1", port))
        except OSError as exc:
            raise ValueError("The requested demo port is in use") from exc
    for name, content in files.items():
        path = demo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    installation = {"files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, "project_id": manifest.get("project_id"), "pid": None, "port": port, "state": "installed"}
    atomic_json(record, installation)
    python = demo / ".venv/bin/python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(demo / ".venv")], check=True, capture_output=True, timeout=20)
    probe = subprocess.run([str(python), "-c", "import reportlab"], capture_output=True, timeout=10)
    if probe.returncode:
        install = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(demo / "requirements.txt")], capture_output=True, timeout=75)
        if install.returncode:
            raise ValueError("Demo PDF dependencies could not be installed")
    data = demo / ".runtime/demo"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["PATH"] = os.pathsep.join(["/opt/homebrew/bin", str(Path.home() / ".local/bin"), "/usr/local/bin", os.environ.get("PATH", "")])
    sys.path.insert(0, str(demo))
    from orchestration.engine import Engine
    engine = Engine(data, execution_limit=4)
    try:
        project_id = manifest.get("project_id")
        if project_id:
            engine.store.project(project_id)
            engine.update_team(project_id, payload["project_definition"])
        else:
            project_id = engine.create_project(payload["project_definition"])
        existing = engine.context.read(project_id)["entries"]
        for entry in payload.get("shared_context", {}).get("entries", []):
            if not any(item["title"] == entry["title"] and item["content"] == entry["content"] for item in existing):
                engine.add_project_context(project_id, entry["title"], entry["content"])
        wrapper = demo / "tools/local-bridge"
        wrapper.parent.mkdir(exist_ok=True)
        wrapper.write_text('#!/bin/sh\nset -eu\nexec /bin/zsh -lc "$1"\n')
        wrapper.chmod(0o700)
        config = {project_id: {"name": "Local project tools", "command": str(wrapper), "agent_command": str(wrapper), "hostname": payload["hostname"], "username": payload["username"], "root": str(source_root), "repositories": payload["repositories"]}}
        if payload.get("notes_enabled"):
            config[project_id]["notes_enabled"] = True
        atomic_json(data / "source-bridges.json", config)
        installation.update(project_id=project_id, state="seeded")
        atomic_json(record, installation)
    finally:
        engine.close()
    logs = demo / ".runtime/logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "demo.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(python), "-u", str(demo / "app.py"), "--host", "127.0.0.1", "--port", str(port), "--execution-limit", "4", "--data-dir", str(data)], cwd=demo, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    url = "http://127.0.0.1:" + str(port)
    healthy = False
    for _ in range(75):
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=1) as response:
                healthy = json.load(response) == {"status": "ok"}
            if healthy and process.poll() is None:
                break
        except (OSError, ValueError):
            time.sleep(0.2)
    record_value = {**installation, "pid": process.pid, "state": "running" if healthy else "failed"}
    atomic_json(record, record_value)
    if not healthy or process.poll() is not None:
        raise ValueError("Demo process did not become healthy; inspect its private demo log")
    subprocess.run(["/usr/bin/open", url], check=True, capture_output=True, timeout=10)
    return {"url": url, "demo_directory": str(demo), "project_id": project_id, "pid": process.pid, "log": str(log_path), "opened": True}

def main():
    """Verify the target before selecting one of the fixed allowed actions."""
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError("Bridge action input exceeds its size limit")
    payload = json.loads(raw)
    expected = {"hostname": payload["hostname"], "username": payload["username"]}
    identity = {"hostname": socket.gethostname(), "username": getpass.getuser()}
    if identity != expected:
        raise ValueError("Bridge action remote identity does not match its configuration")
    action = payload["action"]
    if action == "bridge.notes.create":
        result = notes_create(payload)
    elif action == "bridge.demo.launch":
        if sys.version_info < (3, 10):
            raise ValueError("Demo launch needs Python 3.10+ in the target login environment")
        result = demo_launch(payload)
    else:
        raise ValueError("Unsupported bridge action")
    return {"identity": identity, "result": result}

try:
    print(json.dumps(main()))
except Exception as exc:
    print(json.dumps({"error": str(exc)[:1000]}))
    sys.exit(1)
'''


class BridgeActions:
    """Provide only operator-enabled draft editing and isolated demo launch."""

    def __init__(self, source_bridges, *, application_root=None, runner=None):
        """Attach private target policy and the application's trusted runtime source."""
        self.source_bridges = source_bridges
        self.application_root = Path(
            application_root or Path(__file__).resolve().parents[1]
        )
        self.runner = runner or CLIRuntime._run

    def ready(self, project_id, capability):
        """Check local configuration without performing a remote action."""
        if capability not in ("bridge.notes.create", "bridge.demo.launch"):
            return False
        if not getattr(self.source_bridges, "enabled", True):
            return False
        entry = self.source_bridges._configuration(project_id)
        return bool(
            entry
            and entry.get("agent_command")
            and (
                entry.get("notes_enabled") is True
                if capability == "bridge.notes.create"
                else entry.get("demo_directory")
            )
        )

    def _archive(self):
        """Package runtime source only; exclude state, credentials, tests, and dependencies."""
        paths = [
            self.application_root / "app.py",
            self.application_root / "requirements.txt",
        ]
        for folder in ("orchestration", "frontend"):
            paths.extend(
                path
                for path in (self.application_root / folder).rglob("*")
                if path.is_file()
                and not any(
                    part.startswith(".")
                    or part in ("node_modules", "__pycache__", "tests")
                    for part in path.relative_to(self.application_root).parts
                )
            )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(set(paths)):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(
                        "Runtime package cannot contain symlinks or missing files"
                    )
                relative = path.relative_to(self.application_root).as_posix()
                archive.writestr(relative, path.read_bytes())
        result = output.getvalue()
        if len(result) > MAX_ARCHIVE_BYTES:
            raise ValueError("Runtime package exceeds its size limit")
        return base64.b64encode(result).decode("ascii")

    def execute(
        self,
        project_id,
        capability,
        parameters,
        *,
        project_definition=None,
        shared_context=None,
        operation_id=None,
        cancelled=None,
        progress=None
    ):
        """Run one fixed bridge action after validating its configured authorization."""
        if not self.ready(project_id, capability):
            raise PermissionError(
                "This bridge capability is not configured for the project"
            )
        if not isinstance(parameters, dict):
            raise ValueError("Bridge action parameters must be an object")
        entry = self.source_bridges._configuration(project_id)
        payload = {
            key: entry[key] for key in ("hostname", "username", "root", "repositories")
        }
        payload.update(
            action=capability,
            parameters=parameters,
            operation_id=operation_id or "task",
        )
        if capability == "bridge.notes.create":
            title, content = parameters.get("title"), parameters.get("content")
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                raise ValueError("A note title of 1–200 characters is required")
            if (
                not isinstance(content, str)
                or not content.strip()
                or len(content) > MAX_NOTE_CHARACTERS
            ):
                raise ValueError("Note content must contain 1–48,000 characters")
            note_id = parameters.get("note_id")
            if note_id is not None and (
                not isinstance(note_id, str)
                or not note_id
                or len(note_id) > 1000
                or any(c in note_id for c in "\x00\r\n")
            ):
                raise ValueError("Choose a valid app-owned note identity")
        else:
            if not isinstance(project_definition, dict):
                raise ValueError("Demo launch requires the saved project definition")
            payload.update(
                demo_directory=entry["demo_directory"],
                notes_enabled=entry.get("notes_enabled") is True,
                project_definition=project_definition,
                shared_context=shared_context or {"entries": []},
                archive=self._archive(),
            )
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        if len(encoded) > MAX_ACTION_INPUT:
            raise ValueError("Bridge action input exceeds its size limit")
        if progress:
            note_action = "Updating" if parameters.get("note_id") else "Creating"
            progress(
                {
                    "phase": "working",
                    "message": note_action + " the Notes draft on the connected Mac"
                    if capability == "bridge.notes.create"
                    else "Preparing the isolated demo on the connected Mac",
                }
            )
        command = shlex.join(
            [
                "/bin/zsh",
                "-lc",
                "exec " + shlex.join(["python3", "-c", _REMOTE_ACTIONS]),
            ]
        )
        completed = self.runner(
            [entry["agent_command"], command],
            encoded,
            self.application_root,
            ACTION_TIMEOUT,
            cancelled,
        )
        try:
            response = json.loads(completed.stdout)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                "The bridge action did not return a readable result"
            ) from exc
        if not isinstance(response, dict):
            raise ValueError("The bridge action did not return an object result")
        if completed.returncode or response.get("error"):
            raise ValueError(response.get("error") or "The bridge action failed")
        expected = {key: entry[key] for key in ("hostname", "username")}
        if response.get("identity") != expected or not isinstance(
            response.get("result"), dict
        ):
            raise ValueError("The bridge action returned an unverified target result")
        result = dict(response["result"])
        if "project_id" in result:
            result["workspace_project_id"] = result.pop("project_id")
        return {
            "accepted": True,
            "effect": "confirmed",
            "check": "The connected Mac confirmed the requested action",
            "bridge_action": capability,
            "bridge": expected,
            **result,
        }
