"""Evidence-led visual briefs with original functional icons and narrated workflows."""

import html
import json
import math
import re

ICONS = frozenset(
    (
        "code",
        "api",
        "database",
        "cloud",
        "container",
        "workflow",
        "agent",
        "document",
        "search",
        "shield",
        "chart",
        "layers",
    )
)


def extract_story(markdown):
    """Separate one bounded story brief from its unchanged Markdown evidence appendix."""
    matches = list(re.finditer(r"^```story\s*\n(.*?)^```\s*$", markdown, re.M | re.S))
    if len(matches) != 1:
        raise ValueError(
            "Visual story delivery needs one story brief from the final agent. Generate a visual brief before exporting this layout."
        )
    raw = matches[0].group(1)
    if len(raw.encode()) > 48000:
        raise ValueError("The visual story brief exceeds 48 KiB")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError("The visual story brief must contain valid JSON") from exc

    def text(item, limit, label, empty=False):
        if (
            not isinstance(item, str)
            or len(item) > limit
            or (not empty and not item.strip())
        ):
            raise ValueError(f"Invalid visual story {label}")
        return item.strip()

    if not isinstance(value, dict):
        raise ValueError("The visual story brief must be an object")
    story = {
        "title": text(value.get("title"), 100, "title"),
        "subtitle": text(value.get("subtitle", ""), 300, "subtitle", True),
        "chapters": [],
    }
    chapters = value.get("chapters")
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 6:
        raise ValueError("A visual story needs one to six chapters")
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Invalid visual story chapter")
        clean = {
            "title": text(chapter.get("title"), 100, "chapter title"),
            "takeaway": text(chapter.get("takeaway", ""), 240, "takeaway", True),
            "cards": [],
            "steps": [],
        }
        for kind in ("cards", "steps"):
            items = chapter.get(kind, [])
            if not isinstance(items, list) or len(items) > 6:
                raise ValueError(
                    "Each visual chapter supports up to six cards or steps"
                )
            for item in items:
                if not isinstance(item, dict) or item.get("icon") not in ICONS:
                    raise ValueError("Choose a supported explanatory icon")
                entry = {
                    "icon": item["icon"],
                    "title": text(item.get("title"), 100, "item title"),
                    "body": text(item.get("body"), 400, "item body"),
                }
                refs = item.get("source_refs", [])
                if not isinstance(refs, list) or len(refs) > 16:
                    raise ValueError("Invalid visual story source references")
                entry["source_refs"] = [
                    text(ref, 60, "source reference") for ref in refs
                ]
                if kind == "steps":
                    entry["status"] = item.get("status", "documented")
                    if entry["status"] not in ("documented", "inferred", "unknown"):
                        raise ValueError(
                            "Steps must distinguish documented, inferred, and unknown relationships"
                        )
                clean[kind].append(entry)
        if bool(clean["cards"]) == bool(clean["steps"]):
            raise ValueError("Each chapter needs either cards or steps")
        story["chapters"].append(clean)
    appendix = markdown[: matches[0].start()] + markdown[matches[0].end() :]
    return story, appendix.strip()


def draw_icon(canvas, name, x, y, size, color):
    """Draw a labeled technology metaphor as vector geometry, not a provider logo."""
    canvas.saveState()
    canvas.translate(x, y)
    canvas.scale(size / 40, size / 40)
    canvas.setStrokeColor(color)
    canvas.setFillColor(color)
    canvas.setLineWidth(2)
    canvas.setLineCap(1)
    canvas.setLineJoin(1)

    def line(points, closed=False, fill=False):
        path = canvas.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        if closed:
            path.close()
        canvas.drawPath(path, stroke=1, fill=int(fill))

    if name == "code":
        line([(13, 30), (3, 20), (13, 10)])
        line([(27, 30), (37, 20), (27, 10)])
        line([(23, 34), (17, 6)])
    elif name == "database":
        canvas.ellipse(5, 26, 35, 37)
        line([(5, 31), (5, 8)])
        line([(35, 31), (35, 8)])
        canvas.arc(5, 3, 35, 14, 180, 180)
        canvas.arc(5, 15, 35, 26, 180, 180)
    elif name == "cloud":
        p = canvas.beginPath()
        p.moveTo(10, 8)
        p.curveTo(-2, 8, -1, 27, 11, 27)
        p.curveTo(12, 43, 34, 41, 34, 26)
        p.curveTo(46, 25, 41, 8, 31, 8)
        p.close()
        canvas.drawPath(p)
    elif name == "container":
        line([(20, 38), (3, 29), (20, 20), (37, 29), (20, 38)])
        line([(3, 29), (3, 11), (20, 2), (37, 11), (37, 29)])
        line([(20, 20), (20, 2)])
    elif name == "document":
        line([(7, 2), (7, 38), (27, 38), (34, 30), (34, 2)], True)
        line([(26, 38), (26, 29), (34, 29)])
        for height in (22, 15, 8):
            line([(13, height), (27, height)])
    elif name == "search":
        canvas.circle(17, 24, 12)
        line([(26, 15), (37, 4)])
    elif name == "shield":
        line([(20, 38), (35, 32), (33, 14), (20, 2), (7, 14), (5, 32)], True)
        line([(11, 21), (18, 14), (29, 27)])
    elif name == "chart":
        line([(3, 37), (3, 3), (38, 3)])
        for x1, height in ((9, 12), (19, 23), (29, 32)):
            canvas.rect(x1, 4, 5, height, stroke=0, fill=1)
    elif name == "layers":
        for offset in (0, 9, 18):
            line(
                [
                    (3, 15 + offset),
                    (20, 24 + offset),
                    (37, 15 + offset),
                    (20, 6 + offset),
                ],
                True,
            )
    elif name == "agent":
        canvas.roundRect(4, 6, 32, 26, 7)
        line([(20, 32), (20, 39)])
        canvas.circle(20, 39, 1.7, fill=1)
        for x1 in (13, 27):
            canvas.circle(x1, 22, 2.5, fill=1)
        line([(13, 13), (27, 13)])
        line([(0, 15), (4, 15)])
        line([(36, 15), (40, 15)])
    else:
        for x1, y1 in ((2, 24), (26, 24), (14, 2)):
            canvas.roundRect(x1, y1, 12, 12, 2)
        line([(14, 30), (26, 30)])
        line([(8, 24), (8, 18), (20, 18), (20, 14)])
        line([(32, 24), (32, 18), (20, 18)])
    canvas.restoreState()


def story_flowables(story, palette, width):
    """Create full-page illustrated chapters with concise captions and explicit evidence states."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import Flowable, Paragraph, PageBreak

    def paragraph(canvas, value, x, top, w, height, size=10, bold=False, color=None):
        for font_size in (size, size - 0.6, size - 1.2, size - 1.8):
            style = ParagraphStyle(
                "StoryText",
                fontName="ReportSansBold" if bold else "ReportSans",
                fontSize=font_size,
                leading=font_size * 1.3,
                textColor=color or palette["ink"],
            )
            p = Paragraph(html.escape(value).replace("\n", "<br/>"), style)
            _, actual = p.wrap(w, height)
            if actual <= height:
                break
        if actual > height:
            raise ValueError(
                "A visual story caption is too long; shorten it before export"
            )
        p.drawOn(canvas, x, top - actual)
        return actual

    class StoryPage(Flowable):
        """Present one idea per page using explanatory icons and a clear visual sequence."""

        def __init__(self, chapter, index):
            super().__init__()
            self.chapter = chapter
            self.index = index
            self.width = width
            self.height = 658

        def draw(self):
            c = self.canv
            ch = self.chapter
            c.setFillColor(palette["accent"])
            c.roundRect(0, 623, 38, 28, 7, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont("ReportSansBold", 12)
            c.drawCentredString(19, 631, f"{self.index + 1:02d}")
            c.setFont("ReportSansBold", 8)
            c.setFillColor(palette["muted"])
            c.drawString(50, 635, "THE TECHNOLOGY STORY")
            paragraph(c, ch["title"], 0, 604, width, 71, 27, True)
            paragraph(
                c,
                story["subtitle"] if self.index == 0 else ch["takeaway"],
                0,
                522,
                width,
                42,
                10.5,
                color=palette["muted"],
            )
            items = ch["cards"] or ch["steps"]
            if ch["cards"]:
                columns = 2 if len(items) > 1 else 1
                rows = math.ceil(len(items) / columns)
                gap = 14
                cw = (width - gap * (columns - 1)) / columns
                height = min(190, (446 - gap * (rows - 1)) / rows)
                for index, card in enumerate(items):
                    x = index % columns * (cw + gap)
                    top = 468 - index // columns * (height + gap)
                    y = top - height
                    c.setFillColor(palette["paper"])
                    c.setStrokeColor(palette["line"])
                    c.roundRect(x, y, cw, height, 10, fill=1, stroke=1)
                    c.setFillColor(palette["tint"])
                    c.circle(x + 30, top - 30, 20, fill=1, stroke=0)
                    draw_icon(c, card["icon"], x + 17, top - 43, 26, palette["accent"])
                    paragraph(
                        c, card["title"], x + 59, top - 16, cw - 70, 43, 11.4, True
                    )
                    paragraph(
                        c, card["body"], x + 13, top - 64, cw - 26, height - 87, 9.4
                    )
                    c.setFillColor(palette["muted"])
                    c.setFont("ReportSans", 7)
                    c.drawString(x + 13, y + 10, " · ".join(card["source_refs"]))
            else:
                row_height = 446 / len(items)
                for index, step in enumerate(items):
                    top = 472 - index * row_height
                    y = top - row_height + 8
                    c.setFillColor(palette["paper"])
                    c.roundRect(0, y, width, row_height - 8, 9, fill=1, stroke=0)
                    color = (
                        palette["second"]
                        if step["status"] != "documented"
                        else palette["accent"]
                    )
                    draw_icon(c, step["icon"], 16, top - 52, 32, color)
                    paragraph(c, step["title"], 66, top - 10, width - 170, 30, 12, True)
                    paragraph(
                        c, step["body"], 66, top - 34, width - 83, row_height - 45, 9.6
                    )
                    c.setFont("ReportSansBold", 6.5)
                    c.setFillColor(color)
                    c.drawRightString(width - 12, top - 19, step["status"].upper())
                    if index < len(items) - 1:
                        c.setStrokeColor(color)
                        c.setDash(3, 2) if step[
                            "status"
                        ] != "documented" else c.setDash()
                        c.line(32, y + 4, 32, y - 8)
                        c.setDash()
            c.setFillColor(palette["second"])
            c.rect(0, 0, width, 3, fill=1, stroke=0)

    output = []
    for index, chapter in enumerate(story["chapters"]):
        output.extend([StoryPage(chapter, index), PageBreak()])
    return output


def story_html(story):
    """Produce an accessible visual summary without scripts, logos, or external assets."""
    shapes = {
        "code": '<path d="M14 8 3 20l11 12M26 8l11 12-11 12M23 4l-6 32"/>',
        "api": '<rect x="2" y="3" width="12" height="12" rx="2"/><rect x="26" y="3" width="12" height="12" rx="2"/><rect x="14" y="26" width="12" height="12" rx="2"/><path d="M14 9h12M8 15v6h12v5M32 15v6H20"/>',
        "database": '<ellipse cx="20" cy="8" rx="15" ry="5"/><path d="M5 8v24c0 7 30 7 30 0V8M5 20c0 7 30 7 30 0"/>',
        "cloud": '<path d="M10 32C-2 32-2 15 10 14c1-15 25-15 26 0 10 1 10 18-2 18Z"/>',
        "container": '<path d="m20 2 17 9v18l-17 9-17-9V11Zm0 18L3 11m17 9 17-9M20 20v18"/>',
        "agent": '<rect x="4" y="10" width="32" height="26" rx="7"/><path d="M20 10V2M13 29h14M0 22h4m32 0h4"/><circle cx="13" cy="20" r="2"/><circle cx="27" cy="20" r="2"/>',
        "document": '<path d="M7 2h20l7 8v28H7Zm19 0v9h8M13 18h14M13 25h14M13 32h14"/>',
        "search": '<circle cx="17" cy="16" r="12"/><path d="m26 25 12 12"/>',
        "shield": '<path d="m20 2 15 6-2 18-13 12L7 26 5 8Zm-9 17 7 7 11-13"/>',
        "chart": '<path d="M3 2v35h35M10 36V25h5v11M20 36V14h5v22M30 36V4h5v32"/>',
        "layers": '<path d="m3 12 17-9 17 9-17 9Zm0 9 17 9 17-9M3 30l17 9 17-9"/>',
    }

    def icon(name):
        """Embed the same functional metaphor used in PDF cards as safe inline SVG."""
        shape = shapes.get(name, shapes["api"])
        return f'<svg viewBox="0 0 40 40" width="44" height="44" style="width:44px;height:44px;color:#087f8c" role="img" aria-label="{html.escape(name)}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{shape}</svg>'

    sections = []
    for index, chapter in enumerate(story["chapters"], 1):
        cards = chapter["cards"] or chapter["steps"]
        items = "".join(
            '<article style="padding:20px;border:1px solid #bfd5df;border-radius:14px;background:#f1f8fb">'
            + icon(item["icon"])
            + "<h3>"
            + html.escape(item["title"])
            + "</h3><p>"
            + html.escape(item["body"])
            + "</p><small>"
            + html.escape(" · ".join(item["source_refs"]) or item.get("status", ""))
            + "</small></article>"
            for item in cards
        )
        sections.append(
            f'<section><p>CHAPTER {index:02d}</p><h2>{html.escape(chapter["title"])}</h2><p>{html.escape(chapter["takeaway"])}</p><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px">{items}</div></section>'
        )
    return "".join(sections)
