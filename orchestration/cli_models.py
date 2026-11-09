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
MODEL_REFRESH_SECONDS = 300
SPEED_MODES = ("standard", "fast", "ultrafast")
MODEL_CATALOG_REQUEST_ID = "orchestration-model-catalog"
MAX_PROGRESS_TEXT = 8000


class VisibleProgress:
    """Translate native events into bounded public status and visible response text."""

    def __init__(self, provider, callback):
        """Keep partial transport lines private until they form a known event."""
        self.provider = provider
        self.callback = callback
        self.pending = bytearray()
        self.preview = ""
        self.last_event = None

    def emit(self, phase, message, text=None, *, append=False):
        """Publish only caller-supplied status labels and allowlisted visible text."""
        if isinstance(text, str):
            self.preview = ((self.preview if append else "") + text)[
                -MAX_PROGRESS_TEXT:
            ]
        event = {"phase": phase, "message": message, "preview": self.preview}
        if event == self.last_event:
            return
        self.last_event = event
        try:
            self.callback(dict(event))
        except Exception:
            pass  # Observability must never interrupt the model's task.

    def feed(self, chunk):
        """Decode complete stdout JSON lines; diagnostics and unknown data stay private."""
        self.pending.extend(chunk)
        if len(self.pending) > MAX_OUTPUT_BYTES:
            self.pending.clear()
            return
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            self._line(line)

    def finish(self):
        """Inspect a final unterminated transport line without publishing raw bytes."""
        if self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()

    def _line(self, line):
        """Ignore malformed or non-object events instead of exposing raw diagnostics."""
        try:
            event = json.loads(line)
        except (UnicodeError, ValueError, RecursionError):
            return
        if not isinstance(event, dict):
            return
        if self.provider == "openai":
            self._codex(event)
        elif self.provider == "claude":
            self._claude(event)
        elif self.provider == "gemini":
            self._antigravity(event)

    @staticmethod
    def _assistant_text(message):
        """Read text blocks only; thinking, tool calls, and other blocks are excluded."""
        if not isinstance(message, dict) or any(
            message.get(field) in ("analysis", "reasoning", "thinking")
            for field in ("channel", "type")
        ):
            return None
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        return None

    def _codex(self, event):
        """Expose Codex session stages and agent messages, never reasoning items."""
        kind = event.get("type")
        if kind == "thread.started":
            self.emit("starting", "Codex session started")
        elif kind == "turn.started":
            self.emit("working", "Working on the assigned task")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                return
            if (
                item.get("type") == "agent_message"
                and item.get("channel") not in ("analysis", "reasoning", "thinking")
                and isinstance(item.get("text"), str)
            ):
                self.emit("writing", "Writing the response", item["text"])
        elif kind == "turn.completed":
            self.emit("finalizing", "Checking the completed response")

    def _claude(self, event):
        """Expose Claude text deltas while discarding thinking and tool-use blocks."""
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.emit("working", "Claude session started")
        elif kind == "stream_event":
            stream = event.get("event")
            if not isinstance(stream, dict):
                return
            if stream.get("type") == "content_block_delta":
                delta = stream.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    self.emit(
                        "writing",
                        "Writing the response",
                        delta.get("text"),
                        append=True,
                    )
            elif stream.get("type") == "content_block_start":
                block = stream.get("content_block")
                if isinstance(block, dict) and block.get("type") == "text":
                    self.emit(
                        "writing",
                        "Writing the response",
                        block.get("text"),
                        append=True,
                    )
            elif stream.get("type") == "message_stop":
                self.emit("finalizing", "Checking the completed response")
        elif kind == "assistant":
            visible = self._assistant_text(event.get("message"))
            if visible:
                self.emit("writing", "Writing the response", visible)
        elif kind == "result":
            if event.get("subtype") == "success" and not event.get("is_error"):
                self.emit(
                    "finalizing", "Checking the completed response", event.get("result")
                )

    def _antigravity(self, event):
        """Expose known Antigravity assistant text and final responses only."""
        kind = event.get("event")
        if kind in ("session_start", "start"):
            self.emit("working", "Antigravity session started")
        elif kind == "message":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                visible = self._assistant_text(message)
                if visible:
                    self.emit("writing", "Writing the response", visible)
        elif kind == "result":
            result = event.get("result")
            if isinstance(result, dict) and result.get("status") == "SUCCESS":
                self.emit(
                    "finalizing",
                    "Checking the completed response",
                    result.get("response"),
                )


class CLIRuntime:
    """Cache native login/model metadata and execute isolated text-only turns."""

    def __init__(self, *, runner=None, timeout=120):
        """Create a network-free cache; runner injection keeps tests offline."""
        self.runner = runner or self._run
        self.timeout = timeout
        self.lock = threading.RLock()
        self.refresh_locks = {key: threading.Lock() for key in CLI_PROVIDERS}
        self.checked = {}
        self.model_cache = {}
        self.entries = {key: self._entry(key, "unverified") for key in CLI_PROVIDERS}

    @staticmethod
    def _entry(
        provider, state, models=None, model_speed_modes=None, model_details=None
    ):
        """Build public metadata containing no CLI output or account details."""
        info = CLI_PROVIDERS[provider]
        model_modes = model_speed_modes or {}
        details = copy.deepcopy(model_details or {})
        if "default" in (models or []) and "default" not in details:
            details["default"] = {
                "label": "CLI default model",
                "description": "Uses the model currently selected by this CLI.",
            }
        return {
            "id": provider,
            "name": info["name"],
            "tool": info["tool"],
            "configured": state == "signed_in",
            "state": state,
            "models": list(models or []),
            "speed_modes": [
                speed
                for speed in SPEED_MODES
                if speed == "standard"
                or any(speed in modes for modes in model_modes.values())
            ],
            "model_speed_modes": copy.deepcopy(model_modes),
            "model_details": details,
        }

    def _cached_entry(self, provider, state):
        """Retain safe model choices during transient metadata or network failures."""
        with self.lock:
            cached = copy.deepcopy(self.model_cache.get(provider, {}))
        return self._entry(
            provider,
            state,
            cached.get("models"),
            cached.get("model_speed_modes"),
            cached.get("model_details"),
        )

    def _cache_models(self, provider, models, modes=None, details=None):
        """Cache only model metadata; account and authentication payloads are discarded."""
        selected = list(dict.fromkeys(models))[:501]
        with self.lock:
            self.model_cache[provider] = {
                "checked": time.monotonic(),
                "models": selected,
                "model_speed_modes": copy.deepcopy(
                    {
                        key: value
                        for key, value in (modes or {}).items()
                        if key in selected
                    }
                ),
                "model_details": copy.deepcopy(
                    {
                        key: value
                        for key, value in (details or {}).items()
                        if key in selected
                    }
                ),
            }

    def _models_fresh(self, provider):
        """Keep model discovery out of the frequent authentication poll loop."""
        with self.lock:
            cached = self.model_cache.get(provider)
            return (
                cached is not None
                and time.monotonic() - cached["checked"] < MODEL_REFRESH_SECONDS
            )

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
                    entry = self._cached_entry(provider, "unverified")
            with self.lock:
                if entry["state"] in ("signed_out", "not_installed"):
                    self.model_cache.pop(provider, None)
                    entry = self._entry(provider, entry["state"])
                self.entries[provider] = entry
                self.checked[provider] = time.monotonic()
        finally:
            refresh_lock.release()

    def _metadata(self, args, stdin=b""):
        """Capture native status/model output privately with bounded resources."""
        with tempfile.TemporaryDirectory(
            prefix="orchestration-cli-status-"
        ) as directory:
            return self.runner(args, stdin, directory, 15, None)

    def _discover(self, provider, binary):
        """Use provider-supported non-generative checks, never credential files."""
        if provider == "gemini":
            result = self._metadata([binary, "models"])
            models = []
            details = {}
            if result.returncode == 0:
                for line in result.stdout.decode("utf-8").splitlines():
                    candidate = line.split("\t", 1)[0].strip()
                    if "\t" in line:
                        try:
                            model = model_name(candidate)
                            models.append(model)
                            label = (
                                self._metadata_text(line.split("\t", 1)[1], 300)
                                or model
                            )
                            details[model] = self._gemini_detail(model, label)
                        except ValueError:
                            pass
            if models:
                models = ["default", *list(dict.fromkeys(models))[:500]]
                self._cache_models(provider, models, details=details)
                return self._cached_entry(provider, "signed_in")
            return self._cached_entry(provider, self._failure_state(result))
        args = (
            [binary, "login", "status"]
            if provider == "openai"
            else [binary, "auth", "status", "--json"]
        )
        result = self._metadata(args)
        if provider == "claude":
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                return self._cached_entry(provider, "unverified")
            logged_in = payload.get("loggedIn")
            if logged_in is True and result.returncode == 0:
                return self._claude_models(binary)
            if logged_in is False:
                return self._entry(provider, "signed_out")
            return self._cached_entry(provider, "unverified")
        if result.returncode != 0:
            return self._cached_entry(provider, self._failure_state(result))
        if self._models_fresh(provider):
            return self._cached_entry(provider, "signed_in")
        models = ["default"]
        model_speed_modes = {"default": ["standard"]}
        model_details = {}
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
                            model_speed_modes[item["slug"]] = self._model_speeds(item)
                            model_details[item["slug"]] = {
                                "label": self._metadata_text(
                                    item.get("display_name"), 300
                                )
                                or item["slug"],
                                "description": self._metadata_text(
                                    item.get("description"), 1000
                                ),
                            }
                        except ValueError:
                            pass
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            subprocess.SubprocessError,
        ):
            if provider in self.model_cache:
                cached = self._cached_entry(provider, "signed_in")
                self._cache_models(
                    provider,
                    cached["models"],
                    cached["model_speed_modes"],
                    cached["model_details"],
                )
                return cached
        self._cache_models(
            provider, list(dict.fromkeys(models)), model_speed_modes, model_details
        )
        return self._cached_entry(provider, "signed_in")

    @staticmethod
    def _metadata_text(value, maximum):
        """Bound display text without interpreting native metadata as markup or code."""
        if not isinstance(value, str) or "\0" in value:
            return ""
        return value.strip()[:maximum]

    @classmethod
    def _gemini_detail(cls, model, label):
        """Describe documented Gemini families while preserving native display names."""
        description = "Available through Antigravity."
        if model.startswith("gemini-"):
            if "argon" in model.lower():
                description = (
                    "For complex reasoning, architecture, and difficult debugging."
                )
            elif "flash" in model.lower():
                description = "For everyday development work where speed matters."
        return {
            "label": label,
            "description": description,
            "group": "Gemini models"
            if model.startswith("gemini-")
            else "Other models through Antigravity",
        }

    def _claude_models(self, binary):
        """Read native supported models through initialization, without a user turn."""
        provider = "claude"
        if self._models_fresh(provider):
            return self._cached_entry(provider, "signed_in")
        models = ["default"]
        details = {}
        try:
            args, _ = self._command(provider, binary, "default", "")
            args[args.index("--input-format") + 1] = "stream-json"
            args[args.index("--output-format") + 1] = "stream-json"
            args.append("--verbose")
            request = {
                "type": "control_request",
                "request_id": MODEL_CATALOG_REQUEST_ID,
                "request": {"subtype": "initialize"},
            }
            result = self._metadata(args, (json.dumps(request) + "\n").encode())
            if result.returncode != 0:
                raise ValueError("Claude model initialization failed")
            matches = []
            for line in result.stdout.splitlines():
                event = json.loads(line)
                if (
                    not isinstance(event, dict)
                    or event.get("type") != "control_response"
                ):
                    continue
                response = event.get("response")
                if (
                    isinstance(response, dict)
                    and response.get("request_id") == MODEL_CATALOG_REQUEST_ID
                ):
                    matches.append(response)
            if len(matches) != 1 or matches[0].get("subtype") != "success":
                raise ValueError("Claude returned no unique model initialization")
            payload = matches[0].get("response")
            native_models = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(native_models, list) or not native_models:
                raise ValueError("Claude returned no model choices")
            for item in native_models[:500]:
                if not isinstance(item, dict):
                    continue
                try:
                    model = model_name(item.get("value"))
                except ValueError:
                    continue
                label = self._metadata_text(item.get("displayName"), 300)
                if not label:
                    continue
                if model not in models:
                    models.append(model)
                detail = {
                    "label": label,
                    "description": self._metadata_text(item.get("description"), 1000),
                }
                resolved = item.get("resolvedModel")
                try:
                    detail["resolved_model"] = model_name(resolved)
                except ValueError:
                    pass
                details[model] = detail
            if not details:
                raise ValueError("Claude returned no valid model metadata")
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            subprocess.SubprocessError,
        ):
            with self.lock:
                cached = copy.deepcopy(self.model_cache.get(provider))
            if cached:
                models = cached["models"]
                details = cached["model_details"]
        self._cache_models(provider, models, details=details)
        return self._cached_entry(provider, "signed_in")

    @staticmethod
    def _model_speeds(model):
        """Normalize only speed tiers explicitly advertised by the native catalog."""
        advertised = model.get("additional_speed_tiers", [])
        tiers = model.get("service_tiers", [])
        if not isinstance(advertised, list):
            advertised = []
        if not isinstance(tiers, list):
            tiers = []
        values = [
            *advertised,
            *(item.get("id") for item in tiers if isinstance(item, dict)),
        ]
        return [
            speed
            for speed in SPEED_MODES
            if speed == "standard"
            or speed in values
            or (speed == "fast" and "priority" in values)
        ]

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

    def generate(
        self,
        provider,
        model,
        system,
        prompt,
        max_tokens=None,
        *,
        cancelled=None,
        speed="standard",
        progress=None,
    ):
        """Run a bounded CLI turn, optionally requesting a response token target."""
        info = self._provider(provider)
        model = model_name(model)
        self.validate_speed(provider, model, speed)
        if max_tokens is not None and (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not 1 <= max_tokens <= 8192
        ):
            raise ValueError("Choose an output token target from 1 to 8192")
        if not isinstance(system, str) or not isinstance(prompt, str):
            raise ValueError("CLI instructions and prompt must be text")
        if len((system + prompt).encode()) > 512 * 1024:
            raise ValueError("CLI context exceeds the input size limit")
        if progress is not None and not callable(progress):
            raise ValueError("CLI progress callback must be callable")
        binary = shutil.which(info["binary"])
        if not binary:
            raise ValueError(f"Install {info['tool']} before running CLI tasks")
        text = (
            "Complete this text-only task using only the supplied context. "
            "Do not call tools, read other files, or execute commands. "
            + (
                f"Return at most {max_tokens} output tokens."
                if max_tokens is not None
                else ""
            )
            + "\n\n"
            + json.dumps({"instructions": system, "task": prompt}, ensure_ascii=False)
        )
        visible = VisibleProgress(provider, progress) if progress is not None else None
        if visible:
            visible.emit("starting", f"Starting {info['tool']}")
        with tempfile.TemporaryDirectory(prefix="orchestration-cli-turn-") as directory:
            args, stdin = self._command(
                provider, binary, model, text, speed=speed, stream=visible is not None
            )
            if visible:
                visible.emit("working", "Waiting for the model response")
            if visible and self.runner is type(self)._run:
                result = self.runner(
                    args,
                    stdin,
                    directory,
                    self.timeout,
                    cancelled,
                    on_stdout=visible.feed,
                )
                visible.finish()
            else:
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
        if visible:
            visible.emit(
                "finalizing", "Checking the completed response", output["text"]
            )
        return output

    def validate_speed(self, provider, model, speed):
        """Reject unknown or unadvertised speed choices before starting a CLI turn."""
        self._provider(provider)
        if not isinstance(speed, str) or speed not in SPEED_MODES:
            raise ValueError("Choose Standard, Fast, or Ultrafast speed")
        if speed == "standard":
            return
        if provider != "openai":
            raise ValueError("This CLI supports Standard speed only")
        modes = self.status(provider)["model_speed_modes"].get(model, ["standard"])
        if speed not in modes:
            raise ValueError("The selected CLI model does not advertise this speed")

    @staticmethod
    def _command(provider, binary, model, prompt, *, speed="standard", stream=False):
        """Construct fixed argv and stdin; no shell or model-supplied commands."""
        model_args = [] if model == "default" else ["--model", model]
        if provider == "openai":
            if speed not in SPEED_MODES:
                raise ValueError("Choose Standard, Fast, or Ultrafast speed")
            tier = "default" if speed == "standard" else speed
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
                "-c",
                f'service_tier="{tier}"',
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
            args = [
                binary,
                "--print",
                "--output-format",
                "stream-json" if stream else "json",
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
            ]
            if stream:
                args.extend(["--verbose", "--include-partial-messages"])
            return args, prompt.encode()
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
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                events = [
                    json.loads(line) for line in stdout.splitlines() if line.strip()
                ]
                if any(not isinstance(event, dict) for event in events):
                    raise ValueError("Claude returned an invalid event")
                results = [event for event in events if event.get("type") == "result"]
                if len(results) != 1:
                    raise ValueError("Claude returned no unique terminal result")
                result = results[0]
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
    def _run(args, stdin, directory, timeout, cancelled, *, on_stdout=None):
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
                            if key.data == "stdout" and on_stdout is not None:
                                try:
                                    on_stdout(chunk)
                                except Exception:
                                    pass  # Progress consumers cannot break task execution.
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
