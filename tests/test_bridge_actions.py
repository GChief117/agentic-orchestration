"""Bridge mutations are fixed actions with configured scope, exercised without SSH."""

import base64
from contextlib import contextmanager
import getpass
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from orchestration.bridge_actions import BridgeActions, _REMOTE_ACTIONS
from orchestration.postgres_setup import SETUP_CAPABILITIES
from orchestration.aws_access import AWS_CAPABILITIES


def remote_namespace():
    """Load fixed helper definitions without invoking their remote entry point."""
    namespace = {}
    source = _REMOTE_ACTIONS.split("\ntry:\n    print(json.dumps(main()))")[0]
    exec(compile(source, "bridge-actions", "exec"), namespace)
    return namespace


@contextmanager
def installed_workspace_apps(namespace, runner):
    """Fake installed macOS apps while retaining real fixture path validation."""
    original_file, original_directory = Path.is_file, Path.is_dir
    with patch.object(
        Path,
        "is_file",
        lambda path: str(path) == "/opt/homebrew/bin/code" or original_file(path),
    ), patch.object(
        Path,
        "is_dir",
        lambda path: str(path) == "/Applications/pgAdmin 4.app"
        or original_directory(path),
    ), patch.object(
        namespace["os"], "access", return_value=True
    ), patch.object(
        namespace["subprocess"], "run", side_effect=runner
    ):
        yield


class FakeBridges:
    """Represent the private operator configuration used in these tests."""

    def __init__(self):
        self.entry = {
            "hostname": "test.local",
            "username": "tester",
            "root": "/projects",
            "repositories": ["sample"],
            "agent_command": "/operator/bridge",
            "notes_enabled": True,
            "demo_directory": "/demos/sample",
            "editor_workspace": "/projects/Polaris.code-workspace",
            "editor_profile": "Polaris",
            "postgres_enabled": True,
        }

    def _configuration(self, project_id):
        return self.entry if project_id == "project" else None


class BridgeActionTests(unittest.TestCase):
    def setUp(self):
        """Prepare a tiny runtime tree and an injected non-network runner."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in (
            "app.py",
            "requirements.txt",
            "orchestration/engine.py",
            "frontend/index.html",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("runtime fixture")
        self.bridges = FakeBridges()
        self.calls = []
        self.adapter = BridgeActions(
            self.bridges, application_root=self.root, runner=self.runner
        )

    def runner(self, args, data, directory, timeout, cancelled):
        """Return a target-shaped result while recording only test payloads."""
        self.calls.append((args, json.loads(data), directory, timeout, cancelled))
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "identity": {"hostname": "test.local", "username": "tester"},
                    "result": {"note_id": "note-test", "title": "Draft"},
                }
            ).encode(),
            b"",
        )

    def test_capabilities_require_explicit_private_configuration(self):
        self.assertTrue(self.adapter.ready("project", "bridge.notes.create"))
        self.assertTrue(self.adapter.ready("project", "bridge.demo.launch"))
        self.assertTrue(self.adapter.ready("project", "bridge.workspace.open"))
        for capability in SETUP_CAPABILITIES:
            self.assertTrue(self.adapter.ready("project", capability))
        self.assertFalse(self.adapter.ready("project", "shell.run"))
        self.assertFalse(self.adapter.ready("missing", "bridge.notes.create"))
        self.bridges.entry["notes_enabled"] = False
        with self.assertRaises(PermissionError):
            self.adapter.execute(
                "project", "bridge.notes.create", {"title": "Draft", "content": "Hi"}
            )
        self.assertEqual(self.calls, [])

    def test_workspace_capability_requires_editor_and_execution_bridge(self):
        self.bridges.entry.pop("editor_workspace")
        self.assertFalse(self.adapter.ready("project", "bridge.workspace.open"))
        self.bridges.entry["editor_workspace"] = "/projects/Polaris.code-workspace"
        self.bridges.entry.pop("agent_command")
        self.assertFalse(self.adapter.ready("project", "bridge.workspace.open"))

    def test_aws_actions_require_opt_in_and_reject_task_commands(self):
        for capability in AWS_CAPABILITIES:
            with self.subTest(capability=capability):
                self.assertFalse(self.adapter.ready("project", capability))
                self.bridges.entry["aws_login_enabled"] = True
                self.assertTrue(self.adapter.ready("project", capability))
                with self.assertRaises(ValueError):
                    self.adapter.execute("project", capability, {"profile": "other"})
                self.adapter.execute("project", capability, {})
                payload = self.calls[-1][1]
                self.assertTrue(payload["aws_login_enabled"])
                self.assertEqual(payload["parameters"], {})
                del self.bridges.entry["aws_login_enabled"]

    def test_database_setup_requires_explicit_flag_and_execution_bridge(self):
        for flag in (None, False, "true", 1):
            self.bridges.entry["postgres_enabled"] = flag
            for capability in SETUP_CAPABILITIES:
                with self.subTest(capability=capability, flag=flag):
                    self.assertFalse(self.adapter.ready("project", capability))
                    with self.assertRaises(PermissionError):
                        self.adapter.execute("project", capability, {})
        self.bridges.entry["postgres_enabled"] = True
        self.bridges.entry.pop("agent_command")
        for capability in SETUP_CAPABILITIES:
            self.assertFalse(self.adapter.ready("project", capability))
        self.assertEqual(self.calls, [])

    def test_database_setup_uses_fixed_empty_payload_without_a_demo_archive(self):
        for capability in SETUP_CAPABILITIES:
            progress = []
            with patch.object(self.adapter, "_archive", side_effect=AssertionError):
                self.adapter.execute(
                    "project", capability, {}, progress=progress.append
                )
            payload = self.calls[-1][1]
            self.assertEqual(payload["action"], capability)
            self.assertEqual(payload["parameters"], {})
            self.assertIs(payload["postgres_enabled"], True)
            self.assertNotIn("archive", payload)
            self.assertNotIn("project_definition", payload)
            self.assertEqual(progress[0]["phase"], "working")
            self.assertIn("connected Mac", progress[0]["message"])

    def test_database_setup_rejects_task_parameters_before_the_bridge(self):
        for capability in SETUP_CAPABILITIES:
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                self.adapter.execute(
                    "project", capability, {"host": "outside.example.com"}
                )
        self.assertEqual(self.calls, [])

    def test_fixed_remote_program_dispatches_database_setup_after_identity_check(self):
        for capability, function in (
            ("bridge.postgres.prepare", "postgres_prepare"),
            ("bridge.pgadmin.register", "pgadmin_register"),
        ):
            namespace = remote_namespace()
            calls = []
            namespace[function] = lambda payload: calls.append(payload) or {"ok": True}
            payload = {
                "hostname": namespace["socket"].gethostname(),
                "username": getpass.getuser(),
                "action": capability,
                "postgres_enabled": True,
                "parameters": {},
            }
            namespace["sys"] = types.SimpleNamespace(
                stdin=types.SimpleNamespace(
                    buffer=io.BytesIO(json.dumps(payload).encode())
                )
            )
            with self.subTest(capability=capability):
                self.assertEqual(namespace["main"]()["result"], {"ok": True})
                self.assertEqual(calls, [payload])
                payload["hostname"] = "wrong-target"
                namespace["sys"].stdin.buffer = io.BytesIO(json.dumps(payload).encode())
                with self.assertRaisesRegex(ValueError, "identity"):
                    namespace["main"]()
                self.assertEqual(len(calls), 1)

    def test_workspace_launch_uses_configured_paths_without_packaging_a_demo(self):
        progress = []
        with patch.object(self.adapter, "_archive", side_effect=AssertionError):
            self.adapter.execute(
                "project", "bridge.workspace.open", {}, progress=progress.append
            )
        _, payload, _, _, _ = self.calls[0]
        self.assertEqual(payload["parameters"], {"apps": ["vscode", "pgadmin"]})
        self.assertEqual(
            payload["editor_workspace"], "/projects/Polaris.code-workspace"
        )
        self.assertEqual(payload["editor_profile"], "Polaris")
        self.assertNotIn("archive", payload)
        self.assertNotIn("project_definition", payload)
        self.assertIn("workspace applications", progress[0]["message"])

    def test_workspace_apps_are_a_strict_allowlist_before_any_remote_call(self):
        for parameters in (
            {"apps": []},
            {"apps": "vscode"},
            {"apps": ["vscode", "vscode"]},
            {"apps": ["Terminal"]},
            {"apps": ["vscode; open Terminal"]},
            {"apps": [{}]},
            {"apps": ["vscode"], "editor_workspace": "/other/workspace"},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.adapter.execute("project", "bridge.workspace.open", parameters)
        self.assertEqual(self.calls, [])

    def test_workspace_remote_launch_and_verified_window_state(self):
        namespace = remote_namespace()
        workspace = self.root / "Polaris.code-workspace"
        workspace.write_text('{"folders": []}')
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            output = ""
            if args[0] == "/usr/bin/osascript":
                output = "vscode\ttrue\ttrue\tfalse\t1\ttrue\ttrue\npgadmin\ttrue\ttrue\ttrue\t1\ttrue\ttrue\n"
            return subprocess.CompletedProcess(args, 0, output, "")

        with installed_workspace_apps(namespace, run):
            result = namespace["workspace_open"](
                {
                    "root": str(self.root),
                    "editor_workspace": str(workspace),
                    "editor_profile": "Polaris",
                    "parameters": {},
                }
            )
        self.assertEqual(result["opened_apps"], ["vscode", "pgadmin"])
        self.assertEqual(
            calls[0][0],
            [
                "/opt/homebrew/bin/code",
                "--profile",
                "Polaris",
                "--reuse-window",
                str(workspace),
            ],
        )
        self.assertEqual(
            calls[1][0], ["/usr/bin/open", "-a", "/Applications/pgAdmin 4.app"]
        )
        self.assertEqual(calls[2][0], ["/usr/bin/osascript", "-"])
        self.assertTrue(result["arranged"])
        self.assertTrue(result["windows"]["vscode"]["visible"])
        self.assertFalse(result["windows"]["vscode"]["frontmost"])
        self.assertTrue(result["windows"]["pgadmin"]["frontmost"])
        self.assertNotIn("arrangement_error", result)
        script = calls[2][1]["input"]
        self.assertIn('tell application "System Events"', script)
        self.assertIn('tell application "Finder"', script)
        self.assertIn("set position of window 1", script)

    def test_workspace_visibility_failure_does_not_undo_successful_launch(self):
        workspace = self.root / "Polaris.code-workspace"
        workspace.write_text("{}")
        for status in ("denied", "timeout", "partial"):
            namespace = remote_namespace()

            def run(args, **kwargs):
                if args[0] != "/usr/bin/osascript":
                    return subprocess.CompletedProcess(args, 0, "", "")
                if status == "timeout":
                    raise subprocess.TimeoutExpired(args, 25)
                output = (
                    "vscode\ttrue\ttrue\ttrue\tunknown\tfalse\tunknown\n"
                    if status == "partial"
                    else ""
                )
                return subprocess.CompletedProcess(args, status == "denied", output, "")

            with self.subTest(status=status), installed_workspace_apps(namespace, run):
                result = namespace["workspace_open"](
                    {
                        "root": str(self.root),
                        "editor_workspace": str(workspace),
                        "parameters": {"apps": ["vscode"]},
                    }
                )
            self.assertEqual(result["opened_apps"], ["vscode"])
            self.assertEqual(result["editor_profile"], "Default")
            self.assertFalse(result["arranged"])
            self.assertIn("arrangement_error", result)
            self.assertIsNone(result["windows"]["vscode"]["window_count"])
            if status != "partial":
                self.assertIsNone(result["windows"]["vscode"]["visible"])

    def test_workspace_rejects_redirected_or_outside_files_before_launch(self):
        namespace = remote_namespace()
        approved = self.root / "approved"
        approved.mkdir()
        outside = self.root / "outside.code-workspace"
        outside.write_text("{}")
        linked = approved / "linked.code-workspace"
        linked.symlink_to(outside)
        invalid_suffix = approved / "workspace.json"
        invalid_suffix.write_text("{}")
        with patch.object(namespace["subprocess"], "run") as run:
            for workspace in (
                outside,
                linked,
                invalid_suffix,
                Path("relative.code-workspace"),
            ):
                with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                    namespace["workspace_open"](
                        {
                            "root": str(approved),
                            "editor_workspace": str(workspace),
                            "parameters": {},
                        }
                    )
        run.assert_not_called()

    def test_workspace_remote_revalidates_apps_and_profile(self):
        namespace = remote_namespace()
        workspace = self.root / "Polaris.code-workspace"
        workspace.write_text("{}")
        payload = {
            "root": str(self.root),
            "editor_workspace": str(workspace),
            "parameters": {},
        }
        with patch.object(namespace["subprocess"], "run") as run:
            for changes in (
                {"parameters": {"apps": ["Terminal"]}},
                {"parameters": {"apps": ["vscode", "vscode"]}},
                {"parameters": {"command": "arbitrary"}},
                {"editor_profile": "profile\ncommand"},
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    namespace["workspace_open"]({**payload, **changes})
        run.assert_not_called()

    def test_note_content_is_stdin_data_not_part_of_the_remote_command(self):
        content = 'Hi "Jason"; $(touch danger)\nThis is a draft.'
        result = self.adapter.execute(
            "project",
            "bridge.notes.create",
            {"title": "Draft", "content": content},
            operation_id="task-123",
        )
        args, payload, _, timeout, _ = self.calls[0]
        outer = shlex.split(args[1])
        self.assertEqual(outer[:2], ["/bin/zsh", "-lc"])
        self.assertEqual(shlex.split(outer[2])[:3], ["exec", "python3", "-c"])
        self.assertNotIn(content, args[1])
        self.assertEqual(payload["parameters"]["content"], content)
        self.assertEqual(payload["operation_id"], "task-123")
        self.assertEqual(timeout, 120)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["note_id"], "note-test")

    def test_wrong_target_and_remote_failures_are_not_success(self):
        def wrong(*args):
            return subprocess.CompletedProcess(
                [],
                0,
                b'{"identity":{"hostname":"other","username":"tester"},"result":{}}',
                b"",
            )

        self.adapter.runner = wrong
        with self.assertRaisesRegex(ValueError, "unverified"):
            self.adapter.execute(
                "project", "bridge.notes.create", {"title": "Draft", "content": "Hi"}
            )
        self.adapter.runner = lambda *args: subprocess.CompletedProcess(
            [], 1, b'{"error":"Notes is unavailable"}', b"private diagnostics"
        )
        with self.assertRaisesRegex(ValueError, "Notes is unavailable"):
            self.adapter.execute(
                "project", "bridge.notes.create", {"title": "Draft", "content": "Hi"}
            )

    def test_demo_package_excludes_credentials_state_tests_and_node_modules(self):
        for name in (
            ".runtime/secret",
            "frontend/node_modules/pkg/secret.js",
            "frontend/tests/private.test.js",
            "orchestration/__pycache__/cached.pyc",
            ".env",
            "tests/test_private.py",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not travel")
        encoded = self.adapter._archive()
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "app.py",
                    "requirements.txt",
                    "orchestration/engine.py",
                    "frontend/index.html",
                },
            )
        self.adapter.execute(
            "project",
            "bridge.demo.launch",
            {"port": 8765},
            project_definition={"name": "Demo"},
            shared_context={"entries": []},
        )
        self.assertIn("archive", self.calls[0][1])
        self.assertEqual(self.calls[0][1]["demo_directory"], "/demos/sample")
        self.assertEqual(
            self.calls[0][1]["editor_workspace"], "/projects/Polaris.code-workspace"
        )
        self.assertEqual(self.calls[0][1]["editor_profile"], "Polaris")
        self.assertIs(self.calls[0][1]["postgres_enabled"], True)

    def test_identity_is_verified_before_any_mutating_action(self):
        namespace = remote_namespace()
        payload = {
            "hostname": "not-this-host",
            "username": getpass.getuser(),
            "action": "bridge.notes.create",
        }
        namespace["sys"] = types.SimpleNamespace(
            stdin=types.SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
        )
        namespace["notes_create"] = lambda value: self.fail("mutation was reached")
        with self.assertRaisesRegex(ValueError, "identity"):
            namespace["main"]()

    def test_note_escaping_and_idempotent_receipt_do_not_send_messages(self):
        namespace = remote_namespace()
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "note-test\n", "")

        payload = {
            "operation_id": "task-123",
            "parameters": {
                "title": "Draft",
                "content": '<b>Jason</b> "quote"\\path\nSecond line',
            },
        }
        with patch.object(Path, "home", return_value=self.root), patch.object(
            namespace["subprocess"], "run", side_effect=run
        ):
            first = namespace["notes_create"](payload)
            second = namespace["notes_create"](payload)
        self.assertEqual(first["note_id"], "note-test")
        self.assertTrue(second["reused"])
        self.assertEqual(len(calls), 1)
        script = calls[0][1]["input"]
        self.assertIn("&lt;b&gt;Jason&lt;/b&gt;", script)
        self.assertIn("&quot;quote&quot;", script)
        self.assertEqual(namespace["apple_string"]('a"b\\c\nd'), r'"a\"b\\c\nd"')
        self.assertIn("make new note", script)
        self.assertNotIn("Mail", script)
        self.assertNotIn("Messages", script)

    def test_only_app_owned_note_with_same_title_can_be_updated(self):
        namespace = remote_namespace()
        calls = []

        def run(args, **kwargs):
            calls.append(kwargs["input"])
            return subprocess.CompletedProcess(args, 0, "note-test\n", "")

        create = {
            "operation_id": "create",
            "parameters": {"title": "Draft", "content": "Initial message"},
        }
        update = {
            "operation_id": "update",
            "parameters": {
                "title": "Draft",
                "content": "Verified demo is running",
                "note_id": "note-test",
            },
        }
        with patch.object(Path, "home", return_value=self.root), patch.object(
            namespace["subprocess"], "run", side_effect=run
        ):
            namespace["notes_create"](create)
            result = namespace["notes_create"](update)
            again = namespace["notes_create"](update)
            for note_id, title in (
                ("someone-elses-note", "Draft"),
                ("note-test", "Different title"),
            ):
                bad = {
                    "operation_id": "bad",
                    "parameters": {
                        "title": title,
                        "content": "Replacement",
                        "note_id": note_id,
                    },
                }
                with self.assertRaisesRegex(ValueError, "previously created"):
                    namespace["notes_create"](bad)
        self.assertTrue(result["updated"])
        self.assertTrue(again["updated"])
        self.assertTrue(again["reused"])
        self.assertEqual(len(calls), 2)
        self.assertIn("set body of targetNote", calls[1])
        self.assertIn("name of targetNote is not", calls[1])
        self.assertNotIn("make new note", calls[1])

    def test_note_update_rejects_invalid_identity_before_the_bridge(self):
        for identity in ("", "bad\nidentity", 123):
            with self.subTest(identity=identity), self.assertRaisesRegex(
                ValueError, "identity"
            ):
                self.adapter.execute(
                    "project",
                    "bridge.notes.create",
                    {"title": "Draft", "content": "Text", "note_id": identity},
                )
        self.assertEqual(self.calls, [])

    def test_demo_idle_guard_uses_auth_and_refuses_active_work(self):
        namespace = remote_namespace()
        demo = self.root / "demo"
        data = demo / ".runtime/demo"
        data.mkdir(parents=True)
        (data / "api-token").write_text("private-session-token")
        requests = []

        class Response:
            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return json.dumps(self.value).encode()

        states = [
            {"active_executions": 1, "tasks": [], "goals": []},
            {"active_executions": 0, "tasks": [{"state": "queued"}], "goals": []},
            {
                "active_executions": 0,
                "tasks": [],
                "goals": [{"delivery": {"state": "rendering"}}],
            },
            {"active_executions": 0, "tasks": [{"state": "accepted"}], "goals": []},
        ]

        def fetch(request, **kwargs):
            requests.append(request)
            return Response(states.pop(0))

        with patch.object(namespace["urllib"].request, "urlopen", side_effect=fetch):
            for _ in range(3):
                with self.assertRaisesRegex(ValueError, "active or queued"):
                    namespace["ensure_demo_idle"]({"port": 8765}, demo)
            namespace["ensure_demo_idle"]({"port": 8765}, demo)
        self.assertEqual(requests[0].full_url, "http://127.0.0.1:8765/api/state")
        self.assertEqual(
            requests[0].get_header("Authorization"), "Bearer private-session-token"
        )

    def test_demo_idle_guard_fails_closed_when_session_or_state_is_unavailable(self):
        namespace = remote_namespace()
        demo = self.root / "demo"
        data = demo / ".runtime/demo"
        data.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "session"):
            namespace["ensure_demo_idle"]({"port": 8765}, demo)
        (data / "api-token").write_text("private-session-token")
        with patch.object(
            namespace["urllib"].request, "urlopen", side_effect=OSError("offline")
        ):
            with self.assertRaisesRegex(ValueError, "left running"):
                namespace["ensure_demo_idle"]({"port": 8765}, demo)

    def test_demo_refuses_unmanaged_existing_content(self):
        namespace = remote_namespace()
        demo = self.root / "demo"
        demo.mkdir()
        personal = demo / "personal.txt"
        personal.write_text("keep me")
        repositories = self.root / "repositories"
        repositories.mkdir()
        payload = {
            "demo_directory": str(demo),
            "root": str(repositories),
            "repositories": ["sample"],
            "archive": self.adapter._archive(),
            "parameters": {},
        }
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            namespace["demo_launch"](payload)
        self.assertEqual(personal.read_text(), "keep me")

    def test_demo_refuses_source_repository_destination_and_archive_traversal(self):
        namespace = remote_namespace()
        repositories = self.root / "repositories"
        repositories.mkdir()
        payload = {
            "demo_directory": str(repositories / "sample"),
            "root": str(repositories),
            "repositories": ["sample"],
        }
        with self.assertRaisesRegex(ValueError, "separate"):
            namespace["demo_launch"](payload)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("../outside.py", "no")
        with self.assertRaisesRegex(ValueError, "invalid path"):
            namespace["archive_files"](base64.b64encode(data.getvalue()).decode())

    def test_demo_version_check_precedes_any_installation(self):
        namespace = remote_namespace()
        payload = {
            "hostname": namespace["socket"].gethostname(),
            "username": getpass.getuser(),
            "action": "bridge.demo.launch",
        }
        namespace["sys"] = types.SimpleNamespace(
            stdin=types.SimpleNamespace(
                buffer=io.BytesIO(json.dumps(payload).encode())
            ),
            version_info=(3, 9),
        )
        namespace["demo_launch"] = lambda value: self.fail(
            "Old Python reached installation"
        )
        with self.assertRaisesRegex(ValueError, "Python 3.10"):
            namespace["main"]()

    def test_partial_installation_is_owned_and_can_be_retried_after_seed_failure(self):
        namespace = remote_namespace()
        demo = self.root / "isolated-demo"
        repositories = self.root / "repositories"
        repositories.mkdir()
        python = demo / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(Path(sys.executable).resolve())
        payload = {
            "demo_directory": str(demo),
            "root": str(repositories),
            "repositories": ["sample"],
            "archive": self.adapter._archive(),
            "parameters": {"port": 49389},
            "project_definition": {},
        }

        class FailingEngine:
            def __init__(self, *args, **kwargs):
                raise ValueError("simulated seed failure")

        fake = types.SimpleNamespace(Engine=FailingEngine)
        original_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {"orchestration.engine": fake}), patch.dict(
                namespace["os"].environ
            ), patch.object(
                namespace["subprocess"],
                "run",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ):
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "simulated seed failure"):
                        namespace["demo_launch"](payload)
                    record = json.loads(
                        (demo / ".agentic-demo-manifest.json").read_text()
                    )
                    self.assertEqual(record["state"], "installed")
                    self.assertIn("app.py", record["files"])
        finally:
            sys.path[:] = original_path


if __name__ == "__main__":
    unittest.main()
