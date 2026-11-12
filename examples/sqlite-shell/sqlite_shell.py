#!/usr/bin/env python3
"""A local SQL shell using only Python's standard library."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


AGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_items (
    id TEXT PRIMARY KEY NOT NULL,
    source_uri TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'blocked', 'done', 'failed')),
    owner TEXT,
    context_id TEXT REFERENCES context_items(id),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
"""


def connect(database):
    """Create new databases privately; leave existing permissions unchanged."""
    if database != ':memory:':
        path = Path(database)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    connection = sqlite3.connect(database, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def split_sql(source):
    """Split complete statements without splitting quoted semicolons."""
    pending = ''
    for character in source:
        pending += character
        if character == ';' and sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip():
        if not sqlite3.complete_statement(pending + '\n;'):
            raise ValueError('Incomplete SQL statement')
        yield pending


def execute(connection, source, output):
    """Run one input batch atomically, printing query results as JSON lines."""
    # A savepoint also works when the caller already has a transaction.
    results = []
    connection.execute('SAVEPOINT shell_batch')
    def authorize(action, _arg1, _arg2, _database, _trigger):
        if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                      sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        connection.set_authorizer(authorize)
        for statement in split_sql(source):
            cursor = connection.execute(statement)
            if cursor.description is not None:
                columns = [column[0] for column in cursor.description]
                rows = [list(row) for row in cursor]
                results.append({'columns': columns, 'rows': rows})
            elif cursor.rowcount >= 0:
                results.append({'changed': cursor.rowcount})
        connection.set_authorizer(None)
        connection.execute('RELEASE shell_batch')
    except (sqlite3.Error, ValueError):
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.execute('ROLLBACK TO shell_batch')
            connection.execute('RELEASE shell_batch')
        raise
    finally:
        connection.set_authorizer(None)
    for result in results:
        print(json.dumps(result, ensure_ascii=False, default=encode_value), file=output)


def encode_value(value):
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    raise TypeError('Unsupported result value')


def command(connection, line, output):
    """Return False only when the caller requests exit."""
    if line in ('.quit', '.exit'):
        return False
    if line == '.help':
        print('.tables  .schema  .help  .quit\nSQL batches commit automatically.', file=output)
    elif line == '.tables':
        execute(connection, "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name;", output)
    elif line == '.schema':
        execute(connection, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type, name;", output)
    else:
        raise ValueError('Unknown shell command; use .help')
    return True


def repl(connection, input_stream, output, errors, interactive=False):
    pending = ''
    while True:
        if interactive:
            print('...> ' if pending else 'sqlite> ', end='', file=output, flush=True)
        line = input_stream.readline()
        if not line:
            if pending.strip():
                print('Error: unfinished SQL discarded at end of input', file=errors)
                return 1
            return 0
        try:
            if not pending and line.strip().startswith('.'):
                if not command(connection, line.strip(), output):
                    return 0
                continue
            pending += line
            if not sqlite3.complete_statement(pending):
                continue
            batch, pending = pending, ''
            execute(connection, batch, output)
        except (sqlite3.Error, ValueError) as error:
            pending = ''
            print(f'Error: {error}', file=errors)
            if not interactive:
                return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='Local database path, or :memory:')
    parser.add_argument('--init-agent-schema', action='store_true',
                        help='Create empty optional context, task and receipt tables')
    parser.add_argument('--execute', metavar='SQL', help='Execute SQL and exit')
    parser.add_argument('--shell', action='store_true', help='Open shell after initialization')
    args = parser.parse_args(argv)
    try:
        with connect(args.database) as connection:
            if args.init_agent_schema:
                connection.executescript(AGENT_SCHEMA)
            if args.execute is not None:
                execute(connection, args.execute, sys.stdout)
            if args.shell or (not args.init_agent_schema and args.execute is None):
                return repl(connection, sys.stdin, sys.stdout, sys.stderr, sys.stdin.isatty())
    except (sqlite3.Error, OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        if 'connection' in locals():
            connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
