from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from d11.common import Problem, set_d11_home, write
from d11.dashboard import create_app
from d11.workflow import Workflow


class TestProjectEditAndRunDeletion(unittest.TestCase):
    def setUp(self):
        self.orig_environ = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        set_d11_home(self.home)
        self.w = Workflow(self.home)

        # Create a mock source directory with composer.json
        self.src_dir = self.home / "mock_drupal_site"
        self.src_dir.mkdir(parents=True)
        write(self.src_dir / "composer.json", {"name": "test/site", "require": {"drupal/core-recommended": "^10.3"}})

        # Create registered project
        self.pid = "test-project"
        self.pdir = self.home / "projects" / self.pid
        self.pdir.mkdir(parents=True)
        (self.pdir / "site").symlink_to(self.src_dir, target_is_directory=True)

        self.initial_cfg = {
            "schemaVersion": "1.0",
            "zeroCopy": True,
            "repository": "site",
            "environment": {"id": "test-env", "kind": "local", "authorized": True},
            "site": {"uri": "http://test-site.docksal.site"},
            "roots": {"composer": ".", "drupal": "web"},
            "runtime": {"wrapper": "fin"},
            "steps": [],
            "checks": [],
        }
        write(self.pdir / "project.json", self.initial_cfg)
        write(
            self.pdir / "registration.json",
            {
                "id": self.pid,
                "name": "Test Project",
                "source": str(self.src_dir),
                "sourceUrl": "http://test-site.docksal.site",
                "wrapper": "fin",
                "createdAt": "2026-09-14T10:00:00Z",
            },
        )
        write(
            self.pdir / "route-inputs.json",
            {
                "routes": ["/", "/about"],
                "sitemapUrl": "http://test-site.docksal.site/sitemap.xml",
                "captureNavLinks": True,
            },
        )

        self.headers = {"Origin": "http://127.0.0.1:8765"}
        self.client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_environ)
        self.tmp.cleanup()

    def test_get_project_details(self):
        res = self.client.get(f"/api/projects/{self.pid}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], self.pid)
        self.assertEqual(data["name"], "Test Project")
        self.assertEqual(data["sourceUrl"], "http://test-site.docksal.site")
        self.assertEqual(data["wrapper"], "fin")
        self.assertEqual(data["routes"], ["/", "/about"])
        self.assertEqual(data["sitemapUrl"], "http://test-site.docksal.site/sitemap.xml")

    def test_get_nonexistent_project_returns_404_or_409(self):
        res = self.client.get("/api/projects/does-not-exist")
        self.assertIn(res.status_code, (404, 409))

    def test_update_project_configuration(self):
        update_payload = {
            "name": "Updated Site Name",
            "sourceUrl": "http://updated.ddev.site",
            "wrapper": "ddev",
            "routes": "/\n/contact\n/blog",
            "sitemapUrl": "http://updated.ddev.site/sitemap.xml",
            "captureNavLinks": False,
        }
        res = self.client.put(f"/api/projects/{self.pid}", json=update_payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)

        # Verify updated project via GET
        get_res = self.client.get(f"/api/projects/{self.pid}")
        self.assertEqual(get_res.status_code, 200)
        data = get_res.json()
        self.assertEqual(data["name"], "Updated Site Name")
        self.assertEqual(data["sourceUrl"], "http://updated.ddev.site")
        self.assertEqual(data["wrapper"], "ddev")
        self.assertEqual(data["routes"], ["/", "/contact", "/blog"])
        self.assertEqual(data["sitemapUrl"], "http://updated.ddev.site/sitemap.xml")
        self.assertFalse(data["captureNavLinks"])

    def test_update_project_source_directory(self):
        # Create a new source directory
        new_src = self.home / "new_mock_drupal_site"
        new_src.mkdir(parents=True)
        write(new_src / "composer.json", {"name": "test/new-site"})

        res = self.client.put(f"/api/projects/{self.pid}", json={"source": str(new_src)}, headers=self.headers)
        self.assertEqual(res.status_code, 200)

        # Check that site symlink was updated to point to new source
        site_link = self.pdir / "site"
        self.assertTrue(site_link.is_symlink())
        self.assertEqual(site_link.resolve(), new_src.resolve())

    def test_update_project_rejects_invalid_source(self):
        invalid_src = self.home / "nonexistent_dir"
        res = self.client.put(f"/api/projects/{self.pid}", json={"source": str(invalid_src)}, headers=self.headers)
        self.assertEqual(res.status_code, 409)

    def test_delete_project_runs_filtering_and_retention(self):
        runs_dir = self.home / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        # Create 3 audit runs and 1 upgrade run
        for i in range(1, 4):
            rid = f"audit-00{i}"
            r_dir = runs_dir / rid
            r_dir.mkdir(parents=True, exist_ok=True)
            write(
                r_dir / "state.json",
                {
                    "id": rid,
                    "project": self.pid,
                    "action": "guided-audit",
                    "status": "passed",
                    "startedAt": f"2026-09-1{i}T10:00:00Z",
                },
            )

        upg_dir = runs_dir / "upgrade-001"
        upg_dir.mkdir(parents=True, exist_ok=True)
        write(
            upg_dir / "state.json",
            {
                "id": "upgrade-001",
                "project": self.pid,
                "action": "guided-upgrade",
                "status": "passed",
                "startedAt": "2026-09-15T12:00:00Z",
            },
        )

        # Test GET /api/projects/{pid}/runs
        res = self.client.get(f"/api/projects/{self.pid}/runs")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 4)

        # Delete older audit runs, keeping the latest (keep=1, action=guided-audit)
        del_res = self.client.delete(f"/api/projects/{self.pid}/runs?action=guided-audit&keep=1", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)
        del_data = del_res.json()
        self.assertEqual(del_data["deletedCount"], 2)
        self.assertEqual(sorted(del_data["deleted"]), ["audit-001", "audit-002"])

        # Latest audit (audit-003) and upgrade (upgrade-001) should remain
        remaining = self.client.get(f"/api/projects/{self.pid}/runs").json()
        self.assertEqual(remaining["count"], 2)
        remaining_ids = {r["id"] for r in remaining["runs"]}
        self.assertEqual(remaining_ids, {"audit-003", "upgrade-001"})

    def test_delete_run_protects_running_workflow(self):
        runs_dir = self.home / "runs"
        r_dir = runs_dir / "running-run-001"
        r_dir.mkdir(parents=True, exist_ok=True)
        write(
            r_dir / "state.json",
            {
                "id": "running-run-001",
                "project": self.pid,
                "action": "guided-upgrade",
                "status": "running",
                "startedAt": "2026-09-15T12:00:00Z",
            },
        )

        # DELETE /api/runs/{rid} should fail with 409
        del_res = self.client.delete("/api/runs/running-run-001", headers=self.headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertTrue(r_dir.exists())

        # Bulk delete also protects running workflows
        res = self.w.delete_project_runs(self.pid)
        self.assertEqual(res["deletedCount"], 0)
        self.assertTrue(r_dir.exists())

    def test_workflow_prune_api(self):
        runs_dir = self.home / "runs"
        # Create 5 runs
        for i in range(1, 6):
            rid = f"run-{i:03d}"
            r_dir = runs_dir / rid
            r_dir.mkdir(parents=True, exist_ok=True)
            write(
                r_dir / "state.json",
                {
                    "id": rid,
                    "project": self.pid,
                    "status": "passed",
                    "startedAt": f"2026-09-0{i}T10:00:00Z",
                },
            )

        res = self.client.post("/api/workflow/prune", json={"keep": 2}, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["prunedCount"], 3)
        self.assertEqual(data["retainedCount"], 2)


if __name__ == "__main__":
    unittest.main()
