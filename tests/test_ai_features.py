import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import Problem, digest, now, read, write
from d11lib.dashboard import create_app
from d11lib.intake import CONTROL_KEYS, inventory
from d11lib.two_gate import (
    batch_generate_ai_patches,
    generate_handoff_report,
    self_heal_upgrade_failure,
)
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class AiFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.w = Workflow(self.home)
        self.cfg = {
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"id": "isolated", "kind": "local", "authorized": True},
            "site": {"uri": "https://d11-alpha.test"},
            "roots": {"composer": ".", "drupal": "web"},
            "runtime": {"wrapper": "fin"},
            "steps": [],
            "checks": [],
        }
        self.bundle = self.home / "inbox/alpha"
        for n in ("code", "database", "files"):
            (self.bundle / n).mkdir(parents=True)
            (self.bundle / n / "fixture.txt").write_text(n)
        write(
            self.bundle / "manifest.json",
            {
                "reviewer": "fixture",
                "reviewedAt": now(),
                "controls": {k: True for k in CONTROL_KEYS},
                "hashes": {n: inventory(self.bundle / n) for n in ("code", "database", "files")},
            },
        )
        self.w.register("alpha", "alpha", self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_generate_ai_patches_mock(self):
        audit_out = self.home / "runs/test_audit"
        audit_out.mkdir(parents=True)
        write(
            audit_out / "state.json",
            {
                "id": "test_audit",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "completed",
            },
        )
        write(
            audit_out / "compatibility-report.json",
            {
                "extensions": [
                    {
                        "name": "custom_mod",
                        "source": "custom",
                        "status": "remediate",
                        "upgradeStatus": {"issueCount": 2},
                        "rector": {"fixableCount": 1},
                    },
                    {
                        "name": "clean_mod",
                        "source": "custom",
                        "status": "ready",
                        "upgradeStatus": {"issueCount": 0},
                        "rector": {"fixableCount": 0},
                    },
                ]
            },
        )

        with patch("d11lib.two_gate.generate_ai_patch") as mock_gen:
            mock_gen.return_value = {
                "proposalDigest": "sha256:abc12345",
                "diff": "--- a/file\n+++ b/file",
            }
            res = batch_generate_ai_patches(self.w, "test_audit", modules=["custom_mod"])
            self.assertEqual(res["total"], 1)
            self.assertEqual(res["remediated"], 1)
            self.assertEqual(res["results"][0]["status"], "success")
            self.assertEqual(res["results"][0]["proposalDigest"], "sha256:abc12345")

    def test_self_heal_upgrade_failure(self):
        site_root = (self.home / "projects/alpha/site").resolve()
        custom_file = site_root / "web/modules/custom/my_feature/my_feature.module"
        custom_file.parent.mkdir(parents=True, exist_ok=True)
        custom_file.write_text("<?php\n// buggy code\n")

        upg_out = self.home / "runs/test_upg"
        upg_out.mkdir(parents=True)
        write(
            upg_out / "state.json",
            {
                "id": "test_upg",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "blocked",
                "checkpoint": "upgrading_composer_packages",
            },
        )

        # Write simulated worker.log with PHP fatal error pointing to custom_file
        (upg_out / "worker.log").write_text(
            f"PHP Fatal error: Declaration of MyFeature::build() must be compatible in {custom_file} on line 2\n"
        )

        fake_proposal = {
            "version": "1.0",
            "summary": "Fix method signature for D11 compatibility",
            "changes": [
                {
                    "path": "web/modules/custom/my_feature/my_feature.module",
                    "action": "modify",
                    "content": "<?php\n// fixed code\n",
                }
            ],
        }

        with (
            patch("d11lib.ai_providers.diagnose_and_heal") as mock_diag,
            patch("d11lib.proposals.validate_proposal") as mock_val,
        ):
            mock_diag.return_value = (fake_proposal, {})
            mock_val.return_value = {
                "proposal": fake_proposal,
                "digest": "sha256:heal9999",
                "diff": "--- a/my_feature.module\n+++ b/my_feature.module\n@@ -1,2 +1,2 @@",
            }
            res = self_heal_upgrade_failure(self.w, "test_upg")
            self.assertEqual(res["runId"], "test_upg")
            self.assertEqual(res["failingFile"], "web/modules/custom/my_feature/my_feature.module")
            self.assertEqual(res["proposalDigest"], "sha256:heal9999")
            self.assertIn("my_feature.module", res["diff"])

    def test_generate_handoff_report_fallback(self):
        upg_out = self.home / "runs/test_upg_done"
        upg_out.mkdir(parents=True)
        write(
            upg_out / "state.json",
            {
                "id": "test_upg_done",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "completed",
                "checkpoint": "completed",
            },
        )
        write(
            upg_out / "compatibility-decisions.json",
            {
                "token": {"action": "compatible_release", "candidateVersion": "2.0.0"},
                "custom_news": {"action": "ai_manual_patch", "proposalDigest": "sha256:custom123"},
            },
        )
        write(
            upg_out / "visual-results.json",
            {"passed": 3, "failed": 0, "routes": [{"route": "/", "status": "passed"}]},
        )

        res = generate_handoff_report(self.w, "test_upg_done", provider="none")
        self.assertEqual(res["runId"], "test_upg_done")
        self.assertIn("Drupal 11 Upgrade Rehearsal", res["summary"])
        self.assertIn("custom_news", res["summary"])
        self.assertIn("Visual Regression", res["summary"])

    def test_dashboard_api_ai_endpoints(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        headers = {"Origin": "http://127.0.0.1:8765"}

        audit_out = self.home / "runs/api_audit"
        audit_out.mkdir(parents=True)
        write(
            audit_out / "state.json",
            {
                "id": "api_audit",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "completed",
            },
        )
        write(
            audit_out / "compatibility-report.json",
            {"extensions": [{"name": "mod_a", "source": "custom", "status": "remediate"}]},
        )

        # 1. Test POST /api/runs/{rid}/batch-ai-patch
        with patch("d11lib.two_gate.batch_generate_ai_patches") as mock_batch:
            mock_batch.return_value = {
                "auditId": "api_audit",
                "total": 1,
                "remediated": 1,
                "results": [{"module": "mod_a", "status": "success"}],
            }
            r_batch = client.post(
                "/api/runs/api_audit/batch-ai-patch",
                json={"modules": ["mod_a"], "provider": "gemini"},
                headers=headers,
            )
            self.assertEqual(r_batch.status_code, 200)
            self.assertEqual(r_batch.json()["remediated"], 1)

        # 2. Test POST /api/runs/{rid}/self-heal
        upg_out = self.home / "runs/api_upg"
        upg_out.mkdir(parents=True)
        write(
            upg_out / "state.json",
            {
                "id": "api_upg",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "blocked",
                "checkpoint": "upgrading_composer_packages",
            },
        )

        with patch("d11lib.two_gate.self_heal_upgrade_failure") as mock_heal:
            mock_heal.return_value = {
                "runId": "api_upg",
                "failingFile": "web/modules/custom/test.module",
                "proposalDigest": "sha256:1234",
                "diff": "--- a/test\n+++ b/test",
                "summary": "Fix bug",
            }
            r_heal = client.post(
                "/api/runs/api_upg/self-heal",
                json={"provider": "gemini"},
                headers=headers,
            )
            self.assertEqual(r_heal.status_code, 200)
            self.assertEqual(r_heal.json()["proposalDigest"], "sha256:1234")

        # 3. Test POST /api/runs/{rid}/ai-summary
        with patch("d11lib.two_gate.generate_handoff_report") as mock_handoff:
            mock_handoff.return_value = {
                "runId": "api_upg",
                "summary": "# PR Summary\nDone",
            }
            r_sum = client.post(
                "/api/runs/api_upg/ai-summary",
                json={"provider": "gemini"},
                headers=headers,
            )
            self.assertEqual(r_sum.status_code, 200)
            self.assertEqual(r_sum.json()["summary"], "# PR Summary\nDone")


if __name__ == "__main__":
    unittest.main()
