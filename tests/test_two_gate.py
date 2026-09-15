import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import Problem, digest, write
from d11lib.dashboard import create_app
from d11lib.patches import _blockers
from d11lib.risk import evaluate, finding
from d11lib.routes import scenarios, select
from d11lib.two_gate import _approved_batch_manifest, automation_eligibility, delivery_forecast
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class TwoGateTests(unittest.TestCase):
    def test_ai_assisted_forecast_uses_upgrade_scope_only(self):
        compatibility = {
            "summary": {
                "update_available": 2,
                "patch_available": 1,
                "manual_remediation": 1,
                "unknown": 0,
                "blocked": 0,
            }
        }
        result = delivery_forecast(compatibility, {"status": "passed"}, True)
        self.assertEqual(result["lowHours"], 11.0)
        self.assertEqual(result["highHours"], 18.0)
        self.assertIn("uploaded-file inventory/content classification", result["assumptions"][1])
        blocked = delivery_forecast(compatibility, {"status": "blocked"}, False)
        self.assertEqual(blocked["lowHours"], 14.0)
        self.assertEqual(blocked["highHours"], 24.0)

    def test_risk_caps_and_hard_override(self):
        many = [
            finding("d" + str(i), "dependencies_patches", "high", "dependency") for i in range(5)
        ]
        result = evaluate(many)
        self.assertEqual(result["categories"]["dependencies_patches"], 30)
        self.assertEqual(result["recommendation"], "Conditional Go")
        result = evaluate(
            [
                finding(
                    "runtime_identity", "platform_recovery", "low", "wrong runtime", status="failed"
                )
            ]
        )
        self.assertEqual(result["score"], 2)
        self.assertEqual(result["recommendation"], "No-Go")

    def test_automation_eligibility_uses_evidence_and_selected_actions(self):
        go = {"recommendation": "Go", "hardBlockers": []}
        batch = {"blockers": []}
        auto = automation_eligibility(
            go,
            {
                "extensions": [
                    {"name": "token", "selectedAction": "compatible_release", "blockers": []}
                ]
            },
            batch,
            True,
        )
        self.assertEqual(auto["status"], "auto_upgrade_eligible")
        assisted = automation_eligibility(
            go,
            {
                "extensions": [
                    {"name": "token", "selectedAction": "available_patch", "blockers": []}
                ]
            },
            batch,
            True,
        )
        self.assertEqual(assisted["status"], "assisted_upgrade_required")
        self.assertEqual(assisted["riskBearingExtensions"], ["token"])
        blocked = automation_eligibility(
            go,
            {
                "extensions": [
                    {
                        "name": "token",
                        "selectedAction": "defer",
                        "blockers": ["Compatibility decision deferred"],
                    }
                ]
            },
            batch,
            True,
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["unresolvedExtensions"], ["token"])

    def test_approved_batch_expands_extension_decisions(self):
        state = {"id": "audit", "project": "site", "fingerprint": "source-hash"}
        gate = {
            "approvalDigest": "gate-hash",
            "baseline": {"routeDigest": "routes"},
            "runtimeIdentity": {"expected": "site_db"},
        }
        plan = {"planId": "plan", "steps": [{"id": "composer_install"}]}
        compatibility = {
            "decisionDigest": "decisions",
            "extensions": [
                {
                    "name": "token",
                    "type": "module",
                    "package": "drupal/token",
                    "targetVersion": "2.0.0",
                    "verificationChecks": ["composer_validate"],
                    "decision": {"action": "compatible_release", "candidateVersion": "2.0.0"},
                    "patches": [],
                },
                {
                    "name": "legacy",
                    "type": "module",
                    "package": "drupal/legacy",
                    "verificationChecks": ["drupal_bootstrap"],
                    "decision": {"action": "remove"},
                    "patches": [],
                },
            ],
        }
        result = _approved_batch_manifest(
            state, gate, plan, compatibility, "Reviewer", "2026-09-08T00:00:00Z"
        )
        self.assertEqual(result["extensions"]["token"]["selectedVersion"], "2.0.0")
        self.assertEqual(
            result["extensions"]["legacy"]["uninstallSequence"],
            ["drush pm:uninstall legacy", "composer remove drupal/legacy"],
        )
        self.assertEqual(result["orderedStepIds"], ["composer_install"])
        self.assertTrue(result["digest"])

    def test_routes_dedupe_group_cap_and_mobile(self):
        values = ["/", "/news/1", "/news/2", "/about", "https://site.test/about"] + [
            f"/page/{i}" for i in range(120)
        ]
        result = select(values, "https://site.test", 100)
        self.assertEqual(len(result["routes"]), 100)
        self.assertGreater(result["omitted"], 0)
        self.assertEqual(result["routes"][0], "/")
        catalog = scenarios(
            result["routes"],
            {"id": "local", "kind": "local", "authorized": True},
            "http://site",
            "http://site",
            "network",
        )
        self.assertEqual(len(catalog["scenarios"]), 120)
        self.assertEqual(catalog["scenarios"][0]["viewport"], {"width": 1440, "height": 900})
        self.assertEqual(catalog["scenarios"][-1]["viewport"], {"width": 390, "height": 844})
        self.assertEqual(catalog["scenarios"][0]["requiredElements"], ["body"])
        with self.assertRaises(Problem):
            select(["https://external.test/page"], "https://site.test")

    def test_patch_search_is_limited_to_reported_contrib_blockers(self):
        solver = {
            "whyNot": {
                "stdout": "drupal/token 1.0 requires drupal/core ^10\ndrupal/core-recommended 10.4"
            },
            "command": {"stderr": "drupal/webform cannot resolve"},
        }
        self.assertEqual(_blockers(solver), ["drupal/token", "drupal/webform"])

    def test_no_go_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            w = Workflow(home)
            project = home / "projects/site"
            (project / "site").mkdir(parents=True)
            cfg = {
                "schemaVersion": "1.0",
                "repository": "site",
                "environment": {"id": "local", "kind": "local", "authorized": True},
                "site": {"uri": "http://127.0.0.1:9999"},
                "runtime": {"wrapper": "fin"},
                "steps": [],
                "checks": [],
            }
            write(project / "project.json", cfg)
            write(project / "registration.json", {"id": "site", "fixture": False})
            out = home / "runs/audit"
            risk = evaluate(
                [
                    finding(
                        "composer_resolution",
                        "dependencies_patches",
                        "critical",
                        "blocked",
                        status="blocked",
                    )
                ]
            )
            gate = {"risk": risk, "approvalEligible": False}
            gate["approvalDigest"] = digest(gate)
            write(out / "gate.json", gate)
            write(
                out / "state.json",
                {
                    "id": "audit",
                    "project": "site",
                    "action": "guided-audit",
                    "status": "completed",
                    "fingerprint": w.fingerprint(project, w.project("site")[1]),
                },
            )
            from d11lib.two_gate import approve

            with self.assertRaises(Problem):
                approve(
                    w,
                    "audit",
                    {
                        "reviewer": "A",
                        "privacyReviewed": True,
                        "approvalDigest": gate["approvalDigest"],
                    },
                )

    def test_high_level_api_requires_same_origin(self):
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(Path(td)), base_url="http://127.0.0.1:8765")
            self.assertEqual(client.post("/api/projects/missing/audit").status_code, 403)

    def test_each_audit_uses_a_new_baseline_directory(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            w = Workflow(home)
            project = home / "projects/site"
            (project / "site").mkdir(parents=True)
            write(project / "visual.json", {"scenarios": []})
            cfg = {"visual": {"config": "visual.json", "output": "visual-output"}}
            seen = []

            def fake(argv, cwd, timeout):
                target = Path(argv[argv.index("--output") + 1])
                target.mkdir(parents=True, exist_ok=True)
                write(target / "capture-settings.json", {"stable": True})
                seen.append(target)
                return {"exitCode": 0}

            with patch("d11lib.workflow.command", side_effect=fake):
                w.capture(project, cfg, home / "runs/first", "reference")
                w.capture(project, cfg, home / "runs/second", "reference")
            self.assertEqual(
                seen, [home / "runs/first/visual-work", home / "runs/second/visual-work"]
            )

    def test_gate_digest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            w = Workflow(home)
            project = home / "projects/site"
            (project / "site").mkdir(parents=True)
            (project / "site/composer.json").write_text("{}")
            cfg = {
                "schemaVersion": "1.0",
                "repository": "site",
                "environment": {"id": "local", "kind": "local", "authorized": True},
                "site": {"uri": "http://127.0.0.1:9999"},
                "runtime": {"wrapper": "fin"},
                "steps": [],
                "checks": [],
            }
            write(project / "project.json", cfg)
            write(project / "registration.json", {"id": "site", "fixture": False})
            out = home / "runs/audit"
            risk = {"recommendation": "Go", "score": 0, "hardBlockers": []}
            gate = {"risk": risk, "approvalEligible": True, "batchHash": "wrong"}
            gate["approvalDigest"] = digest(gate)
            write(out / "gate.json", gate)
            write(
                out / "plan.json",
                {
                    "planId": "p",
                    "environment": cfg["environment"],
                    "site": cfg["site"],
                    "steps": [],
                    "backup": {},
                },
            )
            write(out / "batch-config.json", cfg)
            write(
                out / "state.json",
                {
                    "id": "audit",
                    "project": "site",
                    "action": "guided-audit",
                    "status": "completed",
                    "fingerprint": w.fingerprint(project, w.project("site")[1]),
                    "batchHash": "wrong",
                },
            )
            gate["risk"]["score"] = 1
            write(out / "gate.json", gate)
            from d11lib.two_gate import approve

            with self.assertRaisesRegex(Problem, "content changed"):
                approve(
                    w,
                    "audit",
                    {
                        "reviewer": "A",
                        "privacyReviewed": True,
                        "approvalDigest": gate["approvalDigest"],
                    },
                )


if __name__ == "__main__":
    unittest.main()
