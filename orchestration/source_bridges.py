"""Read-only repository context through an operator-configured local SSH wrapper.

The private ``source-bridges.json`` file maps project IDs to an executable wrapper,
expected host/user, a canonical remote root, and explicit repository folder names.
Browser requests can choose files inside that allowlist; they cannot supply shell
commands, targets, or credentials. No remote access is attempted by ``configured``.
"""

import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import signal
import subprocess
import time

from . import sources

MAX_CONFIG_BYTES = 65536
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
BRIDGE_TIMEOUT = 30
INVENTORY_TTL = 30
MAX_REPOSITORIES = 64
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

# This source is controlled by the application, never a model or browser request.
# Reusing the module keeps remote reads subject to the same descriptor-based
# no-symlink and bounded UTF-8 checks as local source attachments.
_REMOTE_SOURCE = (
    inspect.getsource(sources)
    + r'''
import getpass
import json
import re
import socket
import sys

def bridge_sensitive(path):
    for part in path.split("/"):
        name = part.lower()
        stem = name.rsplit(".", 1)[0]
        if stem in {"secrets", "secret", "credentials", "credential", "tokens", "token", "passwords", "password", "auth", "service-account", "service_account"}:
            return True
    return False

def bridge_revision(root):
    """Read only known Git ref metadata; never run repository commands or hooks."""
    try:
        fd = _directory_fd(root)
        try:
            git_fd = _relative_directory_fd(fd, [".git"])
        finally:
            os.close(fd)
        try:
            def read_ref(parts, limit):
                parent = _relative_directory_fd(git_fd, parts[:-1])
                try:
                    ref = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                finally:
                    os.close(parent)
                with os.fdopen(ref, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        return ""
                    return stream.read(limit).decode("ascii", errors="strict").strip()
            head = read_ref(["HEAD"], 512)
            if head.startswith("ref: "):
                ref_name = head[5:]
                if not re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref_name) or any(p in ("", ".", "..") for p in ref_name.split("/")):
                    return None
                try:
                    head = read_ref(ref_name.split("/"), 128)
                except OSError:
                    packed = read_ref(["packed-refs"], 65536)
                    head = next((line.split(" ")[0] for line in packed.splitlines() if line.endswith(" " + ref_name)), "")
            return head if re.fullmatch(r"[a-fA-F0-9]{40,64}", head) else None
        finally:
            os.close(git_fd)
    except (OSError, ValueError, UnicodeError):
        return None

def bridge_inventory(root, repo):
    names = {"readme", "readme.md", "readme.txt", "package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "composer.json", "pom.xml", "dockerfile", "makefile"}
    candidates = []
    fd = _directory_fd(root)
    try:
        with os.scandir(fd) as entries:
            for index, entry in enumerate(entries):
                if index >= 512:
                    break
                if entry.name.lower() not in names or entry.is_symlink():
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE_BYTES:
                    candidates.append(repo + "/" + entry.name)
    finally:
        os.close(fd)
    return {"name": repo, "available": True, "revision": bridge_revision(root), "candidates": sorted(candidates)}

def bridge_main(payload):
    identity = {"hostname": socket.gethostname(), "username": getpass.getuser()}
    if identity != {"hostname": payload["hostname"], "username": payload["username"]}:
        raise ValueError("Source bridge remote identity does not match its configuration")
    base = _source_root(payload["root"])
    action = payload["action"]
    roots = {}
    inventory = []
    for repo in payload["repositories"]:
        try:
            root = _source_root(str(base / repo))
            fd = _directory_fd(root)
            os.close(fd)
            roots[repo] = root
            if action == "inventory":
                inventory.append(bridge_inventory(root, repo))
        except (OSError, ValueError):
            if action == "inventory":
                inventory.append({"name": repo, "available": False, "revision": None, "candidates": []})
    if action == "inventory":
        return {"identity": identity, "repositories": inventory}
    if action == "list":
        files, truncated = [], len(roots) != len(payload["repositories"])
        for repo, root in roots.items():
            result = list_source_files(str(root))
            truncated = truncated or result["truncated"]
            for item in result["files"]:
                path = repo + "/" + item["path"]
                if bridge_sensitive(path):
                    continue
                if len(files) >= MAX_SCAN_ENTRIES:
                    truncated = True
                    break
                files.append({"path": path, "size": item["size"]})
            if len(files) >= MAX_SCAN_ENTRIES:
                truncated = True
                break
        return {"identity": identity, "files": files, "truncated": truncated}
    if action == "read":
        results, total = {}, 0
        selected = {}
        for relative in payload["paths"]:
            repo, path = relative.split("/", 1)
            if repo not in roots or bridge_sensitive(relative):
                raise ValueError("Source file is outside the configured readable repositories")
            selected.setdefault(repo, []).append(path)
        for repo, paths in selected.items():
            for item in read_source_files(str(roots[repo]), paths):
                total += len(item["content"])
                if total > MAX_TOTAL_CHARACTERS:
                    raise ValueError("Selected source files exceed 48,000 characters")
                relative = repo + "/" + item["path"]
                results[relative] = dict(item, path=relative)
        return {"identity": identity, "sources": [results[path] for path in payload["paths"]]}
    raise ValueError("Unsupported source bridge operation")

try:
    print(json.dumps(bridge_main(json.loads(sys.argv[1])), ensure_ascii=False))
except (ValueError, PermissionError) as exc:
    print(json.dumps({"error": str(exc)}))
except OSError:
    print(json.dumps({"error": "Configured remote source folder is unavailable"}))
'''
)


class SourceBridges:
    """Load private allowlists and fetch bounded, read-only remote source context."""

    def __init__(self, data_dir, *, enabled=True, runner=None):
        """Keep bridge configuration local and allow an isolated runner in tests."""
        self.config_path = Path(data_dir) / "source-bridges.json"
        self.enabled = enabled
        self.runner = runner or self._run
        self._inventory_cache = {}

    def _configuration(self, project_id):
        """Validate one allowlisted target without making a network request."""
        if not self.enabled:
            raise PermissionError("Source bridges are unavailable in hosted mode")
        if not isinstance(project_id, str) or not project_id.isalnum():
            raise ValueError("Invalid project identity")
        if self.config_path.is_symlink():
            raise ValueError("Source bridge configuration cannot be a symbolic link")
        if not self.config_path.exists():
            return None
        with self.config_path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("Source bridge configuration exceeds its size limit")
        try:
            config = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                "Source bridge configuration must contain valid JSON"
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(
                "Source bridge configuration must map project IDs to targets"
            )
        entry = config.get(project_id)
        if entry is None:
            return None
        required = {"command", "hostname", "username", "root", "repositories"}
        if (
            not isinstance(entry, dict)
            or not required <= set(entry)
            or set(entry)
            - required
            - {"name", "agent_command", "demo_directory", "notes_enabled"}
        ):
            raise ValueError("Source bridge target has invalid configuration fields")
        entry = dict(entry)
        command = entry["command"]
        if not isinstance(command, str) or not Path(command).is_absolute():
            raise ValueError(
                "Source bridge command must be an absolute executable path"
            )
        executable = Path(command)
        if (
            not executable.is_file()
            or executable.resolve() != executable
            or not os.access(executable, os.X_OK)
        ):
            raise ValueError(
                "Source bridge command must be a canonical executable file"
            )
        for field in ("hostname", "username"):
            if not isinstance(entry[field], str) or not _SAFE_NAME.fullmatch(
                entry[field]
            ):
                raise ValueError("Source bridge identity is invalid")
        root = entry["root"]
        if (
            not isinstance(root, str)
            or not root.startswith("/")
            or "\x00" in root
            or str(PurePosixPath(root)) != root
            or ".." in PurePosixPath(root).parts
        ):
            raise ValueError("Source bridge root must be an absolute canonical path")
        repositories = entry["repositories"]
        if (
            not isinstance(repositories, list)
            or not 1 <= len(repositories) <= MAX_REPOSITORIES
            or any(
                not isinstance(repo, str) or not _SAFE_NAME.fullmatch(repo)
                for repo in repositories
            )
            or len(set(repositories)) != len(repositories)
        ):
            raise ValueError(
                "Source bridge repositories must be distinct safe folder names"
            )
        if "name" in entry and (
            not isinstance(entry["name"], str)
            or not entry["name"].strip()
            or len(entry["name"]) > 120
        ):
            raise ValueError("Source bridge name must be short text")
        if "agent_command" in entry:
            agent_command = entry["agent_command"]
            if (
                not isinstance(agent_command, str)
                or not Path(agent_command).is_absolute()
            ):
                raise ValueError(
                    "Native agent command must be an absolute executable path"
                )
            agent_path = Path(agent_command)
            if (
                not agent_path.is_file()
                or agent_path.resolve() != agent_path
                or not os.access(agent_path, os.X_OK)
            ):
                raise ValueError(
                    "Native agent command must be a canonical executable file"
                )
        if "notes_enabled" in entry and type(entry["notes_enabled"]) is not bool:
            raise ValueError("Notes capability must be explicitly enabled or disabled")
        if "demo_directory" in entry:
            demo = entry["demo_directory"]
            if (
                not isinstance(demo, str)
                or str(PurePosixPath(demo)) != demo
                or PurePosixPath(demo).parent != PurePosixPath(root)
                or PurePosixPath(demo).name in repositories
            ):
                raise ValueError(
                    "The demo directory must be a separate folder directly inside the configured root"
                )
        return entry

    def configured(self, project_id):
        """Report configuration availability without claiming a live connection."""
        return self.enabled and self._configuration(project_id) is not None

    @staticmethod
    def _run(args, timeout):
        """Run the fixed wrapper command with a deadline and bounded output pipes."""
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        started = time.monotonic()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if time.monotonic() - started > timeout:
                        raise ValueError("Source bridge request timed out")
                    for key, _ in selector.select(0.1):
                        data = os.read(key.fileobj.fileno(), 32768)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        streams[key.data].extend(data)
                        if sum(map(len, streams.values())) > MAX_OUTPUT_BYTES:
                            raise ValueError(
                                "Source bridge response exceeded its size limit"
                            )
            code = process.wait(
                timeout=max(0.1, timeout - (time.monotonic() - started))
            )
            if code:
                raise ValueError(
                    "Source bridge command failed; check the configured connection"
                )
            return bytes(streams["stdout"])
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def _request(self, project_id, action, paths=None, entry=None):
        """Send only a fixed reader program and validated data through the wrapper."""
        entry = entry or self._configuration(project_id)
        if entry is None:
            raise ValueError("No source bridge is configured for this project")
        payload = {
            key: entry[key] for key in ("hostname", "username", "root", "repositories")
        }
        payload.update(action=action, paths=paths or [])
        command = shlex.join(["python3", "-c", _REMOTE_SOURCE, json.dumps(payload)])
        try:
            raw = self.runner([entry["command"], command], BRIDGE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Source bridge connection is unavailable") from exc
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
            raise ValueError("Source bridge returned an invalid response")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Source bridge returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("Source bridge returned an invalid response")
        if "error" in result:
            raise ValueError(str(result["error"])[:1000])
        expected = {key: entry[key] for key in ("hostname", "username")}
        if result.get("identity") != expected:
            raise ValueError(
                "Source bridge remote identity does not match its configuration"
            )
        field = {"inventory": "repositories", "list": "files", "read": "sources"}[
            action
        ]
        if not isinstance(result.get(field), list):
            raise ValueError("Source bridge returned an invalid response")
        result.pop("identity")
        result["bridge"] = {
            **expected,
            "name": entry.get("name", "Source bridge"),
            "status": "connected",
        }
        return result

    def inventory(self, project_id):
        """Return cached repository revisions and likely starting files for planning."""
        entry = self._configuration(project_id)
        key = json.dumps(entry, sort_keys=True)
        cached = self._inventory_cache.get(project_id)
        if cached and cached[0] == key and time.monotonic() - cached[1] < INVENTORY_TTL:
            return cached[2]
        result = self._request(project_id, "inventory", entry=entry)
        self._inventory_cache[project_id] = (key, time.monotonic(), result)
        return result

    def list_files(self, project_id):
        """Explicitly list safe files from only this project's allowed repositories."""
        return self._request(project_id, "list")

    def validate_paths(self, project_id, paths):
        """Validate the local allowlist without an SSH probe or reading file contents."""
        entry = self._configuration(project_id)
        if entry is None:
            raise ValueError("No source bridge is configured for this project")
        if (
            not isinstance(paths, list)
            or not 1 <= len(paths) <= sources.MAX_SOURCE_FILES
            or any(not isinstance(path, str) for path in paths)
            or len(set(paths)) != len(paths)
        ):
            raise ValueError("Select between one and eight distinct source files")
        for path in paths:
            parts = sources._relative_parts(path)
            if len(parts) < 2 or parts[0] not in entry["repositories"]:
                raise ValueError("Source file is outside the configured repositories")
            if any(
                part.lower().rsplit(".", 1)[0]
                in {
                    "secrets",
                    "secret",
                    "credentials",
                    "credential",
                    "tokens",
                    "token",
                    "passwords",
                    "password",
                    "auth",
                    "service-account",
                    "service_account",
                }
                for part in parts
            ):
                raise ValueError("Secret files cannot be attached as project sources")
        return paths

    def read_files(self, project_id, paths):
        """Read chosen virtual paths after local validation; recheck remotely on open."""
        self.validate_paths(project_id, paths)
        entry = self._configuration(project_id)
        result = self._request(project_id, "read", paths, entry)
        return result["sources"]
