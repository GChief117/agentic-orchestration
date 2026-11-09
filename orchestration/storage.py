"""Durable task, attempt, and event records. Scheduling owns transaction boundaries."""

import fcntl
import json
from pathlib import Path
import sqlite3
import time
import uuid


class Store:
    def __init__(self, directory):
        """Acquire exclusive ownership of a data directory and open its SQLite store.

        Create or migrate tables and mark interrupted attempts failed without replaying
        their potentially completed effects. A second engine on the directory fails.
        """
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
        for row in self.db.execute(
            "SELECT id,delivery FROM goals WHERE delivery IS NOT NULL"
        ).fetchall():
            delivery = json.loads(row["delivery"])
            if delivery.get("state") == "rendering":
                delivery.update(
                    state="failed",
                    error="Delivery was interrupted by restart. Export it again to retry.",
                )
                self.db.execute(
                    "UPDATE goals SET delivery=? WHERE id=?",
                    (json.dumps(delivery), row["id"]),
                )
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
        """Add project/agent storage and block legacy queued tasks without owners.

        Preserve existing records rather than assigning them to invented projects.
        """
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY, name TEXT, mode TEXT, charter TEXT,
              max_concurrency INTEGER, created REAL);
            CREATE TABLE IF NOT EXISTS project_sources(
              project_id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, markers TEXT);
            CREATE TABLE IF NOT EXISTS agents(
              id TEXT PRIMARY KEY, project_id TEXT, key TEXT, name TEXT, role TEXT,
              instructions TEXT, responsibilities TEXT, capabilities TEXT,
              reports_to TEXT, max_concurrency INTEGER, UNIQUE(project_id,key));
        """
        )
        for table, additions in {
            "goals": ("project_id", "deliverable", "delivery", "research_mode"),
            "tasks": ("project_id", "agent_id"),
            "attempts": ("project_id", "agent_id"),
        }.items():
            columns = {
                row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")
            }
            for column in additions:
                if column not in columns:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        agent_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(agents)")
        }
        project_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(projects)")
        }
        if "team_model" not in project_columns:
            self.db.execute("ALTER TABLE projects ADD COLUMN team_model TEXT")
        for name, declaration in {
            "provider": "TEXT",
            "model": "TEXT",
            "collaborators": "TEXT NOT NULL DEFAULT '[]'",
            "execution_mode": "TEXT NOT NULL DEFAULT 'api'",
            "execution_speed": "TEXT NOT NULL DEFAULT 'standard'",
        }.items():
            if name not in agent_columns:
                self.db.execute(f"ALTER TABLE agents ADD COLUMN {name} {declaration}")
        # Old records remain inspectable, but cannot run without a project or owner.
        self.db.execute(
            """UPDATE tasks SET state='blocked',
            error='Legacy task has no project; resubmit with an explicit project and agent'
            WHERE project_id IS NULL AND state='queued'"""
        )

    def create_project(self, definition):
        """Stage a validated charter and its persistent agent roster; return project ID.

        Resolve reporting keys to stored agent IDs. The caller owns the transaction.
        """
        project_id = uuid.uuid4().hex
        agent_ids = {agent.key: uuid.uuid4().hex for agent in definition.agents}
        self.db.execute(
            "INSERT INTO projects (id,name,mode,charter,max_concurrency,created,team_model) VALUES(?,?,?,?,?,?,?)",
            (
                project_id,
                definition.name,
                definition.mode,
                json.dumps(definition.charter),
                definition.max_concurrency,
                time.time(),
                json.dumps(definition.team_model) if definition.team_model else None,
            ),
        )
        for agent in definition.agents:
            self.db.execute(
                "INSERT INTO agents (id,project_id,key,name,role,instructions,responsibilities,capabilities,reports_to,max_concurrency,provider,model,collaborators,execution_mode,execution_speed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    agent.provider,
                    agent.model,
                    json.dumps(agent.collaborators),
                    agent.execution_mode,
                    agent.execution_speed,
                ),
            )
        self.event(
            None,
            f"Project created: {definition.name} · {len(definition.agents)} assigned agents",
        )
        return project_id

    def update_team(self, project_id, definition):
        """Update idle-project agents by stable key; preserve IDs referenced by history.

        Call inside the engine's transaction after checking pending work. Agents
        with recorded tasks cannot be removed because receipts retain ownership.
        """
        existing = {agent["key"]: agent for agent in self.agents(project_id)}
        desired = {agent.key for agent in definition.agents}
        for key, agent in existing.items():
            if (
                key not in desired
                and self.db.execute(
                    "SELECT 1 FROM tasks WHERE agent_id=? LIMIT 1", (agent["id"],)
                ).fetchone()
            ):
                raise ValueError(
                    f"Cannot remove {agent['name']}: it has recorded tasks"
                )
        identifiers = {
            agent.key: existing[agent.key]["id"]
            if agent.key in existing
            else uuid.uuid4().hex
            for agent in definition.agents
        }
        for agent in definition.agents:
            self.db.execute(
                """INSERT INTO agents
                (id,project_id,key,name,role,instructions,responsibilities,capabilities,reports_to,max_concurrency,provider,model,collaborators,execution_mode,execution_speed)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,role=excluded.role,instructions=excluded.instructions,
                responsibilities=excluded.responsibilities,capabilities=excluded.capabilities,
                reports_to=excluded.reports_to,max_concurrency=excluded.max_concurrency,
                provider=excluded.provider,model=excluded.model,collaborators=excluded.collaborators,
                execution_mode=excluded.execution_mode,execution_speed=excluded.execution_speed""",
                (
                    identifiers[agent.key],
                    project_id,
                    agent.key,
                    agent.name,
                    agent.role,
                    agent.instructions,
                    json.dumps(agent.responsibilities),
                    json.dumps(agent.capabilities),
                    identifiers.get(agent.reports_to),
                    agent.max_concurrency,
                    agent.provider,
                    agent.model,
                    json.dumps(agent.collaborators),
                    agent.execution_mode,
                    agent.execution_speed,
                ),
            )
        for key, agent in existing.items():
            if key not in desired:
                self.db.execute("DELETE FROM agents WHERE id=?", (agent["id"],))
        self.db.execute(
            "UPDATE projects SET max_concurrency=?,team_model=? WHERE id=?",
            (
                definition.max_concurrency,
                json.dumps(definition.team_model) if definition.team_model else None,
                project_id,
            ),
        )
        self.event(None, f"Team updated: {definition.name}")

    def project(self, project_id):
        """Load a project and decode its charter, or raise ValueError for an unknown ID."""
        row = self.db.execute(
            "SELECT p.*, s.path AS source_path, s.markers AS source_markers FROM projects p LEFT JOIN project_sources s ON s.project_id=p.id WHERE p.id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown project")
        return self.decode(row)

    def agents(self, project_id=None):
        """Return decoded agent records, optionally restricted to one project."""
        if project_id is None:
            rows = self.db.execute("SELECT * FROM agents ORDER BY rowid")
        else:
            rows = self.db.execute(
                "SELECT * FROM agents WHERE project_id=? ORDER BY rowid", (project_id,)
            )
        return [self.decode(row) for row in rows]

    def goal(self, goal_id):
        """Return one durable goal, including its requested and produced deliverable."""
        row = self.db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown goal")
        return self.decode(row)

    def event(self, task_id, message):
        """Append a timestamped event to the current transaction without committing it."""
        self.db.execute(
            "INSERT INTO events(at,task_id,message) VALUES(?,?,?)",
            (time.time(), task_id, message),
        )

    def create_goal(
        self,
        project_id,
        title,
        definitions,
        target,
        deliverable=None,
        research_mode="source_context",
    ):
        """Stage a goal and normalized tasks, returning the generated goal ID.

        Link each task to its project and agent; translate plan-local dependency
        indices into durable task IDs. The caller commits the transaction.
        """
        goal_id = uuid.uuid4().hex
        identifiers = [uuid.uuid4().hex for _ in definitions]
        self.db.execute(
            "INSERT INTO goals(id,title,created,project_id,deliverable,research_mode) VALUES(?,?,?,?,?,?)",
            (
                goal_id,
                title,
                time.time(),
                project_id,
                json.dumps(deliverable) if deliverable else None,
                research_mode,
            ),
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
        """Copy a SQLite row to a dictionary and decode its JSON-valued columns."""
        value = dict(row)
        for key in (
            "parameters",
            "dependencies",
            "resources",
            "receipt",
            "charter",
            "team_model",
            "source_markers",
            "responsibilities",
            "capabilities",
            "collaborators",
            "deliverable",
            "delivery",
        ):
            if key in value:
                value[key] = json.loads(value[key]) if value[key] else None
        return value

    def tasks(self):
        """Return task records in insertion order, including their latest receipts."""
        return [
            self.decode(row)
            for row in self.db.execute("SELECT * FROM tasks ORDER BY rowid")
        ]

    def scheduling_state(self):
        """Return projects, agents, and tasks needed for a dispatch decision.

        Avoid loading the full attempt and event history on every scheduler tick.
        """
        return {
            "projects": [
                self.decode(row)
                for row in self.db.execute(
                    "SELECT p.*, s.path AS source_path, s.markers AS source_markers FROM projects p LEFT JOIN project_sources s ON s.project_id=p.id ORDER BY p.created"
                )
            ],
            "agents": self.agents(),
            "tasks": self.tasks(),
        }

    def snapshot(self):
        """Return decoded project, agent, task, goal, event, and attempt records."""
        return {
            **self.scheduling_state(),
            "goals": [
                self.decode(row)
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
        """Stage a running task and a new attempt before any external effects occur.

        Return the attempt ID; persistence is finalized by the caller transaction.
        """
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
        """Stage a task outcome, update its running attempt, and append an event.

        Retain the supplied receipt and error; do not commit the transaction here.
        """
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
        """Mark a task blocked because a prerequisite did not pass acceptance."""
        self.finish(task_id, "blocked", None, "Dependency did not pass acceptance")

    def cancel(self, goal_id):
        """Stage queued-task cancellation and active-task cancellation requests.

        Return the active task IDs whose executor signals must also be notified.
        """
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
        """Commit staged state changes and events to SQLite."""
        self.db.commit()

    def close(self):
        """Close SQLite and release the exclusive data-directory lock."""
        self.db.close()
        fcntl.flock(self.lease, fcntl.LOCK_UN)
        self.lease.close()
