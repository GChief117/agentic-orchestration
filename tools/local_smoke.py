#!/usr/bin/env python3
"""Exercise a real local API process, project coordination, and restart persistence."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class LocalServer:
    def __init__(self, directory):
        self.directory = directory
        self.process = None
        self.log = None
        self.url = None
        self.token = None

    def start(self):
        log_path = self.directory / "server.log"
        self.log = log_path.open("w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(ROOT / "app.py"),
                "--port",
                "0",
                "--execution-limit",
                "3",
                "--data-dir",
                str(self.directory),
            ],
            cwd=ROOT,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Backend exited during startup; inspect server.log")
            match = re.search(
                r"Backend API: (http://127\.0\.0\.1:\d+)", log_path.read_text()
            )
            if match:
                self.url = match.group(1)
                token_file = self.directory / "api-token"
                self.token = token_file.read_text()
                if stat.S_IMODE(token_file.stat().st_mode) != 0o600:
                    raise AssertionError("API credential permissions must be 0600")
                self.request("/api/state")
                return
            time.sleep(0.05)
        raise TimeoutError("Backend startup timed out")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def request(self, path, body=None, *, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(
            self.url + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)


def wait_for_goals(server, identifiers):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = server.request("/api/state")
        goals = [goal for goal in state["goals"] if goal["id"] in identifiers]
        if len(goals) == len(identifiers) and all(
            goal["state"] in ("accepted", "failed", "cancelled") for goal in goals
        ):
            return state
        time.sleep(0.02)
    raise TimeoutError("Goals did not finish")


def check_capacity(state):
    projects = {
        project["id"]: project["max_concurrency"] for project in state["projects"]
    }
    agents = {agent["id"]: agent["max_concurrency"] for agent in state["agents"]}
    limits = state["capacity"]
    events = []
    for task in state["tasks"]:
        if task["started"] is not None and task["finished"] is not None:
            events.extend([(task["started"], 1, task), (task["finished"], -1, task)])
    live, project_load, agent_load, target_load = {}, Counter(), Counter(), Counter()
    peak = 0
    for _, delta, task in sorted(events, key=lambda item: (item[0], item[1])):
        project_load[task["project_id"]] += delta
        agent_load[task["agent_id"]] += delta
        target_load[task["target"]] += delta
        if delta > 0:
            for other in live.values():
                assert not any(
                    name == held and "write" in (mode, held_mode)
                    for name, mode in task["resources"]
                    for held, held_mode in other["resources"]
                ), "Resource claim overlap"
            live[task["id"]] = task
        else:
            live.pop(task["id"])
        peak = max(peak, len(live))
        assert len(live) <= limits["execution_limit"], "Global execution limit exceeded"
        assert all(
            count <= projects[key] for key, count in project_load.items()
        ), "Project limit exceeded"
        assert all(
            count <= agents[key] for key, count in agent_load.items()
        ), "Agent limit exceeded"
        assert all(
            count <= limits["target_limits"][key] for key, count in target_load.items()
        ), "Target limit exceeded"
    by_id = {task["id"]: task for task in state["tasks"]}
    for task in state["tasks"]:
        if task["started"] is not None:
            for dependency in task["dependencies"]:
                parent = by_id[dependency]
                assert (
                    parent["state"] == "accepted"
                ), "Task started before dependency acceptance"
                assert (
                    parent["finished"] <= task["started"]
                ), "Dependency execution overlapped"
    return peak


def run(directory):
    server = LocalServer(directory)
    checks = []
    try:
        server.start()
        try:
            server.request("/api/state", authenticated=False)
        except HTTPError as error:
            assert error.code == 403
            error.close()
        else:
            raise AssertionError("Unauthenticated API access was accepted")
        checks.append("Unauthenticated API requests rejected")

        template = json.loads((ROOT / "examples/greenfield_project.json").read_text())
        template.update(name="Local test / Alpha", max_concurrency=1)
        alpha = server.request("/api/projects", template)["id"]
        template.update(name="Local test / Beta", max_concurrency=2, team_size=4)
        second_builder = dict(
            template["agents"][1], key="second-builder", name="Second builder"
        )
        template["agents"].append(second_builder)
        beta = server.request("/api/projects", template)["id"]
        checks.append("Projects initialized with separate 3-agent and 4-agent teams")

        plan = json.loads((ROOT / "examples/backend_goal.json").read_text())
        first = json.loads(json.dumps(plan))
        first.update(project_id=alpha, title="Alpha artifact")
        first["tasks"][1]["parameters"]["content"] = "alpha output\n"
        first["tasks"][2]["parameters"]["expected"] = "alpha output\n"
        second = json.loads(json.dumps(plan))
        second.update(project_id=beta, title="Beta artifact")
        second["tasks"][1]["parameters"]["content"] = "beta output\n"
        second["tasks"][2]["parameters"]["expected"] = "beta output\n"
        second["tasks"].append(
            {
                "id": "parallel-write",
                "agent": "second-builder",
                "capability": "files.write",
                "parameters": {"path": "parallel.txt", "content": "parallel output\n"},
            }
        )
        goals = [server.request("/api/goals", value)["id"] for value in (first, second)]
        state = wait_for_goals(server, goals)
        assert all(goal["state"] == "accepted" for goal in state["goals"])
        assert len(state["tasks"]) == 7
        assert len(state["attempts"]) == 7
        peak = check_capacity(state)
        checks.append("Seven tasks accepted with persisted, agent-linked attempts")
        checks.append(
            "Recorded intervals satisfy global, project, agent, target, resource, and dependency limits"
        )

        for task in state["tasks"]:
            receipt = task["receipt"]
            if receipt.get("artifact"):
                result = server.request("/api/artifact/" + task["id"])
                assert (
                    hashlib.sha256(result["content"].encode()).hexdigest()
                    == receipt["sha256"]
                )
                assert receipt["project_id"] == task["project_id"]
                assert receipt["agent_id"] == task["agent_id"]
        files = {
            task["project_id"]: task["receipt"]["artifact"]
            for task in state["tasks"]
            if task["capability"] == "files.write"
            and task["title"] == "Write the release artifact"
        }
        assert files[alpha] != files[beta]
        checks.append(
            "Actual artifact contents match receipt digests and remain separated by project"
        )

        before = len(state["tasks"])
        try:
            server.request(
                "/api/goals",
                {
                    "project_id": alpha,
                    "title": "Disallowed assignment",
                    "tasks": [
                        {
                            "id": "bad",
                            "agent": "reviewer",
                            "capability": "files.write",
                            "parameters": {
                                "path": "bad.txt",
                                "content": "must not write",
                            },
                        }
                    ],
                },
            )
        except HTTPError as error:
            assert error.code == 400
            error.close()
        else:
            raise AssertionError("Agent capability violation was accepted")
        assert len(server.request("/api/state")["tasks"]) == before
        checks.append("Unauthorized agent assignment rejected before task creation")

        failure = server.request(
            "/api/goals",
            {
                "project_id": alpha,
                "title": "Expected verification failure",
                "tasks": [
                    {
                        "id": "mismatch",
                        "agent": "reviewer",
                        "capability": "files.inspect",
                        "parameters": {
                            "path": "release/checklist.txt",
                            "expected": "wrong",
                        },
                    },
                    {
                        "id": "blocked",
                        "agent": "builder",
                        "capability": "files.write",
                        "parameters": {
                            "path": "must-not-exist.txt",
                            "content": "blocked",
                        },
                        "depends_on": ["mismatch"],
                    },
                ],
            },
        )["id"]
        state = wait_for_goals(server, [failure])
        failed_tasks = [task for task in state["tasks"] if task["goal_id"] == failure]
        assert [task["state"] for task in failed_tasks] == ["failed", "blocked"]
        assert not (
            directory / "artifacts/projects" / alpha / "must-not-exist.txt"
        ).exists()
        checks.append("Intentional verification failure blocks downstream effects")

        server.stop()
        server.start()
        recovered = server.request("/api/state")
        assert recovered["projects"] == state["projects"]
        assert recovered["agents"] == state["agents"]
        assert recovered["tasks"] == state["tasks"]
        assert recovered["attempts"] == state["attempts"]
        checks.append(
            "Restart preserves charters, teams, task outcomes, and attempts without replay"
        )
        return {
            "status": "passed",
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "projects": 2,
            "team_sizes": [3, 4],
            "project_limits": [1, 2],
            "global_execution_limit": 3,
            "peak_recorded_executions": peak,
            "accepted_tasks": 7,
            "expected_failed_tasks": 1,
            "expected_blocked_tasks": 1,
            "checks": checks,
        }
    finally:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    directory = args.output_dir or Path(
        tempfile.mkdtemp(prefix="local-smoke-", dir=runtime)
    )
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "state.sqlite3").exists():
        parser.error("Use a fresh output directory to preserve prior runs")
    try:
        report = run(directory)
    except Exception as error:
        report = {"status": "failed", "error": str(error)}
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        raise
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Local report: {report_path}")


if __name__ == "__main__":
    main()
