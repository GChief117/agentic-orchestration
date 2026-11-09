"""Source attachment boundaries tested only with isolated temporary fixtures."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration.sources import list_source_files, read_source_files, source_file


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()

    def file(self, name, data=b"print('hello')\n"):
        """Create a temporary source fixture without touching any real project."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_lists_supported_sources_and_reads_selected_utf8_with_digests(self):
        source = self.file("src/main.py", "Hello café\n".encode())
        self.file("README", b"Read me")
        self.file("Dockerfile", b"FROM scratch")
        self.assertEqual(source_file(str(self.root), "src/main.py"), source)
        listed = list_source_files(str(self.root))
        self.assertFalse(listed["truncated"])
        self.assertEqual(
            [item["path"] for item in listed["files"]],
            ["Dockerfile", "README", "src/main.py"],
        )
        result = read_source_files(str(self.root), ["src/main.py"])
        self.assertEqual(
            result,
            [
                {
                    "path": "src/main.py",
                    "content": "Hello café\n",
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        )

    def test_absolute_traversal_hidden_and_secret_paths_are_rejected(self):
        for name in (
            ".env",
            ".env.example",
            ".hidden/ok.py",
            "credentials.json",
            "keys/client.pem",
            "secret.key",
            "id_rsa",
            "id_ed25519",
        ):
            self.file(name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                source_file(str(self.root), name)
        self.file("ok.py")
        for name in (
            "../ok.py",
            "src/../ok.py",
            "/ok.py",
            "./ok.py",
            "src\\ok.py",
            "src//ok.py",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                source_file(str(self.root), name)
        self.assertEqual(
            [item["path"] for item in list_source_files(str(self.root))["files"]],
            ["ok.py"],
        )

    def test_symlink_files_directories_and_roots_are_rejected(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "outside.py").write_text("outside")
        (self.root / "link.py").symlink_to(outside / "outside.py")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        (self.root.parent / "root-link").symlink_to(self.root, target_is_directory=True)
        for name in ("link.py", "linked/outside.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_source_files(str(self.root), [name])
        with self.assertRaises(ValueError):
            list_source_files(str(self.root.parent / "root-link"))
        self.assertEqual(list_source_files(str(self.root))["files"], [])

    def test_binary_suffixes_non_utf8_nul_content_and_nonregular_files_fail(self):
        self.file("image.png", b"image")
        with self.assertRaises(ValueError):
            source_file(str(self.root), "image.png")
        self.file("invalid.txt", b"\xff")
        self.file("binary.txt", b"ok\x00bad")
        os.mkfifo(self.root / "pipe.txt")
        for name in ("invalid.txt", "binary.txt", "pipe.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_source_files(str(self.root), [name])
        self.assertNotIn(
            "image.png",
            [item["path"] for item in list_source_files(str(self.root))["files"]],
        )

    def test_file_and_combined_size_limits_and_duplicate_selection(self):
        self.file("big.txt", b"a" * 65537)
        with self.assertRaises(ValueError):
            source_file(str(self.root), "big.txt")
        self.file("a.txt", b"a" * 30000)
        self.file("b.txt", b"b" * 18001)
        for paths in (
            [],
            ["a.txt"] * 2,
            ["a.txt"] * 9,
            ["a.txt", "b.txt"],
            "a.txt",
            [None],
        ):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                read_source_files(str(self.root), paths)
        self.file("b.txt", b"b" * 18000)
        self.assertEqual(
            sum(
                len(item["content"])
                for item in read_source_files(str(self.root), ["a.txt", "b.txt"])
            ),
            48000,
        )
        os.link(self.root / "a.txt", self.root / "alias.txt")
        with self.assertRaisesRegex(ValueError, "once"):
            read_source_files(str(self.root), ["a.txt", "alias.txt"])

    def test_scan_bounds_and_dependency_skips(self):
        self.file("node_modules/package/dependency.js")
        self.file("build/output.js")
        self.file("vendor/lib.py")
        self.file("a/b/c/d/e/f/g/h/allowed.py")
        self.file("a/b/c/d/e/f/g/h/i/too_deep.py")
        self.assertEqual(
            [item["path"] for item in list_source_files(str(self.root))["files"]],
            ["a/b/c/d/e/f/g/h/allowed.py"],
        )
        self.assertTrue(list_source_files(str(self.root))["truncated"])
        with patch("orchestration.sources.MAX_SCAN_ENTRIES", 2):
            result = list_source_files(str(self.root))
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["files"]), 2)

    def test_read_rechecks_path_after_selection(self):
        selected = self.file("main.py", b"selected")
        self.assertEqual(source_file(str(self.root), "main.py"), selected)
        outside = self.root.parent / "elsewhere.py"
        outside.write_text("elsewhere")
        selected.unlink()
        selected.symlink_to(outside)
        with self.assertRaises(ValueError):
            read_source_files(str(self.root), ["main.py"])
