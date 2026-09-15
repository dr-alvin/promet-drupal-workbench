from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from d11.audit_report import build, validate
from d11.common import write
from d11.visual_audit import compose_project_name


class AuditReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def _create_mock_source_assessment(self):
        source = self.root / "source-result.json"
        data = {
            "schemaVersion": "1.0",
            "releaseCandidateId": "rc-001",
            "releaseGate": "green",
            "findings": [
                {
                    "id": "SEC-001",
                    "summary": "Core dependencies clean",
                    "status": "passed",
                    "clientSummary": "No known core vulnerabilities detected.",
                }
            ],
            "deliveryBudget": {
                "targetHours": 20.0,
                "checkpointHours": 3.0,
                "projectDecision": "Approved",
            },
        }
        write(source, data)
        return source

    def test_build_and_validate_default_branding(self):
        source = self._create_mock_source_assessment()
        out = self.root / "default_out"
        cfg = {"deliveryBudget": {"targetHours": 20.0}}

        model = build(source, cfg, out, docx=False)
        self.assertEqual(model["client"], "Drupal Upgrade Automation Toolkit")

        # Verify markdown titles use neutral branding
        summary_md = (out / "client-summary.md").read_text()
        self.assertIn(
            "# Drupal Upgrade Automation Toolkit Drupal 11 client audit summary", summary_md
        )

        # Validation check
        val = validate(out)
        self.assertEqual(val["status"], "passed")
        self.assertEqual(val["reports"], 4)

    def test_build_with_custom_client_branding(self):
        source = self._create_mock_source_assessment()
        out = self.root / "custom_out"
        cfg = {
            "client": {"name": "FortBend County", "author": "FortBend Tech Team"},
            "deliveryBudget": {"targetHours": 25.0, "projectDecision": "Approved"},
        }

        model = build(source, cfg, out, docx=True)
        self.assertEqual(model["client"], "FortBend County")

        # Verify markdown titles use client branding
        brief_md = (out / "Drupal-11-Upgrade-Approval-Brief.md").read_text()
        self.assertIn("# FortBend County Drupal 11 delivery approval brief", brief_md)

        # DOCX generated and valid
        docx_file = out / "client-summary.docx"
        self.assertTrue(docx_file.is_file())

        val = validate(out)
        self.assertEqual(val["status"], "passed")
        self.assertEqual(val["reports"], 8)  # 4 md + 4 docx

    def test_compose_project_name_parameterization(self):
        # 1. Default without client
        self.assertEqual(compose_project_name({"projectId": "portal"}), "d11-portal")
        self.assertEqual(compose_project_name(explicit_project="my-site"), "d11-my-site")

        # 2. Client specified in config -> d11-<client>-<proj>
        cfg_client = {"projectId": "portal", "client": "acme"}
        self.assertEqual(compose_project_name(cfg_client), "d11-acme-portal")

        cfg_client_obj = {"projectId": "portal", "client": {"name": "CityGov"}}
        self.assertEqual(compose_project_name(cfg_client_obj), "d11-citygov-portal")

        # 3. Custom containerPrefix
        cfg_custom_prefix = {"projectId": "portal", "containerPrefix": "toolkit"}
        self.assertEqual(compose_project_name(cfg_custom_prefix), "toolkit-portal")

        # 4. Custom containerPrefix + Client
        cfg_both = {"projectId": "portal", "client": "acme", "containerPrefix": "custom"}
        self.assertEqual(compose_project_name(cfg_both), "custom-acme-portal")


if __name__ == "__main__":
    unittest.main()
