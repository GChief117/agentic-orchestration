"""Forecast dependency and capacity effects without live models or sleeping."""

import copy
import unittest

from orchestration.estimates import estimate_goals, estimate_plan


def task(
    identifier,
    agent="a",
    *,
    state="queued",
    goal="g",
    project="p",
    dependencies=(),
    resources=(),
    started=None,
    finished=None,
    capability="model.generate",
):
    """Build a minimal authoritative scheduling record for ETA tests."""
    return {
        "id": identifier,
        "agent_id": agent,
        "goal_id": goal,
        "project_id": project,
        "target": "local-workspace",
        "state": state,
        "capability": capability,
        "dependencies": list(dependencies),
        "resources": list(resources),
        "started": started,
        "finished": finished,
    }


def snapshot(tasks, *, capacity=2, history=True):
    """Provide shared model timing history and explicit capacity for two projects."""
    records = list(tasks)
    if history:
        records += [
            task("h1", state="accepted", goal="history", started=60, finished=70),
            task("h2", state="accepted", goal="history", started=70, finished=80),
        ]
    return {
        "projects": [
            {"id": "p", "max_concurrency": 2},
            {"id": "q", "max_concurrency": 2},
        ],
        "agents": [
            {
                "id": key,
                "key": key,
                "project_id": "p" if key != "c" else "q",
                "max_concurrency": 1,
                "execution_mode": "cli",
                "provider": "openai",
                "model": "test-model",
                "execution_speed": "standard",
            }
            for key in ("a", "b", "c")
        ],
        "goals": [
            {"id": key, "project_id": "p" if key != "other" else "q", "created": 80}
            for key in ("g", "other")
        ],
        "tasks": records,
        "capacity": {
            "execution_limit": capacity,
            "target_limits": {"local-workspace": capacity},
        },
    }


class EstimateTests(unittest.TestCase):
    def test_history_uses_recorded_model_not_an_agents_later_assignment(self):
        value = snapshot([task("pending")], history=False)
        for index in range(4):
            item = task(
                f"h{index}",
                state="accepted",
                goal="history",
                started=0,
                finished=10 if index else 80,
            )
            item["receipt"] = {
                "execution_mode": "cli",
                "provider": "openai",
                "model": "retired-model" if index == 0 else "test-model",
                "execution_speed": "standard",
            }
            value["tasks"].append(item)
        result = estimate_goals(value, now=100)["g"]
        self.assertEqual(result["remaining_seconds"], 10)
        self.assertEqual(result["sample_count"], 3)

    def test_parallel_and_serial_dependencies_use_different_eta(self):
        parallel = snapshot([task("one"), task("two", "b")])
        serial = snapshot([task("one"), task("two", "b", dependencies=["one"])])
        self.assertEqual(
            estimate_goals(parallel, now=100)["g"]["remaining_seconds"], 10
        )
        result = estimate_goals(serial, now=100)["g"]
        self.assertEqual(result["remaining_seconds"], 20)
        self.assertEqual(result["task_timings"]["two"]["starts_in_seconds"], 10)
        self.assertEqual(result["task_timings"]["two"]["eta_at"], 120)
        self.assertEqual(result["basis"], "history")

    def test_agent_resource_and_workspace_limits_affect_forecast(self):
        cases = [
            snapshot([task("one"), task("two")]),
            snapshot(
                [
                    task("one", resources=[("same", "write")]),
                    task("two", "b", resources=[("same", "read")]),
                ]
            ),
            snapshot([task("one"), task("two", "b")], capacity=1),
        ]
        for value in cases:
            with self.subTest(tasks=value["tasks"][:2]):
                self.assertEqual(
                    estimate_goals(value, now=100)["g"]["remaining_seconds"], 20
                )

    def test_competing_goals_share_capacity(self):
        value = snapshot(
            [task("one"), task("two", "c", goal="other", project="q")], capacity=1
        )
        estimates = estimate_goals(value, now=100)
        self.assertEqual(estimates["g"]["remaining_seconds"], 10)
        self.assertEqual(estimates["other"]["remaining_seconds"], 20)

    def test_running_work_subtracts_elapsed_time_and_preserves_input(self):
        value = snapshot(
            [
                task("one", state="running", started=96),
                task("two", "b", dependencies=["one"]),
            ]
        )
        before = copy.deepcopy(value)
        result = estimate_goals(value, now=100)["g"]
        self.assertEqual(result["remaining_seconds"], 16)
        self.assertEqual(result["elapsed_seconds"], 20)
        self.assertEqual(result["active_tasks"], 1)
        self.assertEqual(result["waiting_tasks"], 1)
        self.assertEqual(result["task_timings"]["one"]["elapsed_seconds"], 4)
        self.assertEqual(value, before)

    def test_cold_start_is_an_allowance_not_a_precise_eta(self):
        value = snapshot(
            [task("one"), task("two", "b", dependencies=["one"])], history=False
        )
        result = estimate_goals(value, now=100)["g"]
        self.assertEqual(result["basis"], "execution_limit")
        self.assertEqual(result["remaining_high_seconds"], 240)
        self.assertIsNone(result["remaining_seconds"])
        self.assertIsNone(result["eta_at"])
        self.assertEqual(result["confidence"], "low")

    def test_terminal_goals_have_actual_elapsed_time_and_no_future_eta(self):
        value = snapshot(
            [task("one", state="accepted", started=85, finished=95)], history=False
        )
        result = estimate_goals(value, now=150)["g"]
        self.assertEqual(result["basis"], "completed")
        self.assertEqual(result["elapsed_seconds"], 15)
        self.assertEqual(result["completed_tasks"], 1)
        self.assertIsNone(result["remaining_high_seconds"])
        self.assertEqual(result["task_timings"]["one"]["elapsed_seconds"], 10)
        self.assertIsNone(result["task_timings"]["one"]["eta_at"])

    def test_long_running_and_unbounded_operations_remain_uncertain(self):
        for value in [
            snapshot([task("one", state="running", started=0)]),
            snapshot([task("one", capability="files.write")], history=False),
        ]:
            result = estimate_goals(value, now=200)["g"]
            self.assertEqual(result["basis"], "unknown")
            self.assertIsNone(result["remaining_high_seconds"])

    def test_preview_preserves_plan_ids_and_includes_existing_work(self):
        value = snapshot([task("active", state="running", started=95)], capacity=1)
        plan = [
            {
                "id": "draft",
                "agent": "b",
                "capability": "model.generate",
                "parameters": {"path": "draft.md"},
                "depends_on": [],
            },
            {
                "id": "review",
                "agent": "a",
                "capability": "model.generate",
                "parameters": {"path": "review.md"},
                "depends_on": ["draft"],
            },
        ]
        before = copy.deepcopy(value)
        result = estimate_plan(plan, value["agents"], value, "p", now=100)
        self.assertEqual(result["remaining_seconds"], 25)
        self.assertEqual(set(result["task_timings"]), {"draft", "review"})
        self.assertEqual(value, before)

    def test_unavailable_capacity_and_large_graphs_do_not_claim_an_eta(self):
        value = snapshot([task("one")])
        value["capacity"]["execution_limit"] = 0
        self.assertIsNone(estimate_goals(value, now=100)["g"]["eta_at"])
        value = snapshot([task(str(index), "a") for index in range(257)])
        self.assertEqual(estimate_goals(value, now=100)["g"]["basis"], "unknown")
