"""Coordinate durable state, scheduling policy, and execution adapters."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from .connections import LocalConnection
from .execution import ExecutionCancelled, LocalExecutor
from .plans import parse_tasks
from .scheduling import CapacityLimits, goal_state, select_work
from .projects import parse_project, positive_integer
from .storage import Store
from .providers import ProviderConnections
from .models import ModelRuntime
from .cli_models import CLIRuntime
from .estimates import estimate_goals
from .planning import propose_goal, team_revision
from .sources import list_source_files
from .discovery import ProjectDiscovery, imported_definition
from .context import ProjectContext
from .source_bridges import SourceBridges
from .delivery import (
    DELIVERY_EXTENSIONS,
    DELIVERY_MIME_TYPES,
    MAX_DELIVERY_BYTES,
    MAX_DELIVERY_SOURCE_BYTES,
    final_artifact_tasks,
    parse_deliverable,
    require_renderer,
)


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
        deliverable_renderer=None,
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
        self.context = ProjectContext(directory)
        self.source_bridges = SourceBridges(directory, enabled=allow_source_access)
        self.allow_source_access = allow_source_access
        self.models = ModelRuntime()
        self.cli = CLIRuntime() if allow_cli else None
        self.executor = executor or LocalExecutor(
            self.connection,
            self.models,
            cli_models=self.cli,
            allow_source_access=allow_source_access,
            on_progress=self._record_activity,
            source_reader=self.read_project_files,
        )
        self.store = Store(directory)
        self.providers = ProviderConnections(directory, runtime=self.cli)
        self.discovery = ProjectDiscovery()
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.active = {}
        self.live_activity = {}
        self.threads = {}
        self.scheduler = None
        self.closed = False
        self.deliverable_renderer = deliverable_renderer
        self.delivering = {}

    def _require_open(self):
        """Raise RuntimeError when mutation is attempted during or after shutdown."""
        if self.closed or self.stopping.is_set():
            raise RuntimeError("Engine is shutting down")

    def _record_activity(self, task_id, event):
        """Keep bounded public progress in memory; never persist model reasoning streams."""
        if not isinstance(event, dict):
            return
        phase = event.get("phase", "working")
        if phase not in ("starting", "working", "writing", "finalizing"):
            return
        message = event.get("message", "Working on the assigned task")
        preview = event.get("preview")
        if not isinstance(message, str) or (
            preview is not None and not isinstance(preview, str)
        ):
            return
        with self.lock:
            if task_id not in self.active:
                return
            previous = self.live_activity.get(task_id, {})
            self.live_activity[task_id] = {
                "phase": phase,
                "message": message[:300],
                "updated_at": time.time(),
                "preview": preview[:8000]
                if preview is not None
                else previous.get("preview", ""),
            }

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
        if self.source_bridges.configured(project_id):
            return self.source_bridges.list_files(project_id)
        if not project.get("source_path"):
            raise ValueError("Import a local project before selecting source files")
        return list_source_files(project["source_path"])

    def read_project_files(self, project_id, paths):
        """Read explicit project sources through the configured source connection."""
        from .sources import read_source_files

        if not self.allow_source_access:
            raise PermissionError("Source files are unavailable in hosted mode")
        with self.lock:
            project = self.store.project(project_id)
        if self.source_bridges.configured(project_id):
            return self.source_bridges.read_files(project_id, paths)
        if not project.get("source_path"):
            raise PermissionError(
                "Source files require an imported local project or configured source bridge"
            )
        return read_source_files(project.get("source_path"), paths)

    def project_context(self, project_id):
        """Return durable shared context without reading repository contents."""
        with self.lock:
            self.store.project(project_id)
            saved = self.context.read(project_id)
        bridge = None
        if self.source_bridges.configured(project_id):
            try:
                inventory = self.source_bridges.inventory(project_id)
                bridge = {
                    **inventory["bridge"],
                    "repositories": inventory["repositories"],
                }
            except (ValueError, PermissionError, OSError) as exc:
                bridge = {
                    "name": "Source bridge",
                    "status": "unavailable",
                    "error": str(exc),
                    "repositories": [],
                }
        return {**saved, "source_bridge": bridge}

    def add_project_context(self, project_id, title, content):
        """Save shared evidence while idle so an active run keeps consistent context."""
        with self.lock:
            self._require_open()
            self.store.project(project_id)
            if self.store.db.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND state IN ('queued','running','cancelling') LIMIT 1",
                (project_id,),
            ).fetchone():
                raise ValueError(
                    "Wait for this project's active work to finish before changing shared context"
                )
            self.context.add(project_id, title, content)
        return self.project_context(project_id)

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
                    "team_model": document.get("team_model", project.get("team_model")),
                },
                self.connection,
            )
            with self.store.db:
                self.store.update_team(project_id, definition)
        return project_id

    def plan_goal(
        self,
        project_id,
        goal,
        coordinator=None,
        source_files=None,
        *,
        allow_cli=True,
        deliverable=None,
    ):
        """Ask a configured chart agent for a reviewable task plan without starting it."""
        with self.lock:
            self._require_open()
        return propose_goal(
            self,
            project_id,
            goal,
            coordinator,
            source_files,
            allow_cli=allow_cli,
            deliverable=deliverable,
        )

    def submit(
        self,
        project_id,
        title,
        tasks,
        *,
        allow_cli=True,
        expected_team_revision=None,
        deliverable=None,
    ):
        """Validate and atomically queue a goal in an existing project.

        project_id identifies the team; tasks name its agent keys and dependencies.
        Return a durable goal ID. Submission itself does not wait for execution.
        """
        if not isinstance(project_id, str):
            raise ValueError("An explicit project ID is required")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError("Title must contain 1–120 characters")
        presentation = parse_deliverable(deliverable)
        require_renderer(presentation)
        with self.lock:
            self._require_open()
            project = self.store.project(project_id)
            team = self.store.agents(project_id)
            if expected_team_revision is not None:
                if not isinstance(
                    expected_team_revision, str
                ) or expected_team_revision != team_revision(
                    project, team, self.context.read(project_id)
                ):
                    raise ValueError(
                        "The team or project changed after planning. Review a new plan before starting."
                    )
            definitions = parse_tasks(
                tasks,
                self.connection,
                project_id,
                team,
                source_root=project.get("source_path")
                if self.allow_source_access
                else None,
                source_validator=(
                    lambda paths: self.source_bridges.validate_paths(project_id, paths)
                )
                if self.source_bridges.configured(project_id)
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
                    if agent.get("execution_mode", "api") == "cli":
                        runtime.validate_speed(
                            agent["provider"],
                            agent["model"],
                            agent.get("execution_speed", "standard"),
                        )
            with self.store.db:
                goal_id = self.store.create_goal(
                    project_id,
                    title.strip(),
                    definitions,
                    "local-workspace",
                    presentation if deliverable is not None else None,
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
            worker_threads = list(
                set(self.threads.values()) | set(self.delivering.values())
            )
        if self.scheduler:
            self.scheduler.join()
        # Release the lock while workers persist final receipts and outcomes.
        for thread in worker_threads:
            if thread is not threading.current_thread():
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
                        "goal": self.store.goal(task["goal_id"]),
                        "shared_context": self.context.read(task["project_id"]),
                        "agent": agents[task["agent_id"]],
                        "team": [
                            {
                                "id": peer["id"],
                                "key": peer["key"],
                                "name": peer["name"],
                                "role": peer["role"],
                                "instructions": peer["instructions"],
                                "responsibilities": peer["responsibilities"],
                                "capabilities": peer["capabilities"],
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
                                and source["goal_id"] == task["goal_id"]
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
            self.live_activity.pop(task["id"], None)
        try:
            if not self.stopping.is_set():
                self.export_goal(task["goal_id"], automatic=True)
        finally:
            with self.lock:
                self.threads.pop(task["id"], None)

    def export_goal(self, goal_id, deliverable=None, *, automatic=False):
        """Render finished source artifacts into the requested format outside the lock.

        The source receipts stay unchanged. Export failures are durable and visible,
        so an available Markdown artifact cannot be mistaken for a delivered PDF.
        """
        with self.lock:
            if automatic and (self.closed or self.stopping.is_set()):
                return None
            self._require_open()
            goal = self.store.goal(goal_id)
            tasks = [task for task in self.store.tasks() if task["goal_id"] == goal_id]
            if automatic and (not goal.get("deliverable") or goal.get("delivery")):
                return goal.get("delivery")
            if not tasks or any(task["state"] != "accepted" for task in tasks):
                if automatic:
                    return None
                raise ValueError(
                    "Finish all goal tasks before exporting the deliverable"
                )
            if goal_id in self.delivering:
                if automatic:
                    return goal.get("delivery")
                raise ValueError("This goal's deliverable is already being prepared")
            settings = parse_deliverable(
                deliverable if deliverable is not None else goal.get("deliverable")
            )
            delivery = {**settings, "state": "rendering"}
            self.store.db.execute(
                "UPDATE goals SET deliverable=?,delivery=? WHERE id=?",
                (json.dumps(settings), json.dumps(delivery), goal_id),
            )
            self.store.commit()
            self.delivering[goal_id] = threading.current_thread()
        try:
            require_renderer(settings)
            finals = final_artifact_tasks(tasks)
            if not finals:
                raise ValueError(
                    "No saved text artifact is available to turn into a deliverable"
                )
            parts, source_tasks, total = [], [], 0
            for source in finals:
                receipt = source["receipt"]
                path = self.connection.path(receipt["artifact"])
                if path.stat().st_size > 65536:
                    raise ValueError("A source artifact exceeds its size limit")
                with path.open("rb") as stream:
                    data = stream.read(65537)
                if len(data) > 65536:
                    raise ValueError("A source artifact exceeds its size limit")
                if hashlib.sha256(data).hexdigest() != receipt.get("sha256"):
                    raise ValueError(
                        "A source artifact changed after its execution receipt"
                    )
                total += len(data)
                if total > MAX_DELIVERY_SOURCE_BYTES:
                    raise ValueError(
                        "Final artifacts exceed the combined presentation limit"
                    )
                text = data.decode("utf-8")
                parts.append(
                    text if len(finals) == 1 else f"## {source['title']}\n\n{text}"
                )
                source_tasks.append(source["id"])
            if self.deliverable_renderer is None:
                from .reports import render_deliverable

                renderer = render_deliverable
            else:
                renderer = self.deliverable_renderer
            result = renderer(
                "\n\n".join(parts), goal["title"], settings["format"], settings["style"]
            )
            if (
                not isinstance(result, bytes)
                or not result
                or len(result) > MAX_DELIVERY_BYTES
            ):
                raise ValueError(
                    "The rendered deliverable is empty or exceeds its size limit"
                )
            if settings["format"] == "pdf" and not result.startswith(b"%PDF-"):
                raise ValueError("The renderer did not return a valid PDF document")
            extension = DELIVERY_EXTENSIONS[settings["format"]]
            relative = f"deliverables/{goal_id}/report.{extension}"
            path = self.connection.project_path(goal["project_id"], relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=path.parent, prefix=".pending-"
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(result)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            delivery = {
                **settings,
                "state": "ready",
                "name": f"report.{extension}",
                "artifact": str(path.relative_to(self.connection.workspace)),
                "bytes": len(result),
                "sha256": hashlib.sha256(result).hexdigest(),
                "source_task_ids": source_tasks,
                "created": time.time(),
            }
        except Exception as exc:
            delivery = {**settings, "state": "failed", "error": str(exc)[:1000]}
        finally:
            with self.lock:
                self.store.db.execute(
                    "UPDATE goals SET delivery=? WHERE id=?",
                    (json.dumps(delivery), goal_id),
                )
                self.store.commit()
                self.delivering.pop(goal_id, None)
        return delivery

    def read_deliverable(self, goal_id):
        """Read one verified binary deliverable through its goal, never an arbitrary path."""
        with self.lock:
            goal = self.store.goal(goal_id)
            delivery = goal.get("delivery") or {}
        if delivery.get("state") != "ready":
            raise ValueError("This goal does not have a ready deliverable")
        path = self.connection.path(delivery["artifact"])
        if path.stat().st_size > MAX_DELIVERY_BYTES:
            raise ValueError("Deliverable exceeds its size limit")
        with path.open("rb") as stream:
            data = stream.read(MAX_DELIVERY_BYTES + 1)
        if len(data) > MAX_DELIVERY_BYTES:
            raise ValueError("Deliverable exceeds its size limit")
        if hashlib.sha256(data).hexdigest() != delivery["sha256"]:
            raise ValueError(
                "The deliverable changed after generation; export it again"
            )
        return data, delivery["name"], DELIVERY_MIME_TYPES[delivery["format"]]

    def snapshot(self):
        """Return durable records with derived team size, agent activity, and capacity.

        This read does not change scheduling state or launch additional work.
        """
        with self.lock:
            snapshot = self.store.snapshot()
            snapshot["active_executions"] = len(self.active)
            activity = {key: dict(value) for key, value in self.live_activity.items()}
        for goal in snapshot["goals"]:
            members = [
                task for task in snapshot["tasks"] if task["goal_id"] == goal["id"]
            ]
            goal.update(
                accepted=sum(task["state"] == "accepted" for task in members),
                total=len(members),
                state=goal_state(task["state"] for task in members),
            )
            goal["deliverable"] = parse_deliverable(goal.get("deliverable"))
        for task in snapshot["tasks"]:
            task["activity"] = activity.get(task["id"])
        for agent in snapshot["agents"]:
            agent["active_executions"] = sum(
                task["agent_id"] == agent["id"]
                and task["state"] in ("running", "cancelling")
                for task in snapshot["tasks"]
            )
            agent["state"] = "busy" if agent["active_executions"] else "idle"
        for project in snapshot["projects"]:
            project["has_source_bridge"] = self.source_bridges.configured(project["id"])
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
        timing = estimate_goals(snapshot, cli_timeout=getattr(self.cli, "timeout", 120))
        task_timing = {}
        for goal in snapshot["goals"]:
            goal_timing = timing.get(goal["id"])
            if goal_timing:
                task_timing.update(goal_timing.pop("task_timings", {}))
            goal["timing"] = goal_timing
        for task in snapshot["tasks"]:
            task["timing"] = task_timing.get(task["id"])
        return snapshot
