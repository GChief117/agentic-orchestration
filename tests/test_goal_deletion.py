"""Goal deletion removes idle records without racing workers or removing outputs."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_handler
from orchestration.engine import Engine


class GoalFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, execution_limit=1)
        definition = json.loads(
            (Path(__file__).parents[1] / "examples/greenfield_project.json").read_text()
        )
        self.project_id = self.engine.create_project(definition)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def goal(self, title="Remove this goal"):
        return self.engine.submit(
            self.project_id,
            title,
            [
                {
                    "id": "check",
                    "agent": "coordinator",
                    "capability": "terminal.python_version",
                }
            ],
        )


class GoalDeletionTests(GoalFixture):
    def test_queued_deletion_prevents_dispatch_and_preserves_other_goals(self):
        deleted = self.goal()
        retained = self.goal("Keep this goal")
        self.engine.delete_goal(deleted)
        snapshot = self.engine.snapshot()
        self.assertEqual([goal["id"] for goal in snapshot["goals"]], [retained])
        self.assertEqual({task["goal_id"] for task in snapshot["tasks"]}, {retained})
        self.assertEqual(snapshot["attempts"], [])
        self.assertEqual(len(snapshot["projects"]), 1)
        self.assertEqual(len(snapshot["agents"]), 3)

    def test_completed_deletion_cleans_related_history_and_preserves_files(self):
        goal_id = self.goal()
        task = self.engine.store.tasks()[0]
        output = self.engine.connection.project_path(self.project_id, "delivered.txt")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("Keep this deliverable")
        with self.engine.lock, self.engine.store.db:
            self.engine.store.start(task)
            self.engine.store.finish(
                task["id"], "accepted", {"artifact": "delivered.txt"}
            )
        self.engine.delete_goal(goal_id)
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["goals"], [])
        self.assertEqual(snapshot["tasks"], [])
        self.assertEqual(snapshot["attempts"], [])
        self.assertFalse(
            any(event["task_id"] == task["id"] for event in snapshot["events"])
        )
        self.assertEqual(output.read_text(), "Keep this deliverable")
        self.engine.close()
        self.engine = Engine(self.temp.name, execution_limit=1)
        self.assertEqual(self.engine.snapshot()["goals"], [])
        self.assertEqual(output.read_text(), "Keep this deliverable")

    def test_running_and_cancelling_goals_cannot_be_deleted(self):
        goal_id = self.goal()
        task = self.engine.store.tasks()[0]
        with self.engine.lock, self.engine.store.db:
            self.engine.store.start(task)
        for state in ("running", "cancelling"):
            with self.subTest(state=state):
                with self.engine.lock, self.engine.store.db:
                    self.engine.store.db.execute(
                        "UPDATE tasks SET state=? WHERE id=?", (state, task["id"])
                    )
                with self.assertRaisesRegex(ValueError, "wait for it to stop"):
                    self.engine.delete_goal(goal_id)
                self.assertEqual(len(self.engine.snapshot()["goals"]), 1)

    def test_finalizing_worker_and_renderer_keep_ownership(self):
        goal_id = self.goal()
        task_id = self.engine.store.tasks()[0]["id"]
        self.engine.threads[task_id] = threading.current_thread()
        try:
            with self.assertRaisesRegex(ValueError, "wait for it to stop"):
                self.engine.delete_goal(goal_id)
        finally:
            self.engine.threads.clear()
        self.engine.delivering[goal_id] = threading.current_thread()
        try:
            with self.assertRaisesRegex(ValueError, "deliverable to finish"):
                self.engine.delete_goal(goal_id)
        finally:
            self.engine.delivering.clear()
        self.engine.delete_goal(goal_id)

    def test_failed_cancelled_and_blocked_goals_can_be_deleted(self):
        for state in ("failed", "cancelled", "blocked"):
            with self.subTest(state=state):
                goal_id = self.goal()
                with self.engine.lock, self.engine.store.db:
                    self.engine.store.db.execute(
                        "UPDATE tasks SET state=? WHERE goal_id=?", (state, goal_id)
                    )
                self.engine.delete_goal(goal_id)
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_unknown_goal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown goal"):
            self.engine.delete_goal("missing")


class GoalDeletionHTTPTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.engine, "test-session")
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def delete(self, goal_id, token="test-session", origin=None):
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        if origin:
            headers["Origin"] = origin
        return urlopen(
            Request(
                f"http://127.0.0.1:{self.server.server_port}/api/goals/{goal_id}/delete",
                data=b"{}",
                headers=headers,
            )
        )

    def test_delete_endpoint_requires_authentication_and_same_origin(self):
        goal_id = self.goal()
        for options in ({"token": "wrong"}, {"origin": "https://untrusted.example"}):
            with self.subTest(options=options), self.assertRaises(HTTPError) as error:
                self.delete(goal_id, **options)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        self.assertEqual(len(self.engine.snapshot()["goals"]), 1)
        with self.delete(goal_id) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"ok": True})

    def test_delete_endpoint_reports_unknown_or_running_goal(self):
        goal_id = self.goal()
        with self.engine.lock, self.engine.store.db:
            self.engine.store.start(self.engine.store.tasks()[0])
        for identifier in (goal_id, "missing"):
            with (
                self.subTest(identifier=identifier),
                self.assertRaises(HTTPError) as error,
            ):
                self.delete(identifier)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
