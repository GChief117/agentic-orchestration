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


def parse_deliverable(value=None):
    """Accept only supported formats and visual styles, preserving legacy Markdown."""
    if value is None:
        return dict(DEFAULT_DELIVERABLE)
    if not isinstance(value, dict) or set(value) - {"format", "style"}:
        raise ValueError("Choose a supported deliverable format and visual style")
    result = {**DEFAULT_DELIVERABLE, **value}
    if result["format"] not in DELIVERY_MIME_TYPES:
        raise ValueError("Choose PDF, HTML, or Markdown for the deliverable")
    if result["style"] not in ("editorial", "ocean", "neon"):
        raise ValueError("Choose Editorial, Ocean, or Neon for the deliverable style")
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
