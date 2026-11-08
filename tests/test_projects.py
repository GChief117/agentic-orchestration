"""Project boundaries and team scheduling, without any model or remote service."""

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from orchestration.engine import Engine
from orchestration.scheduling import CapacityLimits, select_work

ROOT = Path(__file__).resolve().parents[1]


def project_definition():
    return json.loads((ROOT / "examples/greenfield_project.json").read_text())


def write_task(content="hello", agent="builder", path="note.txt"):
    return {
        "id": "write",
        "agent": agent,
        "capability": "files.write",
        "parameters": {"path": path, "content": content},
    }


class CapturingExecutor:
    def __init__(self):
        self.operations = []
        self.lock = threading.Lock()

    def execute(self, task, cancelled):
        with self.lock:
            self.operations.append(task)
        return {"accepted": True, "effect": "none"}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, execution_limit=6)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def complete(self, count):
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.engine.snapshot()
            if len(state["tasks"]) == count and all(
                t["state"] == "accepted" for t in state["tasks"]
            ):
                return state
            time.sleep(0.01)
        self.fail("Tasks did not complete")

    def test_roster_and_charter_survive_restart_without_running_goals(self):
        project = self.engine.create_project(project_definition())
        before = self.engine.snapshot()
        self.assertEqual(before["projects"][0]["team_size"], 3)
        self.assertEqual(before["projects"][0]["max_concurrency"], 2)
        self.assertTrue(all(agent["state"] == "idle" for agent in before["agents"]))
        self.engine.close()
        self.engine = Engine(self.temp.name, execution_limit=8)
        after = self.engine.snapshot()
        self.assertEqual(after["projects"][0]["id"], project)
        self.assertEqual(
            after["projects"][0]["charter"], before["projects"][0]["charter"]
        )
        self.assertEqual(after["agents"], before["agents"])
        self.assertEqual(after["goals"], [])

    def test_team_size_is_independent_of_execution_limit(self):
        definition = project_definition()
        definition["max_concurrency"] = 1
        self.engine.create_project(definition)
        state = self.engine.snapshot()
        self.assertEqual(state["projects"][0]["team_size"], 3)
        self.assertEqual(state["projects"][0]["max_concurrency"], 1)
        self.assertEqual(state["capacity"]["execution_limit"], 6)

    def test_bad_project_is_rejected_atomically(self):
        invalid = []
        value = project_definition()
        value["team_size"] = 4
        invalid.append(value)
        value = project_definition()
        value["agents"][1]["key"] = "coordinator"
        invalid.append(value)
        value = project_definition()
        value["agents"][0]["reports_to"] = "builder"
        invalid.append(value)
        value = project_definition()
        value["agents"][0]["reports_to"] = "missing"
        invalid.append(value)
        value = project_definition()
        value["max_concurrency"] = 9
        invalid.append(value)
        value = project_definition()
        value["charter"]["success_criteria"] = []
        invalid.append(value)
        value = project_definition()
        value["mode"] = "unknown"
        invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.engine.create_project(value)
        self.assertEqual(self.engine.snapshot()["projects"], [])
        self.assertEqual(self.engine.snapshot()["agents"], [])

    def test_agent_capabilities_are_enforced(self):
        project = self.engine.create_project(project_definition())
        with self.assertRaises(PermissionError):
            self.engine.submit(project, "Unauthorized", [write_task(agent="reviewer")])
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_unassigned_agents_and_missing_projects_are_rejected(self):
        project = self.engine.create_project(project_definition())
        for owner in ("outsider", None):
            task = write_task()
            task["agent"] = owner
            with self.assertRaises(ValueError):
                self.engine.submit(project, "Unknown agent", [task])
        with self.assertRaises(ValueError):
            self.engine.submit("missing", "Unknown project", [write_task()])
        with self.assertRaises(ValueError):
            self.engine.submit(None, "Missing project", [write_task()])

    def test_projects_have_separate_files_with_same_relative_name(self):
        first = self.engine.create_project(project_definition())
        second = self.engine.create_project(project_definition())
        self.engine.submit(first, "First", [write_task("one")])
        self.engine.submit(second, "Second", [write_task("two")])
        state = self.complete(2)
        contents = {
            task["project_id"]: self.engine.connection.path(
                task["receipt"]["artifact"]
            ).read_text()
            for task in state["tasks"]
        }
        self.assertEqual(contents, {first: "one", second: "two"})
        self.assertNotEqual(
            state["tasks"][0]["agent_id"], state["tasks"][1]["agent_id"]
        )

    def test_project_path_cannot_escape_to_another_project(self):
        project = self.engine.create_project(project_definition())
        with self.assertRaises(PermissionError):
            self.engine.submit(
                project, "Escape", [write_task(path="../other/note.txt")]
            )

    def test_executor_receives_own_charter_and_assigned_role(self):
        executor = CapturingExecutor()
        self.engine.executor = executor
        project = self.engine.create_project(project_definition())
        self.engine.submit(project, "Context", [write_task()])
        state = self.complete(1)
        operation = executor.operations[0]
        self.assertEqual(operation["context"]["project"]["id"], project)
        self.assertEqual(operation["context"]["agent"]["key"], "builder")
        self.assertEqual(operation["context"]["agent"]["role"], "implementation")
        self.assertEqual(
            operation["context"]["project"]["charter"], project_definition()["charter"]
        )
        self.assertEqual(state["attempts"][0]["agent_id"], operation["agent_id"])
        self.assertEqual(state["attempts"][0]["project_id"], project)

    def test_cancel_one_project_goal_leaves_other_project_work_intact(self):
        first = self.engine.create_project(project_definition())
        second = self.engine.create_project(project_definition())
        cancelled = self.engine.submit(first, "Cancelled", [write_task("one")])
        self.engine.submit(second, "Continues", [write_task("two")])
        self.engine.cancel_goal(cancelled)
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.engine.snapshot()
            if {task["state"] for task in state["tasks"]} == {"accepted", "cancelled"}:
                break
            time.sleep(0.01)
        self.assertEqual(
            [task["state"] for task in state["tasks"]], ["cancelled", "accepted"]
        )

    def test_global_capacity_has_no_implicit_default(self):
        with self.assertRaises(TypeError):
            Engine(self.temp.name)
        with self.assertRaises(ValueError):
            Engine(self.temp.name, execution_limit=0)


class TeamSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            dict(
                id="a1",
                project_id="a",
                agent_id="builder-a",
                goal_id="first",
                target="local",
                resources=[],
                state="queued",
                dependencies=[],
            ),
            dict(
                id="a2",
                project_id="a",
                agent_id="builder-a",
                goal_id="second",
                target="local",
                resources=[],
                state="queued",
                dependencies=[],
            ),
            dict(
                id="a3",
                project_id="a",
                agent_id="reviewer-a",
                goal_id="first",
                target="local",
                resources=[],
                state="queued",
                dependencies=[],
            ),
            dict(
                id="b1",
                project_id="b",
                agent_id="builder-b",
                goal_id="third",
                target="local",
                resources=[],
                state="queued",
                dependencies=[],
            ),
        ]
        self.limits = CapacityLimits(
            8,
            {"a": 2, "b": 1},
            {"builder-a": 1, "reviewer-a": 1, "builder-b": 1},
            {"local": 8},
        )

    def test_agent_slots_and_project_capacities(self):
        selected = select_work(self.tasks, set(), self.limits).dispatch
        self.assertEqual([task["id"] for task in selected], ["a1", "b1", "a3"])
        self.assertNotIn("a2", [task["id"] for task in selected])

    def test_project_limit_is_independent_of_agent_limits(self):
        limits = CapacityLimits(
            8, {"a": 1, "b": 1}, self.limits.agents, self.limits.targets
        )
        selected = select_work(self.tasks, set(), limits).dispatch
        self.assertEqual([task["id"] for task in selected], ["a1", "b1"])

    def test_target_limit_applies_across_projects(self):
        limits = CapacityLimits(
            8, self.limits.projects, self.limits.agents, {"local": 1}
        )
        self.assertEqual(len(select_work(self.tasks, set(), limits).dispatch), 1)

    def test_global_limit_applies_across_projects(self):
        limits = CapacityLimits(
            2, self.limits.projects, self.limits.agents, self.limits.targets
        )
        self.assertEqual(
            [task["id"] for task in select_work(self.tasks, set(), limits).dispatch],
            ["a1", "b1"],
        )

    def test_existing_active_agent_cannot_be_dispatched_again(self):
        tasks = copy.deepcopy(self.tasks)
        tasks[0]["state"] = "running"
        selected = select_work(tasks, {"a1"}, self.limits).dispatch
        self.assertEqual([task["id"] for task in selected], ["b1", "a3"])


class LegacyMigrationTests(unittest.TestCase):
    def test_legacy_records_are_retained_without_inventing_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            db = sqlite3.connect(Path(directory) / "state.sqlite3")
            db.execute(
                """CREATE TABLE tasks(id TEXT PRIMARY KEY,goal_id TEXT,title TEXT,capability TEXT,target TEXT,
                          parameters TEXT,dependencies TEXT,resources TEXT,state TEXT,receipt TEXT,error TEXT,started REAL,finished REAL)"""
            )
            db.execute(
                "INSERT INTO tasks VALUES('old','goal','Legacy','files.write','local-workspace','{}','[]','[]','queued',NULL,NULL,NULL,NULL)"
            )
            db.commit()
            db.close()
            engine = Engine(directory, execution_limit=1)
            try:
                state = engine.snapshot()
                self.assertEqual(state["projects"], [])
                self.assertEqual(state["tasks"][0]["state"], "blocked")
                self.assertIn("Legacy", state["tasks"][0]["error"])
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
