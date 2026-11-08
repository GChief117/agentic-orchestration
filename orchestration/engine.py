"""Coordinate durable state, scheduling policy, and execution adapters."""

from pathlib import Path
import threading

from .connections import LocalConnection
from .execution import ExecutionCancelled, LocalExecutor
from .plans import parse_tasks
from .scheduling import goal_state, select_work
from .storage import Store


class Engine:
    def __init__(self, directory, workers=2, executor=None):
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        self.connection = LocalConnection(Path(directory) / "artifacts")
        self.executor = executor or LocalExecutor(self.connection)
        self.store = Store(directory)
        self.workers = workers
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.active = {}
        self.threads = {}
        self.scheduler = None
        self.closed = False

    def submit(self, title, tasks):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError("Title must contain 1–120 characters")
        definitions = parse_tasks(tasks, self.connection)
        with self.lock:
            if self.closed or self.stopping.is_set():
                raise RuntimeError("Engine is shutting down")
            goal_id = self.store.create_goal(
                title.strip(), definitions, "local-workspace"
            )
            self.store.commit()
        return goal_id

    def start(self):
        with self.lock:
            if self.closed or self.stopping.is_set():
                raise RuntimeError("Engine cannot restart after shutdown")
            if self.scheduler is None:
                self.scheduler = threading.Thread(
                    target=self._loop, name="orchestration-scheduler", daemon=True
                )
                self.scheduler.start()

    def close(self):
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
        with self.lock:
            for task_id in self.store.cancel(goal_id):
                if task_id in self.active:
                    self.active[task_id].set()
            self.store.commit()

    def _loop(self):
        while not self.stopping.wait(0.05):
            self.tick()

    def tick(self):
        with self.lock:
            if self.stopping.is_set():
                return
            decision = select_work(self.store.tasks(), set(self.active), self.workers)
            for task_id in decision.blocked:
                self.store.block(task_id)
            dispatch = []
            for task in decision.dispatch:
                signal = threading.Event()
                attempt_id = self.store.start(task)
                self.active[task["id"]] = signal
                dispatch.append((task, signal, attempt_id))
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
        with self.lock:
            snapshot = self.store.snapshot()
            snapshot["active_workers"] = len(self.active)
        for goal in snapshot["goals"]:
            members = [
                task for task in snapshot["tasks"] if task["goal_id"] == goal["id"]
            ]
            goal.update(
                accepted=sum(task["state"] == "accepted" for task in members),
                total=len(members),
                state=goal_state(task["state"] for task in members),
            )
        snapshot.update(targets=[self.connection.describe()], worker_limit=self.workers)
        return snapshot
