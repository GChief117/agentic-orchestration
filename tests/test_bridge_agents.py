"""Native research routing tests use synthetic events, never provider accounts."""

import json
import shlex
import subprocess
import sys
import threading
import unittest

from orchestration.bridge_agents import (
    BridgeResearchError,
    BridgeAgentRuntime,
    ResearchProgress,
    _REMOTE_DRIVER,
)


class FakeBridges:
    """Provide an operator-owned bridge fixture without touching real configuration."""

    enabled = True

    def __init__(self):
        self.entry = {
            "hostname": "bridge.local",
            "username": "researcher",
            "root": "/workspace/repositories",
            "repositories": ["sample"],
            "agent_command": "/operator/stdin-bridge",
        }

    def _configuration(self, project_id):
        return self.entry if project_id == "project" else None


def events(*, read_skill=True, read_source=True, identity="bridge.local"):
    """Produce a successful Codex tool turn with private output to be discarded."""
    result = [
        {"type": "bridge.verified", "hostname": identity, "username": "researcher"},
        {"type": "thread.started", "thread_id": "thread"},
    ]
    if read_skill:
        result.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "skill",
                    "type": "command_execution",
                    "command": "cat ~/.codex/skills/pio-map-codebase/SKILL.md",
                    "exit_code": 0,
                    "aggregated_output": "private skill contents",
                },
            }
        )
    if read_source:
        result.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "source",
                    "type": "command_execution",
                    "command": "cat sample/src/main.py",
                    "exit_code": 0,
                    "aggregated_output": "private source contents",
                },
            }
        )
    result.extend(
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "thought",
                    "type": "reasoning",
                    "text": "private reasoning",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "answer",
                    "type": "agent_message",
                    "text": "Verified source report",
                },
            },
            {"type": "turn.completed", "usage": {"output_tokens": 4}},
        ]
    )
    return b"\n".join(json.dumps(event).encode() for event in result) + b"\n"


class BridgeAgentTests(unittest.TestCase):
    def setUp(self):
        self.bridges = FakeBridges()
        self.output = events()
        self.calls = []
        self.progress = []

        def runner(args, stdin, cwd, timeout, cancelled, *, on_stdout):
            self.calls.append((args, json.loads(stdin), cwd, timeout, cancelled))
            for index in range(0, len(self.output), 29):
                on_stdout(self.output[index : index + 29])
            return subprocess.CompletedProcess(
                args, 0, self.output, b"private diagnostics"
            )

        self.runtime = BridgeAgentRuntime(self.bridges, runner=runner)

    def generate(self):
        return self.runtime.generate(
            "project",
            "openai",
            "default",
            "Role context",
            "Inspect the sample repository",
            progress=self.progress.append,
        )

    def test_routes_to_fixed_driver_with_prompt_only_on_stdin(self):
        result = self.generate()
        args, payload, _cwd, timeout, _cancelled = self.calls[0]
        self.assertEqual(args[0], "/operator/stdin-bridge")
        self.assertEqual(shlex.split(args[1])[:2], ["python3", "-c"])
        self.assertNotIn("Role context", args[1])
        self.assertEqual(payload["repositories"], ["sample"])
        self.assertEqual(payload["speed"], "standard")
        self.assertIn("pio-map-codebase/SKILL.md", payload["text"])
        self.assertIn("do not refresh or delete", payload["text"])
        self.assertEqual(timeout, 300)
        self.assertEqual(result["text"], "Verified source report")
        self.assertTrue(result["research"]["skill_read"])
        self.assertTrue(result["research"]["source_read"])
        self.assertEqual(result["research"]["skills"], ["pio-map-codebase"])
        self.assertEqual(len(result["research"]["tool_events"]), 2)
        self.assertNotIn("private source contents", json.dumps(result))
        self.assertNotIn("private reasoning", json.dumps(self.progress))

    def test_no_provider_or_local_fallback(self):
        self.assertFalse(self.runtime.ready("project", "claude"))
        self.assertFalse(self.runtime.ready("missing", "openai"))
        with self.assertRaisesRegex(ValueError, "Codex only"):
            self.runtime.generate("project", "claude", "default", "", "")
        self.bridges.enabled = False
        self.assertFalse(self.runtime.ready("project", "openai"))
        self.assertEqual(self.calls, [])

    def test_requires_verified_identity_and_real_skill_and_source_reads(self):
        for output in (
            events(read_skill=False),
            events(read_source=False),
            events(identity="other-host"),
        ):
            self.output = output
            with self.assertRaises(ValueError):
                self.generate()

    def test_relative_source_reads_and_failed_proof_metadata_are_preserved(self):
        self.output = events().replace(
            b"cat sample/src/main.py", b"sed -n '1,80p' src/runtime.py"
        )
        self.assertTrue(self.generate()["research"]["source_read"])
        self.output = events(read_skill=False)
        with self.assertRaises(BridgeResearchError) as caught:
            self.generate()
        self.assertFalse(caught.exception.research["skill_read"])
        self.assertTrue(caught.exception.research["source_read"])
        self.assertEqual(caught.exception.research["skills"], [])
        self.assertIn("SKILL.md", str(caught.exception))

    def test_cancellation_and_oversized_context_stop_before_launch(self):
        signal = threading.Event()
        signal.set()
        with self.assertRaises(InterruptedError):
            self.runtime.generate(
                "project", "openai", "default", "", "", cancelled=signal
            )
        with self.assertRaisesRegex(ValueError, "512 KiB"):
            self.runtime.generate("project", "openai", "default", "", "x" * 524288)
        self.assertEqual(self.calls, [])

    def test_unexpected_mutation_and_external_tools_are_rejected(self):
        for kind in ("file_change", "mcp_tool_call", "web_search"):
            output = json.dumps(
                {"type": "item.completed", "item": {"type": kind}}
            ).encode()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.runtime._parse(output)

    def test_native_speed_is_explicit_and_unknown_tiers_cannot_fall_back(self):
        result = self.runtime.generate(
            "project", "openai", "default", "", "", speed="ultrafast"
        )
        self.assertEqual(self.calls[-1][1]["speed"], "ultrafast")
        self.assertEqual(result["research"]["execution_speed"], "ultrafast")
        with self.assertRaisesRegex(ValueError, "Ultrafast"):
            self.runtime.generate(
                "project", "openai", "default", "", "", speed="unsupported"
            )

    def test_tool_evidence_redacts_values_and_stays_bounded(self):
        collector = ResearchProgress(self.progress.append, ["sample"])
        value = {
            "type": "item.completed",
            "item": {
                "id": "one",
                "type": "command_execution",
                "command": "cat sample/file.py; API_KEY=supersecret password=hidden "
                + "x" * 400,
                "exit_code": 0,
            },
        }
        collector.feed(json.dumps(value).encode() + b"\n")
        command = collector.tool_events[0]["command"]
        self.assertNotIn("supersecret", command)
        self.assertNotIn("hidden", command)
        self.assertLessEqual(len(command), 300)

    def test_remote_driver_checks_identity_before_starting_codex(self):
        request = {
            "hostname": "definitely-not-this-host",
            "username": "invalid",
            "root": "/missing",
            "repositories": ["sample"],
            "model": "default",
            "text": "no model should be called",
        }
        completed = subprocess.run(
            [sys.executable, "-c", _REMOTE_DRIVER],
            input=json.dumps(request).encode(),
            capture_output=True,
            timeout=3,
        )
        self.assertEqual(completed.returncode, 2)
        event = json.loads(completed.stdout)
        self.assertEqual(event["type"], "bridge.error")
        self.assertIn("identity", event["message"])


if __name__ == "__main__":
    unittest.main()
