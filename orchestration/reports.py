"""Styled, portable report exports from saved team text, with vector diagrams."""

from collections import OrderedDict, deque
from datetime import datetime, timezone
import html
import io
import math
from pathlib import Path
import re

PALETTES = {
    "editorial": {
        "ink": "292823",
        "muted": "746B60",
        "accent": "A6462C",
        "second": "277E80",
        "paper": "FBF7EF",
        "tint": "F4E4D4",
        "line": "DBCDBC",
    },
    "ocean": {
        "ink": "123342",
        "muted": "536F7A",
        "accent": "007A94",
        "second": "D47742",
        "paper": "F2F9FA",
        "tint": "DDEEF2",
        "line": "BDD4DC",
    },
    "neon": {
        "ink": "142A46",
        "muted": "506987",
        "accent": "1565E8",
        "second": "7749C9",
        "paper": "F3F7FF",
        "tint": "DFEAFE",
        "line": "B9CDEC",
    },
}
MAX_SOURCE_BYTES = 512 * 1024


def _plain(text):
    """Normalize portable punctuation and strip display-only Markdown decoration."""
    text = (
        str(text).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", " - ")
    )
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text.replace("**", "").replace("`", "").replace("\\n", "\n")


def _inline(text):
    """Render a small inline subset after escaping all source-supplied HTML."""
    text = html.escape(
        str(text).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", " - ")
    )
    text = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def parse_report(text):
    """Parse headings, paragraphs, lists, tables, and fenced diagram/code blocks."""
    if not isinstance(text, str) or len(text.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Report source must be text of at most 512 KiB")
    lines = text.replace("\r\n", "\n").splitlines()
    blocks, paragraph = [], []

    def flush():
        if paragraph:
            blocks.append(("paragraph", " ".join(paragraph)))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush()
        elif line.startswith("```"):
            flush()
            language = line[3:].strip().lower()
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            blocks.append(
                ("diagram" if language == "mermaid" else "code", "\n".join(code))
            )
        elif re.match(r"^#{1,6}\s+", line):
            flush()
            level, title = line.split(" ", 1)
            blocks.append(("heading", (len(level), title)))
        elif (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[index + 1].strip())
        ):
            flush()
            rows = [[cell.strip() for cell in line.strip("|").split("|")]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(
                    [
                        cell.strip()
                        for cell in lines[index].strip().strip("|").split("|")
                    ]
                )
                index += 1
            blocks.append(("table", rows))
            continue
        elif re.match(r"^(?:[-*+] |\d+[.)] )", line):
            flush()
            match = re.match(r"^([-*+]|\d+[.)])\s+(.*)", line)
            blocks.append(("list", (match[1], match[2])))
        elif line.startswith(">"):
            flush()
            blocks.append(("quote", line.lstrip("> ")))
        elif line in ("---", "***", "___"):
            flush()
            blocks.append(("rule", ""))
        else:
            paragraph.append(line)
        index += 1
    flush()
    return blocks


def parse_diagram(source):
    """Read a bounded Mermaid flowchart subset without executing scripts or HTML."""
    nodes, edges = OrderedDict(), []
    node_pattern = re.compile(
        r"([A-Za-z_][\w-]*)\s*(?:\[\((.*?)\)\]|\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})"
    )
    edge_pattern = re.compile(
        r"(?<![\w-])(?=([A-Za-z_][\w-]*)\s*(-->|-\.->|==>|---)\s*(?:\|([^|]*)\|\s*)?([A-Za-z_][\w-]*))"
    )
    for line in source.splitlines():
        line = line.strip().rstrip(";")
        if not line or line.startswith(
            (
                "flowchart ",
                "graph ",
                "subgraph ",
                "end",
                "%%",
                "style ",
                "class",
                "linkStyle ",
            )
        ):
            continue
        for match in node_pattern.finditer(line):
            label = next(
                (part for part in match.groups()[1:] if part is not None), match[1]
            )
            label = re.sub(r"<br\s*/?>", "\n", label, flags=re.I).strip('"')
            nodes[match[1]] = _plain(label)
        simple = node_pattern.sub(lambda match: match[1], line)
        simple = re.sub(
            r"-\.\s+(.+?)\s+\.->", lambda match: "-.->|" + match[1] + "|", simple
        )
        simple = re.sub(
            r"--\s+([^>|]+?)\s+-->", lambda match: "-->|" + match[1] + "|", simple
        )
        for match in edge_pattern.finditer(simple):
            first, arrow, label, second = match.groups()
            nodes.setdefault(first, first)
            nodes.setdefault(second, second)
            edges.append((first, second, _plain(label or ""), "." in arrow))
    if not nodes or not edges:
        raise ValueError(
            "This diagram needs a Mermaid flowchart with labeled nodes and arrows"
        )
    if len(nodes) > 80 or len(edges) > 160:
        raise ValueError("Split large diagrams into sections of at most 80 nodes")
    return nodes, edges


def diagram_panels(source):
    """Lay out weakly connected flowchart components into readable vector panels."""
    nodes, edges = parse_diagram(source)
    adjacency = {key: set() for key in nodes}
    for first, second, _, _ in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    seen, panels = set(), []
    for root in nodes:
        if root in seen:
            continue
        queue, component = deque([root]), []
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            queue.extend(sorted(adjacency[node] - seen))
        selected = set(component)
        component_edges = [
            edge for edge in edges if edge[0] in selected and edge[1] in selected
        ]
        incoming = {key: set() for key in component}
        outgoing = {key: set() for key in component}
        for first, second, _, _ in component_edges:
            incoming[second].add(first)
            outgoing[first].add(second)
        roots = [key for key in component if not incoming[key]] or component[:1]
        ranks = {key: 0 for key in roots}
        queue = deque(roots)
        while queue:
            first = queue.popleft()
            for second in outgoing[first]:
                if second not in ranks:
                    ranks[second] = ranks[first] + 1
                    queue.append(second)
        for key in component:
            ranks.setdefault(key, 0)
        layers = []
        for rank in sorted(set(ranks.values())):
            members = [key for key in component if ranks[key] == rank]
            layers.extend(members[i : i + 3] for i in range(0, len(members), 3))
        # Keep every panel printable at comfortable text sizes. Continuation
        # captions name external connections instead of silently dropping edges.
        for start in range(0, len(layers), 5):
            rows = layers[start : start + 5]
            keys = {key for row in rows for key in row}
            internal = [
                edge for edge in component_edges if edge[0] in keys and edge[1] in keys
            ]
            external = [
                edge
                for edge in component_edges
                if (edge[0] in keys) != (edge[1] in keys)
            ]
            panels.append(
                {"rows": rows, "nodes": nodes, "edges": internal, "external": external}
            )
    return panels


def _svg_panel(panel, palette):
    """Produce standalone SVG for the same safe flowchart layout used in PDFs."""
    width, gap, node_width = 900, 126, 252
    height = len(panel["rows"]) * gap + 34
    positions = {}
    for row_index, row in enumerate(panel["rows"]):
        for column, key in enumerate(row):
            x = (width - len(row) * (node_width + 32) + 32) / 2 + column * (
                node_width + 32
            )
            positions[key] = (x, 20 + row_index * gap)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Architecture flowchart"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#{palette["second"]}"/></marker></defs>'
    ]
    for edge_index, (first, second, label, dashed) in enumerate(panel["edges"], 1):
        x1, y1 = positions[first]
        x2, y2 = positions[second]
        x1 += node_width / 2
        x2 += node_width / 2
        y1 += 66
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        parts.append(
            f'<path d="M{x1},{y1} C{x1},{(y1+y2)/2} {x2},{(y1+y2)/2} {x2},{y2}" fill="none" stroke="#{palette["second"]}" stroke-width="2" marker-end="url(#arrow)"{dash}/>'
        )
        parts.append(
            f'<circle cx="{(x1+x2)/2}" cy="{(y1+y2)/2}" r="10" fill="white" stroke="#{palette["second"]}"/><text x="{(x1+x2)/2}" y="{(y1+y2)/2+4}" text-anchor="middle" font-size="11" fill="#{palette["ink"]}">{edge_index}</text>'
        )
    for key, (x, y) in positions.items():
        label = panel["nodes"][key]
        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_width}" height="66" rx="9" fill="white" stroke="#{palette["accent"]}" stroke-width="2"/><foreignObject x="{x+10}" y="{y+6}" width="{node_width-20}" height="56"><div xmlns="http://www.w3.org/1999/xhtml" style="font:600 14px/1.3 Arial;text-align:center;color:#{palette["ink"]};display:flex;height:56px;align-items:center;justify-content:center">{html.escape(label)}</div></foreignObject>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _html_report(blocks, title, style, story=None):
    """Create a self-contained report with print styling and inline vector diagrams."""
    palette = PALETTES[style]
    body = []
    if story:
        from .report_story import story_html

        body.extend([story_html(story), "<h2>Evidence appendix</h2>"])
    for block_index, (kind, value) in enumerate(blocks):
        if kind == "heading":
            level, text = value
            if level == 1:
                continue
            level = min(level, 4)
            body.append(f"<h{level}>{_inline(text)}</h{level}>")
        elif kind == "paragraph":
            body.append(f"<p>{_inline(value)}</p>")
        elif kind == "quote":
            body.append(f"<aside>{_inline(value)}</aside>")
        elif kind == "list":
            marker, text = value
            body.append(
                f'<p class="list"><span>{html.escape(marker if marker[0].isdigit() else "•")}</span>{_inline(text)}</p>'
            )
        elif kind == "table":
            rows = []
            for index, row in enumerate(value):
                tag = "th" if index == 0 else "td"
                rows.append(
                    "<tr>"
                    + "".join(f"<{tag}>{_inline(cell)}</{tag}>" for cell in row)
                    + "</tr>"
                )
            body.append(
                '<div class="table-wrap"><table>' + "".join(rows) + "</table></div>"
            )
        elif kind == "diagram":
            for panel in diagram_panels(value):
                key = "".join(
                    "<li><b>"
                    + html.escape(f"{panel['nodes'][first]} → {panel['nodes'][second]}")
                    + "</b>: "
                    + html.escape(label or "Connected")
                    + (" (dashed connection)" if dashed else "")
                    + "</li>"
                    for first, second, label, dashed in panel["edges"]
                )
                continuation = "".join(
                    "<p><b>Continues in another panel:</b> "
                    + html.escape(
                        f"{panel['nodes'][first]} → {panel['nodes'][second]}: {label}"
                    )
                    + "</p>"
                    for first, second, label, _ in panel["external"]
                )
                body.append(
                    "<figure>"
                    + _svg_panel(panel, palette)
                    + "<figcaption><b>Relationship key</b><ol>"
                    + key
                    + "</ol>"
                    + continuation
                    + "</figcaption></figure>"
                )
        elif kind == "code":
            body.append("<pre>" + html.escape(value) + "</pre>")
    css = f"""*{{box-sizing:border-box}}body{{margin:0;background:#{palette['paper']};color:#{palette['ink']};font:16px/1.65 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:64px 48px}}header{{border-top:10px solid #{palette['accent']};padding:36px 0 42px;border-bottom:1px solid #{palette['line']}}}.eyebrow{{color:#{palette['accent']};letter-spacing:.16em;font-size:12px;font-weight:750}}h1{{font:600 clamp(34px,5vw,62px)/1.08 Georgia,serif;max-width:850px}}h2{{font:600 30px/1.2 Georgia,serif;color:#{palette['accent']};margin-top:48px;border-top:1px solid #{palette['line']};padding-top:24px}}h3{{font-size:21px;margin-top:28px}}p{{max-width:88ch}}.list{{display:flex;gap:14px;margin:9px 0}}.list span{{color:#{palette['accent']};font-weight:bold}}aside,pre{{background:#{palette['tint']};padding:20px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse;font-size:13px;line-height:1.5}}th{{background:#{palette['accent']};color:white;text-align:left}}td,th{{padding:14px;border:1px solid #{palette['line']};vertical-align:top}}tr:nth-child(odd){{background:#{palette['tint']}}}.table-wrap{{overflow:auto}}figure{{margin:28px 0;padding:18px;background:#{palette['tint']};border-radius:12px}}svg{{width:100%;display:block}}footer{{border-top:1px solid #{palette['line']};margin-top:54px;padding-top:18px;color:#{palette['muted']};font-size:12px}}@media print{{body{{background:white}}main{{padding:0}}h2{{break-after:avoid}}tr,figure{{break-inside:avoid}}thead{{display:table-header-group}}}}"""
    return (
        f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><style>{css}</style><main><header><div class="eyebrow">ORCHESTRATION / TEAM REPORT</div><h1>{html.escape(_plain(title))}</h1><p>Evidence, architecture, and findings</p></header>{"".join(body)}<footer>Generated from the team’s saved output · {datetime.now(timezone.utc):%d %B %Y}</footer></main></html>'
    ).encode()


def _pdf_report(blocks, title, style, story=None):
    """Lay out report text, repeating table headers, and real vector architecture graphics."""
    try:
        import reportlab
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            PageBreak,
            Flowable,
            HRFlowable,
        )
    except ImportError as exc:
        raise ValueError(
            "PDF export needs ReportLab. Install the app's requirements.txt, or choose HTML."
        ) from exc
    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, filename in (
        ("ReportSans", "Vera.ttf"),
        ("ReportSansBold", "VeraBd.ttf"),
        ("ReportSansItalic", "VeraIt.ttf"),
    ):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily(
        "ReportSans",
        normal="ReportSans",
        bold="ReportSansBold",
        italic="ReportSansItalic",
        boldItalic="ReportSansBold",
    )
    palette = {
        key: colors.HexColor("#" + value) for key, value in PALETTES[style].items()
    }
    width, page_height, margin = 612, 792, 46
    body_width = width - 2 * margin
    heading_font = "Times-Bold" if style == "editorial" else "ReportSansBold"
    styles = {
        "body": ParagraphStyle(
            "Body",
            fontName="ReportSans",
            fontSize=10,
            leading=15,
            textColor=palette["ink"],
            spaceAfter=8,
            splitLongWords=True,
        ),
        "h2": ParagraphStyle(
            "Section",
            fontName=heading_font,
            fontSize=21,
            leading=26,
            textColor=palette["accent"],
            spaceBefore=19,
            spaceAfter=12,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "Subsection",
            fontName="ReportSansBold",
            fontSize=12.5,
            leading=18,
            textColor=palette["ink"],
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName="ReportSans",
            fontSize=7.7,
            leading=11.1,
            textColor=palette["muted"],
            spaceAfter=6,
            splitLongWords=True,
        ),
        "cell": ParagraphStyle(
            "Cell",
            fontName="ReportSans",
            fontSize=8.3,
            leading=12.1,
            textColor=palette["ink"],
            splitLongWords=True,
        ),
        "header": ParagraphStyle(
            "CellHeader",
            fontName="ReportSansBold",
            fontSize=8,
            leading=11,
            textColor=colors.white,
        ),
        "node": ParagraphStyle(
            "Node",
            fontName="ReportSansBold",
            fontSize=8,
            leading=10.5,
            textColor=palette["ink"],
            alignment=TA_CENTER,
        ),
        "code": ParagraphStyle(
            "Code",
            fontName="Courier",
            fontSize=7.2,
            leading=10,
            textColor=palette["ink"],
            wordWrap="CJK",
            backColor=palette["tint"],
            borderPadding=10,
            spaceAfter=12,
        ),
    }

    class Cover(Flowable):
        """Draw a spacious title page with a colored frame and summary excerpt."""

        def __init__(self):
            super().__init__()
            self.width, self.height = body_width, 660

        def draw(self):
            canvas = self.canv
            canvas.setFillColor(palette["tint"])
            canvas.roundRect(0, 5, body_width, 650, 15, fill=1, stroke=0)
            canvas.setFillColor(palette["accent"])
            canvas.roundRect(24, 600, 48, 34, 6, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("ReportSansBold", 18)
            canvas.drawCentredString(48, 610, "O")
            canvas.setFillColor(palette["accent"])
            canvas.setFont("ReportSansBold", 9)
            canvas.drawString(88, 615, "ORCHESTRATION / TEAM REPORT")
            title_style = ParagraphStyle(
                "CoverTitle",
                fontName=heading_font,
                fontSize=34,
                leading=40,
                textColor=palette["ink"],
            )
            heading = Paragraph(html.escape(_plain(title)), title_style)
            _, heading_height = heading.wrap(body_width - 56, 300)
            heading.drawOn(canvas, 28, 554 - heading_height)
            canvas.setStrokeColor(palette["second"])
            canvas.setLineWidth(3)
            canvas.line(28, 518 - heading_height, 142, 518 - heading_height)
            excerpt = next(
                (value for kind, value in blocks if kind == "paragraph"),
                "A report prepared from the team’s saved findings and supporting evidence.",
            )
            excerpt = _plain(excerpt)
            if len(excerpt) > 600:
                excerpt = excerpt[:597].rsplit(" ", 1)[0] + "…"
            intro = Paragraph(
                html.escape(excerpt),
                ParagraphStyle(
                    "CoverIntro", parent=styles["body"], fontSize=11, leading=18
                ),
            )
            _, intro_height = intro.wrap(body_width - 58, 300)
            intro.drawOn(canvas, 29, min(440, 481 - heading_height) - intro_height)
            labels = [
                ("EVIDENCE", "Sources and scope"),
                ("ARCHITECTURE", "Visual relationships"),
                ("FINDINGS", "Decisions and gaps"),
            ]
            for index, (label, detail) in enumerate(labels):
                x = 28 + index * 158
                canvas.setFillColor(palette["second"])
                canvas.circle(x + 5, 112, 4, fill=1, stroke=0)
                canvas.setFillColor(palette["ink"])
                canvas.setFont("ReportSansBold", 8)
                canvas.drawString(x, 89, label)
                canvas.setFont("ReportSans", 7.5)
                canvas.drawString(x, 72, detail)
            canvas.setFillColor(palette["muted"])
            canvas.setFont("ReportSans", 8)
            canvas.drawString(
                28, 32, datetime.now(timezone.utc).strftime("Prepared %d %B %Y")
            )

    class Diagram(Flowable):
        """Draw one architecture component with labeled nodes and vector arrows."""

        def __init__(self, panel, panel_width=body_width):
            super().__init__()
            self.panel = panel
            self.width = panel_width
            self.height = (
                len(panel["rows"]) * 105 + 24 + (30 if panel["external"] else 0)
            )

        def draw(self):
            canvas, panel = self.canv, self.panel
            canvas.setFillColor(palette["paper"])
            canvas.roundRect(0, 0, self.width, self.height, 9, fill=1, stroke=0)
            positions = {}
            for row_index, row in enumerate(panel["rows"]):
                node_width = min(
                    210, (self.width - 24 - (len(row) - 1) * 20) / len(row)
                )
                for column, key in enumerate(row):
                    x = (
                        self.width - len(row) * (node_width + 20) + 20
                    ) / 2 + column * (node_width + 20)
                    y = self.height - 18 - 56 - row_index * 105
                    positions[key] = (x, y, node_width)
            for edge_index, (first, second, label, dashed) in enumerate(panel["edges"]):
                x1, y1, w1 = positions[first]
                x2, y2, w2 = positions[second]
                x1 += w1 / 2
                x2 += w2 / 2
                if y2 >= y1:
                    # Side routes keep back/peer links out of the node text.
                    x1 += w1 / 2
                    x2 += w2 / 2
                    y1 += 28
                    y2 += 28
                    mid = min(self.width - 5, max(x1, x2) + 12)
                    points = [(x1, y1), (mid, y1), (mid, y2), (x2, y2)]
                else:
                    y2 += 56
                    mid = (y1 + y2) / 2 + ((edge_index % 3) - 1) * 9
                    points = [(x1, y1), (x1, mid), (x2, mid), (x2, y2)]
                canvas.setStrokeColor(palette["second"])
                canvas.setLineWidth(1.1)
                canvas.setDash(3, 2) if dashed else canvas.setDash()
                path = canvas.beginPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                canvas.drawPath(path)
                canvas.setDash()
                previous, end = points[-2], points[-1]
                angle = math.atan2(end[1] - previous[1], end[0] - previous[0])
                canvas.setFillColor(palette["second"])
                arrow = canvas.beginPath()
                arrow.moveTo(*end)
                for offset in (-0.5, 0.5):
                    arrow.lineTo(
                        end[0] - 5 * math.cos(angle + offset),
                        end[1] - 5 * math.sin(angle + offset),
                    )
                arrow.close()
                canvas.drawPath(arrow, fill=1, stroke=0)
                # Numbered connectors avoid overlapping long edge labels; the
                # full relationship key below preserves every label verbatim.
                lx = (points[1][0] + points[2][0]) / 2
                ly = (points[1][1] + points[2][1]) / 2
                canvas.setFillColor(colors.white)
                canvas.setStrokeColor(palette["second"])
                canvas.circle(lx, ly, 6, fill=1, stroke=1)
                canvas.setFillColor(palette["ink"])
                canvas.setFont("ReportSansBold", 6)
                canvas.drawCentredString(lx, ly - 2, str(edge_index + 1))
            for key, (x, y, node_width) in positions.items():
                canvas.setFillColor(colors.white)
                canvas.setStrokeColor(palette["accent"])
                canvas.setLineWidth(1.3)
                canvas.roundRect(x, y, node_width, 56, 7, fill=1, stroke=1)
                node_style = (
                    styles["node"]
                    if node_width >= 130
                    else ParagraphStyle(
                        "CompactNode", parent=styles["node"], fontSize=6.8, leading=9
                    )
                )
                text = Paragraph(
                    html.escape(panel["nodes"][key]).replace("\n", "<br/>"),
                    node_style,
                )
                _, height = text.wrap(node_width - 14, 48)
                text.drawOn(canvas, x + 7, y + (56 - height) / 2)

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=(width, page_height),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=51,
        bottomMargin=44,
        title=_plain(title),
        author="Orchestration",
        pageCompression=1,
    )

    def page_frame(canvas, document):
        """Repeat restrained navigation, accent line, and page numbers on each page."""
        canvas.saveState()
        canvas.setFillColor(palette["accent"])
        canvas.rect(0, page_height - 7, width, 7, fill=1, stroke=0)
        if document.page > 1:
            canvas.setFont("ReportSansBold", 7)
            canvas.setFillColor(palette["muted"])
            canvas.drawString(
                margin, page_height - 30, "ORCHESTRATION / " + _plain(title)[:80]
            )
        canvas.setStrokeColor(palette["line"])
        canvas.setLineWidth(0.5)
        canvas.line(margin, 33, width - margin, 33)
        canvas.setFont("ReportSans", 7)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(
            margin, 20, "Team report · Evidence and interpretation remain distinct"
        )
        canvas.drawRightString(width - margin, 20, str(document.page))
        canvas.restoreState()

    brief = story
    if brief:
        from .report_story import story_flowables

        story = story_flowables(brief, palette, body_width)
        if not any(
            kind == "heading"
            and value[0] > 1
            and value[1].strip().lower() == "evidence appendix"
            for kind, value in blocks
        ):
            story.append(Paragraph("Evidence appendix", styles["h2"]))
    else:
        story = [Cover(), PageBreak()]
    for block_index, (kind, value) in enumerate(blocks):
        if kind == "heading":
            level, text = value
            if level > 1:
                if level == 2 and any(
                    kind == "diagram"
                    for kind, _ in blocks[block_index + 1 : block_index + 4]
                ):
                    story.append(PageBreak())
                story.append(
                    Paragraph(_inline(text), styles["h2" if level == 2 else "h3"])
                )
        elif kind == "paragraph":
            paragraph_style = (
                styles["small"]
                if value.startswith("Shared-context revision:")
                else styles["body"]
            )
            story.append(Paragraph(_inline(value), paragraph_style))
        elif kind == "quote":
            note = Table(
                [[Paragraph(_inline(value), styles["body"])]], colWidths=[body_width]
            )
            note.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), palette["tint"]),
                        ("BOX", (0, 0), (-1, -1), 0.5, palette["line"]),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )
            story.extend([note, Spacer(1, 10)])
        elif kind == "list":
            marker, text = value
            story.append(
                Paragraph(
                    _inline(text),
                    ParagraphStyle(
                        "ListItem",
                        parent=styles["body"],
                        leftIndent=15,
                        firstLineIndent=0,
                        bulletIndent=0,
                    ),
                    bulletText=marker if marker[0].isdigit() else "•",
                )
            )
        elif kind == "table":
            columns = max(len(row) for row in value)
            reference_table = value[0][0].strip().lower() in (
                "ref",
                "ref.",
                "reference",
            )
            fractions = [0.20, 0.45, 0.35] if columns == 3 else [1 / columns] * columns
            if reference_table and columns == 3:
                fractions = [0.09, 0.59, 0.32]
            widths = [body_width * fraction for fraction in fractions]
            cell_style = (
                ParagraphStyle(
                    "ReferenceCell", parent=styles["cell"], fontSize=7.3, leading=10
                )
                if reference_table
                else styles["cell"]
            )
            rows = [
                [
                    Paragraph(
                        _inline(cell), styles["header"] if index == 0 else cell_style
                    )
                    for cell in row + [""] * (columns - len(row))
                ]
                for index, row in enumerate(value)
            ]
            table = Table(
                rows,
                colWidths=widths,
                repeatRows=1,
                splitByRow=1,
                splitInRow=0,
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), palette["accent"]),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, palette["paper"]],
                        ),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 4 if reference_table else 9),
                        (
                            "BOTTOMPADDING",
                            (0, 0),
                            (-1, -1),
                            4 if reference_table else 9,
                        ),
                    ]
                )
            )
            story.extend([table, Spacer(1, 13)])
        elif kind == "diagram":
            story.extend(
                [
                    Spacer(1, 10),
                    Paragraph("ARCHITECTURE / RELATIONSHIPS", styles["small"]),
                ]
            )

            def panel_content(panel, panel_width):
                """Keep a component beside its full, readable relationship key."""
                content = [
                    Diagram(panel, panel_width),
                    Spacer(1, 10),
                    Paragraph("Relationship key", styles["h3"]),
                ]
                for index, (first, second, label, dashed) in enumerate(
                    panel["edges"], 1
                ):
                    relation = f"{panel['nodes'][first]} → {panel['nodes'][second]}"
                    detail = (": " + label) if label else ""
                    if dashed:
                        detail += " (dashed connection)"
                    content.append(
                        Paragraph(
                            f"<b>{index}. {html.escape(relation)}</b>{html.escape(detail)}",
                            styles["small"],
                        )
                    )
                for first, second, label, _ in panel["external"]:
                    content.append(
                        Paragraph(
                            "<b>Continues in another panel:</b> "
                            + html.escape(
                                f"{panel['nodes'][first]} → {panel['nodes'][second]}: {label}"
                            ),
                            styles["small"],
                        )
                    )
                content.append(Spacer(1, 12))
                return content

            panels = diagram_panels(value)
            cursor = 0
            while cursor < len(panels):
                panel = panels[cursor]
                if (
                    len(panel["rows"]) <= 2
                    and cursor + 1 < len(panels)
                    and len(panels[cursor + 1]["rows"]) <= 2
                ):
                    pair = Table(
                        [
                            [
                                panel_content(panel, body_width / 2 - 8),
                                panel_content(panels[cursor + 1], body_width / 2 - 8),
                            ]
                        ],
                        colWidths=[body_width / 2, body_width / 2],
                    )
                    pair.setStyle(
                        TableStyle(
                            [
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                            ]
                        )
                    )
                    story.append(pair)
                    cursor += 2
                else:
                    story.extend(panel_content(panel, body_width))
                    cursor += 1
        elif kind == "code":
            story.append(
                Paragraph(html.escape(value).replace("\n", "<br/>"), styles["code"])
            )
        elif kind == "rule":
            story.append(
                HRFlowable(
                    width="100%", color=palette["line"], spaceBefore=9, spaceAfter=9
                )
            )
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    return output.getvalue()


def render_deliverable(
    markdown, title, format="pdf", style="editorial", *, layout="reference"
):
    """Return a complete binary or text export; never fetch remote assets or run source code."""
    if style not in PALETTES:
        raise ValueError("Choose editorial, ocean, or neon report styling")
    if format not in ("pdf", "html", "markdown"):
        raise ValueError("Choose PDF, HTML, or Markdown for the deliverable")
    if layout not in ("reference", "visual_story"):
        raise ValueError("Choose visual story or reference layout")
    story = None
    if layout == "visual_story" and format != "markdown":
        from .report_story import extract_story

        story, markdown = extract_story(markdown)
    blocks = parse_report(markdown)
    if not isinstance(title, str) or not title.strip() or len(title) > 240:
        raise ValueError("A report title of 1–240 characters is required")
    if format == "markdown":
        return markdown.encode()
    if format == "html":
        return _html_report(blocks, title, style, story)
    return _pdf_report(blocks, title, style, story)
