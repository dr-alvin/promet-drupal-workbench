from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from d11.common import schema
from d11.doctor import (
    check_ai_provider,
    check_d11_home,
    check_disk_headroom,
    check_docker_daemon,
    check_python_runtime,
    check_tool,
    doctor_cli,
    run_doctor,
)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_check_python_runtime(self):
        with patch.object(sys, "version_info", (3, 11, 0)):
            res = check_python_runtime()
            self.assertEqual(res["status"], "ok")

        with patch.object(sys, "version_info", (3, 9, 6)):
            res = check_python_runtime()
            self.assertEqual(res["status"], "warning")
            self.assertIn("Python 3.10+ recommended", res["message"])

        with patch.object(sys, "version_info", (3, 8, 10)):
            res = check_python_runtime()
            self.assertEqual(res["status"], "error")
            self.assertIn("Python 3.9+ required", res["message"])

    def test_check_d11_home(self):
        # Valid writable home
        res = check_d11_home(self.home)
        self.assertEqual(res["status"], "ok")
        self.assertIn("Ready, Writable", res["message"])

        # Unwritable home
        unwritable = self.home / "readonly"
        unwritable.mkdir()
        try:
            os.chmod(unwritable, 0o400)
            res_ro = check_d11_home(unwritable)
            # Depending on OS/root status, either error or ok
            if not os.access(unwritable, os.W_OK):
                self.assertEqual(res_ro["status"], "error")
        finally:
            os.chmod(unwritable, 0o700)

    def test_check_disk_headroom(self):
        mock_usage = MagicMock()
        # 50 GB free
        mock_usage.free = 50 * (1024**3)
        mock_usage.total = 100 * (1024**3)
        with patch("shutil.disk_usage", return_value=mock_usage):
            res = check_disk_headroom(self.home, min_gb=20.0)
            self.assertEqual(res["status"], "ok")
            self.assertIn("50.0 GB free", res["message"])

        # 5 GB free (warning)
        mock_usage.free = 5 * (1024**3)
        with patch("shutil.disk_usage", return_value=mock_usage):
            res = check_disk_headroom(self.home, min_gb=20.0)
            self.assertEqual(res["status"], "warning")
            self.assertIn("5.0 GB free", res["message"])

    def test_check_docker_daemon(self):
        # 1. Docker not installed
        with patch("shutil.which", return_value=None):
            res = check_docker_daemon()
            self.assertEqual(res["status"], "warning")
            self.assertIn("Docker CLI not found", res["message"])

        # 2. Docker daemon running
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "27.4.0\n"
        with (
            patch("shutil.which", return_value="/usr/local/bin/docker"),
            patch("subprocess.run", return_value=mock_proc),
        ):
            res = check_docker_daemon()
            self.assertEqual(res["status"], "ok")
            self.assertIn("v27.4.0", res["message"])

        # 3. Docker daemon unreachable
        mock_proc_fail = MagicMock()
        mock_proc_fail.returncode = 1
        mock_proc_fail.stderr = "Cannot connect to the Docker daemon"
        with (
            patch("shutil.which", return_value="/usr/local/bin/docker"),
            patch("subprocess.run", return_value=mock_proc_fail),
        ):
            res = check_docker_daemon()
            self.assertEqual(res["status"], "warning")
            self.assertIn("daemon unreachable", res["message"])

    def test_check_ai_provider(self):
        # 1. Available provider
        mock_inv = [
            {
                "id": "gemini",
                "name": "Google Gemini",
                "mode": "api",
                "available": True,
                "safeInterface": True,
                "model": "gemini-3.6-flash",
            }
        ]
        with patch("d11.doctor.inventory", return_value=mock_inv):
            res = check_ai_provider()
            self.assertEqual(res["status"], "ok")
            self.assertIn("Google Gemini", res["message"])

        # 2. No available providers (offline mode)
        with patch("d11.doctor.inventory", return_value=[]):
            res = check_ai_provider()
            self.assertEqual(res["status"], "warning")
            self.assertIn("offline heuristic remediation", res["message"])

    def test_check_tool(self):
        with patch("shutil.which", return_value="/usr/bin/git"):
            res = check_tool("git", "Git", "git", required=True)
            self.assertEqual(res["status"], "ok")

        with patch("shutil.which", return_value=None):
            res_req = check_tool("git", "Git", "git", required=True)
            self.assertEqual(res_req["status"], "error")

            res_opt = check_tool("ddev", "DDEV", "ddev", required=False)
            self.assertEqual(res_opt["status"], "ok")
            self.assertIn("optional", res_opt["message"])

    def test_run_doctor_and_schema(self):
        report = run_doctor(home_dir=self.home)
        self.assertEqual(report["schemaVersion"], "1.0")
        self.assertIn("summary", report)
        self.assertIn("checks", report)
        self.assertEqual(report["summary"]["total"], len(report["checks"]))

        # Schema validation
        schema(report, "doctor")

    def test_doctor_cli_json(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = doctor_cli(["--json", "--home", str(self.home)])
        self.assertEqual(code, 0)

        output = json.loads(buf.getvalue())
        schema(output, "doctor")
        self.assertEqual(output["home"], str(self.home))

    def test_doctor_cli_human(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = doctor_cli(["--home", str(self.home)])
        self.assertEqual(code, 0)
        out_text = buf.getvalue()
        self.assertIn("Drupal 11 Upgrade Toolkit Doctor", out_text)
        self.assertIn(str(self.home), out_text)

    def test_check_project_runtime_database(self):
        from d11.doctor import check_project_runtime_database

        # 1. Project with DDEV MariaDB 10.4 (unsupported for D11)
        proj = self.home / "project1"
        proj.mkdir(parents=True)
        ddev_dir = proj / ".ddev"
        ddev_dir.mkdir()
        (ddev_dir / "config.yaml").write_text("database:\n  type: mariadb\n  version: '10.4'\n")

        res = check_project_runtime_database(proj)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "error")
        self.assertIn("MariaDB 10.4 is unsupported", res["message"])

        # 2. Project with DDEV MariaDB 10.11 (supported)
        (ddev_dir / "config.yaml").write_text("database:\n  type: mariadb\n  version: '10.11'\n")
        res_ok = check_project_runtime_database(proj)
        self.assertEqual(res_ok["status"], "ok")

    def test_check_project_drush_version(self):
        from d11.doctor import check_project_drush_version

        # Project with Drush 11 (outdated for D11)
        proj = self.home / "project2"
        proj.mkdir(parents=True)
        (proj / "composer.json").write_text(json.dumps({"require": {"drush/drush": "^11.0"}}))

        res = check_project_drush_version(proj)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "warning")
        self.assertIn("requires Drush 13+", res["message"])

        # Project with Drush 13 (supported)
        (proj / "composer.json").write_text(json.dumps({"require": {"drush/drush": "^13.0"}}))
        res_ok = check_project_drush_version(proj)
        self.assertEqual(res_ok["status"], "ok")


if __name__ == "__main__":
    unittest.main()
