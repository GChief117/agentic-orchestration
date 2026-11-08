#!/usr/bin/env python3
"""Serve the backend JSON API: python3 app.py."""

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse

from orchestration.engine import Engine

ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 262144
MAX_ARTIFACT_BYTES = 65536
FRONTEND = ROOT / "frontend"
STATIC_ASSETS = {
    **{
        f"/assets/styles/{name}.css": (
            FRONTEND / "styles" / f"{name}.css",
            "text/css; charset=utf-8",
        )
        for name in (
            "base",
            "layout",
            "projects",
            "work",
            "forms",
            "organization",
            "project-setup",
            "responsive",
            "themes",
        )
    },
    "/assets/styles.css": (FRONTEND / "styles.css", "text/css; charset=utf-8"),
    "/assets/favicon.svg": (FRONTEND / "favicon.svg", "image/svg+xml"),
    **{
        f"/assets/{name}.js": (
            FRONTEND / "src" / f"{name}.js",
            "text/javascript; charset=utf-8",
        )
        for name in (
            "main",
            "app",
            "api",
            "domain",
            "views",
            "forms",
            "form-controls",
            "project-form",
            "presets",
            "organization",
            "speech",
            "organization-editor",
            "team-form",
            "provider-form",
            "import-form",
            "appearance",
            "branding",
        )
    },
}


def make_handler(engine, token):
    """Create an HTTP handler bound to one engine and its private session token.

    The returned class serves the local frontend and authenticated JSON API.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # HTTP logs must not capture request credentials or artifact contents.
            """Suppress request logging so headers and artifact content are not recorded."""
            pass

        @property
        def origin(self):
            """Return the canonical loopback origin for this running HTTP server."""
            return f"http://127.0.0.1:{self.server.server_port}"

        def send(self, status, value, content_type="application/json"):
            """Write a JSON or byte response with privacy and content-security headers.

            status is an HTTP code; content_type selects JSON encoding or raw bytes.
            """
            body = (
                json.dumps(value).encode()
                if content_type == "application/json"
                else value
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted_host(self):
            """Return whether the request Host matches this exact loopback server."""
            return self.headers.get("Host") == urlparse(self.origin).netloc

        def authorized(self):
            """Check the request host, optional origin, and constant-time bearer token."""
            origin = self.headers.get("Origin")
            return (
                self.trusted_host()
                and (not origin or origin == self.origin)
                and secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token
                )
            )

        def do_GET(self):
            """Route frontend assets and reads; require authorization for API records.

            Only the local HTML shell receives the session bootstrap credential.
            """
            if not self.trusted_host():
                self.send(HTTPStatus.FORBIDDEN, {"error": "Invalid host"})
                return

            route = urlparse(self.path).path
            if route == "/":
                origin = self.headers.get("Origin")
                if origin and origin != self.origin:
                    self.send(HTTPStatus.FORBIDDEN, {"error": "Invalid origin"})
                    return
                page = (
                    (FRONTEND / "index.html")
                    .read_text()
                    .replace("__ORCHESTRATION_SESSION__", escape(token, quote=True))
                )
                self.send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
            elif route in STATIC_ASSETS:
                path, content_type = STATIC_ASSETS[route]
                self.send(HTTPStatus.OK, path.read_bytes(), content_type)
            elif not self.authorized():
                self.send(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Local session authorization required"},
                )
            elif route == "/api/projects":
                state = engine.snapshot()
                self.send(
                    HTTPStatus.OK,
                    {"projects": state["projects"], "agents": state["agents"]},
                )
            elif route == "/api/projects/scan-roots":
                self.send(HTTPStatus.OK, {"roots": engine.discovery.roots()})
            elif route == "/api/providers":
                self.send(HTTPStatus.OK, {"providers": engine.providers.list()})
            elif route == "/api/state":
                self.send(HTTPStatus.OK, engine.snapshot())
            elif route.startswith("/api/artifact/"):
                self.serve_artifact(route.removeprefix("/api/artifact/"))
            else:
                self.send(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def serve_artifact(self, task_id):
            """Return bounded text for the artifact referenced by a known task receipt.

            Task IDs resolve through stored records rather than arbitrary client paths.
            """
            task = next(
                (task for task in engine.snapshot()["tasks"] if task["id"] == task_id),
                None,
            )
            receipt = task.get("receipt") if task else None
            if not receipt or not receipt.get("artifact"):
                self.send(HTTPStatus.NOT_FOUND, {"error": "Artifact not found"})
                return
            try:
                path = engine.connection.path(receipt["artifact"])
                if path.stat().st_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("Artifact too large")
                result = {"content": path.read_text(), "name": receipt["artifact"]}
            except (OSError, ValueError, PermissionError):
                self.send(HTTPStatus.NOT_FOUND, {"error": "Artifact unavailable"})
                return
            self.send(HTTPStatus.OK, result)

        def read_body(self):
            """Decode a size-limited JSON object, raising ValueError for invalid bodies."""
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return data

        def do_POST(self):
            """Validate and route project/team changes, goal submission, or cancellation.

            Input and permission errors become JSON 400 responses.
            """
            if not self.authorized():
                self.send(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Local session authorization required"},
                )
                return
            try:
                data = self.read_body()
                route = urlparse(self.path).path
                if route == "/api/projects/scan":
                    self.send(
                        HTTPStatus.OK,
                        engine.scan_projects(
                            data.get("root"), data.get("include_root", False)
                        ),
                    )
                elif route == "/api/projects/import":
                    self.send(
                        HTTPStatus.CREATED,
                        {
                            "projects": engine.import_projects(
                                data.get("scan_id"), data.get("candidate_ids")
                            )
                        },
                    )
                elif route == "/api/providers/sign-in":
                    self.send(
                        HTTPStatus.OK, engine.providers.sign_in(data.get("provider"))
                    )
                elif route == "/api/projects":
                    project_id = engine.create_project(data)
                    self.send(HTTPStatus.CREATED, {"id": project_id})
                elif route.startswith("/api/projects/") and route.endswith("/team"):
                    project_id = route.removeprefix("/api/projects/").removesuffix(
                        "/team"
                    )
                    engine.update_team(project_id, data)
                    self.send(HTTPStatus.OK, {"id": project_id})
                elif route == "/api/goals":
                    goal_id = engine.submit(
                        data.get("project_id"), data.get("title"), data.get("tasks")
                    )
                    self.send(HTTPStatus.CREATED, {"id": goal_id})
                elif route == "/api/cancel":
                    goal_id = data.get("id")
                    if not isinstance(goal_id, str):
                        raise ValueError("Goal ID is required")
                    engine.cancel_goal(goal_id)
                    self.send(HTTPStatus.OK, {"ok": True})
                else:
                    self.send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except (ValueError, TypeError, PermissionError) as exc:
                self.send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def main():
    """Parse server settings, persist a private token, and run the loopback service.

    The engine and HTTP socket are closed when the process stops.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".runtime")
    parser.add_argument("--execution-limit", type=int, required=True)
    args = parser.parse_args()
    engine = Engine(args.data_dir, execution_limit=args.execution_limit)
    token = secrets.token_urlsafe(32)
    token_path = args.data_dir / "api-token"
    try:
        # Replaced on every start; never log or commit the credential.
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(token)

        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), make_handler(engine, token)
        )
    except Exception:
        engine.close()
        raise
    engine.start()
    print(f"Backend API: http://127.0.0.1:{server.server_port}", flush=True)
    print(
        "Local deterministic tasks only; no AI provider or remote bridge connected.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    main()
