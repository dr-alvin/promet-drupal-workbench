import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.browser_reports import markdown, sections, view
from d11lib.common import Problem, digest, file_hash, read, write
from d11lib.dashboard import create_app
from d11lib.guided_setup import Setup, selected_path, source_inventory
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class GuidedTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name).resolve()
        self.w = Workflow(self.root / "managed")
        self.setup = Setup(self.w)
        self.src = self.root / "source"
        self.src.mkdir()
        write(self.src / "composer.json", {"require": {"drupal/core-recommended": "^10"}})
        (self.src / "web/sites/default/files").mkdir(parents=True)
        (self.src / ".docksal").mkdir()
        self.body = {"id": "sample-site", "source": str(self.src), "name": "Sample site"}

    def tearDown(self):
        self.t.cleanup()

    def test_draft_visible_and_source_unchanged(self):
        before = file_hash(self.src / "composer.json")
        d = self.setup.create(self.body)
        self.assertEqual(d["wrapper"], "fin")
        self.assertEqual(d["status"], "draft")
        self.assertTrue(self.w.projects()[0]["draft"])
        self.assertEqual(before, file_hash(self.src / "composer.json"))
        with self.assertRaises(Problem):
            self.setup.create(self.body)

    def test_path_and_archive_boundaries(self):
        for path in ["relative", "/", "/tmp/../tmp"]:
            with self.assertRaises(Problem):
                selected_path(path)
        link = self.root / "alias"
        link.symlink_to(self.src)
        with self.assertRaises(Problem):
            self.setup.create({**self.body, "source": str(link)})
        archive = self.root / "dump.tar"
        archive.write_text("not sql")
        with self.assertRaises(Problem):
            self.setup.create({**self.body, "database": str(archive)})

    def test_review_requires_prepared_evidence(self):
        self.setup.create(self.body)
        with self.assertRaises(Problem):
            self.setup.review(
                "sample-site", {"privacyReviewed": True, "reviewer": "Person", "note": "ok"}
            )

    def test_interrupted_setup_visible(self):
        d = self.setup.create(self.body)
        d.update(status="running", pid=99999999)
        write(self.setup.path(d["id"]) / "draft.json", d)
        self.assertEqual(self.setup.list()[0]["status"], "reconciliation_required")
        self.assertEqual(
            self.setup.reconcile(d["id"], "Person", "No import started")["status"], "draft"
        )

    def test_partial_copy_is_not_reimported(self):
        d = self.setup.create(self.body)
        p = self.setup.path(d["id"])
        (p / "bundle").mkdir()
        d["status"] = "reconciliation_required"
        write(p / "draft.json", d)
        with self.assertRaises(Problem):
            self.setup.reconcile(d["id"], "Person", "Retry")
        with self.assertRaises(Problem):
            self.setup.start(d["id"])

    def test_resume_only_final_verification(self):
        d = self.setup.create(self.body)
        p = self.setup.path(d["id"])
        bundle = p / "bundle"
        for n in ("code", "database", "files"):
            (bundle / n).mkdir(parents=True)
        (bundle / "code/a.txt").write_text("code")
        (bundle / "database/sanitized.sql.gz").write_text("db")
        (bundle / "files/a.txt").write_text("file")
        (p / "runtime").mkdir()
        (p / "runtime/sanitized.sql.gz").write_text("db")
        (p / "runtime/test-login.json").write_text("{}")
        write(p / "runtime.json", {"site": {"uri": "http://127.0.0.1:9999"}})
        write(p / "sanitization.json", {"accountsAnonymized": True})
        write(p / "source-before.json", source_inventory(self.src))
        d.update(
            status="reconciliation_required",
            checkpoint="sanitizing_copy",
            sourceUrl="http://127.0.0.1:8097/",
            sitemapUrl="http://127.0.0.1:60605/sitemap.xml",
        )
        write(p / "draft.json", d)
        with patch("d11lib.provision.verify_runtime") as verify:
            result = self.setup.resume_verification(d["id"])
        verify.assert_called_once()
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["sitemapUrl"], "http://127.0.0.1:8097/sitemap.xml")
        self.assertTrue(read(p / "preparation.json")["recoveredFinalVerification"])

    def test_resume_refuses_earlier_partial_stage(self):
        d = self.setup.create(self.body)
        d.update(status="reconciliation_required", checkpoint="importing_database")
        write(self.setup.path(d["id"]) / "draft.json", d)
        with self.assertRaises(Problem):
            self.setup.resume_verification(d["id"])

    def test_global_setup_lock(self):
        self.setup.create(self.body)
        other = self.setup.create({**self.body, "id": "another-site"})
        other.update(status="running", pid=os.getpid())
        write(self.setup.path(other["id"]) / "draft.json", other)
        with self.assertRaises(Problem):
            self.setup.start("sample-site")

    def test_api_origin_and_setup(self):
        c = TestClient(create_app(self.w.home), base_url="http://127.0.0.1:8765")
        h = {"origin": "http://127.0.0.1:8765"}
        self.assertEqual(c.post("/api/setup", json=self.body).status_code, 403)
        self.assertEqual(c.post("/api/setup", json=self.body, headers=h).status_code, 200)
        self.assertEqual(
            c.get("/api/setup/sample-site", headers=h).json()["draft"]["status"], "draft"
        )
        self.assertEqual(
            c.post("/api/setup/sample-site/review", json={}, headers=h).status_code, 409
        )
        with patch(
            "d11lib.guided_setup.Setup.scan",
            return_value={"setup": {"status": "running"}, "run": None},
        ) as scan:
            response = c.post("/api/projects/sample-site/scan", json={}, headers=h)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["setup"]["status"], "running")
        scan.assert_called_once_with("sample-site")

    def test_report_shared_model_and_partial(self):
        p = self.w.home / "projects/sample-site"
        (p / "site").mkdir(parents=True)
        cfg = {
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"id": "test", "kind": "local", "authorized": True},
        }
        write(p / "project.json", cfg)
        write(p / "registration.json", {"id": "sample-site", "fixture": False})
        out = self.w.home / "runs/run1"
        state = {
            "id": "run1",
            "project": "sample-site",
            "action": "assess",
            "status": "blocked",
            "checkpoint": "inputs_verified",
            "error": "<img src=x onerror=alert(1)>",
            "elapsedSeconds": 1,
        }
        write(out / "state.json", state)
        model = self.w.report("run1")
        self.assertIn("Executive Summary", markdown(model))
        self.assertEqual(len(sections(model)), 7)
        client = sections(model)[0]
        self.assertIn("Outside upgrade audit", client["facts"])
        self.assertIn(
            "Standalone uploaded-file inventory/content classification",
            client["facts"]["Outside upgrade audit"],
        )
        rendered = view(self.w, "run1")
        self.assertTrue(rendered["partial"])
        self.assertFalse(rendered["configChanged"])
        cfg["site"] = {"uri": "http://127.0.0.1:9999"}
        write(p / "project.json", cfg)
        self.assertTrue(view(self.w, "run1")["configChanged"])
        (out / "evidence.json").write_text('{"run":')
        self.assertTrue(view(self.w, "run1")["partial"])
        with self.assertRaises(Problem):
            self.w.artifact("run1", "../../source/composer.json")

    def test_stale_review_hash(self):
        d = self.setup.create(self.body)
        p = self.setup.path(d["id"])
        bundle = p / "bundle"
        for n in ("code", "database", "files"):
            (bundle / n).mkdir(parents=True)
            (bundle / n / "a.txt").write_text("x")
        from d11lib.intake import inventory

        hashes = {n: inventory(bundle / n) for n in ("code", "database", "files")}
        write(p / "preparation.json", {"hashes": hashes})
        d["status"] = "review_required"
        write(p / "draft.json", d)
        (bundle / "code/a.txt").write_text("changed")
        with self.assertRaises(Problem):
            self.setup.review(
                d["id"],
                {
                    "reviewer": "Operator",
                    "note": "Reviewed",
                    "privacyReviewed": True,
                    "reviewHash": digest(hashes),
                },
            )

    def test_second_project_roots(self):
        src = self.root / "second"
        src.mkdir()
        write(src / "composer.json", {"require": {}})
        (src / "docroot").mkdir()
        (src / ".docksal").mkdir()
        d = self.setup.create({"id": "second-project", "source": str(src)})
        self.assertEqual(d["drupal"], "docroot")

    def test_liveness_permission_error_ignored(self):
        d = self.setup.create(self.body)
        d.update(status="running", pid=12345)
        write(self.setup.path(d["id"]) / "draft.json", d)
        run_dir = self.w.home / "runs/test-run"
        run_dir.mkdir(parents=True)
        write(
            run_dir / "state.json",
            {
                "id": "test-run",
                "status": "running",
                "pid": 12345,
                "startedAt": "2026-09-08T00:00:00Z",
            },
        )
        with patch("os.kill", side_effect=PermissionError("Operation not permitted")):
            self.assertEqual(self.setup.list()[0]["status"], "running")
            self.assertEqual(self.w.runs()[0]["status"], "running")

    def test_upgrade_report_links_audit_evidence(self):
        p = self.w.home / "projects/sample-site"
        (p / "site").mkdir(parents=True)
        cfg = {
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"id": "test", "kind": "local", "authorized": True},
        }
        write(p / "project.json", cfg)
        write(p / "registration.json", {"id": "sample-site", "fixture": False})

        # Create upstream Gate 1 audit run
        audit_dir = self.w.home / "runs/audit1"
        write(
            audit_dir / "state.json",
            {
                "id": "audit1",
                "project": "sample-site",
                "action": "guided-audit",
                "status": "completed",
                "startedAt": "2026-09-12T10:00:00Z",
                "finishedAt": "2026-09-12T10:05:00Z",
            },
        )
        write(
            audit_dir / "gate.json",
            {
                "exactCoreVersion": "11.4.6",
                "target": "11.4.6",
                "automationLabel": "Semi-Automated",
                "planId": "plan-12345",
                "approvalEligible": True,
                "baseline": {"selected": 25, "passed": True, "omitted": 0},
                "risk": {"score": 20, "recommendation": "Go", "categories": {}, "findings": []},
            },
        )
        write(
            audit_dir / "compatibility-report.json",
            {
                "summary": {
                    "total": 50,
                    "ready": 45,
                    "unresolved": 0,
                    "actions": {"compatible_release": 20, "keep": 25, "manual_remediation": 5},
                },
                "extensions": [],
            },
        )
        write(
            audit_dir / "gate-approval.json",
            {"reviewer": "developer.test", "approvedAt": "2026-09-12T10:10:00Z"},
        )

        # Create guided-upgrade run referencing audit1
        upg_dir = self.w.home / "runs/upg1"
        write(
            upg_dir / "state.json",
            {
                "id": "upg1",
                "project": "sample-site",
                "action": "guided-upgrade",
                "batch": "audit1",
                "status": "needs_attention",
                "checkpoint": "gate_2_visual_review",
                "candidatePreserved": True,
                "elapsedSeconds": 120.5,
            },
        )
        write(
            upg_dir / "result/execution.json",
            {
                "target": "11.4.6",
                "steps": [
                    {"id": "composer_install", "status": "passed", "elapsedSeconds": 45.0},
                    {"id": "database_updates", "status": "passed", "elapsedSeconds": 12.0},
                ],
            },
        )

        # View report for upg1
        rendered = view(self.w, "upg1")
        self.assertIsNotNone(rendered)
        exec_sec = rendered["sections"][0]
        self.assertEqual(exec_sec["facts"]["Target Drupal core"], "Drupal 11.4.6")
        self.assertIn("2/2 steps passed", exec_sec["facts"]["Upgrade execution"])
        self.assertIn("45 of 50 extensions compatible", exec_sec["facts"]["Extensions ready"])
        self.assertIn("0 remaining", exec_sec["facts"]["Compatibility decisions remaining"])

        gate_sec = rendered["sections"][3]
        self.assertEqual(gate_sec["facts"]["Exact core version"], "11.4.6")
        self.assertIn("developer.test", gate_sec["facts"]["Approved"])

        vis_sec = rendered["sections"][4]
        self.assertIn("25 routes captured", vis_sec["facts"]["Selected baseline routes"])
        self.assertIn("Passed", vis_sec["facts"]["Baseline capture"])
        self.assertIn("Preserved & Active", vis_sec["facts"]["Candidate site status"])

        # Check copied artifacts
        self.assertTrue((upg_dir / "gate.json").is_file())
        self.assertTrue((upg_dir / "gate-approval.json").is_file())


if __name__ == "__main__":
    unittest.main()
