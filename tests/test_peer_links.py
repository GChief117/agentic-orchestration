"""Peer collaboration stays independent of reporting and survives team edits."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from orchestration.engine import Engine

ROOT = Path(__file__).resolve().parents[1]


class PeerLinkTests(unittest.TestCase):
    def test_peer_triangle_persists_and_edge_removal_keeps_reporting_unchanged(self):
        """Save sibling peer links, restart, remove one edge, and retain managers."""
        project = json.loads((ROOT / "examples/greenfield_project.json").read_text())
        researcher = copy.deepcopy(project["agents"][1])
        researcher.update(
            key="researcher",
            name="Researcher",
            role="Research",
            reports_to="coordinator",
            collaborators=[],
        )
        project["agents"].append(researcher)
        project["team_size"] = len(project["agents"])
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory, execution_limit=4)
            try:
                project_id = engine.create_project(project)
                original = {
                    agent["key"]: agent for agent in engine.snapshot()["agents"]
                }
                managers = {
                    key: agent["reports_to"] for key, agent in original.items()
                }
                roster = copy.deepcopy(project["agents"])
                directed_edges = {
                    "coordinator": [],
                    "builder": ["reviewer"],
                    "reviewer": ["researcher"],
                    "researcher": ["builder"],
                }
                for agent in roster:
                    agent["collaborators"] = directed_edges[agent["key"]]
                engine.update_team(
                    project_id,
                    {
                        "agents": roster,
                        "team_size": len(roster),
                        "max_concurrency": project["max_concurrency"],
                    },
                )
                peers = self._agents(engine)
                self._assert_triangle(peers)
                self.assertEqual(
                    {key: agent["reports_to"] for key, agent in peers.items()},
                    managers,
                )
                self.assertEqual(engine.snapshot()["tasks"], [])
            finally:
                engine.close()

            engine = Engine(directory, execution_limit=4)
            try:
                peers = self._agents(engine)
                self._assert_triangle(peers)
                self.assertEqual(
                    {key: agent["reports_to"] for key, agent in peers.items()},
                    managers,
                )
                keys_by_id = {agent["id"]: key for key, agent in peers.items()}
                roster = []
                for key, agent in peers.items():
                    updated = copy.deepcopy(agent)
                    updated["reports_to"] = keys_by_id.get(agent["reports_to"])
                    if key == "builder":
                        updated["collaborators"].remove("reviewer")
                    elif key == "reviewer":
                        updated["collaborators"].remove("builder")
                    roster.append(updated)
                engine.update_team(
                    project_id,
                    {
                        "agents": roster,
                        "team_size": len(roster),
                        "max_concurrency": project["max_concurrency"],
                    },
                )
                remaining = self._agents(engine)
                self.assertEqual(remaining["coordinator"]["collaborators"], [])
                self.assertEqual(remaining["builder"]["collaborators"], ["researcher"])
                self.assertEqual(remaining["reviewer"]["collaborators"], ["researcher"])
                self.assertEqual(
                    set(remaining["researcher"]["collaborators"]),
                    {"builder", "reviewer"},
                )
                self.assertEqual(
                    {key: agent["reports_to"] for key, agent in remaining.items()},
                    managers,
                )
            finally:
                engine.close()

            engine = Engine(directory, execution_limit=4)
            try:
                remaining = self._agents(engine)
                self.assertNotIn("reviewer", remaining["builder"]["collaborators"])
                self.assertNotIn("builder", remaining["reviewer"]["collaborators"])
                self.assertEqual(
                    {key: agent["reports_to"] for key, agent in remaining.items()},
                    managers,
                )
            finally:
                engine.close()

    @staticmethod
    def _agents(engine):
        """Index the persisted roster by the stable keys used for peer links."""
        return {agent["key"]: agent for agent in engine.snapshot()["agents"]}

    def _assert_triangle(self, agents):
        """Verify three siblings are mutually linked without involving the manager."""
        peers = {"builder", "reviewer", "researcher"}
        self.assertEqual(agents["coordinator"]["collaborators"], [])
        for key in peers:
            self.assertEqual(set(agents[key]["collaborators"]), peers - {key})
