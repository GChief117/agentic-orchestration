"""Provider sign-in through unmodified local CLIs; credentials stay with each CLI."""

import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "tool": "Codex CLI",
        "binary": "codex",
        "login": ["login"],
        "check": ["login", "status"],
        "docs": "https://developers.openai.com/codex/auth",
    },
    "claude": {
        "name": "Claude",
        "tool": "Claude Code",
        "binary": "claude",
        "login": ["auth", "login"],
        "check": ["auth", "status", "--json"],
        "docs": "https://code.claude.com/docs/en/authentication",
    },
    "gemini": {
        "name": "Gemini",
        "tool": "Antigravity CLI",
        "binary": "agy",
        "login": [],
        "check": None,
        "docs": "https://antigravity.google/",
    },
}


class ProviderConnections:
    """Discover official tools and launch explicit, user-driven sign-in sessions."""

    def __init__(self, directory):
        """Keep generated launchers under the private runtime, outside project files."""
        self.directory = Path(directory) / "provider-signin"

    def list(self):
        """Return safe status metadata without reading or returning credential files."""
        return [self.status(key) for key in PROVIDERS]

    def status(self, key):
        """Ask supported CLIs about login; tool availability alone is not authentication."""
        provider = self._provider(key)
        binary = shutil.which(provider["binary"])
        state = "not_installed" if not binary else "unverified"
        if binary and provider["check"]:
            try:
                result = subprocess.run(
                    [binary, *provider["check"]],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    stdin=subprocess.DEVNULL,
                )
                if key == "claude":
                    logged_in = json.loads(result.stdout).get("loggedIn") is True
                else:
                    logged_in = result.returncode == 0
                state = "signed_in" if logged_in else "signed_out"
            except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
                state = "unverified"
        return {
            "id": key,
            "name": provider["name"],
            "tool": provider["tool"],
            "state": state,
            "installed": bool(binary),
            "can_launch": bool(binary) and platform.system() == "Darwin",
            "command": shlex.join([provider["binary"], *provider["login"]]),
            "docs": provider["docs"],
        }

    def sign_in(self, key):
        """Open the provider's own sign-in in Terminal after an authenticated click.

        No shell input is accepted from the client. OAuth tokens and account details
        remain owned by the official CLI, and launch success never means signed in.
        """
        provider = self._provider(key)
        binary = shutil.which(provider["binary"])
        if not binary:
            raise ValueError(
                f"Install {provider['tool']} first, then refresh connections."
            )
        if platform.system() != "Darwin":
            raise ValueError(
                "Run the displayed sign-in command in your local terminal."
            )
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        command = shlex.join([binary, *provider["login"]])
        script = (
            "#!/bin/zsh\n"
            f"export PATH={shlex.quote(os.environ.get('PATH', ''))}\n"
            f"cd -- {shlex.quote(str(self.directory.resolve()))}\n"
            f"exec {command}\n"
        )
        descriptor, filename = tempfile.mkstemp(
            prefix=f"{key}-", suffix=".command", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(script)
            Path(filename).chmod(0o700)
            subprocess.run(
                ["/usr/bin/open", "-a", "Terminal", filename],
                check=True,
                capture_output=True,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            Path(filename).unlink(missing_ok=True)
            raise ValueError(
                "Could not open Terminal. Run the displayed command manually."
            ) from exc
        return {
            "state": "launched",
            "message": "Finish sign-in in Terminal, then refresh status.",
        }

    @staticmethod
    def _provider(key):
        """Reject unknown provider IDs before constructing any command or filename."""
        if not isinstance(key, str) or key not in PROVIDERS:
            raise ValueError("Unknown model provider")
        return PROVIDERS[key]
