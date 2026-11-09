"""Bounded local CLI text execution using each tool's existing saved sign-in.

CLI configuration is supplied per invocation. No credentials are copied, no
user command is evaluated, and model tools are disabled or denied. This module
does not provide a general-purpose shell or a source-editing agent runtime.
"""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .models import model_name


CLI_PROVIDERS = {
    "openai": {"name": "OpenAI", "tool": "Codex CLI", "binary": "codex"},
    "claude": {"name": "Claude", "tool": "Claude Code", "binary": "claude"},
    "gemini": {"name": "Gemini", "tool": "Antigravity CLI", "binary": "agy"},
}

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 65536
REFRESH_SECONDS = 2


class CLIRuntime:
    """Cache native login/model metadata and execute isolated text-only turns."""

    def __init__(self, *, runner=None, timeout=120):
        """Create a network-free cache; runner injection keeps tests offline."""
        self.runner = runner or self._run
        self.timeout = timeout
        self.lock = threading.RLock()
        self.refresh_locks = {key: threading.Lock() for key in CLI_PROVIDERS}
        self.checked = {}
        self.entries = {key: self._entry(key, "unverified") for key in CLI_PROVIDERS}

    @staticmethod
    def _entry(provider, state, models=None):
        """Build public metadata containing no CLI output or account details."""
        info = CLI_PROVIDERS[provider]
        return {
            "id": provider,
            "name": info["name"],
            "tool": info["tool"],
            "configured": state == "signed_in",
            "state": state,
            "models": list(models or []),
        }

    @staticmethod
    def _provider(provider):
        """Reject unknown provider IDs before discovering or running binaries."""
        if not isinstance(provider, str) or provider not in CLI_PROVIDERS:
            raise ValueError("Choose OpenAI, Claude, or Gemini")
        return CLI_PROVIDERS[provider]

    def catalog(self):
        """Return cached, safe metadata without starting a process or network call."""
        with self.lock:
            return copy.deepcopy(list(self.entries.values()))

    def status(self, provider):
        """Return one cached entry, including its last verified login state."""
        self._provider(provider)
        with self.lock:
            return copy.deepcopy(self.entries[provider])

    def ready(self, provider):
        """Check cached readiness; execution still handles revoked credentials."""
        return self.status(provider)["configured"]

    def refresh(self, provider=None):
        """Refresh native login checks, deduplicated per provider with a short TTL."""
        if provider is not None:
            self._provider(provider)
            self._refresh_one(provider)
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(self._refresh_one, CLI_PROVIDERS))
        return self.catalog()

    def _refresh_one(self, provider):
        """Run a bounded native metadata check; transient failures are unverified."""
        refresh_lock = self.refresh_locks[provider]
        if not refresh_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                if (
                    time.monotonic() - self.checked.get(provider, -float("inf"))
                    < REFRESH_SECONDS
                ):
                    return
            binary = shutil.which(CLI_PROVIDERS[provider]["binary"])
            if binary is None:
                entry = self._entry(provider, "not_installed")
            else:
                try:
                    entry = self._discover(provider, binary)
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    AttributeError,
                    subprocess.SubprocessError,
                ):
                    entry = self._entry(provider, "unverified")
            with self.lock:
                self.entries[provider] = entry
                self.checked[provider] = time.monotonic()
        finally:
            refresh_lock.release()

    def _metadata(self, args):
        """Capture native status/model output privately with bounded resources."""
        with tempfile.TemporaryDirectory(
            prefix="orchestration-cli-status-"
        ) as directory:
            return self.runner(args, b"", directory, 15, None)

    def _discover(self, provider, binary):
        """Use provider-supported non-generative checks, never credential files."""
        if provider == "gemini":
            result = self._metadata([binary, "models"])
            models = []
            if result.returncode == 0:
                for line in result.stdout.decode("utf-8").splitlines():
                    candidate = line.split("\t", 1)[0].strip()
                    if "\t" in line:
                        try:
                            models.append(model_name(candidate))
                        except ValueError:
                            pass
            if models:
                return self._entry(
                    provider,
                    "signed_in",
                    ["default", *list(dict.fromkeys(models))[:500]],
                )
            return self._entry(provider, self._failure_state(result))
        args = (
            [binary, "login", "status"]
            if provider == "openai"
            else [binary, "auth", "status", "--json"]
        )
        result = self._metadata(args)
        if provider == "claude":
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                return self._entry(provider, "unverified")
            logged_in = payload.get("loggedIn")
            if logged_in is True and result.returncode == 0:
                return self._entry(provider, "signed_in", ["default"])
            if logged_in is False:
                return self._entry(provider, "signed_out")
            return self._entry(provider, "unverified")
        if result.returncode != 0:
            return self._entry(provider, self._failure_state(result))
        models = ["default"]
        try:
            result = self._metadata([binary, "debug", "models"])
            if result.returncode == 0:
                payload = json.loads(result.stdout)
                for item in payload.get("models", [])[:500]:
                    if (
                        isinstance(item, dict)
                        and item.get("visibility", "list") == "list"
                    ):
                        try:
                            models.append(model_name(item.get("slug")))
                        except ValueError:
                            pass
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            subprocess.SubprocessError,
        ):
            pass  # Native model discovery is optional after a successful login check.
        return self._entry(provider, "signed_in", list(dict.fromkeys(models)))

    @staticmethod
    def _failure_state(result):
        """Recognize explicit logout while leaving network/tool failures uncertain."""
        message = (
            (result.stdout + result.stderr).decode("utf-8", errors="replace").lower()
        )
        logged_out = (
            "not logged in",
            "authentication required",
            "not authenticated",
            "please log in",
            "please sign in",
            "sign-in required",
        )
        return (
            "signed_out"
            if any(term in message for term in logged_out)
            else "unverified"
        )

    def generate(self, provider, model, system, prompt, max_tokens, *, cancelled=None):
        """Run one native CLI text turn and require a complete, bounded result."""
        info = self._provider(provider)
        model = model_name(model)
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not 1 <= max_tokens <= 8192
        ):
            raise ValueError("Choose an output token target from 1 to 8192")
        if not isinstance(system, str) or not isinstance(prompt, str):
            raise ValueError("CLI instructions and prompt must be text")
        if len((system + prompt).encode()) > 512 * 1024:
            raise ValueError("CLI context exceeds the input size limit")
        binary = shutil.which(info["binary"])
        if not binary:
            raise ValueError(f"Install {info['tool']} before running CLI tasks")
        text = (
            "Complete this text-only task using only the supplied context. "
            "Do not call tools, read other files, or execute commands. "
            f"Return at most {max_tokens} output tokens.\n\n"
            + json.dumps({"instructions": system, "task": prompt}, ensure_ascii=False)
        )
        with tempfile.TemporaryDirectory(prefix="orchestration-cli-turn-") as directory:
            args, stdin = self._command(provider, binary, model, text)
            result = self.runner(args, stdin, directory, self.timeout, cancelled)
        if result.returncode != 0:
            state = self._failure_state(result)
            if state == "signed_out":
                with self.lock:
                    self.entries[provider] = self._entry(provider, "signed_out")
                raise ValueError(f"Sign in to {info['tool']} before running CLI tasks")
            raise ValueError(
                f"{info['tool']} did not complete the task; check model access and the local tool"
            )
        output = self._parse(provider, result.stdout)
        if not isinstance(output["text"], str):
            raise ValueError("The CLI returned an invalid text result")
        if not output["text"].strip():
            raise ValueError("The CLI returned no text output")
        if len(output["text"].encode()) > MAX_TEXT_BYTES:
            raise ValueError("CLI text output exceeds the 64 KiB artifact limit")
        output["token_limit_enforced"] = False
        if not isinstance(output["usage"], dict):
            output["usage"] = {}
        if not isinstance(output["response_id"], (str, type(None))):
            output["response_id"] = None
        return output

    @staticmethod
    def _command(provider, binary, model, prompt):
        """Construct fixed argv and stdin; no shell or model-supplied commands."""
        model_args = [] if model == "default" else ["--model", model]
        if provider == "openai":
            args = [
                binary,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--json",
                "--color",
                "never",
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                "mcp_servers={}",
            ]
            for feature in (
                "shell_tool",
                "hooks",
                "apps",
                "plugins",
                "remote_plugin",
                "browser_use",
                "computer_use",
                "image_generation",
                "view_image",
                "multi_agent",
                "memories",
                "skill_search",
                "goals",
                "sleep_tool",
            ):
                args.extend(["-c", f"features.{feature}=false"])
            return [*args, *model_args, "-"], prompt.encode()
        if provider == "claude":
            return [
                binary,
                "--print",
                "--output-format",
                "json",
                "--input-format",
                "text",
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--no-session-persistence",
                "--no-chrome",
                "--disable-slash-commands",
                "--safe-mode",
                "--restricted",
                *model_args,
            ], prompt.encode()
        settings = {
            "toolPermission": "strict",
            "allowNonWorkspaceAccess": False,
            "permissions": {
                "allow": [],
                "ask": [],
                "deny": [
                    "read_file(*)",
                    "write_file(*)",
                    "read_url(*)",
                    "execute_url(*)",
                    "command(*)",
                    "mcp(*)",
                    "unsandboxed(*)",
                ],
            },
        }
        args = [
            binary,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--mode",
            "plan",
            "--disable-slash-commands",
            "--settings",
            json.dumps(settings),
            *model_args,
        ]
        stdin = (
            json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
        ).encode()
        return args, stdin

    @staticmethod
    def _parse(provider, stdout):
        """Parse terminal result envelopes, ignoring untrusted diagnostics."""
        if provider == "claude":
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("The CLI returned an invalid result")
            return {
                "text": result.get("result", ""),
                "complete": result.get("type") == "result"
                and result.get("subtype") == "success"
                and not result.get("is_error", False),
                "usage": result.get("usage", {}),
                "response_id": result.get("session_id"),
            }
        events = [
            json.loads(line)
            for line in stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("The CLI returned an invalid event")
        if provider == "gemini":
            for event in events:
                step = event.get("step_update", {})
                if isinstance(step, dict) and (
                    step.get("step_type") == "tool" or step.get("subagent_info")
                ):
                    raise ValueError("Antigravity attempted a tool in a text-only task")
            results = [
                event.get("result")
                for event in events
                if event.get("event") == "result"
            ]
            if len(results) != 1 or not isinstance(results[0], dict):
                raise ValueError("Antigravity returned no unique terminal result")
            result = results[0]
            return {
                "text": result.get("response", ""),
                "complete": result.get("status") == "SUCCESS",
                "usage": result.get("usage", {}),
                "response_id": result.get("conversation_id"),
            }
        messages = []
        complete = False
        usage = {}
        response_id = None
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Codex returned an invalid event")
            kind = event.get("type")
            if kind in ("error", "turn.failed"):
                raise ValueError("Codex reported a failed turn")
            if kind == "thread.started":
                response_id = event.get("thread_id")
            if kind == "item.completed":
                item = event.get("item", {})
                if not isinstance(item, dict):
                    raise ValueError("Codex returned an invalid item")
                if item.get("type") == "agent_message":
                    message = item.get("text", "")
                    if not isinstance(message, str):
                        raise ValueError("Codex returned invalid message text")
                    messages.append(message)
                if item.get("type") in (
                    "command_execution",
                    "file_change",
                    "mcp_tool_call",
                    "web_search",
                ):
                    raise ValueError("Codex attempted a tool in a text-only task")
            if kind == "turn.completed":
                complete = True
                usage = event.get("usage", {})
        return {
            "text": "\n".join(messages),
            "complete": complete,
            "usage": usage,
            "response_id": response_id,
        }

    @staticmethod
    def _run(args, stdin, directory, timeout, cancelled):
        """Drain bounded pipes while monitoring timeout and cooperative cancellation."""
        env = os.environ.copy()
        # Local CLI mode uses provider sign-in, rather than a coincidental API-key environment.
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "CODEX_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        ):
            env.pop(key, None)
        env["NO_COLOR"] = "1"
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=directory,
            env=env,
            start_new_session=True,
        )
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        started = time.monotonic()
        offset = 0
        try:
            with selectors.DefaultSelector() as selector:
                for name in streams:
                    pipe = getattr(process, name)
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
                os.set_blocking(process.stdin.fileno(), False)
                if stdin:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()
                while selector.get_map():
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("CLI task cancelled")
                    if time.monotonic() - started > timeout:
                        raise ValueError("CLI task exceeded its time limit")
                    for key, _ in selector.select(0.1):
                        if key.data == "stdin":
                            try:
                                offset += os.write(
                                    key.fd, stdin[offset : offset + 65536]
                                )
                            except BrokenPipeError:
                                offset = len(stdin)
                            if offset >= len(stdin):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                                continue
                            streams[key.data].extend(chunk)
                            if sum(map(len, streams.values())) > MAX_OUTPUT_BYTES:
                                raise ValueError(
                                    "CLI process output exceeded the size limit"
                                )
                while process.poll() is None:
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("CLI task cancelled")
                    if time.monotonic() - started > timeout:
                        raise ValueError("CLI task exceeded its time limit")
                    time.sleep(0.05)
            return subprocess.CompletedProcess(
                args,
                process.returncode,
                bytes(streams["stdout"]),
                bytes(streams["stderr"]),
            )
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait()
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
