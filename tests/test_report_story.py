"""Visual story reports preserve evidence and reject unsupported content shapes."""

import copy
import importlib.util
import io
import json
import unittest

from orchestration.report_story import ICONS, extract_story
from orchestration.reports import render_deliverable

HAS_PDF_TOOLS = all(
    importlib.util.find_spec(package) is not None for package in ("reportlab", "pypdf")
)


def brief():
    """Return a small original story, independent of any private project files."""
    return {
        "title": "How the project works",
        "subtitle": "Follow a request from intake to a saved result.",
        "chapters": [
            {
                "title": "Give each technology a job",
                "takeaway": "The service and data layer have different responsibilities.",
                "cards": [
                    {
                        "icon": "api",
                        "title": "Request interface",
                        "body": "The service accepts a request.",
                        "source_refs": ["R1"],
                    },
                    {
                        "icon": "database",
                        "title": "Persistent records",
                        "body": "The database retains a checked result.",
                        "source_refs": ["R2"],
                    },
                ],
            },
            {
                "title": "Trace the handoff",
                "takeaway": "Keep unknown behavior separate from documented behavior.",
                "steps": [
                    {
                        "icon": "document",
                        "title": "Read the input",
                        "body": "The request supplies source text.",
                        "status": "documented",
                        "source_refs": ["R1"],
                    },
                    {
                        "icon": "agent",
                        "title": "Produce a result",
                        "body": "The proposed model step requires runtime verification.",
                        "status": "inferred",
                        "source_refs": ["R2"],
                    },
                    {
                        "icon": "shield",
                        "title": "Check the result",
                        "body": "The verification contract has not been established.",
                        "status": "unknown",
                        "source_refs": [],
                    },
                ],
            },
        ],
    }


def source(story=None, appendix=None):
    """Wrap one structured story alongside a Markdown evidence appendix."""
    if appendix is None:
        appendix = "# Evidence appendix\n\nPreserve this exact source sentence.\n\n| Ref | Source |\n| --- | --- |\n| R1 | src/api.py |\n| R2 | src/store.py |\n"
    return (
        "```story\n"
        + json.dumps(brief() if story is None else story)
        + "\n```\n\n"
        + appendix
    )


class StorySchemaTests(unittest.TestCase):
    def test_extracts_one_story_without_rewriting_the_evidence_appendix(self):
        appendix = "# Evidence\n\nKeep **formatting**, `code`, and internal spaces.\n\nFinal evidence.\n"
        story, extracted = extract_story(source(appendix=appendix))
        self.assertEqual(story["title"], "How the project works")
        self.assertEqual(extracted, appendix.strip())
        self.assertEqual(
            [step["status"] for step in story["chapters"][1]["steps"]],
            ["documented", "inferred", "unknown"],
        )

    def test_requires_one_explicit_story_instead_of_silently_inventing_one(self):
        for document in ("# Plain report\nNo structured story.", source() + source()):
            with self.subTest(document=document[:30]), self.assertRaisesRegex(
                ValueError, "one story brief"
            ):
                extract_story(document)
        with self.assertRaisesRegex(ValueError, "one story brief"):
            render_deliverable(
                "# Plain report", "Report", "html", layout="visual_story"
            )

    def test_rejects_invalid_json_and_oversized_briefs(self):
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            extract_story("```story\n{invalid}\n```\nAppendix")
        oversized = brief()
        oversized["extra"] = "x" * 48000
        with self.assertRaisesRegex(ValueError, "48 KiB"):
            extract_story(source(oversized))
        with self.assertRaisesRegex(ValueError, "must be an object"):
            extract_story("```story\n[]\n```")

    def test_chapter_counts_and_exclusive_cards_or_steps_are_bounded(self):
        invalid = []
        for count in (0, 7):
            value = brief()
            value["chapters"] = [
                copy.deepcopy(value["chapters"][0]) for _ in range(count)
            ]
            invalid.append(value)
        empty = brief()
        empty["chapters"][0]["cards"] = []
        invalid.append(empty)
        mixed = brief()
        mixed["chapters"][0]["steps"] = [
            copy.deepcopy(mixed["chapters"][1]["steps"][0])
        ]
        invalid.append(mixed)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_story(source(value))

    def test_card_and_step_counts_cannot_exceed_six(self):
        for field, index in (("cards", 0), ("steps", 1)):
            value = brief()
            value["chapters"][index][field] = [
                copy.deepcopy(value["chapters"][index][field][0]) for _ in range(7)
            ]
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "six cards or steps"
            ):
                extract_story(source(value))

    def test_text_icons_status_and_reference_limits_are_enforced(self):
        invalid = []
        for key, content in (("title", "x" * 101), ("subtitle", "x" * 301)):
            value = brief()
            value[key] = content
            invalid.append(value)
        for field, content in (
            ("title", ""),
            ("body", "x" * 401),
            ("icon", "remote-image"),
            ("source_refs", ["R"] * 17),
            ("source_refs", ["x" * 61]),
        ):
            value = brief()
            value["chapters"][0]["cards"][0][field] = content
            invalid.append(value)
        status = brief()
        status["chapters"][1]["steps"][0]["status"] = "verified-production"
        invalid.append(status)
        takeaway = brief()
        takeaway["chapters"][0]["takeaway"] = "x" * 241
        invalid.append(takeaway)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_story(source(value))

    def test_html_escapes_story_and_preserves_the_source_appendix(self):
        value = brief()
        value["chapters"][0]["title"] = "<script>unsafe()</script>"
        value["chapters"][0]["cards"][0][
            "body"
        ] = '<img src="https://example.invalid/tracker" onerror="unsafe()">'
        report = render_deliverable(
            source(value), "Report", "html", "ocean", layout="visual_story"
        ).decode()
        self.assertNotIn("<script>", report)
        self.assertNotIn("<img ", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("&lt;img ", report)
        self.assertIn("Evidence appendix", report)
        self.assertIn("Preserve this exact source sentence.", report)
        self.assertIn("src/api.py", report)
        self.assertNotIn("```story", report)

    def test_markdown_export_keeps_the_editable_story_source(self):
        original = source()
        self.assertEqual(
            render_deliverable(original, "Report", "markdown", layout="visual_story"),
            original.encode(),
        )


@unittest.skipUnless(HAS_PDF_TOOLS, "PDF checks require ReportLab and pypdf")
class StoryPDFTests(unittest.TestCase):
    def test_pdf_puts_the_visual_chapters_before_the_full_evidence_appendix(self):
        from pypdf import PdfReader

        data = render_deliverable(
            source(), "Report", "pdf", "ocean", layout="visual_story"
        )
        reader = PdfReader(io.BytesIO(data))
        first = reader.pages[0].extract_text()
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("Give each technology a job", first)
        self.assertIn("Request interface", first)
        self.assertLess(
            text.index("Trace the handoff"), text.index("Evidence appendix")
        )
        for phrase in (
            "Persistent records",
            "Read the input",
            "DOCUMENTED",
            "INFERRED",
            "UNKNOWN",
            "Preserve this exact source sentence.",
            "src/api.py",
            "src/store.py",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("```story", text)
        for page in reader.pages:
            for obj in page.get("/Resources", {}).get("/XObject", {}).values():
                self.assertNotEqual(obj.get_object().get("/Subtype"), "/Image")
        self.assertTrue(
            any(b" m" in page.get_contents().get_data() for page in reader.pages[:2])
        )

    def test_every_supported_icon_can_be_rendered_as_vector_art_without_external_assets(
        self,
    ):
        from pypdf import PdfReader

        value = brief()
        icons = sorted(ICONS)
        value["chapters"] = [
            {
                "title": f"Technology group {index + 1}",
                "cards": [
                    {
                        "icon": icon,
                        "title": icon.title(),
                        "body": "A short explanatory technology caption.",
                        "source_refs": [],
                    }
                    for icon in icons[start : start + 6]
                ],
            }
            for index, start in enumerate(range(0, len(icons), 6))
        ]
        data = render_deliverable(
            source(value), "Icons", "pdf", "editorial", layout="visual_story"
        )
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() for page in reader.pages)
        for icon in icons:
            self.assertIn(icon.title(), text)
        self.assertGreaterEqual(len(reader.pages), 3)
