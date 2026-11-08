"""Verify read-only discovery, explicit selections, and persistent project imports."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration.discovery import ProjectDiscovery
from orchestration.engine import Engine


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.projects = self.root / "projects"
        self.projects.mkdir()

    def project(self, name, marker):
        """Make a project fixture whose contents must remain untouched."""
        path = self.projects / name
        path.mkdir(parents=True)
        (path / marker).write_text("original content")
        return path

    def test_scan_finds_nested_projects_but_skips_dependencies_hidden_and_symlinks(
        self,
    ):
        first = self.project("web", "package.json")
        second = self.project("services/api", "pyproject.toml")
        nested = self.project("web/examples/demo", "package.json")
        self.project("node_modules/dependency", "package.json")
        self.project(".hidden/private", "Cargo.toml")
        (self.projects / "alias").symlink_to(first, target_is_directory=True)
        result = ProjectDiscovery().scan(str(self.projects))
        self.assertEqual(
            {item["path"] for item in result["projects"]}, {str(first), str(second)}
        )
        self.assertEqual((first / "package.json").read_text(), "original content")
        self.assertFalse(result["truncated"])
        direct = ProjectDiscovery().scan(str(first))
        self.assertIn(str(nested), {item["path"] for item in direct["projects"]})

    def test_selections_reject_unknown_expired_and_relocated_candidates(self):
        first = self.project("web", "package.json")
        discovery = ProjectDiscovery()
        scan = discovery.scan(str(self.projects))
        identifier = scan["projects"][0]["id"]
        for selected in ([], ["unknown"], [identifier, identifier]):
            with self.assertRaises(ValueError):
                discovery.selected(scan["scan_id"], selected)
        with patch("orchestration.discovery.time.monotonic", return_value=10**20):
            with self.assertRaisesRegex(ValueError, "expired"):
                discovery.selected(scan["scan_id"], [identifier])
        first.rename(self.projects / "moved")
        with self.assertRaisesRegex(ValueError, "moved or changed"):
            discovery.selected(scan["scan_id"], [identifier])

    def test_explicit_folder_option_supports_projects_without_markers(self):
        discovery = ProjectDiscovery()
        self.assertEqual(discovery.scan(str(self.projects))["projects"], [])
        result = discovery.scan(str(self.projects), include_root=True)
        self.assertEqual(result["projects"][0]["path"], str(self.projects))
        self.assertEqual(result["projects"][0]["kinds"], ["Folder"])

    def test_depth_limit_is_reported(self):
        self.project("a/b/c/d/e/f/g", "Cargo.toml")
        result = ProjectDiscovery().scan(str(self.projects))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["projects"], [])

    def test_import_is_selected_idempotent_persistent_and_original_is_untouched(self):
        first = self.project("web", "package.json")
        self.project("api", "pyproject.toml")
        runtime = self.root / "runtime"
        engine = Engine(runtime, execution_limit=4)
        try:
            scan = engine.scan_projects(str(self.projects))
            self.assertEqual(engine.snapshot()["projects"], [])
            candidate = next(
                item for item in scan["projects"] if item["path"] == str(first)
            )
            result = engine.import_projects(scan["scan_id"], [candidate["id"]])
            self.assertEqual(
                result, engine.import_projects(scan["scan_id"], [candidate["id"]])
            )
            state = engine.snapshot()
            self.assertEqual(len(state["projects"]), 1)
            self.assertEqual(len(state["agents"]), 3)
            project = state["projects"][0]
            self.assertEqual(project["mode"], "existing")
            self.assertEqual(project["source_path"], str(first))
            self.assertEqual(project["source_markers"], ["JavaScript"])
            self.assertEqual(state["tasks"], [])
            self.assertEqual(list(first.iterdir()), [first / "package.json"])
            self.assertEqual((first / "package.json").read_text(), "original content")
            # Imported teams use the same editable organization contract.
            agents = state["agents"]
            by_id = {item["id"]: item["key"] for item in agents}
            team = [
                {**item, "reports_to": by_id.get(item["reports_to"])} for item in agents
            ]
            engine.update_team(
                project["id"], {"agents": team, "team_size": 3, "max_concurrency": 2}
            )
            rescan = engine.scan_projects(str(self.projects))
            self.assertTrue(
                next(item for item in rescan["projects"] if item["path"] == str(first))[
                    "imported"
                ]
            )
        finally:
            engine.close()
        engine = Engine(runtime, execution_limit=4)
        try:
            self.assertEqual(
                engine.snapshot()["projects"][0]["source_path"], str(first)
            )
            self.assertEqual(engine.snapshot()["projects"][0]["max_concurrency"], 2)
        finally:
            engine.close()
