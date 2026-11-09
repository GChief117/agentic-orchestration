"""AWS sign-in uses native tools without live sessions or credential capture."""

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
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
def native_test_environment(namespace, runner, launcher=None):
    """Provide a fake installed CLI, verified host, and subprocess runner."""
    with patch.object(
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
        self.assertEqual(AWS_CAPABILITIES, {"bridge.aws.login", "bridge.aws.status"})
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


if __name__ == "__main__":
    unittest.main()
