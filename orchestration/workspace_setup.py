"""Run finite setup steps selected from private, operator-reviewed definitions.

Tasks select a step name. They cannot provide commands, environment values, or
verification programs. Native command output is never returned to the app.
"""

import inspect
import re
from pathlib import PurePosixPath

SETUP_CAPABILITY = "bridge.setup.run"


def _setup_text(value, maximum):
    """Accept bounded single-line configuration text without control characters."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _setup_relative(value):
    """Require one canonical repository-relative input path."""
    return (
        _setup_text(value, 500)
        and not PurePosixPath(value).is_absolute()
        and "\\" not in value
        and str(PurePosixPath(value)) == value
        and all(part not in (".", "..") for part in PurePosixPath(value).parts)
        and value != "."
    )


def _setup_argv(value):
    """Validate a fixed argv vector without interpreting shell syntax."""
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 32
        and all(_setup_text(argument, 16384) for argument in value)
        and PurePosixPath(value[0]).is_absolute()
        and str(PurePosixPath(value[0])) == value[0]
        and ".." not in PurePosixPath(value[0]).parts
    )


def validate_setup_parameters(parameters):
    """Allow a task to select exactly one private setup step by its stable ID."""
    if (
        not isinstance(parameters, dict)
        or set(parameters) != {"step"}
        or not isinstance(parameters["step"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", parameters["step"])
    ):
        raise ValueError("Choose one configured setup step")
    return {"step": parameters["step"]}


def _setup_aws_guard(account_id, profile):
    """Require a complete AWS guard or explicit nulls for a local-only step."""
    if account_id is None and profile is None:
        return True
    return (
        isinstance(account_id, str)
        and re.fullmatch(r"[0-9]{12}", account_id) is not None
        and isinstance(profile, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", profile) is not None
    )


def validate_setup_steps(entry):
    """Validate private command definitions; return normalized independent copies."""
    steps = entry.get("setup_steps", {})
    if not isinstance(steps, dict) or len(steps) > 64:
        raise ValueError("Setup steps must be a bounded map of reviewed definitions")
    result = {}
    required = {
        "label",
        "repository",
        "argv",
        "timeout_seconds",
        "input_hashes",
        "verify_argv",
        "aws_account_id",
        "aws_profile",
    }
    allowed_aws = {
        "AWS_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_SDK_LOAD_CONFIG",
    }
    forbidden_env = {
        "ENV",
        "BASH_ENV",
        "ZDOTDIR",
        "PATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "LD_PRELOAD",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
    }
    for name, specification in steps.items():
        validate_setup_parameters({"step": name})
        if (
            not isinstance(specification, dict)
            or not required <= set(specification)
            or set(specification) - required - {"env"}
            or not _setup_text(specification["label"], 160)
            or not isinstance(specification["repository"], str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", specification["repository"]
            )
            or specification["repository"] not in entry.get("repositories", [])
            or not _setup_argv(specification["argv"])
            or not _setup_argv(specification["verify_argv"])
            or type(specification["timeout_seconds"]) is not int
            or not 1 <= specification["timeout_seconds"] <= 1800
            or not _setup_aws_guard(
                specification["aws_account_id"], specification["aws_profile"]
            )
        ):
            raise ValueError("Setup step has invalid reviewed configuration")
        hashes = specification["input_hashes"]
        if (
            not isinstance(hashes, dict)
            or not 1 <= len(hashes) <= 512
            or any(
                not _setup_relative(path)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
                for path, digest in hashes.items()
            )
        ):
            raise ValueError("Setup steps require reviewed repository input hashes")
        environment = specification.get("env", {})
        if not isinstance(environment, dict) or len(environment) > 32:
            raise ValueError(
                "Setup environment must contain bounded non-secret settings"
            )
        for key, value in environment.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key)
                or key in forbidden_env
                or key.startswith("DYLD_")
                or (key.startswith("AWS_") and key not in allowed_aws)
                or re.search(
                    r"(?:^|_)(?:KEY|TOKEN|PASSWORD|SECRET|CREDENTIALS?|COOKIE|SESSION)(?:_|$)",
                    key,
                )
                or not _setup_text(value, 4096)
                or re.search(r"://[^/\s]*@", value)
                or re.search(
                    r"[?&](?:token|key|secret|password|credential)=[^&]+", value, re.I
                )
                or (
                    key == "AWS_CONFIG_FILE"
                    and (
                        not PurePosixPath(value).is_absolute()
                        or str(PurePosixPath(value)) != value
                        or ".." in PurePosixPath(value).parts
                    )
                )
            ):
                raise ValueError(
                    "Setup environment must contain bounded non-secret settings"
                )
        result[name] = {
            **specification,
            "argv": list(specification["argv"]),
            "verify_argv": list(specification["verify_argv"]),
            "input_hashes": {path: digest.lower() for path, digest in hashes.items()},
            "env": dict(environment),
        }
    return result


_VALIDATORS = "\n".join(
    inspect.getsource(function)
    for function in (
        _setup_text,
        _setup_relative,
        _setup_argv,
        validate_setup_parameters,
        _setup_aws_guard,
        validate_setup_steps,
    )
)

REMOTE_WORKSPACE_SETUP = (
    "\nimport re\nfrom pathlib import PurePosixPath\n"
    + _VALIDATORS
    + r'''
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import tempfile
import time

SETUP_LEDGER_LIMIT = 1024

class SetupInterrupted(Exception):
    """Represent a requested remote setup cancellation without native diagnostics."""

def setup_directory(value, create=False):
    """Resolve an owned canonical directory without traversing symbolic links."""
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Setup requires canonical workspace directories")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Setup cannot traverse symbolic links")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("Setup workspace is unavailable")
    return path

def setup_state(root):
    """Keep operation ownership private and separated by the workspace root."""
    folder = Path.home() / "Library/Application Support/Agentic Orchestration/setup"
    folder = setup_directory(folder, create=True)
    target = setup_directory(folder / hashlib.sha256(str(root).encode()).hexdigest(), create=True)
    for path in (folder, target):
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Setup state must be private to the connected user")
    return target

def setup_write_record(path, value):
    """Publish only bounded operation metadata as a private atomic JSON record."""
    if path.is_symlink():
        raise ValueError("Setup operation record cannot be redirected")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def setup_read_record(path):
    """Read a previous owned operation without replaying uncertain work."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Setup operation record must be private and regular")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Setup operation record exceeds its size limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Setup operation record is invalid")
    return result

@contextmanager
def setup_workspace_lock(directory):
    """Prevent separate goals from changing the same workspace concurrently."""
    fd = os.open(directory / "workspace.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Setup workspace lock must be private and regular")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another setup operation owns this workspace")
        yield

def setup_environment(specification):
    """Remove inherited AWS overrides and apply only reviewed environment values."""
    forbidden = {"ENV", "BASH_ENV", "ZDOTDIR", "PYTHONSTARTUP", "PYTHONHOME", "LD_PRELOAD"}
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("AWS_") and not key.startswith("DYLD_") and key not in forbidden}
    environment.update(specification.get("env", {}))
    profile = specification.get("env", {}).get("AWS_PROFILE", specification["aws_profile"])
    if profile is not None:
        environment["AWS_PROFILE"] = profile
    environment.update(AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off", AWS_EC2_METADATA_DISABLED="true")
    return environment

def setup_verify_inputs(repository, hashes):
    """Check reviewed inputs by content hash immediately before starting commands."""
    total = 0
    for relative, expected in hashes.items():
        path = repository / relative
        if path.resolve() != path or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("A reviewed setup input was redirected")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                raise ValueError("A reviewed setup input is unavailable or oversized")
            total += info.st_size
            if total > 64 * 1024 * 1024:
                raise ValueError("Reviewed setup inputs exceed their combined size limit")
            digest_state = hashlib.sha256()
            for chunk in iter(lambda: stream.read(65536), b""):
                digest_state.update(chunk)
            digest = digest_state.hexdigest()
        if digest != expected:
            raise ValueError("Reviewed setup inputs changed; review the step again")

def setup_verify_aws(specification, environment):
    """Verify the configured work account without returning identity or token values."""
    guard_environment = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    guard_environment.update(AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off", AWS_EC2_METADATA_DISABLED="true")
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/aws", "sts", "get-caller-identity", "--profile",
             specification["aws_profile"], "--no-cli-pager", "--output", "json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15, env=guard_environment,
        )
        identity = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) < 8192 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError("Setup requires a verified AWS CLI session for the configured work account") from None
    if not isinstance(identity, dict) or identity.get("Account") != specification["aws_account_id"]:
        raise ValueError("Setup requires a verified AWS CLI session for the configured work account")

def setup_process_birth(pid):
    """Record child ownership metadata without reading process arguments."""
    try:
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2)
        return result.stdout.strip()[:80] if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

def setup_process_matches(pid, birth):
    """Recognize an existing owner without adopting or signaling its process."""
    if type(pid) is not int or pid <= 1 or not isinstance(birth, str) or not birth or len(birth) > 80:
        return False
    try:
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "uid=,lstart="],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2)
        fields = result.stdout.strip().split(None, 1)
        return (result.returncode == 0 and len(fields) == 2
                and int(fields[0]) == os.getuid() and fields[1] == birth)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False

def setup_require_reconciled_workspace(directory, current):
    """Block new work when another operation may still own remote effects."""
    for index, path in enumerate(directory.iterdir()):
        if index >= SETUP_LEDGER_LIMIT:
            raise ValueError("Setup ledger limit reached; reconcile the workspace before continuing")
        if path == current or not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
            continue
        record = setup_read_record(path)
        if record is None or record.get("state") in ("complete", "failed"):
            continue
        if record.get("state") != "running":
            raise ValueError("A previous setup operation needs reconciliation before new work")
        if (setup_process_matches(record.get("child_pid"), record.get("child_birth"))
                or setup_process_matches(record.get("supervisor_pid"), record.get("supervisor_birth"))):
            raise ValueError("Another setup operation is still running in this workspace")
        raise ValueError("A previous setup operation needs reconciliation before new work")

def setup_stop_process(process):
    """Terminate the owned subprocess group, including normal build children."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

def setup_run_command(argv, repository, environment, deadline, interrupted, record, record_path, phase):
    """Execute fixed arguments without exposing native stdout or stderr."""
    if interrupted[0]:
        raise SetupInterrupted("Setup was interrupted; reconcile the operation before retrying")
    if time.monotonic() >= deadline:
        raise ValueError("Setup " + phase + " exceeded its time limit")
    try:
        process = subprocess.Popen(argv, cwd=repository, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
    except OSError:
        raise ValueError("Setup " + phase + " could not start") from None
    record.update(phase=phase, child_pid=process.pid, child_birth=setup_process_birth(process.pid))
    setup_write_record(record_path, record)
    try:
        while process.poll() is None:
            if interrupted[0]:
                raise SetupInterrupted("Setup was interrupted; reconcile the operation before retrying")
            if time.monotonic() >= deadline:
                raise ValueError("Setup " + phase + " exceeded its time limit")
            time.sleep(0.1)
        if interrupted[0]:
            raise SetupInterrupted("Setup was interrupted; reconcile the operation before retrying")
        if process.returncode:
            record["exit_code"] = process.returncode
            raise ValueError("Setup " + phase + " exited with code " + str(process.returncode))
        return process.returncode
    finally:
        setup_stop_process(process)

def workspace_setup(payload):
    """Run and verify one operator-defined finite step under a durable workspace lock."""
    if (payload.get("hostname") != socket.gethostname()
            or payload.get("username") != getpass.getuser()):
        raise ValueError("Setup requires the configured remote host and user")
    parameters = validate_setup_parameters(payload.get("parameters"))
    name = parameters["step"]
    specification = validate_setup_steps({"repositories": payload.get("repositories", []),
                                          "setup_steps": {name: payload.get("setup_step")}})[name]
    aws_guard_enabled = specification["aws_account_id"] is not None
    operation = payload.get("operation_id")
    if not isinstance(operation, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", operation):
        raise ValueError("Setup requires a stable operation identity")
    root = setup_directory(payload["root"])
    repository = setup_directory(root / specification["repository"])
    directory = setup_state(root)
    path = directory / (hashlib.sha256(operation.encode()).hexdigest() + ".json")
    fingerprint = hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with setup_workspace_lock(directory):
        previous = setup_read_record(path)
        if previous is not None:
            if (previous.get("operation_id") != operation or previous.get("step") != name
                    or previous.get("definition_sha256") != fingerprint):
                raise ValueError("Setup operation definition changed; reconcile it before retrying")
            if previous.get("state") == "complete" and isinstance(previous.get("result"), dict):
                stored = previous["result"]
                duration = stored.get("duration_seconds")
                if (stored.get("verified") is not True or stored.get("exit_code") != 0
                        or stored.get("aws_account_verified") is not aws_guard_enabled
                        or type(duration) not in (int, float) or not 0 <= duration <= 1900):
                    raise ValueError("Completed setup receipt is invalid; reconcile before retrying")
                return {"step": name, "label": specification["label"], "repository": specification["repository"],
                        "exit_code": 0, "verified": True, "aws_account_verified": aws_guard_enabled,
                        "duration_seconds": duration, "already_completed": True}
            raise ValueError("A previous setup attempt needs reconciliation before retrying")
        setup_require_reconciled_workspace(directory, path)
        record = {"operation_id": operation, "step": name, "definition_sha256": fingerprint,
                  "state": "running", "phase": "preflight", "started": time.time(),
                  "supervisor_pid": os.getpid(), "supervisor_birth": setup_process_birth(os.getpid())}
        setup_write_record(path, record)
        interrupted = [False]
        def stop(_signum, _frame):
            interrupted[0] = True
        previous_handlers = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGHUP)}
        try:
            setup_verify_inputs(repository, specification["input_hashes"])
            environment = setup_environment(specification)
            if aws_guard_enabled:
                setup_verify_aws(specification, environment)
            setup_verify_inputs(repository, specification["input_hashes"])
            deadline = time.monotonic() + specification["timeout_seconds"]
            setup_run_command(specification["argv"], repository, environment, deadline,
                              interrupted, record, path, "command")
            setup_run_command(specification["verify_argv"], repository, environment, deadline,
                              interrupted, record, path, "verification")
            result = {"step": name, "label": specification["label"], "repository": specification["repository"],
                      "exit_code": 0, "verified": True, "aws_account_verified": aws_guard_enabled,
                      "duration_seconds": round(time.time() - record["started"], 3)}
            record.update(state="complete", phase="complete", finished=time.time(), result=result)
            setup_write_record(path, record)
            return result
        except (ValueError, SetupInterrupted) as exc:
            record.update(state="failed", finished=time.time(), error=str(exc))
            setup_write_record(path, record)
            raise ValueError(str(exc)) from None
        except Exception:
            record.update(state="failed", finished=time.time(), error="Setup failed during " + record["phase"])
            setup_write_record(path, record)
            raise ValueError(record["error"]) from None
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
'''
)
