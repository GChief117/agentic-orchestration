import copy
import json
from pathlib import Path
import unittest
from tools.validate_plan import validate

ROOT = Path(__file__).resolve().parents[1]


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "examples/multi_goal.json").read_text())

    def assert_error(self, text):
        self.assertTrue(
            any(text in error for error in validate(self.plan)), validate(self.plan)
        )

    def test_example_passes(self):
        self.assertEqual(validate(self.plan), [])

    def test_cycle(self):
        self.plan["tasks"][0]["depends_on"] = ["synthesize"]
        self.assert_error("cycle")

    def test_unknown_dependency(self):
        self.plan["tasks"][0]["depends_on"] = ["missing"]
        self.assert_error("unknown dependency")

    def test_same_batch_dependency(self):
        self.plan["tasks"][1]["depends_on"] = ["research_sources"]
        self.assert_error("earlier batch")

    def test_resource_conflict(self):
        self.plan["tasks"][1]["resources"] = [{"name": "source-notes", "mode": "read"}]
        self.assert_error("conflicting access")

    def test_concurrent_reads_allowed(self):
        for task in self.plan["tasks"][:2]:
            task["resources"] = [{"name": "shared", "mode": "read"}]
        self.assertEqual(validate(self.plan), [])

    def test_capacity(self):
        self.plan["tasks"][1]["owner"] = "researcher"
        self.assert_error("exceeds capacity")

    def test_increased_capacity(self):
        self.plan["tasks"][1]["owner"] = "researcher"
        self.plan["agents"][0]["capacity"] = 2
        self.assertEqual(validate(self.plan), [])

    def test_missing_task(self):
        self.plan["batches"].pop()
        self.assert_error("missing from batches")

    def test_duplicate_id(self):
        self.plan["tasks"].append(copy.deepcopy(self.plan["tasks"][0]))
        self.assert_error("duplicate tasks id")

    def test_duplicate_batch_position(self):
        self.plan["batches"][0].append("research_sources")
        self.assert_error("multiple batch positions")

    def test_missing_acceptance(self):
        self.plan["tasks"][0]["acceptance"] = []
        self.assert_error("acceptance")

    def test_empty_goal_acceptance(self):
        self.plan["goals"][0]["acceptance"] = [" "]
        self.assert_error("goal acceptance")

    def test_unknown_owner(self):
        self.plan["tasks"][0]["owner"] = "ghost"
        self.assert_error("unknown owner")

    def test_unknown_goal(self):
        self.plan["tasks"][0]["goal"] = "ghost"
        self.assert_error("unknown goal")

    def test_invalid_resource(self):
        self.plan["tasks"][0]["resources"] = [{"name": "code", "mode": "execute"}]
        self.assert_error("invalid resource")

    def test_duplicate_resource(self):
        self.plan["tasks"][0]["resources"] *= 2
        self.assert_error("duplicate resource")

    def test_boolean_capacity_rejected(self):
        self.plan["agents"][0]["capacity"] = True
        self.assert_error("positive integer")

    def test_malformed_shapes_report_errors(self):
        for value in [
            None,
            [],
            {},
            {
                "version": "0.1",
                "goals": [None],
                "agents": [{"id": "a", "capacity": "one"}],
                "tasks": [
                    {
                        "id": "t",
                        "goal": [],
                        "owner": {},
                        "depends_on": [None],
                        "resources": [None],
                    }
                ],
                "batches": [[{}]],
            },
        ]:
            with self.subTest(value=value):
                self.assertTrue(validate(value))

    def test_long_cycle_does_not_overflow_recursion(self):
        self.plan["tasks"] = [
            dict(
                id=f"t{i}",
                goal="research",
                owner="researcher",
                acceptance=["checked"],
                resources=[],
                depends_on=[f"t{(i+1)%1500}"],
            )
            for i in range(1500)
        ]
        self.plan["batches"] = [[f"t{i}"] for i in range(1500)]
        self.assert_error("cycle")


if __name__ == "__main__":
    unittest.main()
