"""Shared notes and explicit bridge sources stay attached to the saved project."""

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from orchestration.engine import Engine


class RecordingRuntime:
    """Record planner and task inputs without an account or network request."""

    def __init__(self):
        self.calls = []
        self.sources = []
        self.plan = {
            "summary": "Produce the requested report.",
            "readiness": {
                "status": "ready",
                "reason": "The supplied context supports this report.",
                "missing_context": [],
                "requires_source_context": False,
            },
            "tasks": [
                {
                    "id": "report",
                    "title": "Write the report",
                    "agent": "builder",
                    "parameters": {"prompt": "Write the requested report."},
                    "depends_on": [],
                }
            ],
        }

    def ready(self, _provider):
        return True

    def validate_speed(self, _provider, _model, _speed):
        return None

    def generate(self, provider, model, system, prompt, max_tokens, **kwargs):
        self.calls.append({"system": system, "prompt": json.loads(prompt)})
        planning = "selected_coordinator" in self.calls[-1]["prompt"]
        return {
            "text": json.dumps(self.plan) if planning else "Source-grounded report.",
            "complete": True,
            "usage": {},
            "response_id": None,
            "token_limit_enforced": False,
        }


class FakeSourceBridge:
    """Represent one already-configured read-only bridge using literal test data."""

    def __init__(self, project_id):
        self.project_id = project_id
        self.reads = []
        self.validations = []

    def configured(self, project_id):
        return project_id == self.project_id

    def inventory(self, _project_id):
        return {
            "bridge": {"name": "Test bridge", "status": "connected"},
            "repositories": [
                {
                    "name": "sample",
                    "available": True,
                    "revision": "a" * 40,
                    "candidates": ["sample/README.md"],
                }
            ],
        }

    def validate_paths(self, project_id, paths):
        self.validations.append((project_id, list(paths)))
        if paths != ["sample/README.md"]:
            raise ValueError("Source file is outside the test bridge")
        return paths

    def read_files(self, project_id, paths):
        self.reads.append((project_id, list(paths)))
        self.validate_paths(project_id, paths)
        content = "The sample repository uses a Python HTTP service."
        return [
            {
                "path": paths[0],
                "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        ]


class SharedContextTests(unittest.TestCase):
    def setUp(self):
        """Create a persisted chart with fake CLI execution and no source root."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.engine = Engine(self.directory, execution_limit=3)
        self.addCleanup(lambda: self.engine.close())
        definition = json.loads(
            (
                Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
            ).read_text()
        )
        for agent in definition["agents"]:
            agent.update(
                execution_mode="cli",
                provider="openai",
                model="test-model",
                capabilities=["model.generate"],
            )
        self.project_id = self.engine.create_project(definition)
        self.runtime = RecordingRuntime()
        self.engine.cli = self.runtime
        self.engine.executor.cli_models = self.runtime

    def task(self, **parameters):
        """Return an explicit model task whose result belongs to this project."""
        return {
            "id": "report",
            "agent": "builder",
            "capability": "model.generate",
            "parameters": {
                "path": "report.md",
                "prompt": "Summarize our shared context.",
                **parameters,
            },
        }

    def finish_work(self):
        """Run one scheduling tick and wait for the isolated fake model result."""
        self.engine.tick()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = self.engine.snapshot()["tasks"]
            if tasks and all(
                task["state"] in ("accepted", "failed", "blocked") for task in tasks
            ):
                self.assertTrue(
                    all(task["state"] == "accepted" for task in tasks), tasks
                )
                return tasks
            time.sleep(0.01)
        self.fail("Fixture work did not finish")

    def test_notes_survive_restart_with_their_revision(self):
        saved = self.engine.add_project_context(
            self.project_id,
            "Owner context",
            "The delivery is a concise technical report.",
        )
        self.engine.close()
        self.engine = Engine(self.directory, execution_limit=3)
        restored = self.engine.project_context(self.project_id)
        self.assertEqual(restored, saved)
        self.assertEqual(
            restored["entries"][0]["content"],
            "The delivery is a concise technical report.",
        )
        self.assertEqual(len(restored["revision"]), 64)

    def test_planner_receives_shared_notes_without_manual_file_selection(self):
        self.engine.add_project_context(
            self.project_id, "Scope", "Focus the report on customer onboarding."
        )
        self.engine.plan_goal(self.project_id, "Prepare a report")
        prompt = self.runtime.calls[0]["prompt"]
        self.assertEqual(prompt["selected_source_files"], [])
        self.assertEqual(
            prompt["shared_context"]["entries"][0]["content"],
            "Focus the report on customer onboarding.",
        )

    def test_saved_notes_remain_available_when_the_bridge_is_offline(self):
        self.engine.add_project_context(self.project_id, "Scope", "Persistent context")
        bridge = FakeSourceBridge(self.project_id)

        def unavailable(_project_id):
            raise ValueError("Source bridge connection is unavailable")

        bridge.inventory = unavailable
        self.engine.source_bridges = bridge
        context = self.engine.project_context(self.project_id)
        self.assertEqual(context["entries"][0]["content"], "Persistent context")
        self.assertEqual(context["source_bridge"]["status"], "unavailable")

    def test_cli_task_receives_original_goal_and_shared_notes(self):
        saved = self.engine.add_project_context(
            self.project_id, "Scope", "Do not omit operating costs."
        )
        self.engine.submit(self.project_id, "Prepare the owner report", [self.task()])
        self.finish_work()
        system = json.loads(self.runtime.calls[0]["system"])
        self.assertEqual(system["goal"]["title"], "Prepare the owner report")
        self.assertEqual(system["shared_context"]["revision"], saved["revision"])
        self.assertEqual(
            system["shared_context"]["entries"][0]["content"],
            "Do not omit operating costs.",
        )

    def test_context_cannot_change_while_project_work_is_queued(self):
        self.engine.submit(self.project_id, "Queued work", [self.task()])
        with self.assertRaisesRegex(ValueError, "active work"):
            self.engine.add_project_context(
                self.project_id, "Late note", "This changes the requirements."
            )
        self.assertEqual(self.engine.context.read(self.project_id)["entries"], [])

    def test_context_edit_invalidates_the_accepted_plan_revision(self):
        plan = self.engine.plan_goal(self.project_id, "Prepare a report")
        self.engine.add_project_context(
            self.project_id, "New requirement", "Include a cost table."
        )
        with self.assertRaisesRegex(ValueError, "changed after planning"):
            self.engine.submit(
                self.project_id,
                "Prepare a report",
                plan["tasks"],
                expected_team_revision=plan["team_revision"],
            )
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_bridge_per_task_sources_reach_executor_without_local_source_root(self):
        bridge = FakeSourceBridge(self.project_id)
        self.engine.source_bridges = bridge
        self.runtime.plan["readiness"]["requires_source_context"] = True
        self.runtime.plan["tasks"][0]["parameters"]["source_files"] = [
            "sample/README.md"
        ]
        plan = self.engine.plan_goal(
            self.project_id, "Describe the actual sample stack"
        )
        self.assertIsNone(self.engine.store.project(self.project_id).get("source_path"))
        self.assertEqual(
            plan["tasks"][0]["parameters"]["source_files"], ["sample/README.md"]
        )
        self.assertEqual(
            self.runtime.calls[0]["prompt"]["remote_source_inventory"],
            bridge.inventory(self.project_id),
        )
        self.engine.submit(
            self.project_id,
            "Describe the actual sample stack",
            plan["tasks"],
            expected_team_revision=plan["team_revision"],
        )
        tasks = self.finish_work()
        prompt = self.runtime.calls[-1]["prompt"]
        self.assertIn(
            "Python HTTP service", prompt["selected_source_files"][0]["content"]
        )
        self.assertEqual(
            tasks[0]["receipt"]["source_files"][0]["path"], "sample/README.md"
        )
        self.assertEqual(len(bridge.reads), 1)
        self.assertGreaterEqual(len(bridge.validations), 2)

    def test_persisted_context_rejects_oversized_and_malformed_entries(self):
        path = self.engine.context._path(self.project_id)
        valid = {"id": "note", "title": "Scope", "content": "Context", "created": 1}
        cases = [
            "x" * (256 * 1024 + 1),
            json.dumps([valid] * 33),
            json.dumps([{**valid, "content": "x" * 48001}]),
            json.dumps([{**valid, "content": None}]),
            json.dumps([{**valid, "created": float("inf")}]),
            json.dumps([{**valid, "title": ""}]),
            json.dumps([valid, valid]),
            json.dumps(["not an entry"]),
        ]
        for content in cases:
            with self.subTest(content=content[:80]):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    self.engine.context.read(self.project_id)

    def test_persisted_context_does_not_follow_a_symbolic_link(self):
        outside = self.directory / "outside.json"
        outside.write_text("[]")
        self.engine.context._path(self.project_id).symlink_to(outside)
        with self.assertRaises(ValueError):
            self.engine.context.read(self.project_id)

    def test_hosted_execution_refuses_even_an_injected_source_connection(self):
        self.engine.source_bridges = FakeSourceBridge(self.project_id)
        self.engine.allow_source_access = False
        with self.assertRaisesRegex(PermissionError, "hosted"):
            self.engine.read_project_files(self.project_id, ["sample/README.md"])


if __name__ == "__main__":
    unittest.main()
