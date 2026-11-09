"""Provider execution and multi-agent handoffs without live network calls or billing."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from orchestration.engine import Engine
from orchestration.models import ModelRuntime

ROOT = Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def test_provider_requests_parse_text_and_never_export_keys(self):
        cases = {
            "openai": {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Result"}],
                    }
                ],
            },
            "claude": {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Result"}],
            },
            "gemini": {
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": "Result"}]}}
                ]
            },
        }
        for provider, response in cases.items():
            with self.subTest(provider=provider), patch.dict(
                "os.environ", {}, clear=True
            ):
                calls = []

                def request(name, key, url, body=None):
                    calls.append((name, key, url, body))
                    if body is None:
                        return (
                            {
                                "models": [
                                    {
                                        "name": "models/test-model",
                                        "supportedGenerationMethods": [
                                            "generateContent"
                                        ],
                                    }
                                ]
                            }
                            if name == "gemini"
                            else {"data": [{"id": "test-model"}]}
                        )
                    return response

                runtime = ModelRuntime(request=request)
                runtime.connect(provider, "private-test-key")
                self.assertNotIn("private-test-key", json.dumps(runtime.catalog()))
                result = runtime.generate(
                    provider, "test-model", "Instructions", "Task", 100
                )
                self.assertEqual(result["text"], "Result")
                self.assertTrue(result["complete"])
                self.assertEqual(len(calls), 2)
                self.assertNotIn("private-test-key", calls[-1][2])
                self.assertNotIn("tools", calls[-1][3])
                runtime.disconnect(provider)
                with self.assertRaisesRegex(ValueError, "API key"):
                    runtime.generate(provider, "test-model", "System", "Task", 100)

    def test_model_handoff_uses_assigned_providers_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {}, clear=True
        ):
            engine = Engine(directory, execution_limit=3)
            try:
                calls = []

                def request(provider, key, url, body=None):
                    if body is None:
                        return {"data": [{"id": "test-model"}]}
                    calls.append((provider, body))
                    if provider == "openai":
                        return {
                            "id": "response-1",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "Builder completed the draft.",
                                        }
                                    ],
                                }
                            ],
                        }
                    return {
                        "id": "response-2",
                        "stop_reason": "end_turn",
                        "content": [
                            {"type": "text", "text": "Reviewer evaluated the draft."}
                        ],
                    }

                engine.models.request = request
                engine.models.connect("openai", "private-openai")
                engine.models.connect("claude", "private-claude")
                project = json.loads(
                    (ROOT / "examples/greenfield_project.json").read_text()
                )
                for agent in project["agents"]:
                    if agent["key"] in ("builder", "reviewer"):
                        agent.update(
                            provider="openai"
                            if agent["key"] == "builder"
                            else "claude",
                            model="test-model",
                            capabilities=["model.generate"],
                            collaborators=["reviewer"]
                            if agent["key"] == "builder"
                            else [],
                        )
                project_id = engine.create_project(project)
                engine.submit(
                    project_id,
                    "Build and review",
                    [
                        {
                            "id": "build",
                            "agent": "builder",
                            "capability": "model.generate",
                            "parameters": {
                                "path": "draft.txt",
                                "prompt": "Create a draft",
                                "max_tokens": 100,
                            },
                        },
                        {
                            "id": "review",
                            "agent": "reviewer",
                            "capability": "model.generate",
                            "depends_on": ["build"],
                            "parameters": {
                                "path": "review.txt",
                                "prompt": "Review the draft",
                                "max_tokens": 100,
                            },
                        },
                    ],
                )
                engine.start()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = engine.snapshot()
                    if len(state["tasks"]) == 2 and all(
                        task["state"] in ("accepted", "failed", "blocked")
                        for task in state["tasks"]
                    ):
                        break
                    time.sleep(0.01)
                self.assertEqual(
                    [task["state"] for task in state["tasks"]], ["accepted", "accepted"]
                )
                self.assertEqual([call[0] for call in calls], ["openai", "claude"])
                self.assertIn(
                    "Builder completed the draft.",
                    calls[1][1]["messages"][0]["content"],
                )
                self.assertNotIn("private-openai", json.dumps(state))
                self.assertNotIn("private-claude", json.dumps(state))
                agents = {agent["key"]: agent for agent in state["agents"]}
                self.assertIn("builder", agents["reviewer"]["collaborators"])
                self.assertEqual(state["tasks"][1]["receipt"]["provider"], "claude")
                self.assertEqual(
                    engine.connection.path(
                        state["tasks"][0]["receipt"]["artifact"]
                    ).read_text(),
                    "Builder completed the draft.",
                )
            finally:
                engine.close()
            engine = Engine(directory, execution_limit=3)
            try:
                self.assertFalse(engine.models.ready("openai"))
                self.assertEqual(
                    next(
                        agent
                        for agent in engine.snapshot()["agents"]
                        if agent["key"] == "builder"
                    )["model"],
                    "test-model",
                )
            finally:
                engine.close()

    def test_unconnected_model_tasks_never_enter_the_queue(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {}, clear=True
        ):
            engine = Engine(directory, execution_limit=2)
            try:
                project = json.loads(
                    (ROOT / "examples/greenfield_project.json").read_text()
                )
                project["agents"][0].update(
                    provider="openai",
                    model="test-model",
                    capabilities=["model.generate"],
                )
                project_id = engine.create_project(project)
                with self.assertRaisesRegex(ValueError, "Connect"):
                    engine.submit(
                        project_id,
                        "Unconnected",
                        [
                            {
                                "id": "a",
                                "agent": project["agents"][0]["key"],
                                "capability": "model.generate",
                                "parameters": {"prompt": "Task", "path": "result.txt"},
                            }
                        ],
                    )
                self.assertEqual(engine.snapshot()["tasks"], [])
            finally:
                engine.close()
