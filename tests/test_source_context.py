"""Selected project source reaches model context without permitting source writes."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from orchestration.engine import Engine


class SourceContextTests(unittest.TestCase):
    def test_imported_source_is_explicitly_attached_and_receipted(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {}, clear=True
        ):
            directory = Path(temporary).resolve()
            source = directory / "work"
            source.mkdir()
            (source / "pyproject.toml").write_text('[project]\nname="work"\n')
            (source / "app.py").write_text(
                'def current_work():\n    return "source context"\n'
            )
            (source / "private.txt").write_text("unselected text must not be sent")
            (source / ".env").write_text("DO_NOT_SEND=secret")
            engine = Engine(directory / "runtime", execution_limit=2)
            try:
                scan = engine.scan_projects(str(source))
                project_id = engine.import_projects(
                    scan["scan_id"], [scan["projects"][0]["id"]]
                )[0]["id"]
                files = engine.project_files(project_id)
                self.assertIn("app.py", [item["path"] for item in files["files"]])
                self.assertNotIn(".env", [item["path"] for item in files["files"]])
                state = engine.snapshot()
                agents = state["agents"]
                aliases = {agent["id"]: agent["key"] for agent in agents}
                roster = [
                    {**agent, "reports_to": aliases.get(agent["reports_to"])}
                    for agent in agents
                ]
                builder = next(agent for agent in roster if agent["key"] == "builder")
                builder.update(
                    provider="openai",
                    execution_mode="api",
                    model="test-model",
                    capabilities=["model.generate"],
                )
                engine.update_team(
                    project_id, {"agents": roster, "team_size": 3, "max_concurrency": 2}
                )
                prompts = []

                def request(_provider, _key, _url, body=None):
                    if body is None:
                        return {"data": [{"id": "test-model"}]}
                    prompts.append(body["input"])
                    return {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Source review complete",
                                    }
                                ],
                            }
                        ],
                    }

                engine.models.request = request
                engine.models.connect("openai", "test-credential")
                task = {
                    "id": "review-source",
                    "agent": "builder",
                    "capability": "model.generate",
                    "parameters": {
                        "path": "review.txt",
                        "prompt": "Review the selected source",
                        "source_files": ["app.py"],
                    },
                }
                engine.submit(project_id, "Review source", [task])
                engine.start()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    tasks = engine.snapshot()["tasks"]
                    if tasks[0]["state"] in ("accepted", "failed"):
                        break
                    time.sleep(0.01)
                self.assertEqual(tasks[0]["state"], "accepted")
                self.assertIn("source context", prompts[0])
                self.assertNotIn("unselected text", prompts[0])
                self.assertNotIn("DO_NOT_SEND", prompts[0])
                self.assertEqual(
                    tasks[0]["receipt"]["source_files"][0]["path"], "app.py"
                )
                self.assertEqual(
                    (source / "app.py").read_text(),
                    'def current_work():\n    return "source context"\n',
                )
            finally:
                engine.close()

    def test_hosted_engine_rejects_source_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Engine(temporary, execution_limit=1, allow_source_access=False)
            try:
                with self.assertRaisesRegex(PermissionError, "hosted"):
                    engine.project_files("any")
            finally:
                engine.close()
