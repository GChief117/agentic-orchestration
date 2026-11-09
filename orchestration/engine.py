"""Coordinate durable state, scheduling policy, and execution adapters."""

import json
from pathlib import Path
import threading

from .connections import LocalConnection
from .execution import ExecutionCancelled, LocalExecutor
from .plans import parse_tasks
from .scheduling import CapacityLimits, goal_state, select_work
from .projects import parse_project, positive_integer
from .storage import Store
from .providers import ProviderConnections
from .models import ModelRuntime
from .cli_models import CLIRuntime
from .sources import list_source_files
from .discovery import ProjectDiscovery, imported_definition


class Engine:
    def __init__(
        self,
        directory,
        *,
        execution_limit,
        target_limits=None,
        executor=None,
        allow_source_access=True,
        allow_cli=True,
    ):
        """Build the local runtime with explicitly supplied global execution capacity.

        Optional target_limits narrow machine capacity. Open persistent storage and
        prepare coordination state; no scheduler or agent is started automatically.
        """
        self.execution_limit = positive_integer(execution_limit, "Execution limit")
        if target_limits is None:
            target_limits = {"local-workspace": self.execution_limit}
        if not isinstance(target_limits, dict) or set(target_limits) != {
            "local-workspace"
        }:
            raise ValueError(
                "Target limits must explicitly cover the supported local target"
            )
        self.target_limits = {
            key: positive_integer(value, "Target limit")
            for key, value in target_limits.items()
        }
        self.connection = LocalConnection(Path(directory) / "artifacts")
        self.allow_source_access = allow_source_access
        self.models = ModelRuntime()
        self.cli = CLIRuntime() if allow_cli else None
        self.executor = executor or LocalExecutor(
            self.connection,
            self.models,
            cli_models=self.cli,
            allow_source_access=allow_source_access,
        )
        self.store = Store(directory)
        self.providers = ProviderConnections(directory, runtime=self.cli)
        self.discovery = ProjectDiscovery()
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.active = {}
        self.threads = {}
        self.scheduler = None
        self.closed = False

    def _require_open(self):
        """Raise RuntimeError when mutation is attempted during or after shutdown."""
        if self.closed or self.stopping.is_set():
            raise RuntimeError("Engine is shutting down")

    def create_project(self, document):
        """Validate and atomically persist a charter and roster; return the project ID.

        Creates logical agent identities without starting model-provider processes.
        """
        definition = parse_project(document, self.connection)
        with self.lock:
            self._require_open()
            with self.store.db:
                project_id = self.store.create_project(definition)
        return project_id

    def model_catalog(self, *, local_tools=True):
        """Combine API models with cached local CLI readiness without launching tools."""
        providers = self.models.catalog()
        if local_tools and self.cli is not None:
            local = {item["id"]: item for item in self.cli.catalog()}
            return [{**item, "cli": local.get(item["id"])} for item in providers]
        return providers

    def scan_projects(self, root, include_root=False):
        """Discover folders read-only and mark paths already registered in this workspace."""
        result = self.discovery.scan(root, include_root)
        with self.lock:
            imported = {
                row[0]
                for row in self.store.db.execute("SELECT path FROM project_sources")
            }
        return {
            **result,
            "projects": [
                {**item, "imported": item["path"] in imported}
                for item in result["projects"]
            ],
        }

    def import_projects(self, scan_id, identifiers):
        """Atomically register selected scan results; never copy or execute source files."""
        candidates = self.discovery.selected(scan_id, identifiers)
        definitions = [
            parse_project(imported_definition(item), self.connection)
            for item in candidates
        ]
        with self.lock:
            self._require_open()
            existing = {
                row[0]: row[1]
                for row in self.store.db.execute(
                    "SELECT path, project_id FROM project_sources"
                )
            }
            imported = []
            with self.store.db:
                for item, definition in zip(candidates, definitions):
                    project_id = existing.get(item["path"])
                    if project_id is None:
                        project_id = self.store.create_project(definition)
                        self.store.db.execute(
                            "INSERT INTO project_sources VALUES(?,?,?)",
                            (project_id, item["path"], json.dumps(item["kinds"])),
                        )
                    imported.append(
                        {
                            "id": project_id,
                            "name": definition.name,
                            "path": item["path"],
                        }
                    )
        return imported

    def project_files(self, project_id):
        """List safe text-file metadata from one imported local project; never read contents."""
        if not self.allow_source_access:
            raise PermissionError("Local source files are unavailable in hosted mode")
        with self.lock:
            project = self.store.project(project_id)
        if not project.get("source_path"):
            raise ValueError("Import a local project before selecting source files")
        return list_source_files(project["source_path"])

    def update_team(self, project_id, document):
        """Persist chart edits for an idle project while preserving historical ownership.

        Reject changes while work is queued/running and validate the full new
        hierarchy and capability roster atomically before updating it.
        """
        if not isinstance(project_id, str) or not isinstance(document, dict):
            raise ValueError("Project ID and team definition are required")
        with self.lock:
            self._require_open()
            project = self.store.project(project_id)
            if self.store.db.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND state IN ('queued','running','cancelling') LIMIT 1",
                (project_id,),
            ).fetchone():
                raise ValueError(
                    "Wait for queued and running project work to finish before editing the team"
                )
            definition = parse_project(
                {
                    **project,
                    "agents": document.get("agents"),
                    "team_size": document.get("team_size"),
                    "max_concurrency": document.get("max_concurrency"),
                },
                self.connection,
            )
            with self.store.db:
                self.store.update_team(project_id, definition)
        return project_id

    def submit(self, project_id, title, tasks, *, allow_cli=True):
        """Validate and atomically queue a goal in an existing project.

        project_id identifies the team; tasks name its agent keys and dependencies.
        Return a durable goal ID. Submission itself does not wait for execution.
        """
        if not isinstance(project_id, str):
            raise ValueError("An explicit project ID is required")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError("Title must contain 1–120 characters")
        with self.lock:
            self._require_open()
            project = self.store.project(project_id)
            team = self.store.agents(project_id)
            definitions = parse_tasks(
                tasks,
                self.connection,
                project_id,
                team,
                source_root=project.get("source_path")
                if self.allow_source_access
                else None,
            )
            by_id = {agent["id"]: agent for agent in team}
            for task in definitions:
                if task.capability == "model.generate":
                    agent = by_id[task.agent_id]
                    if agent.get("execution_mode", "api") == "cli":
                        if self.cli is None or not allow_cli:
                            raise PermissionError(
                                "Local CLI execution is unavailable in hosted mode"
                            )
                        runtime = self.cli
                    else:
                        runtime = self.models
                    if not runtime.ready(agent["provider"]):
                        raise ValueError(
                            "Connect the assigned model provider before submitting this goal"
                        )
            with self.store.db:
                goal_id = self.store.create_goal(
                    project_id, title.strip(), definitions, "local-workspace"
                )
        return goal_id

    def start(self):
        """Start one background scheduler loop, leaving an existing loop unchanged."""
        with self.lock:
            if self.closed or self.stopping.is_set():
                raise RuntimeError("Engine cannot restart after shutdown")
            if self.scheduler is None:
                self.scheduler = threading.Thread(
                    target=self._loop, name="orchestration-scheduler", daemon=True
                )
                self.scheduler.start()

    def close(self):
        """Request cooperative worker cancellation, join threads, and close storage.

        Repeated calls after a completed shutdown are harmless.
        """
        self.stopping.set()
        with self.lock:
            if self.closed:
                return
            for signal in self.active.values():
                signal.set()
            worker_threads = list(self.threads.values())
        if self.scheduler:
            self.scheduler.join()
        # Release the lock while workers persist final receipts and outcomes.
        for thread in worker_threads:
            thread.join()
        with self.lock:
            self.store.close()
            self.closed = True

    def cancel_goal(self, goal_id):
        """Persist cancellation for a goal and signal its active executor attempts.

        Completed results are retained and cancellation does not undo prior effects.
        """
        with self.lock:
            for task_id in self.store.cancel(goal_id):
                if task_id in self.active:
                    self.active[task_id].set()
            self.store.commit()

    def _loop(self):
        """Run scheduling ticks until the runtime receives its shutdown signal."""
        while not self.stopping.wait(0.05):
            self.tick()

    def tick(self):
        """Select eligible work and persist dispatch intent before starting threads.

        Hold the coordination lock while reserving capacity and resources so tasks
        cannot be double-dispatched within this engine.
        """
        with self.lock:
            if self.stopping.is_set():
                return
            state = self.store.scheduling_state()
            limits = CapacityLimits(
                self.execution_limit,
                {
                    project["id"]: project["max_concurrency"]
                    for project in state["projects"]
                },
                {agent["id"]: agent["max_concurrency"] for agent in state["agents"]},
                self.target_limits,
            )
            decision = select_work(state["tasks"], set(self.active), limits)
            projects = {project["id"]: project for project in state["projects"]}
            agents = {agent["id"]: agent for agent in state["agents"]}
            for task_id in decision.blocked:
                self.store.block(task_id)
            dispatch = []
            for task in decision.dispatch:
                signal = threading.Event()
                attempt_id = self.store.start(task)
                self.active[task["id"]] = signal
                operation = dict(
                    task,
                    context={
                        "project": projects[task["project_id"]],
                        "agent": agents[task["agent_id"]],
                        "team": [
                            {
                                "id": peer["id"],
                                "key": peer["key"],
                                "name": peer["name"],
                                "role": peer["role"],
                                "reports_to": peer["reports_to"],
                                "collaborators": peer.get("collaborators", []),
                            }
                            for peer in state["agents"]
                            if peer["project_id"] == task["project_id"]
                        ],
                        "handoffs": sorted(
                            [
                                source
                                for source in state["tasks"]
                                if source["project_id"] == task["project_id"]
                                and source["state"] == "accepted"
                                and (
                                    source["id"] in task["dependencies"]
                                    or agents[source["agent_id"]]["key"]
                                    in agents[task["agent_id"]].get("collaborators", [])
                                )
                            ],
                            key=lambda source: (
                                source["id"] not in task["dependencies"],
                                -(source.get("finished") or 0),
                            ),
                        ),
                    },
                )
                dispatch.append((operation, signal, attempt_id))
            # Persist dispatch intent before allowing external effects.
            self.store.commit()
            for task, signal, attempt_id in dispatch:
                thread = threading.Thread(
                    target=self._run,
                    args=(task, signal, attempt_id),
                    name="attempt-" + attempt_id,
                    daemon=True,
                )
                self.threads[task["id"]] = thread
                thread.start()

    def _run(self, task, cancelled, attempt_id):
        """Execute one assigned attempt and persist its result or failure.

        Pass project/agent context to the adapter, preserve effect uncertainty on
        errors, and release the active execution slot after saving the outcome.
        """
        receipt, error = None, None
        try:
            operation = dict(task, attempt_id=attempt_id)
            receipt = self.executor.execute(operation, cancelled)
            state = (
                "cancelled"
                if cancelled.is_set()
                else ("accepted" if receipt["accepted"] else "failed")
            )
            if state == "failed":
                error = "Acceptance check failed"
        except ExecutionCancelled as exc:
            state, error = "cancelled", str(exc)
            receipt = {"effect": "none", "operation_id": attempt_id, "accepted": False}
        except Exception as exc:
            state, error = "failed", str(exc)
            receipt = {
                "effect": "unknown",
                "operation_id": attempt_id,
                "accepted": False,
            }
        with self.lock:
            if cancelled.is_set():
                state = "cancelled"
            self.store.finish(task["id"], state, receipt, error)
            self.store.commit()
            self.active.pop(task["id"], None)
            self.threads.pop(task["id"], None)

    def snapshot(self):
        """Return durable records with derived team size, agent activity, and capacity.

        This read does not change scheduling state or launch additional work.
        """
        with self.lock:
            snapshot = self.store.snapshot()
            snapshot["active_executions"] = len(self.active)
        for goal in snapshot["goals"]:
            members = [
                task for task in snapshot["tasks"] if task["goal_id"] == goal["id"]
            ]
            goal.update(
                accepted=sum(task["state"] == "accepted" for task in members),
                total=len(members),
                state=goal_state(task["state"] for task in members),
            )
        for agent in snapshot["agents"]:
            agent["active_executions"] = sum(
                task["agent_id"] == agent["id"]
                and task["state"] in ("running", "cancelling")
                for task in snapshot["tasks"]
            )
            agent["state"] = "busy" if agent["active_executions"] else "idle"
        for project in snapshot["projects"]:
            project["team_size"] = sum(
                agent["project_id"] == project["id"] for agent in snapshot["agents"]
            )
            project["active_executions"] = sum(
                agent["active_executions"]
                for agent in snapshot["agents"]
                if agent["project_id"] == project["id"]
            )
            project["goal_count"] = sum(
                goal["project_id"] == project["id"] for goal in snapshot["goals"]
            )
        snapshot.update(
            targets=[self.connection.describe()],
            capacity={
                "execution_limit": self.execution_limit,
                "target_limits": self.target_limits,
            },
        )
        return snapshot
