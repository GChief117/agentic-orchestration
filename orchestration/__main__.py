"""Execute a backend task graph and print its durable result as JSON."""

import argparse
import json
from pathlib import Path
import time

from .engine import Engine


def main():
    """Run a task plan in a new or existing project using explicit execution capacity.

    Print the final snapshot and return a success/failure exit code; always close
    the engine and request cancellation if the supplied deadline is exceeded.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(".runtime"))
    parser.add_argument("--execution-limit", type=int, required=True)
    project = parser.add_mutually_exclusive_group(required=True)
    project.add_argument(
        "--project", type=Path, help="Create a greenfield project from this definition"
    )
    project.add_argument(
        "--project-id", help="Use an existing project and its assigned team"
    )
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        plan = json.loads(args.plan.read_text())
        if not isinstance(plan, dict):
            raise ValueError("Plan must be an object")
        engine = Engine(args.data_dir, execution_limit=args.execution_limit)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"{exc}\n")
    try:
        project_id = args.project_id
        if args.project:
            project_id = engine.create_project(json.loads(args.project.read_text()))
        goal_id = engine.submit(project_id, plan.get("title"), plan.get("tasks"))
        engine.start()
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            state = engine.snapshot()
            goal = next(goal for goal in state["goals"] if goal["id"] == goal_id)
            if goal["state"] in ("accepted", "failed", "cancelled"):
                print(json.dumps(state, indent=2))
                return 0 if goal["state"] == "accepted" else 1
            time.sleep(0.02)
        engine.cancel_goal(goal_id)
        parser.exit(1, "Deadline exceeded; cancellation requested\n")
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
