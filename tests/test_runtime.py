import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_handler
from orchestration.engine import Engine
from orchestration.execution import ExecutionCancelled
from orchestration.scheduling import conflicts, goal_state, select_work

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/backend_goal.json"


def example():
    return json.loads(EXAMPLE.read_text())


class GateExecutor:
    """Controllable worker to verify dispatch without relying on operation timing."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.lock = threading.Lock()

    def execute(self, task, cancelled):
        with self.lock:
            self.calls.append(task["id"])
        self.started.set()
        while not self.release.wait(0.01):
            if cancelled.is_set():
                raise ExecutionCancelled("Cancelled at worker boundary")
        return {"accepted": True, "effect": "none", "operation_id": task["attempt_id"]}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def submit_example(self, title="Release"):
        plan = example()
        return self.engine.submit(title, plan["tasks"])

    def wait_finished(self, count):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            state = self.engine.snapshot()
            if len(state["tasks"]) == count and all(
                task["state"] in ("accepted", "failed", "cancelled", "blocked")
                for task in state["tasks"]
            ):
                return state
            time.sleep(0.01)
        self.fail("Task graph did not complete")

    def test_two_goals_execute_with_dependency_order_and_receipts(self):
        self.submit_example("First")
        self.submit_example("Second")
        self.engine.start()
        state = self.wait_finished(6)
        self.assertTrue(all(task["state"] == "accepted" for task in state["tasks"]))
        self.assertEqual(len(state["attempts"]), 6)
        for task in state["tasks"]:
            for dep in task["dependencies"]:
                parent = next(item for item in state["tasks"] if item["id"] == dep)
                self.assertLessEqual(parent["finished"], task["started"])
            if task["receipt"].get("artifact"):
                self.assertTrue(
                    self.engine.connection.path(task["receipt"]["artifact"]).exists()
                )
        self.assertTrue(
            all(attempt["state"] == "accepted" for attempt in state["attempts"])
        )

    def test_independent_goals_use_two_worker_slots(self):
        gate = GateExecutor()
        self.engine.executor = gate
        self.submit_example("First")
        self.submit_example("Second")
        self.engine.tick()
        self.assertTrue(gate.started.wait(2))
        snapshot = self.engine.snapshot()
        running = [task for task in snapshot["tasks"] if task["state"] == "running"]
        self.assertEqual(len(running), 2)
        self.assertEqual(len({task["goal_id"] for task in running}), 2)
        gate.release.set()
        self.engine.start()
        self.wait_finished(6)

    def test_cancel_queued_goal_prevents_dispatch(self):
        goal = self.submit_example()
        self.engine.cancel_goal(goal)
        self.engine.start()
        state = self.wait_finished(3)
        self.assertTrue(all(task["state"] == "cancelled" for task in state["tasks"]))
        self.assertEqual(state["attempts"], [])

    def test_cancel_running_goal_reaches_worker(self):
        gate = GateExecutor()
        self.engine.executor = gate
        goal = self.submit_example()
        self.engine.start()
        self.assertTrue(gate.started.wait(2))
        self.engine.cancel_goal(goal)
        state = self.wait_finished(3)
        self.assertTrue(all(task["state"] == "cancelled" for task in state["tasks"]))

    def test_failed_verification_blocks_dependent(self):
        plan = example()
        plan["tasks"][2]["parameters"]["expected"] = "wrong"
        plan["tasks"].append(
            {
                "id": "dependent",
                "capability": "terminal.python_version",
                "depends_on": ["verify"],
            }
        )
        self.engine.submit("Mismatch", plan["tasks"])
        self.engine.start()
        state = self.wait_finished(4)
        self.assertEqual(
            [task["state"] for task in state["tasks"]],
            ["accepted", "accepted", "failed", "blocked"],
        )

    def test_restart_preserves_records_without_replaying_unknown_effects(self):
        self.submit_example()
        with self.engine.lock:
            task = self.engine.store.tasks()[0]
            self.engine.store.start(task)
            self.engine.store.commit()
        self.engine.close()
        self.engine = Engine(self.temp.name)
        self.engine.start()
        state = self.wait_finished(3)
        self.assertEqual(state["tasks"][0]["state"], "failed")
        self.assertIn("Interrupted", state["tasks"][0]["error"])
        self.assertEqual(state["attempts"][0]["state"], "failed")
        self.assertEqual(state["tasks"][2]["state"], "blocked")

    def test_second_engine_cannot_steal_active_store(self):
        with self.assertRaisesRegex(RuntimeError, "Another engine"):
            Engine(self.temp.name)

    def test_write_aliases_share_exclusive_resource(self):
        gate = GateExecutor()
        self.engine.executor = gate
        self.engine.submit(
            "Writes",
            [
                {
                    "id": "one",
                    "capability": "files.write",
                    "parameters": {"path": "dir/../shared.txt", "content": "one"},
                },
                {
                    "id": "two",
                    "capability": "files.write",
                    "parameters": {"path": "shared.txt", "content": "two"},
                },
            ],
        )
        self.engine.start()
        self.assertTrue(gate.started.wait(2))
        self.engine.tick()
        self.assertEqual(len(gate.calls), 1)
        gate.release.set()
        self.assertEqual(len(self.wait_finished(2)["attempts"]), 2)

    def test_no_target_or_capability_fallback(self):
        with self.assertRaises(PermissionError):
            self.engine.connection.authorize("remote", "files.write")
        with self.assertRaises(PermissionError):
            self.engine.submit("Unsupported", [{"id": "x", "capability": "unknown"}])
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_requested_remote_target_is_not_silently_replaced(self):
        with self.assertRaises(PermissionError):
            self.engine.submit(
                "Remote",
                [
                    {
                        "id": "remote-task",
                        "target": "remote-host",
                        "capability": "terminal.python_version",
                    }
                ],
            )
        self.assertEqual(self.engine.snapshot()["tasks"], [])

    def test_invalid_graph_has_no_partial_database_writes(self):
        plans = [
            [],
            [
                {
                    "id": "a",
                    "capability": "terminal.python_version",
                    "depends_on": ["missing"],
                }
            ],
            [{"id": "a", "capability": "terminal.python_version", "depends_on": ["a"]}],
            [
                {
                    "id": "a",
                    "capability": "files.write",
                    "parameters": {"path": "../escape", "content": "x"},
                }
            ],
        ]
        for tasks in plans:
            with self.subTest(tasks=tasks), self.assertRaises(
                (ValueError, PermissionError)
            ):
                self.engine.submit("Invalid", tasks)
        self.assertEqual(self.engine.snapshot()["goals"], [])

    def test_symlink_escape_is_rejected(self):
        (self.engine.connection.workspace / "outside").symlink_to(
            Path(self.temp.name).parent, target_is_directory=True
        )
        with self.assertRaises(PermissionError):
            self.engine.connection.path("outside/file")


class SchedulingTests(unittest.TestCase):
    @staticmethod
    def task(key, goal="one", resources=None, state="queued", dependencies=None):
        return {
            "id": key,
            "goal_id": goal,
            "resources": resources or [],
            "state": state,
            "dependencies": dependencies or [],
        }

    def test_capacity_and_goal_fairness(self):
        tasks = [self.task("a"), self.task("b"), self.task("c", "two")]
        self.assertEqual(
            [task["id"] for task in select_work(tasks, set(), 2).dispatch], ["a", "c"]
        )
        self.assertEqual(len(select_work(tasks, set(), 1).dispatch), 1)

    def test_shared_reads_and_exclusive_writes(self):
        self.assertFalse(conflicts([("shared", "read")], [("shared", "read")]))
        self.assertTrue(conflicts([("shared", "write")], [("shared", "read")]))
        tasks = [
            self.task("a", resources=[("shared", "write")]),
            self.task("b", resources=[("shared", "read")]),
        ]
        self.assertEqual(
            [task["id"] for task in select_work(tasks, set(), 2).dispatch], ["a"]
        )

    def test_dependencies_wait_for_acceptance(self):
        tasks = [self.task("a", state="running"), self.task("b", dependencies=["a"])]
        self.assertEqual(select_work(tasks, {"a"}, 2).dispatch, [])
        tasks[0]["state"] = "accepted"
        self.assertEqual(
            [task["id"] for task in select_work(tasks, set(), 2).dispatch], ["b"]
        )

    def test_cancelling_goal_is_visible(self):
        self.assertEqual(goal_state(["accepted", "cancelling"]), "cancelling")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.engine, "test-session")
        )
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.engine.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        combined = {
            "Authorization": "Bearer test-session",
            "Content-Type": "application/json",
        }
        if headers:
            combined.update(headers)
        return urlopen(
            Request(
                self.url + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers=combined,
            )
        )

    def test_authenticated_task_submission(self):
        with self.request("/api/goals", example()) as result:
            self.assertEqual(result.status, 201)
        with self.request("/api/state") as result:
            self.assertEqual(len(json.load(result)["tasks"]), 3)

    def test_no_dashboard_is_served(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_auth_and_origin_boundaries(self):
        for headers in (
            {"Authorization": ""},
            {"Origin": "https://example.com"},
            {"Host": "evil.example"},
        ):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.request("/api/state", headers=headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_invalid_request_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/goals", [])
        self.assertEqual(error.exception.code, 400)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
