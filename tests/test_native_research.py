"""Native research must run through saved app goals and assigned team members."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from orchestration.engine import Engine


class FakeResearch:
    """Represent a remote research runtime without launching a model or network call."""

    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.wait_for_cancel = False

    def ready(self, _project_id, provider):
        return provider == "openai"

    def generate(
        self,
        project_id,
        provider,
        model,
        system,
        prompt,
        *,
        cancelled=None,
        progress=None,
        speed="standard",
    ):
        self.calls.append(
            (
                project_id,
                provider,
                model,
                json.loads(system) if system.startswith("{") else system,
                json.loads(prompt),
                cancelled,
            )
        )
        self.started.set()
        if self.wait_for_cancel:
            while not cancelled.wait(0.01):
                pass
            raise InterruptedError("Native work cancelled")
        if "selected_coordinator" in self.calls[-1][4]:
            result = json.dumps(
                {
                    "summary": "Inspect actual source through the workspace.",
                    "readiness": {
                        "status": "ready",
                        "reason": "Native research can read the repository.",
                        "requires_source_context": True,
                        "missing_context": [],
                    },
                    "tasks": [
                        {
                            "id": "report",
                            "title": "Inspect actual code",
                            "agent": "builder",
                            "capability": "model.generate",
                            "parameters": {
                                "prompt": "Read the actual implementation and produce the report."
                            },
                            "depends_on": [],
                        }
                    ],
                }
            )
        else:
            if progress:
                progress({"phase": "working", "message": "Inspected repository source"})
            result = "The native research result cites src/service.py and .docs/codebase/STACK.md."
        return {
            "text": result,
            "complete": True,
            "usage": {},
            "response_id": "native-test",
            "token_limit_enforced": False,
            "research": {
                "mode": "native_bridge",
                "runtime": "Codex CLI",
                "skill": "pio-map-codebase",
                "skill_read": True,
                "source_read": True,
                "tool_events": [
                    {
                        "tool": "command_execution",
                        "command": "cat src/service.py",
                        "status": "completed",
                        "exit_code": 0,
                    }
                ],
            },
        }


class NativeResearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.engine = Engine(self.directory, execution_limit=3)
        self.addCleanup(lambda: self.engine.close())
        self.runtime = FakeResearch()
        self.engine.research = self.runtime
        self.engine.executor.research_runtime = self.runtime
        self.definition = json.loads(
            (
                Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
            ).read_text()
        )
        for agent in self.definition["agents"]:
            agent.update(
                provider="openai",
                model="default",
                execution_mode="cli",
                capabilities=["model.generate"],
            )
        self.project_id = self.engine.create_project(self.definition)

    def task(self):
        return {
            "id": "research",
            "agent": "builder",
            "capability": "model.generate",
            "parameters": {
                "path": "report.md",
                "prompt": "Inspect the repository and deliver an evidence-backed report.",
            },
        }

    def wait_for(self, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = self.engine.snapshot()["tasks"]
            if tasks and tasks[-1]["state"] == state:
                return tasks[-1]
            time.sleep(0.01)
        self.fail(f"Task did not reach {state}")

    def test_saved_goal_dispatches_assigned_agent_and_persists_tool_receipt(self):
        goal_id = self.engine.submit(
            self.project_id,
            "Investigate actual source",
            [self.task()],
            research_mode="native_cli",
        )
        self.engine.start()
        task = self.wait_for("accepted")
        call = self.runtime.calls[0]
        self.assertEqual(call[:3], (self.project_id, "openai", "default"))
        self.assertEqual(call[3]["agent"]["name"], "Artifact builder")
        self.assertEqual(call[3]["goal"]["research_mode"], "native_cli")
        self.assertEqual(call[4]["selected_source_files"], [])
        self.assertTrue(task["receipt"]["research"]["skill_read"])
        self.assertEqual(
            task["receipt"]["research"]["tool_events"][0]["command"],
            "cat src/service.py",
        )
        self.assertEqual(
            self.engine.connection.path(task["receipt"]["artifact"]).read_text(),
            "The native research result cites src/service.py and .docs/codebase/STACK.md.",
        )
        self.engine.close()
        self.engine = Engine(self.directory, execution_limit=3)
        goal = next(
            goal for goal in self.engine.snapshot()["goals"] if goal["id"] == goal_id
        )
        self.assertEqual(goal["research_mode"], "native_cli")

    def test_non_codex_or_hosted_submissions_do_not_queue_native_work(self):
        self.definition["agents"][1]["provider"] = "claude"
        project = self.engine.create_project(self.definition)
        with self.assertRaisesRegex(ValueError, "Codex"):
            self.engine.submit(
                project,
                "Unsupported runtime",
                [self.task()],
                research_mode="native_cli",
            )
        with self.assertRaisesRegex(ValueError, "Native workspace"):
            self.engine.submit(
                self.project_id,
                "Hosted request",
                [self.task()],
                research_mode="native_cli",
                allow_cli=False,
            )
        self.assertEqual(self.engine.snapshot()["tasks"], [])
        self.assertEqual(self.runtime.calls, [])

    def test_cancelled_queued_goal_never_reaches_the_native_runtime(self):
        goal = self.engine.submit(
            self.project_id,
            "Cancel before dispatch",
            [self.task()],
            research_mode="native_cli",
        )
        self.engine.cancel_goal(goal)
        self.engine.tick()
        self.assertEqual(self.engine.snapshot()["tasks"][0]["state"], "cancelled")
        self.assertEqual(self.runtime.calls, [])

    def test_inflight_cancellation_reaches_remote_runtime_signal(self):
        self.runtime.wait_for_cancel = True
        goal = self.engine.submit(
            self.project_id,
            "Cancel during research",
            [self.task()],
            research_mode="native_cli",
        )
        self.engine.start()
        self.assertTrue(self.runtime.started.wait(2))
        self.engine.cancel_goal(goal)
        task = self.wait_for("cancelled")
        self.assertTrue(self.runtime.calls[0][-1].is_set())
        self.assertFalse(task["receipt"]["accepted"])

    def test_native_planning_uses_saved_team_without_manual_attachments(self):
        plan = self.engine.plan_goal(
            self.project_id, "Inspect actual implementation", research_mode="native_cli"
        )
        self.assertEqual(plan["research_mode"], "native_cli")
        self.assertEqual(plan["tasks"][0]["agent"], "builder")
        call = self.runtime.calls[0]
        self.assertEqual(call[0], self.project_id)
        self.assertEqual(call[4]["research_mode"], "native_cli")
        self.assertEqual(call[4]["selected_source_files"], [])
        self.assertEqual(self.engine.snapshot()["tasks"], [])


if __name__ == "__main__":
    unittest.main()
