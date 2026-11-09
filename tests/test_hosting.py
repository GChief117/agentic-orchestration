"""Exercise portable hosting without external services or installed provider CLIs."""

import base64
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_handler
from orchestration.engine import Engine
from orchestration.server_config import validate_hosting


class HostingConfigurationTests(unittest.TestCase):
    def test_network_binding_requires_explicit_hosted_configuration(self):
        with self.assertRaisesRegex(ValueError, "Local mode"):
            validate_hosting("local", "0.0.0.0", None, None)
        with self.assertRaisesRegex(ValueError, "ACCESS_PASSWORD"):
            validate_hosting("hosted", "0.0.0.0", "https://example.com", None)
        for origin in (
            "http://example.com",
            "https://user@example.com",
            "https://example.com/path",
            "https://example.com?x=1",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_hosting("hosted", "0.0.0.0", origin, "a-long-private-password")
        self.assertEqual(
            validate_hosting(
                "hosted", "0.0.0.0", "https://example.com/", "a-long-private-password"
            ),
            "https://example.com",
        )


class HostedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, execution_limit=2)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(
                self.engine,
                "private-session",
                public_origin="https://workspace.example",
                access_password="a-long-private-password",
                local_features=False,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.basic = (
            "Basic " + base64.b64encode(b"workspace:a-long-private-password").decode()
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.engine.close()
        self.temp.cleanup()

    def request(self, path, authorization=None, body=None, **headers):
        """Make a local request with the same Host and Origin seen behind a TLS proxy."""
        headers = {"Host": "workspace.example", **headers}
        if authorization:
            headers["Authorization"] = authorization
        return urlopen(
            Request(
                self.base + path,
                headers=headers,
                data=json.dumps(body).encode() if body is not None else None,
            )
        )

    def test_html_and_assets_require_password_before_session_is_disclosed(self):
        for path in ("/", "/assets/main.js"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path)
            response = caught.exception
            self.assertEqual(response.code, 401)
            self.assertIn("Basic", response.headers["WWW-Authenticate"])
            self.assertNotIn(b"private-session", response.read())
            response.close()
        with self.request("/", self.basic) as response:
            self.assertIn(b'content="private-session"', response.read())

    def test_health_discloses_no_workspace_and_state_requires_bearer(self):
        with self.request("/healthz") as response:
            self.assertEqual(json.load(response), {"status": "ok"})
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/state", self.basic)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with self.request("/api/state", "Bearer private-session") as response:
            self.assertFalse(json.load(response)["features"]["local_tools"])

    def test_local_capabilities_cannot_execute_in_hosted_mode(self):
        with patch.object(self.engine.discovery, "scan") as scan, patch.object(
            self.engine.providers, "sign_in"
        ) as signin:
            for path, body in (
                ("/api/projects/scan", {"root": "/"}),
                ("/api/projects/import", {}),
                ("/api/providers/sign-in", {"provider": "openai"}),
            ):
                with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                    self.request(path, "Bearer private-session", body)
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
            scan.assert_not_called()
            signin.assert_not_called()
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/providers", "Bearer private-session")
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_wrong_origin_or_host_cannot_use_valid_credentials(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                self.request("/api/state", "Bearer private-session", **headers)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
