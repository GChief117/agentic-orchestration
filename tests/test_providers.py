"""Exercise provider boundaries without launching apps or accessing real accounts."""

import subprocess
import tempfile
import unittest
from unittest.mock import patch

from orchestration.providers import ProviderConnections


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.providers = ProviderConnections(self.temp.name)

    @patch("orchestration.providers.shutil.which", return_value=None)
    def test_missing_tools_are_not_connected(self, _which):
        result = self.providers.list()
        self.assertEqual(len(result), 3)
        self.assertTrue(all(item["state"] == "not_installed" for item in result))
        with self.assertRaisesRegex(ValueError, "Install"):
            self.providers.sign_in("gemini")

    @patch("orchestration.providers.shutil.which", return_value="/tools/provider")
    @patch("orchestration.providers.subprocess.run")
    def test_status_redacts_output_and_checks_actual_login(self, run, _which):
        run.return_value = subprocess.CompletedProcess(
            [], 0, '{"loggedIn":false,"token":"SECRET"}', ""
        )
        self.assertEqual(self.providers.status("claude")["state"], "signed_out")
        self.assertNotIn("SECRET", str(self.providers.list()))
        run.return_value = subprocess.CompletedProcess([], 0, '{"loggedIn":true}', "")
        self.assertEqual(self.providers.status("claude")["state"], "signed_in")
        self.assertEqual(self.providers.status("gemini")["state"], "unverified")

    @patch("orchestration.providers.shutil.which", return_value="/tools/provider")
    @patch(
        "orchestration.providers.subprocess.run",
        side_effect=subprocess.TimeoutExpired("tool", 8),
    )
    def test_timeout_is_unverified(self, _run, _which):
        self.assertEqual(self.providers.status("openai")["state"], "unverified")

    @patch("orchestration.providers.platform.system", return_value="Darwin")
    @patch(
        "orchestration.providers.shutil.which", return_value="/tools/has space/claude"
    )
    @patch("orchestration.providers.subprocess.run")
    def test_launch_is_allowlisted_quoted_and_not_claimed_as_login(
        self, run, _which, _system
    ):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.providers.sign_in("claude; evil")
        self.assertFalse(run.called)
        result = self.providers.sign_in("claude")
        self.assertEqual(result["state"], "launched")
        launcher = next(self.providers.directory.glob("*.command"))
        self.assertIn("exec '/tools/has space/claude' auth login", launcher.read_text())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            run.call_args.args[0], ["/usr/bin/open", "-a", "Terminal", str(launcher)]
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("orchestration.providers.platform.system", return_value="Linux")
    @patch("orchestration.providers.shutil.which", return_value="/tools/provider")
    def test_other_platforms_get_manual_instructions(self, _which, _system):
        self.assertFalse(self.providers.status("gemini")["can_launch"])
        with self.assertRaisesRegex(ValueError, "local terminal"):
            self.providers.sign_in("gemini")
