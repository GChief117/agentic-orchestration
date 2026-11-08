"""Durable task, attempt, and event records. Scheduling owns transaction boundaries."""

import fcntl
import json
from pathlib import Path
import sqlite3
import time
import uuid


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.lease = (directory / "engine.lock").open("a")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lease.close()
            raise RuntimeError("Another engine owns this data directory") from exc
        self.db = sqlite3.connect(directory / "state.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY, title TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, goal_id TEXT, title TEXT,
              capability TEXT, target TEXT, parameters TEXT, dependencies TEXT, resources TEXT,
              state TEXT, receipt TEXT, error TEXT, started REAL, finished REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
              at REAL, task_id TEXT, message TEXT);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, task_id TEXT,
              state TEXT, started REAL, finished REAL, receipt TEXT, error TEXT);
        """
        )
        self._migrate_projects()
        # An interrupted operation may have committed effects. Never replay it silently.
        for row in self.db.execute(
            "SELECT id FROM tasks WHERE state IN ('running','cancelling')"
        ).fetchall():
            self.finish(
                row["id"],
                "failed",
                None,
                "Interrupted by restart; reconcile effects before retrying",
            )
        self.db.commit()

    def _migrate_projects(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY, name TEXT, mode TEXT, charter TEXT,
              max_concurrency INTEGER, created REAL);
            CREATE TABLE IF NOT EXISTS agents(
              id TEXT PRIMARY KEY, project_id TEXT, key TEXT, name TEXT, role TEXT,
              instructions TEXT, responsibilities TEXT, capabilities TEXT,
              reports_to TEXT, max_concurrency INTEGER, UNIQUE(project_id,key));
        """
        )
        for table, additions in {
            "goals": ("project_id",),
            "tasks": ("project_id", "agent_id"),
            "attempts": ("project_id", "agent_id"),
        }.items():
            columns = {
                row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")
            }
            for column in additions:
                if column not in columns:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        # Old records remain inspectable, but cannot run without a project or owner.
        self.db.execute(
            """UPDATE tasks SET state='blocked',
            error='Legacy task has no project; resubmit with an explicit project and agent'
            WHERE project_id IS NULL AND state='queued'"""
        )

    def create_project(self, definition):
        project_id = uuid.uuid4().hex
        agent_ids = {agent.key: uuid.uuid4().hex for agent in definition.agents}
        self.db.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?)",
            (
                project_id,
                definition.name,
                definition.mode,
                json.dumps(definition.charter),
                definition.max_concurrency,
                time.time(),
            ),
        )
        for agent in definition.agents:
            self.db.execute(
                "INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    agent_ids[agent.key],
                    project_id,
                    agent.key,
                    agent.name,
                    agent.role,
                    agent.instructions,
                    json.dumps(agent.responsibilities),
                    json.dumps(agent.capabilities),
                    agent_ids.get(agent.reports_to),
                    agent.max_concurrency,
                ),
            )
        self.event(
            None,
            f"Project created: {definition.name} · {len(definition.agents)} assigned agents",
        )
        return project_id

    def project(self, project_id):
        row = self.db.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown project")
        return self.decode(row)

    def agents(self, project_id=None):
        if project_id is None:
            rows = self.db.execute("SELECT * FROM agents ORDER BY rowid")
        else:
            rows = self.db.execute(
                "SELECT * FROM agents WHERE project_id=? ORDER BY rowid", (project_id,)
            )
        return [self.decode(row) for row in rows]

    def event(self, task_id, message):
        self.db.execute(
            "INSERT INTO events(at,task_id,message) VALUES(?,?,?)",
            (time.time(), task_id, message),
        )

    def create_goal(self, project_id, title, definitions, target):
        goal_id = uuid.uuid4().hex
        identifiers = [uuid.uuid4().hex for _ in definitions]
        self.db.execute(
            "INSERT INTO goals(id,title,created,project_id) VALUES(?,?,?,?)",
            (goal_id, title, time.time(), project_id),
        )
        for index, definition in enumerate(definitions):
            self.db.execute(
                """INSERT INTO tasks
              (id,goal_id,title,capability,target,parameters,dependencies,resources,state,project_id,agent_id)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifiers[index],
                    goal_id,
                    definition.title,
                    definition.capability,
                    target,
                    json.dumps(definition.parameters),
                    json.dumps(
                        [
                            identifiers[dependency]
                            for dependency in definition.dependencies
                        ]
                    ),
                    json.dumps(definition.resources),
                    "queued",
                    project_id,
                    definition.agent_id,
                ),
            )
        self.event(None, f"Goal created: {title} · {len(definitions)} tasks")
        return goal_id

    @staticmethod
    def decode(row):
        value = dict(row)
        for key in (
            "parameters",
            "dependencies",
            "resources",
            "receipt",
            "charter",
            "responsibilities",
            "capabilities",
        ):
            if key in value:
                value[key] = json.loads(value[key]) if value[key] else None
        return value

    def tasks(self):
        return [
            self.decode(row)
            for row in self.db.execute("SELECT * FROM tasks ORDER BY rowid")
        ]

    def scheduling_state(self):
        """Read only records needed for dispatch, excluding historical receipts/events."""
        return {
            "projects": [
                self.decode(row)
                for row in self.db.execute("SELECT * FROM projects ORDER BY created")
            ],
            "agents": self.agents(),
            "tasks": self.tasks(),
        }

    def snapshot(self):
        return {
            **self.scheduling_state(),
            "goals": [
                dict(row)
                for row in self.db.execute("SELECT * FROM goals ORDER BY created DESC")
            ],
            "events": [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT 100"
                )
            ],
            "attempts": [
                self.decode(row)
                for row in self.db.execute("SELECT * FROM attempts ORDER BY started")
            ],
        }

    def start(self, task):
        attempt_id = uuid.uuid4().hex
        now = time.time()
        self.db.execute(
            "UPDATE tasks SET state='running',started=? WHERE id=?", (now, task["id"])
        )
        self.db.execute(
            "INSERT INTO attempts(id,task_id,state,started,project_id,agent_id) VALUES(?,?,?,?,?,?)",
            (
                attempt_id,
                task["id"],
                "running",
                now,
                task["project_id"],
                task["agent_id"],
            ),
        )
        self.event(task["id"], "Started: " + task["title"])
        return attempt_id

    def finish(self, task_id, state, receipt, error=None):
        now = time.time()
        encoded = json.dumps(receipt) if receipt else None
        self.db.execute(
            "UPDATE tasks SET state=?,receipt=?,error=?,finished=? WHERE id=?",
            (state, encoded, error, now, task_id),
        )
        self.db.execute(
            "UPDATE attempts SET state=?,receipt=?,error=?,finished=? WHERE task_id=? AND state='running'",
            (state, encoded, error, now, task_id),
        )
        self.event(task_id, f"{state.capitalize()}: {task_id}")

    def block(self, task_id):
        self.finish(task_id, "blocked", None, "Dependency did not pass acceptance")

    def cancel(self, goal_id):
        if not self.db.execute(
            "SELECT id FROM goals WHERE id=?", (goal_id,)
        ).fetchone():
            raise ValueError("Unknown goal")
        active = []
        for row in self.db.execute(
            "SELECT id,state FROM tasks WHERE goal_id=?", (goal_id,)
        ).fetchall():
            if row["state"] == "queued":
                self.finish(row["id"], "cancelled", None)
            elif row["state"] in ("running", "cancelling"):
                self.db.execute(
                    "UPDATE tasks SET state='cancelling' WHERE id=?", (row["id"],)
                )
                active.append(row["id"])
        self.event(None, "Cancellation requested for " + goal_id)
        return active

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()
        fcntl.flock(self.lease, fcntl.LOCK_UN)
        self.lease.close()
