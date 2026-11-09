"""Exercise reviewed setup IDs through planning, submission, and saved execution."""

import json
from pathlib import Path
import tempfile
import time
import unittest

from orchestration.engine import Engine
from orchestration.planning import propose_goal
from orchestration.plans import parse_tasks


class SetupBridge:
    """Advertise only public step metadata and record isolated execution calls."""

    def __init__(self):
        self.available = True
        self.steps = [{"id": "backend.install", "label": "Install backend"}]
        self.calls = []

    def ready(self, _project_id, capability):
        """Keep readiness explicit without consulting a remote workstation."""
        return self.available and capability == "bridge.setup.run"

    def setup_steps(self, _project_id):
        """Return only the public step selector contract."""
        return [dict(step) for step in self.steps]

    def execute(self, project_id, capability, parameters, **context):
        """Record the durable operation identity without running any command."""
        self.calls.append((project_id, capability, parameters, context))
        return {
            "accepted": True,
            "effect": "confirmed",
            "step": parameters["step"],
            "verified": True,
            "exit_code": 0,
        }


class SetupPlanner:
    """Capture planner context and return an operator-shaped setup plan."""

    def __init__(self):
        self.calls = []
        self.step = "backend.install"
        self.extra = {}

    def ready(self, _provider):
        """Advertise the fixture model without opening a provider session."""
        return True

    def generate(self, *args):
        """Return a finite plan without running its proposed workspace action."""
        self.calls.append(args)
        return {
            "complete": True,
            "text": json.dumps(
                {
                    "summary": "Install and verify the configured backend.",
                    "readiness": {
                        "status": "ready",
                        "reason": "The approved setup step is available.",
                        "missing_context": [],
                        "requires_source_context": False,
                    },
                    "tasks": [
                        {
                            "id": "install",
                            "title": "Install backend",
                            "agent": "reviewer",
                            "capability": "bridge.setup.run",
                            "parameters": {"step": self.step, **self.extra},
                            "depends_on": [],
                        }
                    ],
                }
            ),
        }


class WorkspaceSetupIntegrationTests(unittest.TestCase):
    def setUp(self):
        """Build a real temporary saved team with isolated model and bridge fakes."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = Engine(Path(temporary.name) / "runtime", execution_limit=2)
        self.addCleanup(self.engine.close)
        definition = json.loads(
            (
                Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
            ).read_text()
        )
        for agent in definition["agents"]:
            agent.update(
                provider="openai",
                model="test-model",
                execution_mode="api",
                capabilities=["model.generate"],
            )
        definition["agents"][-1]["capabilities"] = ["bridge.setup.run"]
        self.project_id = self.engine.create_project(definition)
        self.bridge = SetupBridge()
        self.planner = SetupPlanner()
        self.engine.bridge_actions = self.bridge
        self.engine.executor.bridge_actions = self.bridge
        self.engine.models = self.planner
        self.task = {
            "id": "install",
            "title": "Install backend",
            "agent": "reviewer",
            "capability": "bridge.setup.run",
            "parameters": {"step": "backend.install"},
            "depends_on": [],
        }

    def test_planner_and_snapshot_publish_safe_selectors_only(self):
        planned = propose_goal(self.engine, self.project_id, "Set up the backend")
        context = json.loads(self.planner.calls[0][3])
        self.assertEqual(context["available_setup_steps"], self.bridge.steps)
        self.assertEqual(
            context["available_workspace_actions"]["reviewer"],
            ["bridge.setup.run"],
        )
        self.assertEqual(planned["tasks"][0]["parameters"], self.task["parameters"])
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["projects"][0]["setup_steps"], self.bridge.steps)
        self.assertEqual(snapshot["tasks"], [])
        self.assertEqual(self.bridge.calls, [])

    def test_planner_cannot_invent_step_or_command(self):
        self.planner.step = "backend.unapproved"
        with self.assertRaisesRegex(ValueError, "configured setup step"):
            propose_goal(self.engine, self.project_id, "Set up the backend")
        self.planner.step = "backend.install"
        self.planner.extra = {"argv": ["/bin/sh", "-c", "unapproved"]}
        with self.assertRaisesRegex(ValueError, "configured setup step"):
            propose_goal(self.engine, self.project_id, "Set up the backend")
        self.assertEqual(self.engine.snapshot()["goals"], [])

    def test_unavailable_or_hosted_plans_cannot_dispatch_setup(self):
        self.bridge.available = False
        with self.assertRaisesRegex(PermissionError, "unavailable"):
            propose_goal(self.engine, self.project_id, "Set up the backend")
        self.assertEqual(
            json.loads(self.planner.calls[-1][3])["available_setup_steps"], []
        )
        self.bridge.available = True
        with self.assertRaisesRegex(PermissionError, "unavailable"):
            propose_goal(
                self.engine, self.project_id, "Set up the backend", allow_cli=False
            )
        self.assertEqual(
            json.loads(self.planner.calls[-1][3])["available_setup_steps"], []
        )

    def test_submission_rechecks_live_catalog_and_readiness(self):
        self.bridge.steps = []
        with self.assertRaisesRegex(ValueError, "configured setup step"):
            self.engine.submit(self.project_id, "Set up the backend", [self.task])
        self.bridge.steps = [{"id": "backend.install", "label": "Install backend"}]
        self.bridge.available = False
        with self.assertRaisesRegex(ValueError, "not connected"):
            self.engine.submit(self.project_id, "Set up the backend", [self.task])
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_setup_claims_both_mutation_resources_and_rejects_extra_parameters(self):
        agents = self.engine.store.agents(self.project_id)
        definition = parse_tasks(
            [self.task], self.engine.connection, self.project_id, agents
        )[0]
        self.assertEqual(
            definition.resources,
            [
                ("bridge-setup:" + self.project_id, "write"),
                ("bridge-local-database:" + self.project_id, "write"),
            ],
        )
        bad = {**self.task, "parameters": {**self.task["parameters"], "env": {}}}
        with self.assertRaisesRegex(ValueError, "configured setup step"):
            self.engine.submit(self.project_id, "Set up the backend", [bad])

    def test_saved_team_execution_uses_durable_task_identity(self):
        goal_id = self.engine.submit(self.project_id, "Set up the backend", [self.task])
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = [
                task for task in self.engine.store.tasks() if task["goal_id"] == goal_id
            ]
            if tasks and tasks[0]["state"] in ("accepted", "failed", "blocked"):
                break
            time.sleep(0.01)
        self.assertEqual(tasks[0]["state"], "accepted")
        self.assertEqual(len(self.bridge.calls), 1)
        project_id, capability, parameters, context = self.bridge.calls[0]
        self.assertEqual(project_id, self.project_id)
        self.assertEqual(capability, "bridge.setup.run")
        self.assertEqual(parameters, {"step": "backend.install"})
        self.assertEqual(context["operation_id"], tasks[0]["id"])
        self.assertEqual(context["project_definition"]["team_size"], 3)
        self.assertTrue(tasks[0]["receipt"]["verified"])


if __name__ == "__main__":
    unittest.main()
