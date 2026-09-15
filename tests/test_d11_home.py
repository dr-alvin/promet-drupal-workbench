"""Tests for D11_HOME configuration, environment overrides, CLI flag, and storage isolation."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from d11 import common
from d11.ai_providers import save_ai_settings
from d11.common import ROOT, get_d11_home, set_d11_home
from d11.workflow import Workflow


class TestD11Home(unittest.TestCase):
    def setUp(self):
        self.orig_environ = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.tmp.name).resolve()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_environ)
        common.D11_HOME = common.get_d11_home()
        self.tmp.cleanup()

    def test_default_d11_home(self):
        os.environ.pop("D11_HOME", None)
        self.assertEqual(get_d11_home(), (Path.home() / ".d11").resolve())

    def test_env_var_override(self):
        os.environ["D11_HOME"] = str(self.home_dir)
        self.assertEqual(get_d11_home(), self.home_dir)

    def test_set_d11_home_helper(self):
        set_d11_home(self.home_dir)
        self.assertEqual(os.environ["D11_HOME"], str(self.home_dir))
        self.assertEqual(get_d11_home(), self.home_dir)

    def test_workflow_defaults_to_d11_home(self):
        set_d11_home(self.home_dir)
        w = Workflow()
        self.assertEqual(w.home, self.home_dir)
        self.assertTrue(self.home_dir.is_dir())
        self.assertTrue((self.home_dir / "inbox").is_dir())

    def test_save_ai_settings_writes_to_d11_home_env(self):
        set_d11_home(self.home_dir)
        save_ai_settings({"geminiApiKey": "test-gemini-key-12345"})
        env_file = self.home_dir / ".env"
        self.assertTrue(env_file.is_file())
        self.assertIn("GEMINI_API_KEY=test-gemini-key-12345", env_file.read_text())

    def test_cli_home_flag_and_doctor_isolation(self):
        target_home = self.home_dir / "cli_home_test"
        self.assertFalse(target_home.exists())

        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        cmd = [sys.executable, "-m", "d11.cli", "--home", str(target_home), "doctor"]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, f"doctor failed: {proc.stderr}")
        self.assertIn(str(target_home), proc.stdout)
        self.assertTrue(target_home.is_dir(), "Doctor should have created target D11_HOME")


if __name__ == "__main__":
    unittest.main()
