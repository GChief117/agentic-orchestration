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
    "/assets/styles.css": (FRONTEND / "styles.css", "text/css; charset=utf-8"),
    "/assets/favicon.svg": (FRONTEND / "favicon.svg", "image/svg+xml"),
    **{
        f"/assets/{name}.js": (
            FRONTEND / "src" / f"{name}.js",
            "text/javascript; charset=utf-8",
        )
        for name in ("main", "app", "api", "domain", "views", "forms")
    },
}


def make_handler(engine, token):
    """Bind one HTTP handler to the runtime and its ephemeral local session."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # HTTP logs must not capture request credentials or artifact contents.
            pass

        @property
        def origin(self):
            return f"http://127.0.0.1:{self.server.server_port}"

        def send(self, status, value, content_type="application/json"):
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
            return self.headers.get("Host") == urlparse(self.origin).netloc

        def authorized(self):
            origin = self.headers.get("Origin")
            return (
                self.trusted_host()
                and (not origin or origin == self.origin)
                and secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token
                )
            )

        def do_GET(self):
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
            elif route == "/api/state":
                self.send(HTTPStatus.OK, engine.snapshot())
            elif route.startswith("/api/artifact/"):
                self.serve_artifact(route.removeprefix("/api/artifact/"))
            else:
                self.send(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def serve_artifact(self, task_id):
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
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return data

        def do_POST(self):
            if not self.authorized():
                self.send(
                    HTTPStatus.FORBIDDEN,
                    {"error": "Local session authorization required"},
                )
                return
            try:
                data = self.read_body()
                route = urlparse(self.path).path
                if route == "/api/projects":
                    project_id = engine.create_project(data)
                    self.send(HTTPStatus.CREATED, {"id": project_id})
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
