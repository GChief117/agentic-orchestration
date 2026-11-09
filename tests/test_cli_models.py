"""Native CLI adapters exercised with fake models, never real account requests."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from orchestration.cli_models import CLIRuntime


class CLIRuntimeTests(unittest.TestCase):
    def test_claude_initialization_discovers_safe_models_without_a_user_turn(self):
        calls = []

        def run(args, stdin, directory, timeout, cancelled):
            calls.append((args, stdin))
            if "auth" in args:
                return subprocess.CompletedProcess(args, 0, b'{"loggedIn":true}', b"")
            request = json.loads(stdin)
            self.assertEqual(request["type"], "control_request")
            self.assertEqual(request["request"], {"subtype": "initialize"})
            self.assertNotIn(b'"type": "user"', stdin)
            self.assertEqual(args[args.index("--tools") + 1], "")
            self.assertIn("--safe-mode", args)
            self.assertIn("--no-session-persistence", args)
            response = {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request["request_id"],
                    "response": {
                        "account": {"token": "PRIVATE", "email": "PRIVATE"},
                        "models": [
                            {
                                "value": "default",
                                "resolvedModel": "claude-test",
                                "displayName": "Default",
                                "description": "Native default",
                            },
                            {
                                "value": "sonnet",
                                "resolvedModel": "claude-sonnet-test",
                                "displayName": "Sonnet",
                                "description": "Efficient routine work",
                            },
                            {
                                "value": "opus",
                                "displayName": "Opus",
                                "description": "Complex work",
                            },
                            {"value": "--unsafe", "displayName": "Rejected"},
                        ],
                    },
                },
            }
            return subprocess.CompletedProcess(
                args, 0, json.dumps(response).encode(), b""
            )

        runtime = CLIRuntime(runner=run)
        with patch(
            "orchestration.cli_models.shutil.which", return_value="/tool/claude"
        ):
            runtime.refresh("claude")
            entry = runtime.status("claude")
            self.assertEqual(entry["models"], ["default", "sonnet", "opus"])
            self.assertEqual(
                entry["model_details"]["sonnet"]["resolved_model"], "claude-sonnet-test"
            )
            self.assertEqual(
                entry["model_details"]["opus"]["description"], "Complex work"
            )
            self.assertNotIn("PRIVATE", json.dumps(entry))
            runtime.checked["claude"] -= 3
            runtime.refresh("claude")
        self.assertEqual(sum(bool(stdin) for _, stdin in calls), 1)
        self.assertEqual(sum("auth" in args for args, _ in calls), 2)

    def test_claude_metadata_failure_keeps_cache_but_logout_clears_it(self):
        logged_in = True
        valid_metadata = True
        initializations = 0

        def run(args, stdin, *_rest):
            nonlocal initializations
            if "auth" in args:
                raw = json.dumps({"loggedIn": logged_in}).encode()
            else:
                initializations += 1
                request = json.loads(stdin)
                raw = json.dumps(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request["request_id"]
                            if valid_metadata
                            else "wrong-id",
                            "response": {
                                "models": [
                                    {
                                        "value": "haiku",
                                        "displayName": "Haiku",
                                        "description": "Quick answers",
                                    }
                                ]
                            },
                        },
                    }
                ).encode()
            return subprocess.CompletedProcess(args, 0, raw, b"")

        runtime = CLIRuntime(runner=run)
        with patch(
            "orchestration.cli_models.shutil.which", return_value="/tool/claude"
        ):
            runtime.refresh("claude")
            valid_metadata = False
            runtime.checked["claude"] -= 3
            runtime.model_cache["claude"]["checked"] -= 301
            runtime.refresh("claude")
            self.assertEqual(runtime.status("claude")["models"], ["default", "haiku"])
            self.assertTrue(runtime.status("claude")["configured"])
            logged_in = False
            runtime.checked["claude"] -= 3
            runtime.refresh("claude")
            self.assertEqual(runtime.status("claude")["models"], [])
            self.assertNotIn("claude", runtime.model_cache)
            logged_in, valid_metadata = True, True
            runtime.checked["claude"] -= 3
            runtime.refresh("claude")
        self.assertEqual(initializations, 3)
        self.assertIn("haiku", runtime.status("claude")["models"])

    def test_claude_invalid_or_ambiguous_initialization_falls_back_to_default(self):
        for mode in ("malformed", "empty", "duplicate"):
            with self.subTest(mode=mode):

                def run(args, stdin, *_rest):
                    if "auth" in args:
                        return subprocess.CompletedProcess(
                            args, 0, b'{"loggedIn":true}', b""
                        )
                    request = json.loads(stdin)
                    response = {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request["request_id"],
                            "response": {
                                "models": []
                                if mode == "empty"
                                else [{"value": "opus", "displayName": "Opus"}]
                            },
                        },
                    }
                    raw = (
                        b"not JSON"
                        if mode == "malformed"
                        else json.dumps(response).encode()
                    )
                    if mode == "duplicate":
                        raw += b"\n" + raw
                    return subprocess.CompletedProcess(args, 0, raw, b"")

                runtime = CLIRuntime(runner=run)
                with patch(
                    "orchestration.cli_models.shutil.which", return_value="/tool/claude"
                ):
                    runtime.refresh("claude")
                self.assertEqual(runtime.status("claude")["models"], ["default"])
                self.assertTrue(runtime.status("claude")["configured"])

    def test_gemini_preserves_native_labels_and_groups_provider_models(self):
        def run(args, *_rest):
            raw = b"gemini-test-argon\tGemini Test Argon (Medium)\ngemini-test-flash\tGemini Test Flash\nclaude-test\tClaude via Antigravity\n"
            return subprocess.CompletedProcess(args, 0, raw, b"")

        runtime = CLIRuntime(runner=run)
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/agy"):
            runtime.refresh("gemini")
        details = runtime.status("gemini")["model_details"]
        self.assertEqual(
            details["gemini-test-argon"]["label"], "Gemini Test Argon (Medium)"
        )
        self.assertIn("complex reasoning", details["gemini-test-argon"]["description"])
        self.assertIn("speed matters", details["gemini-test-flash"]["description"])
        self.assertEqual(
            details["claude-test"]["group"], "Other models through Antigravity"
        )

    def test_native_model_catalog_exposes_only_advertised_speeds(self):
        def run(args, *_rest):
            if args[-2:] == ["debug", "models"]:
                text = json.dumps(
                    {
                        "models": [
                            {
                                "slug": "both-speeds",
                                "additional_speed_tiers": ["fast", "ultrafast"],
                            },
                            {
                                "slug": "fast-only",
                                "service_tiers": [{"id": "priority"}],
                            },
                            {"slug": "standard-only"},
                        ]
                    }
                )
            else:
                text = "Logged in"
            return subprocess.CompletedProcess(args, 0, text.encode(), b"")

        runtime = CLIRuntime(runner=run)
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/codex"):
            runtime.refresh("openai")
        entry = runtime.status("openai")
        self.assertEqual(entry["speed_modes"], ["standard", "fast", "ultrafast"])
        self.assertEqual(
            entry["model_speed_modes"],
            {
                "default": ["standard"],
                "both-speeds": ["standard", "fast", "ultrafast"],
                "fast-only": ["standard", "fast"],
                "standard-only": ["standard"],
            },
        )
        self.assertEqual(runtime.status("claude")["speed_modes"], ["standard"])

    def test_codex_speed_reaches_native_service_tier_without_a_fallback(self):
        calls = []

        def run(args, *_rest):
            calls.append(args)
            output = b'{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}\n{"type":"turn.completed"}'
            return subprocess.CompletedProcess(args, 0, output, b"")

        runtime = CLIRuntime(runner=run)
        runtime.entries["openai"] = runtime._entry(
            "openai",
            "signed_in",
            ["model-a"],
            {"model-a": ["standard", "fast", "ultrafast"]},
        )
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/codex"):
            for speed, tier in [
                ("standard", "default"),
                ("fast", "fast"),
                ("ultrafast", "ultrafast"),
            ]:
                result = runtime.generate(
                    "openai", "model-a", "system", "task", 20, speed=speed
                )
                self.assertTrue(result["complete"])
                self.assertIn(f'service_tier="{tier}"', calls[-1])
            for provider, model, speed in [
                ("claude", "model-a", "fast"),
                ("gemini", "model-a", "ultrafast"),
                ("openai", "default", "fast"),
                ("openai", "missing-model", "ultrafast"),
                ("openai", "model-a", "invalid"),
            ]:
                count = len(calls)
                with self.assertRaises(ValueError):
                    runtime.generate(provider, model, "system", "task", 20, speed=speed)
                self.assertEqual(len(calls), count)

    def test_refresh_uses_native_checks_but_catalog_is_cached_and_redacted(self):
        calls = []

        def run(args, stdin, directory, timeout, cancelled):
            calls.append(args)
            if args[-2:] == ["debug", "models"]:
                text = json.dumps({"models": [{"slug": "test-model"}]})
            elif args[-1] == "models":
                text = "gemini-test\tGemini test\n"
            elif args[-1] == "--json":
                text = '{"loggedIn":true,"token":"PRIVATE"}'
            else:
                text = "Logged in"
            return subprocess.CompletedProcess(args, 0, text.encode(), b"")

        runtime = CLIRuntime(runner=run)
        self.assertEqual(calls, [])
        self.assertFalse(runtime.ready("gemini"))
        with patch(
            "orchestration.cli_models.shutil.which",
            side_effect=lambda key: "/tool/" + key,
        ):
            runtime.refresh()
            self.assertTrue(all(item["configured"] for item in runtime.catalog()))
            self.assertEqual(
                runtime.status("gemini")["models"], ["default", "gemini-test"]
            )
            self.assertEqual(
                runtime.status("openai")["models"], ["default", "test-model"]
            )
            count = len(calls)
            runtime.catalog()
            runtime.status("claude")
            runtime.refresh()
            self.assertEqual(len(calls), count)
        self.assertNotIn("PRIVATE", json.dumps(runtime.catalog()))

    def test_metadata_distinguishes_explicit_logout_from_transient_failure(self):
        def run(args, *_rest):
            message = (
                b"Not logged in" if "login" in args else b"network unavailable PRIVATE"
            )
            return subprocess.CompletedProcess(args, 1, b"", message)

        runtime = CLIRuntime(runner=run)
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/bin"):
            runtime.refresh()
        self.assertEqual(runtime.status("openai")["state"], "signed_out")
        self.assertEqual(runtime.status("gemini")["state"], "unverified")
        self.assertEqual(runtime.status("claude")["state"], "unverified")
        self.assertNotIn("PRIVATE", json.dumps(runtime.catalog()))

    def test_missing_binaries_never_run_a_command(self):
        runtime = CLIRuntime(runner=lambda *_args: self.fail("No process expected"))
        with patch("orchestration.cli_models.shutil.which", return_value=None):
            runtime.refresh()
            self.assertTrue(
                all(item["state"] == "not_installed" for item in runtime.catalog())
            )
            with self.assertRaisesRegex(ValueError, "Install"):
                runtime.generate("gemini", "default", "system", "task", 20)

    def test_generates_with_fixed_arguments_private_stdin_and_isolated_directory(self):
        samples = {
            "openai": "\n".join(
                [
                    '{"type":"thread.started","thread_id":"run-1"}',
                    '{"type":"item.completed","item":{"type":"agent_message","text":"Done"}}',
                    '{"type":"turn.completed","usage":{"output_tokens":1}}',
                ]
            ),
            "claude": '{"type":"result","subtype":"success","result":"Done","usage":{"output_tokens":1},"session_id":"run-1"}',
            "gemini": '{"event":"result","result":{"status":"SUCCESS","response":"Done","usage":{"output_tokens":1},"conversation_id":"run-1"}}',
        }
        calls = []
        for provider, sample in samples.items():
            with self.subTest(provider=provider):

                def run(args, stdin, directory, timeout, cancelled):
                    self.assertTrue(Path(directory).is_dir())
                    self.assertEqual(list(Path(directory).iterdir()), [])
                    self.assertIn(b"private task", stdin)
                    self.assertNotIn("private task", " ".join(args))
                    self.assertNotIn("--dangerously-skip-permissions", args)
                    self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
                    calls.append((args, stdin, directory))
                    return subprocess.CompletedProcess(args, 0, sample.encode(), b"")

                runtime = CLIRuntime(runner=run)
                with patch(
                    "orchestration.cli_models.shutil.which", return_value="/tool/bin"
                ):
                    result = runtime.generate(
                        provider, "default", "instructions", "private task", 20
                    )
                self.assertEqual(result["text"], "Done")
                self.assertTrue(result["complete"])
                self.assertEqual(result["response_id"], "run-1")
                self.assertFalse(result["token_limit_enforced"])
                self.assertFalse(Path(calls[-1][2]).exists())
        self.assertIn("read-only", calls[0][0])
        self.assertIn("features.shell_tool=false", calls[0][0])
        self.assertEqual(calls[1][0][calls[1][0].index("--tools") + 1], "")
        settings = json.loads(calls[2][0][calls[2][0].index("--settings") + 1])
        self.assertIn("command(*)", settings["permissions"]["deny"])
        self.assertIn("read_file(*)", settings["permissions"]["deny"])

    def test_token_target_is_optional_and_explicit_targets_remain_validated(self):
        prompts = []

        def run(args, stdin, *_rest):
            prompts.append(stdin.decode())
            output = b'{"type":"result","subtype":"success","result":"Done"}'
            return subprocess.CompletedProcess(args, 0, output, b"")

        runtime = CLIRuntime(runner=run)
        with patch(
            "orchestration.cli_models.shutil.which", return_value="/tool/claude"
        ):
            result = runtime.generate("claude", "default", "Instructions", "Task")
            self.assertTrue(result["complete"])
            self.assertFalse(result["token_limit_enforced"])
            self.assertNotIn("Return at most", prompts[-1])
            self.assertNotIn("output tokens", prompts[-1])
            runtime.generate("claude", "default", "Instructions", "Task", 512)
            self.assertIn("Return at most 512 output tokens.", prompts[-1])
            for invalid in (0, True, False, -1, 8193, "512"):
                with self.subTest(target=invalid), self.assertRaisesRegex(
                    ValueError, "token target"
                ):
                    runtime.generate(
                        "claude", "default", "Instructions", "Task", invalid
                    )
            self.assertEqual(len(prompts), 2)

    def test_incomplete_results_are_not_accepted_and_codex_tools_are_rejected(self):
        self.assertFalse(
            CLIRuntime._parse(
                "openai",
                b'{"type":"item.completed","item":{"type":"agent_message","text":"partial"}}',
            )["complete"]
        )
        self.assertFalse(
            CLIRuntime._parse(
                "gemini",
                b'{"event":"result","result":{"status":"ERROR","response":"partial"}}',
            )["complete"]
        )
        self.assertFalse(
            CLIRuntime._parse(
                "claude",
                b'{"type":"result","subtype":"error_max_turns","result":"partial"}',
            )["complete"]
        )
        with self.assertRaisesRegex(ValueError, "tool"):
            CLIRuntime._parse(
                "openai", b'{"type":"item.completed","item":{"type":"file_change"}}'
            )
        with self.assertRaisesRegex(ValueError, "tool"):
            CLIRuntime._parse(
                "gemini",
                b'{"event":"step_update","step_update":{"step_type":"tool"}}',
            )
        with self.assertRaisesRegex(ValueError, "invalid event"):
            CLIRuntime._parse("gemini", b'"not an event"')

    def test_generation_rejects_bad_models_oversized_results_and_redacts_errors(self):
        def run(args, *_rest):
            text = {"type": "result", "subtype": "success", "result": "x" * 65537}
            return subprocess.CompletedProcess(args, 0, json.dumps(text).encode(), b"")

        runtime = CLIRuntime(runner=run)
        with self.assertRaises(ValueError):
            runtime.generate("claude", "-unsafe", "", "", 20)
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/bin"):
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                runtime.generate("claude", "default", "", "", 20)
        runtime.runner = lambda args, *_rest: subprocess.CompletedProcess(
            args, 1, b"", b"PRIVATE authentication required"
        )
        with patch("orchestration.cli_models.shutil.which", return_value="/tool/bin"):
            with self.assertRaisesRegex(ValueError, "Sign in") as raised:
                runtime.generate("claude", "default", "", "", 20)
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_process_runner_handles_stdin_and_keeps_api_keys_out(self):
        code = "import os,sys; data=sys.stdin.buffer.read(); print(len(data)); print('ANTHROPIC_API_KEY' in os.environ)"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "PRIVATE"}
        ):
            result = CLIRuntime._run(
                [sys.executable, "-c", code], b"x" * 180000, directory, 5, None
            )
        self.assertEqual(result.stdout.strip(), b"180000\nFalse")

    def test_process_runner_cancels_and_enforces_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(InterruptedError):
                CLIRuntime._run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    b"",
                    directory,
                    5,
                    cancelled,
                )
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "time limit"):
                CLIRuntime._run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    b"",
                    directory,
                    0.1,
                    None,
                )
            self.assertLess(time.monotonic() - started, 3)

    def test_process_runner_limits_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "size limit"):
                CLIRuntime._run(
                    [sys.executable, "-c", "import sys; sys.stdout.write('x'*5000000)"],
                    b"",
                    directory,
                    5,
                    None,
                )
