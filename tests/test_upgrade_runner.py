"""Unit tests for simplified upgrade runner and dashboard endpoints."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.common import Problem, write
from d11lib.dashboard import create_app
from d11lib.source_runtime import detect_runtime
from d11lib.upgrade_runner import run_upgrade
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class UpgradeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.w = Workflow(self.home)
        self.src_tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.src_tmp.name).resolve() / "mock-drupal"
        self.project_dir.mkdir()
        (self.project_dir / "composer.json").write_text(
            json.dumps({"name": "promet/test-project", "require": {"drupal/core": "^10.4"}})
        )

    def tearDown(self):
        self.tmp.cleanup()
        self.src_tmp.cleanup()

    def test_detect_runtime_missing_composer(self):
        empty_dir = self.home / "empty"
        empty_dir.mkdir()
        with self.assertRaises(Problem):
            detect_runtime(empty_dir)

    def test_detect_runtime_ddev(self):
        ddev_dir = self.project_dir / ".ddev"
        ddev_dir.mkdir()
        (ddev_dir / "config.yaml").write_text("name: my-ddev-site\nproject_tld: ddev.site\n")
        info = detect_runtime(self.project_dir)
        self.assertEqual(info["wrapper"], "ddev")
        self.assertEqual(info["name"], "my-ddev-site")
        self.assertEqual(info["id"], "my-ddev-site")
        self.assertEqual(info["sourceUrl"], "https://my-ddev-site.ddev.site")

    def test_detect_runtime_docksal(self):
        docksal_dir = self.project_dir / ".docksal"
        docksal_dir.mkdir()
        (docksal_dir / "docksal.env").write_text("VIRTUAL_HOST=my-docksal-site.docksal.site\n")
        info = detect_runtime(self.project_dir)
        self.assertEqual(info["wrapper"], "fin")
        self.assertEqual(info["sourceUrl"], "http://my-docksal-site.docksal.site")

    def test_dashboard_auto_detect_api(self):
        ddev_dir = self.project_dir / ".ddev"
        ddev_dir.mkdir()
        (ddev_dir / "config.yaml").write_text("name: test-portal\n")

        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        # Test without source
        res = client.post(
            "/api/setup/auto-detect", json={}, headers={"Origin": "http://127.0.0.1:8765"}
        )
        self.assertEqual(res.status_code, 409)

        # Test with valid source
        res2 = client.post(
            "/api/setup/auto-detect",
            json={"source": str(self.project_dir)},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(res2.status_code, 200)
        data = res2.json()
        self.assertEqual(data["wrapper"], "ddev")
        self.assertEqual(data["name"], "test-portal")
        self.assertEqual(data["id"], "test-portal")

    def test_dashboard_auto_resolve_api(self):
        # Create a mock run directory with gate.json and compatibility-report.json
        run_dir = self.home / "runs/audit-1"
        run_dir.mkdir(parents=True)
        write(
            run_dir / "state.json",
            {
                "id": "audit-1",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "audit_complete",
                "fingerprint": "abc123",
            },
        )
        gate_data = {
            "schemaVersion": "1.0",
            "approvalEligible": False,
            "approvalDigest": "fake-digest",
            "targetCore": "11.4.6",
            "currentCore": "10.4.5",
            "risk": {"score": 30, "recommendation": "Conditional Go", "hardBlockers": []},
        }
        write(run_dir / "gate.json", gate_data)
        compat_data = {
            "schemaVersion": "1.2",
            "digest": "compat-digest-1",
            "extensions": [
                {
                    "name": "token",
                    "label": "Token",
                    "type": "module",
                    "source": "contrib",
                    "status": "update_available",
                    "selectedAction": "compatible_release",
                    "releaseCandidates": [{"version": "8.x-1.15", "stability": "stable"}],
                }
            ],
        }
        write(run_dir / "compatibility-report.json", compat_data)

        # Mock project alpha
        proj_dir = self.home / "projects/alpha"
        proj_dir.mkdir(parents=True)
        write(
            proj_dir / "project.json",
            {
                "id": "alpha",
                "environment": {"authorized": True},
                "site": {"uri": "https://test.site"},
            },
        )
        (proj_dir / "site").mkdir()

        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")

        with (
            patch.object(Workflow, "auto_remediate", return_value={"proposals": []}),
            patch.object(
                Workflow, "auto_decide", return_value={"totalDecisions": 1, "resolved": True}
            ),
        ):
            res = client.post(
                "/api/runs/audit-1/auto-resolve",
                json={},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "resolved")
            self.assertIn("gate", data)

    def test_dashboard_quick_summary_api(self):
        run_dir = self.home / "runs/audit-2"
        run_dir.mkdir(parents=True)
        write(
            run_dir / "state.json",
            {"id": "audit-2", "project": "beta", "action": "guided-audit", "status": "completed"},
        )
        write(
            run_dir / "gate.json",
            {
                "schemaVersion": "1.0",
                "approvalEligible": True,
                "targetCore": "11.4.6",
                "currentCore": "10.4.5",
                "risk": {"score": 20, "recommendation": "Go", "hardBlockers": []},
            },
        )
        write(
            run_dir / "compatibility-report.json",
            {
                "schemaVersion": "1.2",
                "digest": "compat-digest-2",
                "extensions": [
                    {
                        "name": "admin_toolbar",
                        "source": "contrib",
                        "selectedAction": "compatible_release",
                    },
                    {"name": "custom_theme", "source": "custom", "selectedAction": "keep"},
                ],
            },
        )
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        res = client.get("/api/runs/audit-2/quick-summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["targetCore"], "11.4.6")
        self.assertTrue(data["approvalEligible"])
        self.assertEqual(data["counts"]["compatible_release"], 1)
        self.assertEqual(data["counts"]["keep"], 1)

    @patch("d11lib.upgrade_runner.Setup.scan")
    @patch("d11lib.upgrade_runner.wait_for_run")
    def test_upgrade_runner_dry_run(self, mock_wait, mock_scan):
        mock_scan.return_value = {"run": {"id": "scan-run-1"}}
        mock_wait.return_value = {"status": "completed"}

        run_dir = self.home / "runs/scan-run-1"
        run_dir.mkdir(parents=True)
        write(
            run_dir / "state.json",
            {
                "id": "scan-run-1",
                "project": "mock-drupal",
                "status": "completed",
                "checkpoint": "audit_complete",
            },
        )
        write(
            run_dir / "gate.json",
            {
                "approvalEligible": True,
                "approvalDigest": "test-digest",
                "targetCore": "11.4.6",
                "currentCore": "10.4.5",
                "risk": {"score": 20, "recommendation": "Go", "hardBlockers": []},
            },
        )
        write(run_dir / "compatibility-report.json", {"schemaVersion": "1.2", "extensions": []})

        with patch("d11lib.upgrade_runner.Workflow") as MockWorkflow:
            mock_wf = MagicMock()
            mock_wf.home = self.home
            mock_wf.run.return_value = (run_dir, {"status": "completed"})
            mock_wf.auto_remediate.return_value = {"proposals": []}
            mock_wf.auto_decide.return_value = {"totalDecisions": 0}
            mock_wf.compatibility.return_value = {"extensions": []}
            MockWorkflow.return_value = mock_wf

            code = run_upgrade(source_path=str(self.project_dir), dry_run=True, yes=True)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
