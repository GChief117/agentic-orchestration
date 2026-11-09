"""Reviewed setup steps execute in temporary local fixtures without AWS access."""

from contextlib import contextmanager
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from orchestration.workspace_setup import (
    REMOTE_WORKSPACE_SETUP,
    validate_setup_parameters,
    validate_setup_steps,
)


class WorkspaceSetupTests(unittest.TestCase):
    def setUp(self):
        """Make isolated repository and private-state locations for every operation."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "workspace"
        self.repository = self.root / "sample"
        self.repository.mkdir(parents=True)
        self.home = self.base / "home"
        self.home.mkdir()
        self.reviewed = self.repository / "reviewed.txt"
        self.reviewed.write_text("reviewed input")
        self.step = {
            "label": "Install reviewed fixture",
            "repository": "sample",
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; p=Path('result.txt'); p.write_text((p.read_text() if p.exists() else '')+'x'); print('SECRET_NATIVE_OUTPUT')",
            ],
            "timeout_seconds": 5,
            "input_hashes": {
                "reviewed.txt": hashlib.sha256(self.reviewed.read_bytes()).hexdigest()
            },
            "verify_argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('result.txt').read_text() == 'x'",
            ],
            "aws_account_id": "551127968046",
            "aws_profile": "default",
        }
        self.payload = {
            "hostname": socket.gethostname(),
            "username": getpass.getuser(),
            "root": str(self.root),
            "repositories": ["sample"],
            "operation_id": "operation-1",
            "parameters": {"step": "sample.install"},
            "setup_step": self.step,
        }
        self.remote = {}
        exec(compile(REMOTE_WORKSPACE_SETUP, "workspace-setup", "exec"), self.remote)

    @contextmanager
    def isolated(self, *, aws=True):
        """Keep state under the fixture home and replace only AWS verification."""
        with patch.object(Path, "home", return_value=self.home):
            if aws:
                with patch.dict(self.remote, {"setup_verify_aws": lambda *_: None}):
                    yield
            else:
                yield

    def record(self):
        """Read the fixture's single private operation ledger after a call."""
        return json.loads(next(self.home.rglob("*.json")).read_text())

    def test_parameter_and_configuration_boundaries(self):
        self.assertEqual(
            validate_setup_parameters({"step": "sample.install"}),
            {"step": "sample.install"},
        )
        for parameters in (
            {},
            {"step": "x", "argv": ["evil"]},
            {"step": "../outside"},
            None,
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_setup_parameters(parameters)
        invalid = [
            {"repository": "outside"},
            {"repository": "/tmp"},
            {"argv": ["relative"]},
            {"timeout_seconds": 1801},
            {"timeout_seconds": True},
            {"input_hashes": {"../outside": "a" * 64}},
            {"input_hashes": {}},
            {"aws_account_id": "wrong"},
            {"aws_account_id": None},
            {"aws_profile": None},
            {"aws_account_id": None, "aws_profile": "invalid profile"},
            {"aws_account_id": "", "aws_profile": None},
            {"env": {"AWS_SECRET_ACCESS_KEY": "private"}},
            {"env": {"UV_INDEX_URL": "https://user:private@example.test"}},
            {"env": {"BASH_ENV": "/tmp/script"}},
            {"env": {"HOME": "/other-user"}},
            {"env": {"AWS_CONFIG_FILE": "relative"}},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_setup_steps(
                    {
                        "repositories": ["sample"],
                        "setup_steps": {"sample.install": {**self.step, **changes}},
                    }
                )

    def test_configuration_allows_reviewed_runtime_profile_and_large_migration_chain(
        self,
    ):
        step = {
            **self.step,
            "input_hashes": {f"migrations/{n}.py": "a" * 64 for n in range(168)},
            "env": {
                "AWS_PROFILE": "polaris-runtime",
                "AWS_CONFIG_FILE": "/private/runtime.config",
                "UV_LOCKED": "1",
            },
        }
        result = validate_setup_steps(
            {"repositories": ["sample"], "setup_steps": {"sample.install": step}}
        )
        self.assertEqual(
            result["sample.install"]["env"]["AWS_PROFILE"], "polaris-runtime"
        )
        self.assertEqual(len(result["sample.install"]["input_hashes"]), 168)

    def test_configuration_accepts_explicitly_local_steps(self):
        step = {**self.step, "aws_account_id": None, "aws_profile": None}
        result = validate_setup_steps(
            {"repositories": ["sample"], "setup_steps": {"sample.local": step}}
        )
        self.assertIsNone(result["sample.local"]["aws_account_id"])
        self.assertIsNone(result["sample.local"]["aws_profile"])

    def test_local_environment_removes_inherited_aws_values_without_none(self):
        step = {**self.step, "aws_account_id": None, "aws_profile": None}
        with patch.dict(
            os.environ,
            {
                "AWS_PROFILE": "inherited",
                "AWS_CONFIG_FILE": "/inherited/config",
                "AWS_ACCESS_KEY_ID": "private",
                "AWS_SESSION_TOKEN": "private",
            },
        ):
            environment = self.remote["setup_environment"](step)
        for key in (
            "AWS_PROFILE",
            "AWS_CONFIG_FILE",
            "AWS_ACCESS_KEY_ID",
            "AWS_SESSION_TOKEN",
        ):
            self.assertNotIn(key, environment)
        self.assertTrue(all(isinstance(value, str) for value in environment.values()))
        step["env"] = {"AWS_PROFILE": "operator-reviewed"}
        self.assertEqual(
            self.remote["setup_environment"](step)["AWS_PROFILE"], "operator-reviewed"
        )

    def test_local_steps_skip_aws_guard_and_reuse_their_false_receipt(self):
        self.step.update(aws_account_id=None, aws_profile=None)
        guard = unittest.mock.Mock(side_effect=AssertionError("AWS guard was called"))
        with (
            self.isolated(aws=False),
            patch.dict(self.remote, {"setup_verify_aws": guard}),
        ):
            first = self.remote["workspace_setup"](self.payload)
            second = self.remote["workspace_setup"](self.payload)
        guard.assert_not_called()
        self.assertFalse(first["aws_account_verified"])
        self.assertFalse(second["aws_account_verified"])
        self.assertTrue(second["already_completed"])
        self.assertEqual((self.repository / "result.txt").read_text(), "x")

    def test_cached_receipt_guard_result_must_match_the_reviewed_step(self):
        for local in (True, False):
            with self.subTest(local=local):
                self.step.update(
                    aws_account_id=None if local else "551127968046",
                    aws_profile=None if local else "default",
                )
                self.payload["operation_id"] = "guard-result-" + str(local)
                self.step["argv"] = [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('result.txt').write_text('x')",
                ]
                with self.isolated():
                    self.remote["workspace_setup"](self.payload)
                    record_path = next(
                        path
                        for path in self.home.rglob("*.json")
                        if json.loads(path.read_text()).get("operation_id")
                        == self.payload["operation_id"]
                    )
                    ledger = json.loads(record_path.read_text())
                    ledger["result"]["aws_account_verified"] = local
                    record_path.write_text(json.dumps(ledger))
                    with self.assertRaisesRegex(ValueError, "receipt is invalid"):
                        self.remote["workspace_setup"](self.payload)

    def test_success_verification_and_duplicate_identity_do_not_replay(self):
        guard = unittest.mock.Mock()
        with (
            self.isolated(aws=False),
            patch.dict(self.remote, {"setup_verify_aws": guard}),
        ):
            first = self.remote["workspace_setup"](self.payload)
            second = self.remote["workspace_setup"](self.payload)
        guard.assert_called_once()
        self.assertTrue(first["aws_account_verified"])
        self.assertTrue(second["aws_account_verified"])
        self.assertTrue(first["verified"])
        self.assertTrue(second["already_completed"])
        self.assertEqual((self.repository / "result.txt").read_text(), "x")
        self.assertNotIn("SECRET_NATIVE_OUTPUT", json.dumps(first))
        self.assertNotIn("SECRET_NATIVE_OUTPUT", json.dumps(self.record()))
        self.assertEqual(self.record()["state"], "complete")
        for path in self.home.rglob("*.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_wrong_identity_or_changed_inputs_fail_before_execution(self):
        with self.isolated():
            with self.assertRaisesRegex(ValueError, "host and user"):
                self.remote["workspace_setup"]({**self.payload, "hostname": "other"})
            self.reviewed.write_text("changed after review")
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                self.remote["workspace_setup"](self.payload)
        self.assertFalse((self.repository / "result.txt").exists())

    def test_cached_receipt_never_returns_extra_native_fields(self):
        with self.isolated():
            self.remote["workspace_setup"](self.payload)
            path = next(self.home.rglob("*.json"))
            ledger = json.loads(path.read_text())
            ledger["result"]["stdout"] = "SECRET_IN_TAMPERED_RECEIPT"
            path.write_text(json.dumps(ledger))
            result = self.remote["workspace_setup"](self.payload)
        self.assertTrue(result["already_completed"])
        self.assertNotIn("stdout", result)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_wrong_aws_account_fails_before_repository_command(self):
        original = subprocess.run

        def runner(args, **kwargs):
            if args[0] == "/opt/homebrew/bin/aws":
                return subprocess.CompletedProcess(
                    args, 0, '{"Account":"792122489747"}', "private diagnostic"
                )
            return original(args, **kwargs)

        with (
            self.isolated(aws=False),
            patch.object(subprocess, "run", side_effect=runner),
        ):
            with self.assertRaisesRegex(ValueError, "configured work account"):
                self.remote["workspace_setup"](self.payload)
        self.assertFalse((self.repository / "result.txt").exists())
        self.assertNotIn("792122489747", json.dumps(self.record()))

    def test_preflight_uses_native_profile_not_runtime_process_profile(self):
        observed = []

        def runner(args, **kwargs):
            observed.append((args, kwargs))
            return subprocess.CompletedProcess(
                args, 0, '{"Account":"551127968046"}', ""
            )

        step = {
            **self.step,
            "env": {
                "AWS_PROFILE": "polaris-runtime",
                "AWS_CONFIG_FILE": "/private/runtime.config",
            },
        }
        with (
            patch.object(subprocess, "run", side_effect=runner),
            patch.dict(
                os.environ,
                {
                    "AWS_CONFIG_FILE": "/inherited/config",
                    "AWS_SESSION_TOKEN": "private",
                },
            ),
        ):
            environment = self.remote["setup_environment"](step)
            self.remote["setup_verify_aws"](step, environment)
        self.assertEqual(environment["AWS_PROFILE"], "polaris-runtime")
        self.assertEqual(environment["AWS_CONFIG_FILE"], "/private/runtime.config")
        self.assertNotIn("AWS_CONFIG_FILE", observed[0][1]["env"])
        self.assertNotIn("AWS_SESSION_TOKEN", observed[0][1]["env"])
        self.assertEqual(
            observed[0][0][observed[0][0].index("--profile") + 1], "default"
        )

    def test_verifier_failure_is_not_a_success_and_cannot_be_replayed(self):
        self.step["verify_argv"] = [
            sys.executable,
            "-c",
            "import sys; print('SECRET_FAILURE'); sys.exit(7)",
        ]
        with self.isolated():
            with self.assertRaisesRegex(
                ValueError, "verification exited with code 7"
            ) as error:
                self.remote["workspace_setup"](self.payload)
            self.assertNotIn("SECRET", str(error.exception))
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                self.remote["workspace_setup"](self.payload)
        self.assertEqual((self.repository / "result.txt").read_text(), "x")
        self.assertEqual(self.record()["state"], "failed")

    def test_timeout_terminates_owned_child_and_preserves_failure_receipt(self):
        self.step.update(
            argv=[sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=1,
        )
        start = time.monotonic()
        with self.isolated(), self.assertRaisesRegex(ValueError, "time limit"):
            self.remote["workspace_setup"](self.payload)
        self.assertLess(time.monotonic() - start, 5)
        ledger = self.record()
        with self.assertRaises(ProcessLookupError):
            os.kill(ledger["child_pid"], 0)
        self.assertEqual(ledger["state"], "failed")

    def test_repository_and_input_symlinks_are_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        payload = copy.deepcopy(self.payload)
        payload["repositories"] = ["linked"]
        payload["setup_step"]["repository"] = "linked"
        with self.isolated(), self.assertRaisesRegex(ValueError, "canonical"):
            self.remote["workspace_setup"](payload)
        self.reviewed.unlink()
        self.reviewed.symlink_to(outside / "input.txt")
        with self.isolated(), self.assertRaisesRegex(ValueError, "redirected"):
            self.remote["workspace_setup"](self.payload)

    def test_active_workspace_lock_blocks_a_second_operation(self):
        with self.isolated():
            state = self.remote["setup_state"](self.root)
            with self.remote["setup_workspace_lock"](state):
                with self.assertRaisesRegex(ValueError, "owns this workspace"):
                    self.remote["workspace_setup"](self.payload)
        self.assertFalse((self.repository / "result.txt").exists())

    def test_other_running_operation_blocks_new_identity_even_after_lock_released(self):
        with self.isolated():
            directory = self.remote["setup_state"](self.root)
            other = directory / (
                hashlib.sha256(b"other-operation").hexdigest() + ".json"
            )
            ledger = {
                "operation_id": "other-operation",
                "state": "running",
                "child_pid": os.getpid(),
                "child_birth": self.remote["setup_process_birth"](os.getpid()),
            }
            self.remote["setup_write_record"](other, ledger)
            with self.assertRaisesRegex(ValueError, "still running"):
                self.remote["workspace_setup"](self.payload)
            ledger["child_birth"] = "different process birth"
            self.remote["setup_write_record"](other, ledger)
            with self.assertRaisesRegex(ValueError, "needs reconciliation"):
                self.remote["workspace_setup"](self.payload)
        self.assertFalse((self.repository / "result.txt").exists())

    def test_other_stale_running_operation_blocks_but_completed_and_failed_do_not(self):
        with self.isolated():
            directory = self.remote["setup_state"](self.root)
            other = directory / (
                hashlib.sha256(b"other-operation").hexdigest() + ".json"
            )
            ledger = {
                "operation_id": "other-operation",
                "state": "running",
                "supervisor_pid": 2147483647,
                "supervisor_birth": "stale birth",
            }
            self.remote["setup_write_record"](other, ledger)
            with self.assertRaisesRegex(ValueError, "needs reconciliation"):
                self.remote["workspace_setup"](self.payload)
            ledger["state"] = "failed"
            self.remote["setup_write_record"](other, ledger)
            completed = directory / (
                hashlib.sha256(b"completed-operation").hexdigest() + ".json"
            )
            self.remote["setup_write_record"](completed, {"state": "complete"})
            result = self.remote["workspace_setup"](self.payload)
        self.assertTrue(result["verified"])

    def test_other_operation_scan_has_a_hard_bound(self):
        with self.isolated():
            directory = self.remote["setup_state"](self.root)
            for number in range(3):
                other = directory / (
                    hashlib.sha256(str(number).encode()).hexdigest() + ".json"
                )
                self.remote["setup_write_record"](other, {"state": "complete"})
            with patch.dict(self.remote, {"SETUP_LEDGER_LIMIT": 2}):
                with self.assertRaisesRegex(ValueError, "ledger limit"):
                    self.remote["workspace_setup"](self.payload)
        self.assertFalse((self.repository / "result.txt").exists())

    def test_cleanup_targets_the_owned_process_group_and_escalates(self):
        process = unittest.mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 3), 0]
        with patch.object(os, "killpg") as terminate:
            self.remote["setup_stop_process"](process)
        self.assertEqual(
            terminate.call_args_list,
            [
                unittest.mock.call(12345, signal.SIGTERM),
                unittest.mock.call(12345, signal.SIGKILL),
            ],
        )

    def test_interruption_prevents_another_phase_from_starting(self):
        with patch.object(subprocess, "Popen") as launch:
            with self.assertRaisesRegex(self.remote["SetupInterrupted"], "interrupted"):
                self.remote["setup_run_command"](
                    self.step["argv"],
                    self.repository,
                    {},
                    time.monotonic() + 5,
                    [True],
                    {},
                    self.base / "unused",
                    "verification",
                )
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
