"""Verify presentation outputs remain separate from durable task evidence."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_handler
from orchestration.delivery import parse_deliverable
from orchestration.engine import Engine


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        """Use real file receipts and an isolated renderer without provider accounts."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.engine = Engine(
            self.temp.name, execution_limit=2, deliverable_renderer=self.render
        )
        self.addCleanup(lambda: self.engine.close())
        document = json.loads(
            (
                Path(__file__).resolve().parents[1] / "examples/greenfield_project.json"
            ).read_text()
        )
        self.project = self.engine.create_project(document)

    def render(self, text, title, format, style):
        """Record inputs and return a distinct binary result for each presentation."""
        self.calls.append((text, title, format, style))
        return (b"%PDF-1.4\n" if format == "pdf" else b"<html>") + text.encode()

    def submit(self, deliverable=None):
        """Write a draft, replace it with the final result, and verify that result."""
        return self.engine.submit(
            self.project,
            "Quarterly report",
            [
                {
                    "id": "draft",
                    "agent": "builder",
                    "capability": "files.write",
                    "parameters": {"path": "draft.md", "content": "Draft"},
                },
                {
                    "id": "final",
                    "agent": "builder",
                    "capability": "files.write",
                    "parameters": {
                        "path": "final.md",
                        "content": "# Final\n\nEvidence.",
                    },
                    "depends_on": ["draft"],
                },
                {
                    "id": "check",
                    "agent": "reviewer",
                    "capability": "files.inspect",
                    "parameters": {
                        "path": "final.md",
                        "expected": "# Final\n\nEvidence.",
                    },
                    "depends_on": ["final"],
                },
            ],
            deliverable=deliverable,
        )

    def finish(self, goal, expect_delivery=False):
        """Wait for completed tasks and, when requested, the separate renderer."""
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = next(
                item for item in self.engine.snapshot()["goals"] if item["id"] == goal
            )
            if current["state"] == "accepted" and (
                not expect_delivery
                or (current.get("delivery") or {}).get("state") in ("ready", "failed")
            ):
                return current
            time.sleep(0.01)
        self.fail("Goal did not finish")

    def test_selected_pdf_is_automatic_and_uses_final_evidence_only(self):
        with patch(
            "orchestration.delivery.importlib.util.find_spec", return_value=object()
        ):
            goal = self.submit({"format": "pdf", "style": "ocean"})
            result = self.finish(goal, True)
        self.assertEqual(result["delivery"]["state"], "ready")
        self.assertEqual(
            self.calls, [("# Final\n\nEvidence.", "Quarterly report", "pdf", "ocean")]
        )
        data, name, mime = self.engine.read_deliverable(goal)
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertEqual((name, mime), ("report.pdf", "application/pdf"))
        tasks = self.engine.store.tasks()
        self.assertEqual(tasks[-1]["receipt"]["bytes"], len(b"# Final\n\nEvidence."))

    def test_legacy_goal_remains_unexported_until_requested(self):
        goal = self.submit()
        current = self.finish(goal)
        self.assertEqual(
            current["deliverable"], {"format": "markdown", "style": "editorial"}
        )
        self.assertIsNone(current["delivery"])
        self.assertFalse(self.calls)
        delivery = self.engine.export_goal(goal, {"format": "html", "style": "neon"})
        self.assertEqual(delivery["state"], "ready")
        self.assertEqual(len(self.engine.store.tasks()), 3)

    def test_settings_and_result_survive_restart(self):
        goal = self.submit({"format": "html", "style": "ocean"})
        before = self.finish(goal, True)
        self.engine.close()
        self.engine = Engine(self.temp.name, execution_limit=1)
        after = self.engine.store.goal(goal)
        self.assertEqual(after["deliverable"], before["deliverable"])
        self.assertEqual(after["delivery"], before["delivery"])
        self.assertIn(b"Evidence", self.engine.read_deliverable(goal)[0])

    def test_visual_story_layout_is_persisted_and_passed_to_renderer(self):
        calls = []

        def story_renderer(text, title, format, style, *, layout):
            calls.append((format, style, layout))
            return b"<html>Visual story</html>"

        self.engine.deliverable_renderer = story_renderer
        goal = self.submit(
            {"format": "html", "style": "ocean", "layout": "visual_story"}
        )
        current = self.finish(goal, True)
        self.assertEqual(current["delivery"]["state"], "ready")
        self.assertEqual(current["deliverable"]["layout"], "visual_story")
        self.assertEqual(current["delivery"]["layout"], "visual_story")
        self.assertEqual(calls, [("html", "ocean", "visual_story")])

    def test_render_failure_is_not_a_ready_pdf_and_can_retry(self):
        goal = self.submit()
        self.finish(goal)
        self.engine.deliverable_renderer = lambda *_: (_ for _ in ()).throw(
            ValueError("Diagram cannot be rendered")
        )
        failed = self.engine.export_goal(goal, {"format": "html"})
        self.assertEqual(failed["state"], "failed")
        self.assertIn("Diagram", failed["error"])
        with self.assertRaisesRegex(ValueError, "ready deliverable"):
            self.engine.read_deliverable(goal)
        self.engine.deliverable_renderer = self.render
        self.assertEqual(self.engine.export_goal(goal)["state"], "ready")

    def test_changed_source_and_changed_download_are_rejected(self):
        goal = self.submit()
        self.finish(goal)
        delivery = self.engine.export_goal(goal, {"format": "html"})
        self.engine.connection.path(delivery["artifact"]).write_bytes(b"Modified")
        with self.assertRaisesRegex(ValueError, "changed after generation"):
            self.engine.read_deliverable(goal)
        final = self.engine.store.tasks()[-1]["receipt"]["artifact"]
        self.engine.connection.path(final).write_text("Modified source")
        failed = self.engine.export_goal(goal)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("changed after", failed["error"])

    def test_pdf_dependency_and_invalid_settings_fail_before_tasks_are_saved(self):
        with patch(
            "orchestration.delivery.importlib.util.find_spec", return_value=None
        ):
            with self.assertRaisesRegex(ValueError, "ReportLab"):
                self.submit({"format": "pdf"})
        self.assertFalse(self.engine.store.tasks())
        for invalid in (
            {"format": "exe"},
            {"style": "unknown"},
            {"layout": "unknown"},
            {"path": "../../escape"},
            [],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_deliverable(invalid)

    def test_rendering_does_not_hold_engine_lock(self):
        goal = self.submit()
        self.finish(goal)
        started, release = threading.Event(), threading.Event()

        def blocked(*args):
            started.set()
            release.wait(2)
            return self.render(*args)

        self.engine.deliverable_renderer = blocked
        worker = threading.Thread(
            target=lambda: self.engine.export_goal(goal, {"format": "html"})
        )
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            state = self.engine.snapshot()
            self.assertEqual(state["goals"][0]["delivery"]["state"], "rendering")
            with self.assertRaisesRegex(ValueError, "already being prepared"):
                self.engine.export_goal(goal)
        finally:
            release.set()
            worker.join()

    def test_http_export_and_binary_download_require_authorization(self):
        goal = self.submit()
        self.finish(goal)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.engine, "test-session")
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/goals/{goal}/deliverable"
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(base)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
            headers = {
                "Authorization": "Bearer test-session",
                "Content-Type": "application/json",
            }
            with urlopen(
                Request(
                    base, data=b'{"format":"html","style":"ocean"}', headers=headers
                )
            ) as response:
                self.assertEqual(json.load(response)["delivery"]["state"], "ready")
            with urlopen(Request(base, headers=headers)) as response:
                self.assertEqual(
                    response.headers["Content-Type"], "text/html; charset=utf-8"
                )
                self.assertIn(
                    'filename="report.html"', response.headers["Content-Disposition"]
                )
                self.assertIn(b"Evidence", response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
