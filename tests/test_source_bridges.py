"""Bridge readers use isolated local fixtures; no real network or credentials."""

import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest

from orchestration.source_bridges import SourceBridges


class SourceBridgeTests(unittest.TestCase):
    def setUp(self):
        """Execute the remote reader locally against a temporary repository tree."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.root = self.data / "repositories"
        self.root.mkdir()
        self.repo = self.root / "sample"
        self.repo.mkdir()
        self.wrapper = self.data / "read-only-wrapper"
        self.wrapper.write_text("#!/bin/sh\nexit 1\n")
        self.wrapper.chmod(0o700)
        self.entry = {
            "name": "Test source bridge",
            "command": str(self.wrapper),
            "hostname": socket.gethostname(),
            "username": getpass.getuser(),
            "root": str(self.root),
            "repositories": ["sample"],
        }
        self.calls = []
        self.write_config()
        self.bridge = SourceBridges(self.data, runner=self.run_locally)

    def write_config(self):
        """Save only fake connection metadata in the test's private data folder."""
        (self.data / "source-bridges.json").write_text(
            json.dumps({"project123": self.entry})
        )

    def run_locally(self, args, timeout):
        """Interpret the fixed remote command without an SSH call or real wrapper."""
        self.calls.append(args)
        command = shlex.split(args[1])
        self.assertEqual(command[:2], ["python3", "-c"])
        return subprocess.run(
            command, capture_output=True, check=True, timeout=timeout
        ).stdout

    def file(self, name, content="sample text"):
        """Create one repository fixture, preserving callers' selected filenames."""
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_configured_does_not_probe_and_hosted_mode_cannot_connect(self):
        self.assertTrue(self.bridge.configured("project123"))
        self.assertFalse(self.bridge.configured("unknown"))
        self.assertEqual(self.calls, [])

    def test_workspace_setup_settings_require_explicit_valid_configuration(self):
        self.entry.update(
            editor_workspace=str(self.root / "Polaris.code-workspace"),
            editor_profile="Polaris",
            postgres_enabled=True,
            aws_login_enabled=True,
        )
        self.write_config()
        self.assertTrue(self.bridge.configured("project123"))
        for field, value in (
            ("postgres_enabled", "true"),
            ("aws_login_enabled", "true"),
            ("editor_workspace", "/tmp/outside.code-workspace"),
            ("editor_workspace", str(self.root / "project.json")),
            ("editor_profile", "bad\nprofile"),
        ):
            with self.subTest(field=field, value=value):
                original = self.entry[field]
                self.entry[field] = value
                self.write_config()
                with self.assertRaises(ValueError):
                    self.bridge.configured("project123")
                self.entry[field] = original
        self.assertEqual(self.calls, [])

    def test_path_validation_never_probes_or_reads_the_remote(self):
        self.assertEqual(
            self.bridge.validate_paths("project123", ["sample/not-created.py"]),
            ["sample/not-created.py"],
        )
        self.assertEqual(self.calls, [])
        for path in (
            "sample/secrets.json",
            "sample/.env",
            "outside/main.py",
            "sample/../main.py",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.validate_paths("project123", [path])
        self.assertEqual(self.calls, [])
        hosted = SourceBridges(self.data, enabled=False, runner=self.run_locally)
        self.assertFalse(hosted.configured("project123"))
        with self.assertRaisesRegex(PermissionError, "hosted"):
            hosted.list_files("project123")
        self.assertEqual(self.calls, [])

    def test_inventory_is_cached_and_returns_root_candidates_and_revision(self):
        self.file("README.md")
        self.file("package.json", '{"name":"sample"}')
        self.file("src/main.py")
        self.file(".git/HEAD", "ref: refs/heads/main\n")
        self.file(".git/refs/heads/main", "a" * 40 + "\n")
        result = self.bridge.inventory("project123")
        self.assertEqual(result["bridge"]["status"], "connected")
        self.assertNotIn("command", result["bridge"])
        self.assertNotIn("root", result["bridge"])
        self.assertEqual(
            result["repositories"],
            [
                {
                    "name": "sample",
                    "available": True,
                    "revision": "a" * 40,
                    "candidates": ["sample/README.md", "sample/package.json"],
                }
            ],
        )
        self.assertEqual(result, self.bridge.inventory("project123"))
        self.assertEqual(len(self.calls), 1)

    def test_list_and_read_preserve_source_shape_and_digest(self):
        self.file("README.md", "Hello café\n")
        self.file("src/main.py", "print('ready')\n")
        self.file(".env", "secret")
        self.file("secrets.json", "secret")
        self.file("node_modules/dependency.js")
        result = self.bridge.list_files("project123")
        self.assertFalse(result["truncated"])
        self.assertEqual(
            [item["path"] for item in result["files"]],
            ["sample/README.md", "sample/src/main.py"],
        )
        read = self.bridge.read_files("project123", ["sample/README.md"])
        self.assertEqual(
            read,
            [
                {
                    "path": "sample/README.md",
                    "content": "Hello café\n",
                    "sha256": hashlib.sha256("Hello café\n".encode()).hexdigest(),
                }
            ],
        )

    def test_identity_mismatch_is_detected_before_missing_root_access(self):
        self.entry["hostname"] = "wrong-host"
        self.entry["root"] = str(self.data / "not-there")
        self.write_config()
        with self.assertRaisesRegex(ValueError, "identity"):
            self.bridge.list_files("project123")

    def test_paths_cannot_escape_allowlist_or_read_secrets(self):
        self.file("safe.py")
        self.file("secrets.json")
        self.file(".env")
        for path in (
            "sample/../elsewhere.py",
            "/sample/safe.py",
            "unconfigured/safe.py",
            "sample/.env",
            "sample/secrets.json",
            "sample//safe.py",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.read_files("project123", [path])
        for paths in ([], ["sample/safe.py"] * 9, [None], [{"path": "sample/safe.py"}]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.bridge.read_files("project123", paths)

    def test_symlink_roots_files_and_directories_cannot_be_read(self):
        outside = self.data / "outside"
        outside.mkdir()
        (outside / "private.py").write_text("private")
        (self.repo / "linked.py").symlink_to(outside / "private.py")
        (self.repo / "linked").symlink_to(outside, target_is_directory=True)
        for path in ("sample/linked.py", "sample/linked/private.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.read_files("project123", [path])
        (self.root / "linkedrepo").symlink_to(outside, target_is_directory=True)
        self.entry["repositories"].append("linkedrepo")
        self.write_config()
        self.assertFalse(
            self.bridge.inventory("project123")["repositories"][1]["available"]
        )
        with self.assertRaises(ValueError):
            self.bridge.read_files("project123", ["linkedrepo/private.py"])

    def test_combined_text_size_duplicate_alias_and_binary_limits(self):
        self.file("a.txt", "a" * 30000)
        self.file("b.txt", "b" * 18001)
        with self.assertRaisesRegex(ValueError, "48,000"):
            self.bridge.read_files("project123", ["sample/a.txt", "sample/b.txt"])
        os.link(self.repo / "a.txt", self.repo / "alias.txt")
        with self.assertRaisesRegex(ValueError, "once"):
            self.bridge.read_files("project123", ["sample/a.txt", "sample/alias.txt"])
        self.file("nul.txt", "before\x00after")
        with self.assertRaisesRegex(ValueError, "Binary"):
            self.bridge.read_files("project123", ["sample/nul.txt"])

    def test_configuration_rejects_shell_commands_and_unsafe_repo_names(self):
        original = self.entry.copy()
        for key, value in (
            ("command", "echo unsafe"),
            ("repositories", ["../outside"]),
            ("hostname", "host; echo nope"),
            ("root", str(self.root) + "/../outside"),
        ):
            self.entry = {**original, key: value}
            self.write_config()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.bridge.configured("project123")
        self.assertEqual(self.calls, [])

    def test_runner_has_bounded_output_timeout_and_generic_failure(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            SourceBridges._run([sys.executable, "-c", "print('x' * 2200000)"], 2)
        with self.assertRaisesRegex(ValueError, "timed out"):
            SourceBridges._run(
                [sys.executable, "-c", "import time;time.sleep(10)"], 0.1
            )
        with self.assertRaisesRegex(ValueError, "command failed") as error:
            SourceBridges._run(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stderr.write('private diagnostic');sys.exit(1)",
                ],
                2,
            )
        self.assertNotIn("private diagnostic", str(error.exception))


if __name__ == "__main__":
    unittest.main()
