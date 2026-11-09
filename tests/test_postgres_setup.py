"""Verify local database setup with isolated fake processes and registries."""

import getpass
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchestration.postgres_setup import (
    REMOTE_POSTGRES_SETUP,
    validate_setup_parameters,
)


class PostgresSetupTests(unittest.TestCase):
    def setUp(self):
        self.remote = {}
        exec(REMOTE_POSTGRES_SETUP, self.remote)
        self.payload = {
            "hostname": socket.gethostname(),
            "username": getpass.getuser(),
            "postgres_enabled": True,
            "parameters": {},
        }

    def test_setup_accepts_only_fixed_actions_without_parameters(self):
        for capability in ("bridge.postgres.prepare", "bridge.pgadmin.register"):
            self.assertEqual(validate_setup_parameters(capability, {}), {})
            with self.assertRaises(ValueError):
                validate_setup_parameters(capability, {"host": "external"})
        with self.assertRaises(ValueError):
            validate_setup_parameters("execute.sql", {})

    def test_identity_and_explicit_enablement_precede_any_database_query(self):
        calls = []
        self.remote["postgres_inventory"] = lambda: calls.append(True)
        for changes in (
            {"postgres_enabled": False},
            {"hostname": "wrong"},
            {"username": "wrong"},
            {"parameters": {"sql": "unsafe"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.remote["postgres_prepare"]({**self.payload, **changes})
        self.assertEqual(calls, [])

    def test_prepare_creates_only_missing_databases_and_is_idempotent(self):
        databases = {"postgres", "scoring", "existing_work"}
        commands = []

        def query(sql):
            commands.append(sql)
            if sql.startswith("SELECT"):
                return json.dumps(
                    {
                        "version": "18.6",
                        "version_num": 180006,
                        "databases": sorted(databases),
                    }
                )
            name = sql.split('"')[1]
            databases.add(name)
            return ""

        self.remote["postgres_query"] = query
        result = self.remote["postgres_prepare"](self.payload)
        self.assertEqual(result["created"], ["datalake", "accelerate", "eab_dev"])
        self.assertEqual(result["existing"], ["scoring"])
        self.assertIn("existing_work", databases)
        self.assertEqual(self.remote["postgres_prepare"](self.payload)["created"], [])
        self.assertEqual(
            [sql for sql in commands if not sql.startswith("SELECT")],
            [
                'CREATE DATABASE "datalake"',
                'CREATE DATABASE "accelerate"',
                'CREATE DATABASE "eab_dev"',
            ],
        )

    def test_prepare_rejects_other_server_versions_before_creating(self):
        self.remote["postgres_query"] = lambda sql: json.dumps(
            {"version": "17.1", "version_num": 170001, "databases": ["postgres"]}
        )
        with self.assertRaisesRegex(ValueError, "PostgreSQL 18"):
            self.remote["postgres_prepare"](self.payload)

    def test_psql_cannot_inherit_passwords_services_or_connection_options(self):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "1\n", "")

        with patch.object(Path, "is_file", return_value=True), patch.object(
            self.remote["os"], "access", return_value=True
        ), patch.object(self.remote["subprocess"], "run", side_effect=run), patch.dict(
            self.remote["os"].environ,
            {"PGPASSWORD": "private", "PGSERVICE": "remote", "PGOPTIONS": "options"},
        ):
            self.assertEqual(self.remote["postgres_query"]("SELECT 1"), "1")
        args, options = calls[0]
        self.assertIn("--no-password", args)
        self.assertEqual(args[args.index("-h") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("-p") + 1], "5432")
        self.assertNotIn("PGPASSWORD", options["env"])
        self.assertNotIn("PGSERVICE", options["env"])
        self.assertNotIn("PGOPTIONS", options["env"])
        self.assertEqual(options["env"]["PGPASSFILE"], "/dev/null")

    def test_pgadmin_import_is_additive_verified_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "pgadmin4.db"
            with closing(sqlite3.connect(registry)) as database, database:
                database.executescript(
                    "CREATE TABLE user(id INTEGER, email TEXT);"
                    "CREATE TABLE server(user_id INTEGER, name TEXT, host TEXT, "
                    "port INTEGER, username TEXT, maintenance_db TEXT);"
                )
                database.execute(
                    "INSERT INTO user VALUES (1, ?)", ("pgadmin4@pgadmin.org",)
                )
                database.execute(
                    "INSERT INTO server VALUES (1, 'Keep me', 'remote', 5432, 'other', 'postgres')"
                )
            self.remote["pgadmin_registry"] = lambda: registry
            self.remote["postgres_inventory"] = lambda: {"version": "18.6"}
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                self.assertIn("--no-replace", args)
                self.assertEqual(args[1], "-c")
                self.assertEqual(args[2], self.remote["PGADMIN_DESKTOP_ENTRY"])
                self.assertEqual(args[args.index("--sqlite-path") + 1], str(registry))
                document = json.loads(
                    Path(args[args.index("load-servers") + 1]).read_text()
                )
                server = document["Servers"]["1"]
                self.assertNotIn("Password", server)
                self.assertEqual(server["Host"], "127.0.0.1")
                with closing(sqlite3.connect(registry)) as database, database:
                    database.execute(
                        "INSERT INTO server VALUES (1, ?, ?, ?, ?, ?)",
                        (
                            server["Name"],
                            server["Host"],
                            server["Port"],
                            server["Username"],
                            server["MaintenanceDB"],
                        ),
                    )
                return subprocess.CompletedProcess(args, 0, "imported", "")

            with patch.object(Path, "is_file", return_value=True), patch.object(
                self.remote["subprocess"], "run", side_effect=run
            ):
                first = self.remote["pgadmin_register"](self.payload)
                second = self.remote["pgadmin_register"](self.payload)
            self.assertFalse(first["existing"])
            self.assertTrue(second["existing"])
            self.assertEqual(len(calls), 1)
            self.assertFalse(list(Path(temporary).glob(".local-server-*.json")))
            with closing(sqlite3.connect(registry)) as database:
                self.assertEqual(
                    database.execute("SELECT count(*) FROM server").fetchone()[0], 2
                )

    def test_desktop_entry_sets_mode_before_loading_setup_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            probe = Path(temporary) / "setup.py"
            probe.write_text(
                "import builtins, json, sys\n"
                "assert __name__ == '__main__'\n"
                "print(json.dumps({'desktop': builtins.SERVER_MODE is False, 'args': sys.argv}))\n"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    self.remote["PGADMIN_DESKTOP_ENTRY"],
                    str(probe),
                    "load-servers",
                    "example.json",
                    "--no-replace",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "desktop": True,
                    "args": [
                        str(probe),
                        "load-servers",
                        "example.json",
                        "--no-replace",
                    ],
                },
            )

    def test_pgadmin_success_exit_without_import_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "pgadmin4.db"
            self.remote["pgadmin_registry"] = lambda: registry
            self.remote["postgres_inventory"] = lambda: {"version": "18.6"}
            self.remote["pgadmin_metadata"] = lambda _: ("pgadmin4@pgadmin.org", [])
            with patch.object(Path, "is_file", return_value=True), patch.object(
                self.remote["subprocess"],
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ), self.assertRaisesRegex(ValueError, "did not confirm"):
                self.remote["pgadmin_register"](self.payload)
            self.assertFalse(list(Path(temporary).glob(".local-server-*.json")))


if __name__ == "__main__":
    unittest.main()
