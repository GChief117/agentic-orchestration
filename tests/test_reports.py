"""Report exports preserve content and produce readable files without executing it."""

import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

from orchestration.reports import MAX_SOURCE_BYTES, parse_diagram, render_deliverable

HAS_PDF_TOOLS = all(
    importlib.util.find_spec(package) is not None for package in ("reportlab", "pypdf")
)

SAMPLE = """# Platform assessment

The team inspected the **service** and its source manifest.

## Architecture

```mermaid
flowchart TD
    Input[Client request] -->|validated request| API[Service API]
    API --> Store[(Persistent records)]
    Store -.->|optional replay| API
```

## Findings

| Component | Finding | Evidence |
| --- | --- | --- |
| Service API | A bounded request contract | src/api.py |
| Persistent records | Durable storage | src/store.py |

- Preserve the supplied evidence.
1. Review the output before acting.
"""


class TextReportTests(unittest.TestCase):
    def test_chained_arrows_preserve_every_relationship_and_complete_node_id(self):
        """Shared middle nodes must not hide an edge or create suffix node IDs."""
        nodes, edges = parse_diagram(
            "flowchart TD\nLongNode[Input] --> Process[Transform] --> Output[Result]"
        )
        self.assertEqual(
            dict(nodes),
            {"LongNode": "Input", "Process": "Transform", "Output": "Result"},
        )
        self.assertEqual(
            edges,
            [
                ("LongNode", "Process", "", False),
                ("Process", "Output", "", False),
            ],
        )

    def test_markdown_export_preserves_source_bytes(self):
        """Selecting the source format must not rewrite the user's document."""
        source = "# Résumé\n\nKeep **formatting**, `code`, and trailing space. \n"
        self.assertEqual(
            render_deliverable(source, "Résumé", "markdown", "editorial"),
            source.encode("utf-8"),
        )

    def test_html_contains_real_diagram_and_readable_report(self):
        """HTML exports show the diagram as vector content rather than Mermaid source."""
        report = render_deliverable(SAMPLE, "Platform assessment", "html", "ocean")
        text = report.decode("utf-8")
        self.assertIn("<!doctype html>", text)
        self.assertIn("<svg", text)
        self.assertIn("Client request", text)
        self.assertIn("Service API", text)
        self.assertIn("Persistent records", text)
        self.assertIn("validated request", text)
        self.assertIn("<table>", text)
        self.assertIn("src/store.py", text)
        self.assertNotIn("flowchart TD", text)
        self.assertNotIn("```", text)

    def test_html_escapes_source_content_and_title(self):
        """Source strings cannot create active HTML or fetch remote assets."""
        content = """# Report

<script>alert('source')</script>

<img src="https://example.invalid/tracker" onerror="alert(1)">

```mermaid
flowchart TD
A[<iframe src='https://example.invalid'>] --> B[Safe text]
```
"""
        text = render_deliverable(content, "<script>title()</script>", "html").decode()
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img ", text)
        self.assertNotIn("<iframe ", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&lt;img ", text)
        self.assertIn("&lt;iframe ", text)

    def test_code_fences_are_displayed_without_execution(self):
        """An export must never run code included in a model's response."""
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "executed.txt"
            content = (
                "# A code example\n\n```python\n"
                f"open({str(forbidden)!r}, 'w').write('executed')\n```\n"
            )
            report = render_deliverable(content, "Code example", "html").decode()
            self.assertIn("<pre>", report)
            self.assertIn("executed.txt", report)
            self.assertFalse(forbidden.exists())

    def test_unsupported_format_and_style_fail_explicitly(self):
        """Do not silently replace the requested export with another format."""
        for format_, style in (("docx", "editorial"), ("pdf", "unknown")):
            with self.subTest(format=format_, style=style), self.assertRaises(
                ValueError
            ):
                render_deliverable(SAMPLE, "Report", format_, style)

    def test_invalid_titles_and_excessive_source_are_rejected(self):
        """Bounded input errors remain actionable instead of allocating huge reports."""
        for title in (None, "", " ", "x" * 241):
            with self.subTest(title=title), self.assertRaises(ValueError):
                render_deliverable("A report", title, "html")
        with self.assertRaisesRegex(ValueError, "512 KiB"):
            render_deliverable("x" * (MAX_SOURCE_BYTES + 1), "Report", "html")

    def test_unsupported_diagram_fails_instead_of_delivering_raw_syntax(self):
        """An unsupported diagram must not masquerade as a completed visualization."""
        for diagram in ("not a graph", "sequenceDiagram\nAlice->>Bob: Hello"):
            with self.subTest(diagram=diagram), self.assertRaisesRegex(
                ValueError, "flowchart"
            ):
                render_deliverable(
                    f"# Report\n\n```mermaid\n{diagram}\n```", "Report", "html"
                )


@unittest.skipUnless(HAS_PDF_TOOLS, "PDF checks require ReportLab and pypdf")
class PDFReportTests(unittest.TestCase):
    def pdf(self, source=SAMPLE, style="editorial"):
        """Return the parsed in-memory PDF without keeping generated test files."""
        from pypdf import PdfReader

        data = render_deliverable(source, "Platform assessment", "pdf", style)
        self.assertTrue(data.startswith(b"%PDF-"))
        return PdfReader(io.BytesIO(data))

    def test_each_palette_produces_a_readable_pdf_with_vector_diagrams(self):
        """All exposed palette choices retain the report and its actual diagram labels."""
        for style in ("editorial", "ocean", "neon"):
            with self.subTest(style=style):
                document = self.pdf(style=style)
                text = "\n".join(page.extract_text() for page in document.pages)
                self.assertGreaterEqual(len(document.pages), 2)
                for expected in (
                    "Platform assessment",
                    "Client request",
                    "Service API",
                    "Persistent records",
                    "src/store.py",
                    "Review the output",
                ):
                    self.assertIn(expected, text)
                self.assertNotIn("flowchart TD", text)
                self.assertNotIn("```", text)
                for page in document.pages:
                    self.assertIsNone(page.get("/AA"))
                    resources = page.get("/Resources", {})
                    for obj in resources.get("/XObject", {}).values():
                        self.assertNotEqual(obj.get_object().get("/Subtype"), "/Image")

    def test_multipage_tables_retain_the_final_row(self):
        """Long reports may paginate but must not lose the last record or its evidence."""
        rows = "\n".join(
            f"| Component {index:03d} | Finding {index:03d}: bounded request evidence. | src/item_{index:03d}.py |"
            for index in range(90)
        )
        content = (
            "# Inventory\n\n## Components\n\n| Component | Finding | Evidence |\n| --- | --- | --- |\n"
            + rows
        )
        document = self.pdf(content)
        self.assertGreater(len(document.pages), 3)
        text = "\n".join(page.extract_text() for page in document.pages)
        self.assertIn("Component 000", text)
        self.assertIn("Component 089", text)
        self.assertIn("src/item_089.py", text)
        self.assertEqual(text.count("src/item_089.py"), 1)

    def test_pdf_does_not_execute_a_source_code_block(self):
        """PDF authoring treats model-generated code as report text only."""
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "executed.txt"
            content = (
                "# Example\n\n```python\n"
                f"open({str(forbidden)!r}, 'w').write('executed')\n```\n"
            )
            document = self.pdf(content)
            text = "\n".join(page.extract_text() for page in document.pages)
            self.assertIn("executed.txt", text)
            self.assertFalse(forbidden.exists())
