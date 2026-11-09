"""Persist shared team defaults separately from individual model overrides."""

import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from orchestration.engine import Engine
from orchestration.planning import team_revision


class TeamModelTests(unittest.TestCase):
    def test_existing_database_gains_team_default_without_losing_its_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with sqlite3.connect(Path(directory) / "state.sqlite3") as database:
                database.execute(
                    "CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,mode TEXT,charter TEXT,max_concurrency INTEGER,created REAL)"
                )
                database.execute(
                    "INSERT INTO projects VALUES(?,?,?,?,?,?)",
                    ("existing", "Existing project", "greenfield", "{}", 1, 1.0),
                )
            engine = Engine(directory, execution_limit=1)
            try:
                project = engine.store.project("existing")
                self.assertEqual(project["name"], "Existing project")
                self.assertIsNone(project["team_model"])
            finally:
                engine.close()

    def definition(self):
        """Return a mixed roster with one explicit shared model preference."""
        path = Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
        definition = json.loads(path.read_text())
        definition["team_model"] = {
            "execution_mode": "cli",
            "provider": "openai",
            "model": "shared-model",
            "execution_speed": "fast",
        }
        for index, agent in enumerate(definition["agents"]):
            agent.update(
                execution_mode="cli",
                provider="claude" if index else "openai",
                model="individual-model",
                execution_speed="standard",
            )
        return definition

    def roster(self, engine, project_id):
        """Convert stored parent IDs back to the keys expected by team updates."""
        agents = engine.store.agents(project_id)
        keys = {agent["id"]: agent["key"] for agent in agents}
        return [
            {**agent, "reports_to": keys.get(agent["reports_to"])} for agent in agents
        ]

    def test_shared_choice_survives_mixed_models_updates_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            definition = self.definition()
            engine = Engine(directory, execution_limit=3)
            try:
                project_id = engine.create_project(definition)
                before = engine.store.agents(project_id)
                old_revision = team_revision(engine.store.project(project_id), before)
                updated = {
                    **definition["team_model"],
                    "model": "new-shared-model",
                    "execution_speed": "ultrafast",
                }
                document = {
                    "agents": self.roster(engine, project_id),
                    "team_size": 3,
                    "max_concurrency": 2,
                    "team_model": updated,
                }
                engine.update_team(project_id, document)
                self.assertEqual(engine.store.agents(project_id), before)
                self.assertEqual(
                    engine.store.project(project_id)["team_model"], updated
                )
                self.assertNotEqual(
                    team_revision(engine.store.project(project_id), before),
                    old_revision,
                )
                # Older clients may edit the roster without sending the new field.
                document.pop("team_model")
                engine.update_team(project_id, document)
                self.assertEqual(
                    engine.store.project(project_id)["team_model"], updated
                )
            finally:
                engine.close()
            engine = Engine(directory, execution_limit=3)
            try:
                self.assertEqual(
                    engine.store.project(project_id)["team_model"], updated
                )
                self.assertEqual(engine.store.agents(project_id), before)
            finally:
                engine.close()

    def test_invalid_shared_settings_are_rejected_without_overwriting_saved_choice(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, execution_limit=3)
            try:
                definition = self.definition()
                project_id = engine.create_project(definition)
                for invalid in (
                    {**definition["team_model"], "api_key": "not-a-project-setting"},
                    {**definition["team_model"], "execution_mode": "api"},
                    {**definition["team_model"], "model": ""},
                ):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        engine.update_team(
                            project_id,
                            {
                                "agents": self.roster(engine, project_id),
                                "team_size": 3,
                                "max_concurrency": 2,
                                "team_model": invalid,
                            },
                        )
                self.assertEqual(
                    engine.store.project(project_id)["team_model"],
                    definition["team_model"],
                )
            finally:
                engine.close()
