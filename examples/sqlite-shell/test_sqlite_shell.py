import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import sqlite_shell as shell


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.connection = shell.connect(':memory:')
        self.connection.executescript(shell.AGENT_SCHEMA)

    def tearDown(self):
        self.connection.close()

    def test_schema_is_empty_and_repeatable(self):
        self.connection.executescript(shell.AGENT_SCHEMA)
        for name in ('context_items', 'tasks', 'receipts'):
            self.assertEqual(self.connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0], 0)

    def test_context_task_and_receipt_references_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            shell.execute(self.connection, "INSERT INTO tasks (id,title,context_id) VALUES ('t','Task','missing');", io.StringIO())
        with self.assertRaises(sqlite3.IntegrityError):
            shell.execute(self.connection, "INSERT INTO receipts (task_id,outcome,note) VALUES ('missing','succeeded','Synthetic');", io.StringIO())
        with self.assertRaises(sqlite3.IntegrityError):
            shell.execute(self.connection, "INSERT INTO tasks (id,title,status) VALUES ('t','Task','invented');", io.StringIO())

    def test_failed_batch_is_rolled_back(self):
        output = io.StringIO()
        with self.assertRaises(sqlite3.Error):
            shell.execute(self.connection, "INSERT INTO tasks (id,title) VALUES ('t','Task'); SELECT * FROM missing;", output)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(output.getvalue(), '')

    def test_quoted_semicolons_and_duplicate_column_names(self):
        output = io.StringIO()
        shell.execute(self.connection, "SELECT 'a;b' AS value, 'second' AS value; SELECT 2;", output)
        self.assertIn('"columns": ["value", "value"], "rows": [["a;b", "second"]]', output.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()), 2)

    def test_transaction_commands_cannot_escape_batch_rollback(self):
        for statement in ('COMMIT;', 'ROLLBACK;', 'SAVEPOINT nested;', "ATTACH ':memory:' AS extra;"):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.Error):
                    shell.execute(self.connection, "INSERT INTO tasks (id,title) VALUES ('t','Task'); " + statement, io.StringIO())
                self.assertEqual(self.connection.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)

    def test_blobs_are_json_serializable(self):
        output = io.StringIO()
        shell.execute(self.connection, "SELECT x'00ff';", output)
        self.assertIn('"blob_hex": "00ff"', output.getvalue())

    def test_script_errors_and_incomplete_input_fail(self):
        for source in ('SELECT * FROM missing;\n', "SELECT 'unfinished\n", '.unknown\n'):
            with self.subTest(source=source):
                errors = io.StringIO()
                result = shell.repl(self.connection, io.StringIO(source), io.StringIO(), errors)
                self.assertEqual(result, 1)
                self.assertTrue(errors.getvalue().startswith('Error:'))

    def test_multiline_repl_and_commands(self):
        output = io.StringIO()
        result = shell.repl(self.connection, io.StringIO('.tables\nSELECT\n 1;\n.quit\n'), output, io.StringIO())
        self.assertEqual(result, 0)
        self.assertIn('context_items', output.getvalue())
        self.assertIn('"rows": [[1]]', output.getvalue())

    def test_new_database_is_private_and_existing_permissions_preserved(self):
        if os.name != 'posix':
            self.skipTest('POSIX permission semantics')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'context.sqlite3'
            shell.connect(str(path)).close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.chmod(0o640)
            shell.connect(str(path)).close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_cli_initializes_and_queries_persistent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'context.sqlite3')
            command = [sys.executable, shell.__file__, path]
            initialized = subprocess.run(command + ['--init-agent-schema'], capture_output=True, text=True)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            inserted = subprocess.run(command + ['--execute', "INSERT INTO tasks (id,title) VALUES ('t','Task');"], capture_output=True, text=True)
            self.assertEqual(inserted.returncode, 0, inserted.stderr)
            queried = subprocess.run(command + ['--execute', 'SELECT id,status FROM tasks'], capture_output=True, text=True)
            self.assertEqual(queried.returncode, 0, queried.stderr)
            self.assertIn('"rows": [["t", "planned"]]', queried.stdout)


if __name__ == '__main__':
    unittest.main()
