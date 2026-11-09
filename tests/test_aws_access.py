"""AWS sign-in uses native tools without live sessions or credential capture."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestration.aws_access import (
    AWS_CAPABILITIES,
    REMOTE_AWS_ACCESS,
    validate_aws_parameters,
)


def remote_namespace():
    """Load the fixed remote program without opening any native application."""
    namespace = {}
    exec(compile(REMOTE_AWS_ACCESS, "aws-access", "exec"), namespace)
    return namespace


@contextmanager
def native_test_environment(namespace, runner, launcher=None, home=None):
    """Provide a fake installed CLI, verified host, and subprocess runner."""
    with tempfile.TemporaryDirectory() as temporary, patch.object(
        Path, "home", return_value=home or Path(temporary).resolve()
    ), patch.dict(
        namespace,
        {
            "aws_process_identity": lambda pid, **kwargs: {
                "uid": os.getuid(),
                "birth": "Sun Nov 8 12:00:00 2026",
            }
        },
    ), patch.object(
        namespace["socket"], "gethostname", return_value="test.local"
    ), patch.object(
        namespace["getpass"], "getuser", return_value="tester"
    ), patch.object(
        Path, "is_file", return_value=True
    ), patch.object(
        namespace["os"], "access", return_value=True
    ), patch.object(
        namespace["subprocess"], "run", side_effect=runner
    ), patch.object(
        namespace["subprocess"],
        "Popen",
        side_effect=launcher or AssertionError("Unexpected native process launch"),
    ):
        yield


class AWSAccessTests(unittest.TestCase):
    def setUp(self):
        """Prepare the only authorized AWS workflow used by these tests."""
        self.namespace = remote_namespace()
        self.payload = {
            "hostname": "test.local",
            "username": "tester",
            "aws_login_enabled": True,
            "parameters": {},
        }

    def test_capabilities_have_no_task_supplied_options(self):
        self.assertEqual(
            AWS_CAPABILITIES,
            {"bridge.aws.login", "bridge.aws.status", "bridge.aws.remote_login"},
        )
        for capability in AWS_CAPABILITIES:
            self.assertEqual(validate_aws_parameters(capability, {}), {})
            for parameters in (None, [], {"profile": "admin"}, {"command": "shell"}):
                with self.subTest(capability=capability, parameters=parameters):
                    with self.assertRaises(ValueError):
                        validate_aws_parameters(capability, parameters)
        with self.assertRaises(ValueError):
            validate_aws_parameters("aws.run", {})

    def test_guard_rejects_disabled_wrong_identity_and_custom_parameters(self):
        runner = Mock()
        with native_test_environment(self.namespace, runner):
            for changes in (
                {"aws_login_enabled": False},
                {"aws_login_enabled": 1},
                {"hostname": "other.local"},
                {"username": "other-user"},
                {"parameters": {"region": "us-west-2"}},
                {"parameters": []},
            ):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    self.namespace["aws_status"]({**self.payload, **changes})
        runner.assert_not_called()

    def test_status_discards_identity_and_removes_credential_overrides(self):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args, 0, '{"Account":"private-account","Arn":"private-identity"}', ""
            )

        overrides = {
            "AWS_ACCESS_KEY_ID": "private-key",
            "AWS_SECRET_ACCESS_KEY": "private-secret",
            "AWS_SESSION_TOKEN": "private-session",
            "AWS_PROFILE": "other-profile",
            "AWS_CONFIG_FILE": "/other/config",
            "AWS_SHARED_CREDENTIALS_FILE": "/other/credentials",
            "AWS_ENDPOINT_URL_STS": "https://other-endpoint.invalid",
            "PATH": "/usr/bin",
        }
        with native_test_environment(self.namespace, run), patch.dict(
            self.namespace["os"].environ, overrides, clear=True
        ):
            result = self.namespace["aws_status"](self.payload)
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["requires_user_action"])
        self.assertEqual(
            calls[0][0],
            [
                "/opt/homebrew/bin/aws",
                "sts",
                "get-caller-identity",
                "--profile",
                "default",
                "--region",
                "us-east-1",
                "--no-cli-pager",
            ],
        )
        for key in overrides:
            if key.startswith("AWS_"):
                self.assertNotIn(key, calls[0][1]["env"])
        self.assertEqual(calls[0][1]["stdout"], subprocess.DEVNULL)
        self.assertEqual(calls[0][1]["stderr"], subprocess.DEVNULL)
        self.assertEqual(calls[0][1]["stdin"], subprocess.DEVNULL)
        self.assertEqual(calls[0][1]["timeout"], 15)
        self.assertNotIn("private", json.dumps(result))

    def test_unconfirmed_status_includes_no_raw_error_or_identity(self):
        for failure in ("unauthenticated", "timeout", "unavailable"):

            def run(args, **kwargs):
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(args, 15)
                if failure == "unavailable":
                    raise OSError("private native diagnostics")
                return subprocess.CompletedProcess(
                    args, 1, "private-identity", "private-error"
                )

            with self.subTest(failure=failure), native_test_environment(
                self.namespace, run
            ):
                result = self.namespace["aws_status"](self.payload)
            self.assertFalse(result["authenticated"])
            self.assertEqual(result["verification"], "not_confirmed")
            self.assertNotIn("private", json.dumps(result))

    def test_verified_saved_sign_in_does_not_start_another_login(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)

        with native_test_environment(self.namespace, run):
            result = self.namespace["aws_login"](self.payload)
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["started"])
        self.assertFalse(result["requires_user_action"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:3], ["sts", "get-caller-identity"])

    def test_login_starts_detached_cli_with_safari_and_a_clean_environment(self):
        calls, launches = [], []
        process = Mock()
        process.pid = 12345
        process.wait.side_effect = subprocess.TimeoutExpired("aws", 1)

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 1)

        def launch(args, **kwargs):
            launches.append((args, kwargs))
            return process

        with native_test_environment(self.namespace, run, launch), patch.dict(
            self.namespace["os"].environ,
            {"AWS_SESSION_TOKEN": "private-token", "AWS_PROFILE": "admin"},
        ):
            result = self.namespace["aws_login"](self.payload)
        self.assertEqual(
            launches[0][0],
            [
                "/opt/homebrew/bin/aws",
                "login",
                "--profile",
                "default",
                "--region",
                "us-east-1",
            ],
        )
        options = launches[0][1]
        self.assertEqual(options["env"]["BROWSER"], "/usr/bin/open -a Safari %s")
        self.assertNotIn("AWS_PROFILE", options["env"])
        self.assertNotIn("AWS_SESSION_TOKEN", options["env"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertTrue(options["start_new_session"])
        self.assertTrue(options["close_fds"])
        process.wait.assert_called_once_with(timeout=1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result["started"])
        self.assertFalse(result["authenticated"])
        self.assertTrue(result["requires_user_action"])
        self.assertNotIn("private", json.dumps(result))

    def test_immediate_login_failure_is_not_reported_as_started(self):
        for failure in ("exit", "unavailable"):

            def run(args, **kwargs):
                return subprocess.CompletedProcess(args, 1)

            def launch(args, **kwargs):
                if failure == "unavailable":
                    raise OSError("private native diagnostics")
                process = Mock()
                process.wait.return_value = 1
                return process

            with self.subTest(failure=failure), native_test_environment(
                self.namespace, run, launch
            ):
                with self.assertRaisesRegex(ValueError, "could not start in Safari"):
                    self.namespace["aws_login"](self.payload)

    def test_immediate_zero_exit_still_requires_authentication_verification(self):
        process = Mock()
        process.wait.return_value = 0
        with native_test_environment(
            self.namespace,
            lambda *args, **kwargs: subprocess.CompletedProcess([], 1),
            lambda *args, **kwargs: process,
        ):
            result = self.namespace["aws_login"](self.payload)
        self.assertTrue(result["started"])
        self.assertFalse(result["authenticated"])
        self.assertTrue(result["requires_user_action"])

    def test_missing_cli_is_rejected_without_subprocesses(self):
        runner = Mock()
        with native_test_environment(self.namespace, runner), patch.object(
            Path, "is_file", return_value=False
        ):
            with self.assertRaisesRegex(ValueError, "official AWS CLI is unavailable"):
                self.namespace["aws_login"](self.payload)
        runner.assert_not_called()

    def test_repeated_login_and_status_reuse_the_owned_pending_process(self):
        process = Mock(pid=12345)
        process.wait.side_effect = subprocess.TimeoutExpired("aws", 1)
        launcher = Mock(return_value=process)
        with native_test_environment(
            self.namespace,
            lambda *args, **kwargs: subprocess.CompletedProcess([], 1),
            launcher,
        ):
            first = self.namespace["aws_login"](self.payload)
            second = self.namespace["aws_login"](self.payload)
            status = self.namespace["aws_status"](self.payload)
            directory = self.namespace["aws_state_directory"]()
            record = directory / "pending.json"
            saved = json.loads(record.read_text())
            self.assertEqual(set(saved), {"pid", "uid", "birth", "started"})
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertTrue(second["pending"])
        self.assertTrue(status["login_in_progress"])
        launcher.assert_called_once()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_stale_reused_or_missing_process_is_not_pending(self):
        with native_test_environment(
            self.namespace, lambda *args, **kwargs: subprocess.CompletedProcess([], 1)
        ):
            directory = self.namespace["aws_state_directory"](create=True)
            identity = {"uid": os.getuid(), "birth": "Sun Nov 8 12:00:00 2026"}
            now = self.namespace["time"].time()
            for timestamp, observed in (
                (now - 601, identity),
                (now + 60, identity),
                (now, {**identity, "birth": "Sun Nov 8 12:01:00 2026"}),
                (now, {**identity, "uid": os.getuid() + 1}),
                (now, None),
            ):
                self.namespace["aws_save_pending"](
                    directory, 12345, identity, timestamp
                )
                with self.subTest(timestamp=timestamp, observed=observed), patch.dict(
                    self.namespace,
                    {"aws_process_identity": lambda *args, **kwargs: observed},
                ):
                    self.assertFalse(self.namespace["aws_pending_login"](directory))

    def test_process_identity_requires_uid_birth_and_exact_login_arguments(self):
        namespace = remote_namespace()
        resolved = Path(namespace["AWS_EXECUTABLE"]).resolve()
        interpreter = (resolved.parent / "python").resolve()
        normal = [
            str(interpreter),
            namespace["AWS_EXECUTABLE"],
            "login",
            "--profile",
            "default",
            "--region",
            "us-east-1",
        ]
        fake_framework = Path(
            "/opt/homebrew/Cellar/python@3.15/3.15.2/Frameworks/Python.framework/Versions/3.15/bin/python3.15"
        )
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == resolved.parent / "python":
                return fake_framework
            return original_resolve(path, *args, **kwargs)

        framework_binary = (
            fake_framework.parent.parent / "Resources/Python.app/Contents/MacOS/Python"
        )
        cases = [
            (normal, os.getuid(), True, False),
            (normal + ["--debug"], os.getuid(), False, False),
            (normal, os.getuid() + 1, False, False),
            (["/bin/zsh", "-c"] + normal, os.getuid(), False, False),
            ([str(framework_binary)] + normal[1:], os.getuid(), True, True),
            (
                [str(fake_framework)] + normal[1:3] + ["--remote"] + normal[3:],
                os.getuid(),
                True,
                True,
            ),
        ]
        for arguments, uid, accepted, framework in cases:
            output = f"{uid} Sun Nov 8 12:00:00 2026 {shlex.join(arguments)}\n"
            with self.subTest(arguments=arguments, uid=uid), patch.object(
                namespace["subprocess"],
                "run",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ), patch.object(Path, "resolve", side_effect=None, autospec=True) as mocked:
                mocked.side_effect = resolve if framework else original_resolve
                identity = namespace["aws_process_identity"](12345)
            self.assertEqual(identity is not None, accepted)
            if accepted:
                self.assertEqual(
                    identity, {"uid": uid, "birth": "Sun Nov 8 12:00:00 2026"}
                )

    def test_pending_files_and_launchers_reject_symlinks(self):
        with native_test_environment(
            self.namespace, lambda *args, **kwargs: subprocess.CompletedProcess([], 1)
        ):
            directory = self.namespace["aws_state_directory"](create=True)
            outside = directory.parent / "preserve.txt"
            outside.write_text("preserve me")
            (directory / "pending.json").symlink_to(outside)
            with self.assertRaises(ValueError):
                self.namespace["aws_pending_login"](directory)
            with self.assertRaises(ValueError):
                self.namespace["aws_save_pending"](directory, 12345, {}, 0)
            with self.assertRaises(ValueError):
                self.namespace["aws_write_launcher"](
                    directory / "pending.json", "replace", 0o600
                )
            self.assertEqual(outside.read_text(), "preserve me")

    def test_remote_login_opens_fixed_private_terminal_launcher(self):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args, int(args[0] == "/opt/homebrew/bin/aws")
            )

        with native_test_environment(self.namespace, run):
            result = self.namespace["aws_remote_login"](self.payload)
            launcher = self.namespace["aws_remote_command_path"]()
            registration = launcher.parent / "remote-register.py"
            contents = launcher.read_text()
            self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(registration.stat().st_mode), 0o600)
            self.assertEqual(
                calls[-1][0], ["/usr/bin/open", "-a", "Terminal", str(launcher)]
            )
            self.assertIn(
                "/opt/homebrew/bin/aws login --remote --profile default --region us-east-1",
                contents,
            )
            self.assertNotIn("https://", contents)
            compile(registration.read_text(), "remote-register", "exec")
        self.assertTrue(result["started"])
        self.assertEqual(result["method"], "remote")
        self.assertFalse(result["authenticated"])
        self.assertTrue(result["requires_user_action"])
        self.assertIn("paste", result["message"])


if __name__ == "__main__":
    unittest.main()
