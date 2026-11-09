"""Validate explicit presentation choices without changing the underlying evidence."""

import importlib.util

DEFAULT_DELIVERABLE = {"format": "markdown", "style": "editorial"}
DELIVERY_MIME_TYPES = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}
DELIVERY_EXTENSIONS = {"pdf": "pdf", "html": "html", "markdown": "md"}
MAX_DELIVERY_SOURCE_BYTES = 512 * 1024
MAX_DELIVERY_BYTES = 16 * 1024 * 1024


def visual_story_instructions():
    """Describe the bounded visual brief that accompanies the full source report."""
    return (
        "Create a visual explanation that tells a coherent story, not a decorated inventory. "
        "Begin with what the reader needs to understand, explain the technology's role, "
        "show a concrete workflow, and end with the key findings and uncertainties. "
        "Return the complete Markdown evidence report and one fenced block labeled story "
        "containing a valid JSON object. The app renders this block as illustrated opening "
        "pages and keeps the detailed report as the evidence appendix. Use this schema: "
        '{"title":"short title","subtitle":"context", "chapters":[{"title":"chapter",'
        '"takeaway":"one clear point","cards":[{"icon":"database","title":"technology",'
        '"body":"what it does and why it matters","source_refs":["existing source reference"]}],'
        '"steps":[{"title":"step","body":"what happens","icon":"document",'
        '"status":"documented"}]}]}. '
        "Use 1–6 chapters. Each chapter needs 1–6 cards or 1–6 steps; the unused array "
        "may be empty. Keep titles at most 100 characters, subtitle at most 300, takeaways "
        "at most 240, and card/step body text at most 400. Icon names must be one of "
        "code, api, database, cloud, container, workflow, agent, document, search, shield, "
        "chart, layers. Step status must be documented, inferred, or unknown. "
        "Use source_refs that already support the report's claims. Icons are explanatory "
        "symbols, not evidence of integrations. Never invent data flows, supported products, "
        "production status, or capabilities to make the story look complete. Label inferred "
        "and unknown handoffs explicitly. Do not include HTML, asset URLs, or executable code "
        "in the story block. Keep the actual findings, limitations, and source references "
        "in the complete report after the visual brief."
    )


def parse_deliverable(value=None):
    """Accept only supported formats and visual styles, preserving legacy Markdown."""
    if value is None:
        return dict(DEFAULT_DELIVERABLE)
    if not isinstance(value, dict) or set(value) - {"format", "style", "layout"}:
        raise ValueError("Choose a supported deliverable format and visual style")
    result = {**DEFAULT_DELIVERABLE, **value}
    if result["format"] not in DELIVERY_MIME_TYPES:
        raise ValueError("Choose PDF, HTML, or Markdown for the deliverable")
    if result["style"] not in ("editorial", "ocean", "neon"):
        raise ValueError("Choose Editorial, Ocean, or Neon for the deliverable style")
    if result.get("layout", "reference") not in ("reference", "visual_story"):
        raise ValueError("Choose Visual story or Reference for the report layout")
    return result


def require_renderer(settings):
    """Reject unavailable PDF support before paid model tasks can begin."""
    if settings["format"] == "pdf" and importlib.util.find_spec("reportlab") is None:
        raise ValueError(
            "PDF delivery requires ReportLab. Install the application's requirements.txt, "
            "or choose HTML or Markdown before starting this goal."
        )


def final_artifact_tasks(tasks):
    """Find the final available artifacts across dependency chains, including inspectors."""
    artifacts = [task for task in tasks if (task.get("receipt") or {}).get("artifact")]
    by_id = {task["id"]: task for task in tasks}
    consumed = set()
    for task in artifacts:
        pending = list(task["dependencies"])
        seen = {task["id"]}
        while pending:
            source_id = pending.pop()
            if source_id in seen:
                continue
            seen.add(source_id)
            consumed.add(source_id)
            source = by_id.get(source_id)
            if source:
                pending.extend(source["dependencies"])
    finals, paths = [], set()
    for task in artifacts:
        path = task["receipt"]["artifact"]
        if task["id"] not in consumed and path not in paths:
            finals.append(task)
            paths.add(path)
    return finals
