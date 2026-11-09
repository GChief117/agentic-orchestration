"""Check bounded transient activity without persisting live response buffers."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from orchestration.engine import Engine


class ReportingCLI:
    def __init__(self):
        """Hold the fake model until the test inspects and cancels its work."""
        self.started = threading.Event()

    def ready(self, _provider):
        return True

    def validate_speed(self, _provider, _model, speed):
        return speed

    def generate(self, *_args, cancelled=None, progress=None, **_kwargs):
        """Emit visible text, then honor the same cancellation signal as a CLI."""
        progress(
            {"phase": "writing", "message": "Writing response", "preview": "x" * 9000}
        )
        self.started.set()
        while not cancelled.wait(0.01):
            pass
        raise InterruptedError("Cancelled")


class LiveActivityTests(unittest.TestCase):
    def test_activity_is_bounded_live_and_removed_after_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, execution_limit=1)
            runtime = ReportingCLI()
            engine.cli = runtime
            engine.executor.cli_models = runtime
            try:
                definition = json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "examples/greenfield_project.json"
                    ).read_text()
                )
                lead = definition["agents"][0]
                lead.update(
                    provider="openai",
                    model="default",
                    execution_mode="cli",
                    capabilities=["model.generate"],
                )
                project = engine.create_project(definition)
                goal = engine.submit(
                    project,
                    "Visible progress",
                    [
                        {
                            "id": "work",
                            "agent": lead["key"],
                            "capability": "model.generate",
                            "parameters": {
                                "path": "result.txt",
                                "prompt": "Write a result",
                            },
                        }
                    ],
                )
                engine.start()
                self.assertTrue(runtime.started.wait(2))
                active = engine.snapshot()["tasks"][0]
                self.assertEqual(active["activity"]["phase"], "writing")
                self.assertEqual(len(active["activity"]["preview"]), 8000)
                self.assertIn("timing", active)
                engine.cancel_goal(goal)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    task = engine.snapshot()["tasks"][0]
                    if task["state"] == "cancelled":
                        break
                    time.sleep(0.01)
                self.assertEqual(task["state"], "cancelled")
                self.assertIsNone(task["activity"])
                self.assertNotIn("x" * 100, json.dumps(engine.store.snapshot()))
            finally:
                engine.close()
