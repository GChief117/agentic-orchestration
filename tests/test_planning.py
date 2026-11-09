"""Chart-aware planning is reviewable and cannot dispatch unvalidated work."""

import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from orchestration.engine import Engine
from orchestration.planning import organization_context, propose_goal, team_revision


class FakeModel:
    def __init__(self, result):
        """Keep planner calls private to this test and return one configured response."""
        self.result = result
        self.calls = []
        self.available = True
        self.on_call = None

    def ready(self, _provider):
        """Represent a connected model without consulting a real account."""
        return self.available

    def validate_speed(self, _provider, _model, _speed):
        """Allow fixture speed values without launching a native tool."""

    def refresh(self, _provider):
        """Leave the fixture's explicit readiness unchanged."""

    def generate(self, *args, **kwargs):
        """Record supplied context and return model-shaped output without side effects."""
        self.calls.append((args, kwargs))
        if self.on_call:
            self.on_call()
        return self.result


class PlanningTests(unittest.TestCase):
    def setUp(self):
        """Create a real chart backed by temporary SQLite and a fake model provider."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / "runtime", execution_limit=3)
        self.addCleanup(self.engine.close)
        definition = json.loads(
            (
                Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
            ).read_text()
        )
        for agent in definition["agents"]:
            agent.update(
                execution_mode="api",
                provider="openai",
                model="test-model",
                capabilities=["model.generate"],
            )
        definition["agents"][1]["collaborators"] = ["reviewer"]
        self.project_id = self.engine.create_project(definition)
        self.plan = {
            "summary": "The builder drafts the report and the reviewer checks it.",
            "tasks": [
                {
                    "id": "draft",
                    "title": "Draft report",
                    "agent": "builder",
                    "parameters": {"prompt": "Draft the requested report."},
                    "depends_on": [],
                },
                {
                    "id": "review",
                    "title": "Review report",
                    "agent": "reviewer",
                    "parameters": {"prompt": "Review the draft against the goal."},
                    "depends_on": ["draft"],
                },
            ],
        }
        self.model = FakeModel(self.response())
        self.engine.models = self.model

    def response(self, *, text=None, complete=True):
        """Return a minimal provider response for the current fixture plan."""
        return {
            "text": json.dumps(self.plan) if text is None else text,
            "complete": complete,
        }

    def test_chart_context_controls_owners_and_plan_does_not_start_tasks(self):
        result = propose_goal(self.engine, self.project_id, "Prepare a report")
        args, kwargs = self.model.calls[0]
        self.assertEqual(args[4], 4096)
        self.assertEqual(kwargs, {})
        context = json.loads(args[3])
        self.assertEqual(context["selected_coordinator"], "coordinator")
        self.assertEqual(
            set(context["available_agent_keys"]), {"coordinator", "builder", "reviewer"}
        )
        chart = context["organization"]
        self.assertIn(
            {"manager": "coordinator", "report": "builder"}, chart["reporting_edges"]
        )
        self.assertIn({"first": "builder", "second": "reviewer"}, chart["peer_edges"])
        builder = next(agent for agent in chart["agents"] if agent["key"] == "builder")
        self.assertIn("Produce artifacts", builder["instructions"])
        self.assertEqual(builder["responsibilities"], ["Write requested artifacts"])
        self.assertRegex(
            result["tasks"][0]["parameters"]["path"],
            r"^results/[a-f0-9]{12}/task-1\.md$",
        )
        self.assertEqual(result["tasks"][1]["depends_on"], ["draft"])
        self.assertEqual(result["coordinator_key"], "coordinator")
        state = self.engine.snapshot()
        self.assertEqual(state["goals"], [])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(
            result["team_revision"],
            team_revision(
                self.engine.store.project(self.project_id),
                self.engine.store.agents(self.project_id),
            ),
        )

    def test_repeated_plans_use_distinct_result_namespaces(self):
        first = propose_goal(self.engine, self.project_id, "Plan the report")
        second = propose_goal(self.engine, self.project_id, "Plan another report")
        self.assertNotEqual(
            first["tasks"][0]["parameters"]["path"],
            second["tasks"][0]["parameters"]["path"],
        )
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_cli_planning_uses_native_output_and_saved_agent_speed(self):
        with self.engine.store.db:
            self.engine.store.db.execute(
                "UPDATE agents SET execution_mode='cli', execution_speed='fast' WHERE key='coordinator'"
            )
        cli = FakeModel(self.response())
        self.engine.cli = cli
        result = propose_goal(self.engine, self.project_id, "Plan the release")
        self.assertEqual(result["coordinator_key"], "coordinator")
        self.assertEqual(self.model.calls, [])
        args, kwargs = cli.calls[0]
        self.assertIsNone(args[4])
        self.assertEqual(kwargs, {"speed": "fast", "cancelled": None})
        with self.assertRaisesRegex(PermissionError, "hosted"):
            propose_goal(self.engine, self.project_id, "Plan", allow_cli=False)

    def test_selected_sources_only_reach_the_planner_and_proposed_tasks(self):
        root = Path(self.temp.name).resolve() / "source"
        root.mkdir()
        (root / "package.json").write_text(
            '{"name":"sample","dependencies":{"example":"1"}}'
        )
        (root / "private.txt").write_text("unselected content")
        (root / ".env").write_text("PRIVATE_TOKEN=never-send")
        with self.engine.store.db:
            self.engine.store.db.execute(
                "INSERT INTO project_sources VALUES(?,?,?)",
                (self.project_id, str(root), "[]"),
            )
        result = propose_goal(
            self.engine,
            self.project_id,
            "Summarize the stack",
            source_files=["package.json"],
        )
        prompt = self.model.calls[0][0][3]
        self.assertIn("example", prompt)
        self.assertNotIn("unselected content", prompt)
        self.assertNotIn("PRIVATE_TOKEN", prompt)
        self.assertTrue(
            all(
                task["parameters"]["source_files"] == ["package.json"]
                for task in result["tasks"]
            )
        )
        with self.assertRaises(ValueError):
            propose_goal(self.engine, self.project_id, "Plan", source_files=[".env"])
        self.assertEqual(len(self.model.calls), 1)

    def test_chart_change_invalidates_a_generated_plan(self):
        def alter_chart():
            with self.engine.lock, self.engine.store.db:
                self.engine.store.db.execute(
                    "UPDATE agents SET instructions='New instructions' WHERE key='builder'"
                )

        self.model.on_call = alter_chart
        with self.assertRaisesRegex(ValueError, "chart changed"):
            propose_goal(self.engine, self.project_id, "Plan")
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_model_request_does_not_hold_the_engine_coordination_lock(self):
        acquired = threading.Event()

        def check_lock():
            def acquire_from_another_thread():
                with self.engine.lock:
                    acquired.set()

            thread = threading.Thread(target=acquire_from_another_thread)
            thread.start()
            self.assertTrue(
                acquired.wait(1), "Planning must not block engine state access"
            )
            thread.join(1)

        self.model.on_call = check_lock
        propose_goal(self.engine, self.project_id, "Plan")
        self.assertTrue(acquired.is_set())

    def test_revision_tracks_chart_identity_and_execution_but_ignores_row_order(self):
        project = self.engine.store.project(self.project_id)
        agents = self.engine.store.agents(self.project_id)
        revision = team_revision(project, agents)
        self.assertEqual(revision, team_revision(project, list(reversed(agents))))
        changed = copy.deepcopy(agents)
        changed[0]["execution_speed"] = "fast"
        self.assertNotEqual(revision, team_revision(project, changed))
        changed = copy.deepcopy(agents)
        changed[0]["id"] = "new-identity"
        self.assertNotEqual(revision, team_revision(project, changed))
        context = organization_context(project, agents)
        self.assertNotIn("source_path", context["project"])

    def test_malformed_unsafe_or_cyclic_plans_never_create_work(self):
        cases = []
        for mutation, error in (
            (lambda plan: plan["tasks"][0].update(agent="not-in-chart"), ValueError),
            (
                lambda plan: plan["tasks"][0].update(capability="files.write"),
                PermissionError,
            ),
            (
                lambda plan: plan["tasks"][0]["parameters"].update(
                    path="../../outside.md"
                ),
                PermissionError,
            ),
            (lambda plan: plan["tasks"][0].update(depends_on=["review"]), ValueError),
            (
                lambda plan: [
                    task["parameters"].update(path="same.md") for task in plan["tasks"]
                ],
                ValueError,
            ),
            (
                lambda plan: plan["tasks"][0]["parameters"].update(command="rm -rf /"),
                ValueError,
            ),
        ):
            plan = copy.deepcopy(self.plan)
            mutation(plan)
            cases.append((json.dumps(plan), error))
        cases.extend(
            [
                ("not json", ValueError),
                ('{"summary":"first","summary":"second","tasks":[]}', ValueError),
                (json.dumps({"summary": "Empty", "tasks": []}), ValueError),
                ("x" * 65537, ValueError),
            ]
        )
        for output, error in cases:
            with self.subTest(output=output[:70]):
                self.model.result = self.response(text=output)
                with self.assertRaises(error):
                    propose_goal(self.engine, self.project_id, "Plan")
                self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_unavailable_or_incomplete_planner_fails_without_scheduling(self):
        self.model.available = False
        with self.assertRaisesRegex(ValueError, "Connect"):
            propose_goal(self.engine, self.project_id, "Plan")
        self.assertEqual(self.model.calls, [])
        self.model.available = True
        self.model.result = self.response(complete=False)
        with self.assertRaisesRegex(ValueError, "did not complete"):
            propose_goal(self.engine, self.project_id, "Plan")
        with self.assertRaisesRegex(ValueError, "coordinator"):
            propose_goal(
                self.engine, self.project_id, "Plan", coordinator_key="invented"
            )
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_source_scope_is_checked_before_generation(self):
        with self.assertRaisesRegex(PermissionError, "imported"):
            propose_goal(
                self.engine, self.project_id, "Plan", source_files=["README.md"]
            )
        self.assertEqual(self.model.calls, [])
        self.engine.allow_source_access = False
        with self.assertRaisesRegex(PermissionError, "hosted"):
            propose_goal(
                self.engine, self.project_id, "Plan", source_files=["README.md"]
            )
        self.assertEqual(self.model.calls, [])
