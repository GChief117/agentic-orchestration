"""Exercise CLI routing, handoffs, persistence, and hosted boundaries without accounts."""

import json
from pathlib import Path
import tempfile
import time
import unittest

from orchestration.engine import Engine


class FakeCLI:
    def __init__(self):
        """Record only the inputs needed to verify provider routing and context."""
        self.calls = []

    def ready(self, _provider):
        """Represent a previously verified native sign-in in these isolated tests."""
        return True

    def validate_speed(self, _provider, _model, speed):
        """Keep requested tiers visible for adapter-independent routing checks."""
        return speed

    def generate(
        self,
        provider,
        model,
        system,
        prompt,
        max_tokens,
        *,
        cancelled=None,
        speed="standard",
    ):
        """Return distinct provider output so downstream handoffs can be checked."""
        self.calls.append((provider, model, system, prompt, speed, max_tokens))
        return {
            "text": provider + " completed its assigned work",
            "complete": True,
            "usage": {},
            "response_id": None,
            "token_limit_enforced": False,
        }


class CLIExecutionTests(unittest.TestCase):
    def definition(self):
        """Assign the example's three agents to independent native providers."""
        path = Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
        definition = json.loads(path.read_text())
        for agent, provider in zip(
            definition["agents"], ("gemini", "openai", "claude")
        ):
            agent.update(
                provider=provider,
                model="default",
                execution_mode="cli",
                capabilities=["model.generate"],
                execution_speed="ultrafast" if provider == "openai" else "standard",
            )
        return definition

    def tasks(self, definition):
        """Build a serial goal whose later agents consume earlier results."""
        return [
            {
                "id": str(index),
                "agent": agent["key"],
                "capability": "model.generate",
                "depends_on": [str(index - 1)] if index else [],
                "parameters": {
                    "prompt": "Complete your assigned step",
                    "path": f"step-{index}.txt",
                },
            }
            for index, agent in enumerate(definition["agents"])
        ]

    def test_all_cli_providers_receive_context_and_persist_their_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, execution_limit=3)
            runtime = FakeCLI()
            engine.cli = runtime
            engine.executor.cli_models = runtime
            try:
                definition = self.definition()
                project = engine.create_project(definition)
                engine.submit(project, "CLI teamwork", self.tasks(definition))
                engine.start()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = engine.snapshot()
                    if all(
                        task["state"] in ("accepted", "failed", "blocked")
                        for task in state["tasks"]
                    ):
                        break
                    time.sleep(0.01)
                self.assertEqual(
                    [task["state"] for task in state["tasks"]], ["accepted"] * 3
                )
                self.assertEqual(
                    [call[0] for call in runtime.calls], ["gemini", "openai", "claude"]
                )
                self.assertIn("gemini completed", runtime.calls[1][3])
                self.assertIn("openai completed", runtime.calls[2][3])
                self.assertEqual(runtime.calls[1][4], "ultrafast")
                self.assertTrue(all(call[5] is None for call in runtime.calls))
                self.assertIn(definition["charter"]["objective"], runtime.calls[0][2])
                for task in state["tasks"]:
                    self.assertEqual(task["receipt"]["execution_mode"], "cli")
                    self.assertFalse(task["receipt"]["token_limit_enforced"])
                self.assertEqual(
                    state["tasks"][1]["receipt"]["execution_speed"], "ultrafast"
                )
            finally:
                engine.close()
            engine = Engine(directory, execution_limit=3)
            try:
                self.assertTrue(
                    all(
                        agent["execution_mode"] == "cli"
                        for agent in engine.snapshot()["agents"]
                    )
                )
                self.assertEqual(
                    engine.snapshot()["agents"][1]["execution_speed"], "ultrafast"
                )
            finally:
                engine.close()

    def test_hosted_request_cannot_dispatch_a_saved_cli_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, execution_limit=3)
            engine.cli = FakeCLI()
            try:
                definition = self.definition()
                project = engine.create_project(definition)
                with self.assertRaisesRegex(PermissionError, "hosted"):
                    engine.submit(
                        project, "Denied", self.tasks(definition), allow_cli=False
                    )
                self.assertEqual(engine.snapshot()["tasks"], [])
            finally:
                engine.close()
