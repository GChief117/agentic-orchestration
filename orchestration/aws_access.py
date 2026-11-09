"""Fixed AWS CLI sign-in actions for an explicitly enabled local bridge.

Authentication stays in the official AWS CLI and the user's Safari session.
These actions never read credential stores or return AWS identities or tokens.
"""

AWS_CAPABILITIES = frozenset(
    {"bridge.aws.login", "bridge.aws.status", "bridge.aws.remote_login"}
)


def validate_aws_parameters(capability, parameters):
    """Allow only the fixed default-profile workflow, without task overrides."""
    if capability not in AWS_CAPABILITIES:
        raise ValueError("Unsupported AWS access action")
    if not isinstance(parameters, dict) or parameters:
        raise ValueError("AWS access actions accept no task parameters")
    return {}


REMOTE_AWS_ACCESS = r'''
from contextlib import contextmanager
import fcntl
import getpass
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time

AWS_EXECUTABLE = "/opt/homebrew/bin/aws"
AWS_PROFILE = "default"
AWS_REGION = "us-east-1"
AWS_STATUS_TIMEOUT = 15
AWS_LOGIN_STARTUP_TIMEOUT = 1
AWS_LOGIN_LIFETIME = 600

def aws_remote_command_path():
    """Return the one app-owned Terminal launcher path."""
    return Path.home() / "Library/Application Support/Agentic Orchestration/aws-login/remote-login.command"

def aws_process_identity(pid, allow_wrapper=False):
    """Recognize only this user's exact native login command and its birth time."""
    if type(pid) is not int or pid <= 1:
        return None
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "uid=,lstart=,args="],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2,
        )
        fields = result.stdout.strip().split(None, 6)
        if result.returncode or len(fields) != 7 or len(result.stdout) > 4096:
            return None
        uid = int(fields[0])
        if uid != os.getuid():
            return None
        if allow_wrapper and fields[6] == "/bin/zsh " + str(aws_remote_command_path()):
            return {"uid": uid, "birth": " ".join(fields[1:6])}
        arguments = shlex.split(fields[6])
        executable = Path(AWS_EXECUTABLE).resolve()
        interpreter = (executable.parent / "python").resolve()
        interpreters = {interpreter}
        if interpreter.parent.name == "bin" and interpreter.parent.parent.parent.name == "Versions":
            interpreters.add(interpreter.parent.parent / "Resources/Python.app/Contents/MacOS/Python")
        if (len(arguments) in (7, 8)
                and Path(arguments[0]).is_absolute()
                and Path(arguments[0]).resolve() in interpreters):
            arguments = arguments[1:]
        if (not arguments or not Path(arguments[0]).is_absolute()
                or Path(arguments[0]).resolve() != executable
                or arguments[1:] not in (["login", "--profile", AWS_PROFILE, "--region", AWS_REGION],
                                        ["login", "--remote", "--profile", AWS_PROFILE, "--region", AWS_REGION])):
            return None
        return {"uid": uid, "birth": " ".join(fields[1:6])}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None

def aws_state_directory(create=False):
    """Use a private canonical app directory without following symbolic links."""
    directory = Path.home() / "Library/Application Support/Agentic Orchestration/aws-login"
    if not directory.is_absolute() or directory.resolve() != directory:
        raise ValueError("AWS login state must use a canonical private directory")
    for parent in (directory, *directory.parents):
        if parent.is_symlink():
            raise ValueError("AWS login state cannot follow symbolic links")
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.exists():
        return None
    status = directory.stat()
    if (not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid()
            or stat.S_IMODE(status.st_mode) != 0o700):
        raise ValueError("AWS login state requires a private directory owned by the current user")
    return directory

@contextmanager
def aws_login_lock(create=False):
    """Serialize pending-login checks and writes to prevent duplicate launches."""
    directory = aws_state_directory(create)
    if directory is None:
        yield None
        return
    try:
        descriptor = os.open(directory / "pending.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise ValueError("AWS login lock must be a private regular file")
    with os.fdopen(descriptor, "a") as lock:
        status = os.fstat(lock.fileno())
        if (not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid()
                or stat.S_IMODE(status.st_mode) != 0o600):
            raise ValueError("AWS login lock must be a private regular file")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield directory

def aws_pending_login(directory):
    """Recognize a live owned request; stale or reused PIDs never count as pending."""
    if directory is None:
        return False
    path = directory / "pending.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        raise ValueError("AWS login record must be a private regular file")
    with os.fdopen(descriptor, "rb") as stream:
        status = os.fstat(stream.fileno())
        if (not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid()
                or stat.S_IMODE(status.st_mode) != 0o600):
            raise ValueError("AWS login record must be a private regular file")
        raw = stream.read(2049)
    if len(raw) > 2048:
        raise ValueError("AWS login record exceeds its size limit")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(value, dict) or set(value) != {"pid", "uid", "birth", "started"}:
        return False
    started = value["started"]
    if (type(started) not in (int, float) or not 0 <= time.time() - started < AWS_LOGIN_LIFETIME
            or type(value["uid"]) is not int or value["uid"] != os.getuid()
            or not isinstance(value["birth"], str) or len(value["birth"]) > 80):
        return False
    identity = aws_process_identity(value["pid"])
    if identity is None and time.time() - started < 5:
        identity = aws_process_identity(value["pid"], allow_wrapper=True)
    return identity == {"uid": value["uid"], "birth": value["birth"]}

def aws_save_pending(directory, pid, identity, started):
    """Atomically save only process ownership metadata, never authentication data."""
    destination = directory / "pending.json"
    if destination.is_symlink():
        raise ValueError("AWS login record cannot be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"pid": pid, "uid": identity["uid"], "birth": identity["birth"], "started": started}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def aws_guard(payload):
    """Require explicit configuration, a verified user/host, and no parameters."""
    if payload.get("aws_login_enabled") is not True:
        raise ValueError("AWS access is not enabled for this bridge")
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict) or parameters:
        raise ValueError("AWS access actions accept no task parameters")
    if (payload.get("hostname") != socket.gethostname()
            or payload.get("username") != getpass.getuser()):
        raise ValueError("AWS access requires the configured remote identity")
    if not Path(AWS_EXECUTABLE).is_file() or not os.access(AWS_EXECUTABLE, os.X_OK):
        raise ValueError("The official AWS CLI is unavailable at its configured location")

def aws_environment():
    """Let the AWS CLI use native default configuration without AWS overrides."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("AWS_")}
    environment.update(AWS_CLI_AUTO_PROMPT="off", AWS_PAGER="",
                       AWS_EC2_METADATA_DISABLED="true")
    return environment

def aws_authenticated():
    """Verify the native saved login while discarding all returned identity data."""
    try:
        result = subprocess.run(
            [AWS_EXECUTABLE, "sts", "get-caller-identity", "--profile", AWS_PROFILE,
             "--region", AWS_REGION, "--no-cli-pager"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=aws_environment(),
            timeout=AWS_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0

def aws_status(payload):
    """Report only whether the saved default login was successfully verified."""
    aws_guard(payload)
    authenticated = aws_authenticated()
    with aws_login_lock() as directory:
        pending = not authenticated and aws_pending_login(directory)
    return {"authenticated": authenticated, "profile": AWS_PROFILE,
            "region": AWS_REGION,
            "verification": "confirmed" if authenticated else "not_confirmed",
            "requires_user_action": not authenticated,
            "login_in_progress": pending}

def aws_login_environment():
    """Give native login only its default configuration and fixed Safari opener."""
    return {
        "HOME": str(Path.home()),
        "USER": getpass.getuser(),
        "LOGNAME": getpass.getuser(),
        "SHELL": "/bin/zsh",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "xterm-256color",
        "LANG": "en_US.UTF-8",
        "BROWSER": "/usr/bin/open -a Safari %s",
        "AWS_CLI_AUTO_PROMPT": "off",
        "AWS_PAGER": "",
        "AWS_EC2_METADATA_DISABLED": "true",
    }

# AWS_TERMINAL_REGISTRATION

def aws_write_launcher(path, content, mode):
    """Write one fixed private launcher without following redirected files."""
    if path.is_symlink():
        raise ValueError("AWS Terminal launcher cannot be a symbolic link")
    descriptor, temporary = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def aws_remote_login(payload):
    """Open the official code-entry flow in Terminal without capturing its output."""
    aws_guard(payload)
    with aws_login_lock(create=True) as directory:
        if aws_authenticated():
            return {"started": False, "authenticated": True, "method": "remote",
                    "requires_user_action": False, "profile": AWS_PROFILE,
                    "region": AWS_REGION, "verification": "confirmed"}
        if aws_pending_login(directory):
            return {"started": False, "authenticated": False, "method": "remote",
                    "pending": True, "login_in_progress": True,
                    "requires_user_action": True, "profile": AWS_PROFILE,
                    "region": AWS_REGION, "verification": "not_confirmed",
                    "message": "A sign-in request is already open. Complete it before starting another."}
        registration = directory / "remote-register.py"
        launcher = aws_remote_command_path()
        command = shlex.join(["/usr/bin/env", "-i"]
                            + [key + "=" + value for key, value in aws_login_environment().items()]
                            + [AWS_EXECUTABLE, "login", "--remote", "--profile", AWS_PROFILE,
                               "--region", AWS_REGION])
        contents = "#!/bin/zsh\nset -eu\n" + shlex.join([sys.executable, str(registration)]) + "\nexec " + command + "\n"
        aws_write_launcher(registration, AWS_TERMINAL_REGISTRATION, 0o600)
        aws_write_launcher(launcher, contents, 0o700)
        try:
            opened = subprocess.run(["/usr/bin/open", "-a", "Terminal", str(launcher)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("The alternate AWS sign-in could not open in Terminal")
        if opened.returncode:
            raise ValueError("The alternate AWS sign-in could not open in Terminal")
    return {"started": True, "authenticated": False, "method": "remote",
            "requires_user_action": True, "profile": AWS_PROFILE,
            "region": AWS_REGION, "verification": "not_confirmed",
            "message": "In Terminal, open the official AWS sign-in link in Safari. Complete sign-in and MFA, then paste the returned authorization code into that Terminal window. Do not paste it into chat. Refresh sign-in status afterward."}

def aws_login(payload):
    """Start native browser sign-in without Terminal automation or output capture."""
    aws_guard(payload)
    with aws_login_lock(create=True) as directory:
        if aws_authenticated():
            return {"started": False, "authenticated": True,
                    "requires_user_action": False, "profile": AWS_PROFILE,
                    "region": AWS_REGION, "verification": "confirmed",
                    "pending": False, "login_in_progress": False}
        if aws_pending_login(directory):
            return {"started": False, "authenticated": False,
                    "requires_user_action": True, "profile": AWS_PROFILE,
                    "region": AWS_REGION, "verification": "not_confirmed",
                    "pending": True, "login_in_progress": True,
                    "message": "AWS sign-in is already open in Safari. Complete that request, then refresh sign-in status."}
        started = time.time()
        try:
            process = subprocess.Popen(
                [AWS_EXECUTABLE, "login", "--profile", AWS_PROFILE,
                 "--region", AWS_REGION],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=aws_login_environment(),
                start_new_session=True, close_fds=True,
            )
        except OSError:
            raise ValueError("AWS sign-in could not start in Safari on the connected Mac")
        try:
            returncode = process.wait(timeout=AWS_LOGIN_STARTUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            returncode = None
        if returncode not in (None, 0):
            raise ValueError("AWS sign-in could not start in Safari on the connected Mac")
        if returncode is None:
            identity = aws_process_identity(process.pid)
            if identity is None:
                raise ValueError("AWS sign-in started, but its process ownership could not be verified")
            aws_save_pending(directory, process.pid, identity, started)
    return {"started": True, "authenticated": False,
            "requires_user_action": True, "profile": AWS_PROFILE,
            "region": AWS_REGION, "verification": "not_confirmed",
            "pending": returncode is None, "login_in_progress": returncode is None,
            "message": "Complete AWS sign-in and MFA in Safari, then refresh sign-in status."}
'''

# The Terminal helper registers its own parent shell immediately before that
# shell execs the fixed AWS command. It contains process metadata only.
_AWS_TERMINAL_REGISTRATION = (
    REMOTE_AWS_ACCESS.split("\ndef aws_status(", 1)[0]
    + r"""
with aws_login_lock(create=True) as directory:
    if aws_pending_login(directory):
        print("An AWS sign-in request is already open. Complete it before starting another.")
        raise SystemExit(3)
    parent = os.getppid()
    identity = aws_process_identity(parent, allow_wrapper=True)
    if identity is None:
        raise SystemExit("The AWS Terminal launcher identity could not be verified")
    aws_save_pending(directory, parent, identity, time.time())
"""
)
REMOTE_AWS_ACCESS = REMOTE_AWS_ACCESS.replace(
    "\n# AWS_TERMINAL_REGISTRATION\n",
    "\nAWS_TERMINAL_REGISTRATION = " + repr(_AWS_TERMINAL_REGISTRATION) + "\n",
    1,
)
