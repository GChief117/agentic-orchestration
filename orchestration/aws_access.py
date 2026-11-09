"""Fixed AWS CLI sign-in actions for an explicitly enabled local bridge.

Authentication stays in the official AWS CLI and the user's Safari session.
These actions never read credential stores or return AWS identities or tokens.
"""

AWS_CAPABILITIES = frozenset({"bridge.aws.login", "bridge.aws.status"})


def validate_aws_parameters(capability, parameters):
    """Allow only the fixed default-profile workflow, without task overrides."""
    if capability not in AWS_CAPABILITIES:
        raise ValueError("Unsupported AWS access action")
    if not isinstance(parameters, dict) or parameters:
        raise ValueError("AWS access actions accept no task parameters")
    return {}


REMOTE_AWS_ACCESS = r'''
import getpass
import os
from pathlib import Path
import socket
import subprocess

AWS_EXECUTABLE = "/opt/homebrew/bin/aws"
AWS_PROFILE = "default"
AWS_REGION = "us-east-1"
AWS_STATUS_TIMEOUT = 15
AWS_LOGIN_STARTUP_TIMEOUT = 1

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
    return {"authenticated": authenticated, "profile": AWS_PROFILE,
            "region": AWS_REGION,
            "verification": "confirmed" if authenticated else "not_confirmed",
            "requires_user_action": not authenticated}

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

def aws_login(payload):
    """Start native browser sign-in without Terminal automation or output capture."""
    aws_guard(payload)
    if aws_authenticated():
        return {"started": False, "authenticated": True,
                "requires_user_action": False, "profile": AWS_PROFILE,
                "region": AWS_REGION, "verification": "confirmed"}
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
    return {"started": True, "authenticated": False,
            "requires_user_action": True, "profile": AWS_PROFILE,
            "region": AWS_REGION, "verification": "not_confirmed",
            "message": "Complete AWS sign-in and MFA in Safari, then refresh sign-in status."}
'''
