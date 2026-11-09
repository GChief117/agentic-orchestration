"""Preserve saved chart identity and real workspace results across agent handoffs."""

import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from orchestration.bridge_actions import BridgeActions
from orchestration.engine import Engine
from orchestration.projects import parse_project


class RecordingCLI:
    """Capture downstream context without starting a provider process."""

    def __init__(self):
        self.calls = []

    def ready(self, _provider):
        """Represent a configured local model in this isolated test."""
        return True

    def validate_speed(self, _provider, _model, _speed):
        """Accept the fixture's explicit standard speed."""

    def generate(self, _provider, _model, system, prompt, _max_tokens, **_kwargs):
        """Retain the exact handoff seen by the next model and return bounded text."""
        self.calls.append({"system": json.loads(system), "prompt": json.loads(prompt)})
        return {
            "text": "The demo is open at the verified workspace URL.",
            "complete": True,
            "usage": {},
            "response_id": None,
            "token_limit_enforced": False,
        }


class RecordingDemoBridge:
    """Return a non-artifact demo receipt without launching an app or contacting SSH."""

    def __init__(self):
        self.calls = []

    def ready(self, _project_id, capability):
        """Advertise only the demo capability needed by this test."""
        return capability == "bridge.demo.launch"

    def execute(self, project_id, capability, parameters, **context):
        """Record the exported chart and return a successful workspace identity."""
        self.calls.append((project_id, capability, parameters, context))
        return {
            "accepted": True,
            "effect": "confirmed",
            "bridge_action": capability,
            "workspace_project_id": "remote-project-123",
            "url": "http://127.0.0.1:8765",
            "opened": True,
        }


class WorkspaceHandoffTests(unittest.TestCase):
    def setUp(self):
        """Persist a 20-agent chart with multiple hierarchy levels and peer links."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = Engine(self.root / "runtime", execution_limit=3)
        self.addCleanup(self.engine.close)
        agents = []
        for index in range(20):
            key = f"agent-{index}"
            parent = (
                None
                if index == 0
                else "agent-0"
                if index < 4
                else f"agent-{1 + index % 3}"
            )
            agents.append(
                {
                    "key": key,
                    "name": f"Agent {index}",
                    "role": "Coordination" if index < 4 else "Implementation",
                    "instructions": "Use the saved charter and pass verified results to the next owner.",
                    "responsibilities": ["Produce the assigned result"],
                    "capabilities": ["model.generate", "bridge.demo.launch"]
                    if index == 0
                    else ["model.generate"],
                    "reports_to": parent,
                    "collaborators": ["agent-5"] if index == 4 else [],
                    "max_concurrency": 1,
                    "provider": "openai",
                    "model": "test-model",
                    "execution_mode": "cli",
                    "execution_speed": "standard",
                }
            )
        self.definition = {
            "name": "Workspace handoff",
            "mode": "greenfield",
            "charter": {
                "objective": "Launch a demo and describe its verified workspace.",
                "scope": ["Use the saved organization"],
                "constraints": ["Keep the test isolated"],
                "success_criteria": ["The next owner receives the workspace URL"],
            },
            "team_size": len(agents),
            "max_concurrency": 3,
            "team_model": {
                "provider": "openai",
                "model": "test-model",
                "execution_mode": "cli",
                "execution_speed": "standard",
            },
            "agents": agents,
        }
        self.project_id = self.engine.create_project(self.definition)

    def test_saved_twenty_agent_chart_round_trips_through_demo_export(self):
        project = self.engine.store.project(self.project_id)
        agents = self.engine.store.agents(self.project_id)
        original_ids = {agent["id"] for agent in agents}
        by_id = {agent["id"]: agent["key"] for agent in agents}
        self.assertTrue(any(agent["reports_to"] in original_ids for agent in agents))
        exported = self.engine._team_definition(project, agents)
        self.assertEqual(exported["team_size"], 20)
        parsed = parse_project(exported, self.engine.connection)
        self.assertEqual(len(parsed.agents), 20)
        self.assertEqual(parsed.team_model, self.definition["team_model"])
        for original, cloned in zip(agents, exported["agents"]):
            self.assertEqual(cloned["reports_to"], by_id.get(original["reports_to"]))
            self.assertNotIn(cloned["reports_to"], original_ids)
            self.assertNotIn("id", cloned)
        clone_id = self.engine.create_project(exported)
        cloned_agents = self.engine.store.agents(clone_id)
        self.assertEqual(len(cloned_agents), 20)
        self.assertTrue(original_ids.isdisjoint(agent["id"] for agent in cloned_agents))
        self.assertEqual(cloned_agents[4]["collaborators"], ["agent-5"])
        self.assertEqual(cloned_agents[5]["collaborators"], ["agent-4"])

    def test_non_artifact_demo_receipt_reaches_the_next_model(self):
        runtime, bridge = RecordingCLI(), RecordingDemoBridge()
        self.engine.cli = runtime
        self.engine.executor.cli_models = runtime
        self.engine.bridge_actions = bridge
        self.engine.executor.bridge_actions = bridge
        goal_id = self.engine.submit(
            self.project_id,
            "Open the workspace and report its URL",
            [
                {
                    "id": "launch",
                    "title": "Launch workspace demo",
                    "agent": "agent-0",
                    "capability": "bridge.demo.launch",
                    "parameters": {"port": 8765},
                },
                {
                    "id": "describe",
                    "title": "Describe opened workspace",
                    "agent": "agent-1",
                    "capability": "model.generate",
                    "parameters": {
                        "path": "workspace.md",
                        "prompt": "Describe the verified URL and opened state.",
                    },
                    "depends_on": ["launch"],
                },
            ],
        )
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            tasks = [
                task for task in self.engine.store.tasks() if task["goal_id"] == goal_id
            ]
            if tasks and all(
                task["state"] in ("accepted", "failed", "blocked") for task in tasks
            ):
                break
            time.sleep(0.01)
        self.assertEqual([task["state"] for task in tasks], ["accepted", "accepted"])
        self.assertEqual(len(runtime.calls), 1)
        handoffs = runtime.calls[0]["prompt"]["completed_handoffs"]
        self.assertEqual(len(handoffs), 1)
        self.assertIsNone(handoffs[0]["artifact"])
        receipt = json.loads(handoffs[0]["result"])
        self.assertEqual(receipt["url"], "http://127.0.0.1:8765")
        self.assertTrue(receipt["opened"])
        self.assertEqual(receipt["project_id"], self.project_id)
        self.assertEqual(receipt["workspace_project_id"], "remote-project-123")
        self.assertFalse(handoffs[0]["truncated"])
        exported = bridge.calls[0][3]["project_definition"]
        self.assertEqual(exported["team_size"], 20)
        self.assertEqual(
            len(parse_project(exported, self.engine.connection).agents), 20
        )

    def test_adapter_keeps_remote_project_identity_separate_from_controller(self):
        runtime_root = self.root / "demo-package"
        for filename in (
            "app.py",
            "requirements.txt",
            "orchestration/runtime.py",
            "frontend/index.html",
        ):
            path = runtime_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")

        class ConfiguredBridge:
            """Provide a target allowlist without a real executable or credentials."""

            def _configuration(self, project_id):
                return {
                    "hostname": "test.local",
                    "username": "tester",
                    "root": "/projects",
                    "repositories": ["sample"],
                    "agent_command": "/fake/bridge",
                    "demo_directory": "/isolated/demo",
                }

        def fake_runner(*_args):
            """Return the remote demo's project ID as the bridge protocol would."""
            return subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "identity": {"hostname": "test.local", "username": "tester"},
                        "result": {
                            "project_id": "remote-project-123",
                            "url": "http://127.0.0.1:8765",
                            "opened": True,
                        },
                    }
                ).encode(),
                b"",
            )

        adapter = BridgeActions(
            ConfiguredBridge(), application_root=runtime_root, runner=fake_runner
        )
        result = adapter.execute(
            self.project_id,
            "bridge.demo.launch",
            {"port": 8765},
            project_definition=self.definition,
        )
        self.assertEqual(result["workspace_project_id"], "remote-project-123")
        self.assertTrue(result["opened"])
        self.assertEqual(result["url"], "http://127.0.0.1:8765")


if __name__ == "__main__":
    unittest.main()
