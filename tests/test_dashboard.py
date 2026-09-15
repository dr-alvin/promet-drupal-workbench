import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import Problem, command, digest, file_hash, now, read, write
from d11lib.dashboard import create_app
from d11lib.effort import effort_totals
from d11lib.intake import CONTROL_KEYS, inventory, safe_path
from d11lib.proposals import apply_changes, validate_proposal
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class DashboardTests(unittest.TestCase):
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

    def tearDown(self):
        self.tmp.cleanup()

    def register(self):
        return self.w.register("alpha", "alpha", self.cfg)

    def test_intake_preserves_inputs(self):
        before = inventory(self.bundle / "code")
        self.register()
        self.assertEqual(before, inventory(self.bundle / "code"))
        self.assertEqual(before, inventory(self.home / "projects/alpha/site"))

    def test_intake_hash_mismatch(self):
        (self.bundle / "code/fixture.txt").write_text("changed")
        with self.assertRaises(Problem):
            self.register()

    def test_reject_unreviewed(self):
        m = json.loads((self.bundle / "manifest.json").read_text())
        m["controls"]["sanitized"] = False
        write(self.bundle / "manifest.json", m)
        with self.assertRaises(Problem):
            self.register()

    def test_traversal_and_symlinks(self):
        for p in ("../outside", "/tmp/outside"):
            with self.assertRaises(Problem):
                safe_path(self.home, p)
        (self.home / "link").symlink_to("/tmp")
        with self.assertRaises(Problem):
            safe_path(self.home, "link/file")

    def test_runtime_fails_closed(self):
        self.register()
        p, c = self.w.project("alpha")
        with self.assertRaises(Problem):
            self.w.runtime_gate(p, c)

    def test_loopback_origin_host(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        self.assertEqual(client.get("/api/projects").status_code, 200)
        self.assertEqual(client.post("/api/runs", json={}).status_code, 403)
        self.assertEqual(
            client.post(
                "/api/runs", json={}, headers={"Origin": "http://127.0.0.1:8765"}
            ).status_code,
            400,
        )
        self.assertEqual(
            client.get("/api/projects", headers={"host": "evil.test"}).status_code, 403
        )

    def test_static_assets(self):
        c = TestClient(create_app(self.home), base_url="http://localhost:8765")
        self.assertEqual(c.get("/").status_code, 200)
        self.assertIn("frame-ancestors", c.get("/").headers["content-security-policy"])

    def test_concurrent_and_interrupted(self):
        self.register()
        out = self.home / "runs/test"
        write(
            out / "state.json",
            {
                "id": "test",
                "project": "alpha",
                "action": "assess",
                "checkpoint": "queued",
                "status": "running",
                "pid": os.getpid(),
            },
        )
        with self.assertRaises(Problem):
            self.w.start("alpha", "assess")
        s = json.loads((out / "state.json").read_text())
        s["pid"] = 99999999
        write(out / "state.json", s)
        self.assertEqual(self.w.runs()[0]["status"], "reconciliation_required")
        with self.assertRaises(Problem):
            self.w.start("alpha", "assess")
        self.w.reconcile("test", "Operator", "Checked no mutation occurred")
        self.assertEqual(self.w.run("test")[1]["status"], "blocked")

    def test_malformed_proposal_and_stale_patch(self):
        self.register()
        root = self.home / "projects/alpha/site"
        with self.assertRaises(Problem):
            validate_proposal({}, root, [], set())
        proposal = {
            "summary": "Fix",
            "findings": ["fixture"],
            "steps": [],
            "limitations": [],
            "changes": [
                {
                    "path": "fixture.txt",
                    "beforeSha256": file_hash(root / "fixture.txt"),
                    "after": "fixed",
                    "finding": "fixture",
                    "rationale": "regression",
                    "verificationCheckIds": ["check"],
                }
            ],
        }
        self.assertIn("fixed", validate_proposal(proposal, root, [], {"check"})["diff"])
        (root / "fixture.txt").write_text("changed")
        with self.assertRaises(Problem):
            apply_changes(root, proposal)

    def test_artifact_allowlist(self):
        out = self.home / "runs/test"
        write(out / "state.json", {"id": "test", "status": "blocked"})
        (out / "secret.txt").write_text("private")
        for name in ("secret.txt", "../../secret.txt"):
            with self.assertRaises(Problem):
                self.w.artifact("test", name)

    def test_two_projects(self):
        self.register()
        cfg = {**self.cfg, "site": {"uri": "https://d11-bravo.test"}}
        self.w.register("alpha", "bravo", cfg)
        self.assertEqual(len(self.w.projects()), 2)

    def test_project_listing_does_not_expose_snapshot_file_names(self):
        self.register()
        registration = next((self.home / "projects").glob("*/registration.json"))
        value = read(registration)
        value["snapshotHashes"] = {"files": {"private/resume.pdf": "hash"}}
        write(registration, value)
        self.assertNotIn("snapshotHashes", self.w.projects()[0])

    def test_effort(self):
        entries = [
            {
                "person": "A",
                "category": "engineering",
                "operation": "start",
                "at": "2026-09-08T00:00:00+00:00",
            },
            {
                "person": "A",
                "category": "engineering",
                "operation": "stop",
                "at": "2026-09-08T00:01:00+00:00",
            },
        ]
        self.assertEqual(effort_totals(entries)["humanEffortSeconds"], 60)
        with self.assertRaises(Problem):
            effort_totals(entries[:1] * 2)
        self.assertIsNone(effort_totals([])["humanEffortSeconds"])

    def test_stale_approval_and_batch_artifacts(self):
        self.register()
        p, cfg = self.w.project("alpha")
        out = self.home / "runs/batch"
        plan = {
            "planId": "reviewed",
            "environment": cfg["environment"],
            "site": cfg["site"],
            "steps": [],
            "backup": {},
            "blockers": [],
        }
        write(out / "plan.json", plan)
        write(out / "batch-config.json", cfg)
        state = {
            "id": "batch",
            "project": "alpha",
            "action": "plan",
            "status": "completed",
            "checkpoint": "batch_review",
            "fingerprint": self.w.fingerprint(p, cfg),
            "batchHash": digest({"plan": plan, "config": cfg, "proposal": None}),
        }
        write(out / "state.json", state)
        with self.assertRaises(Problem):
            self.w.approve("batch", "different", "Operator")
        self.w.approve("batch", "reviewed", "Operator")
        plan["steps"] = [{"id": "unreviewed"}]
        write(out / "plan.json", plan)
        with self.assertRaises(Problem):
            self.w.approve("batch", "reviewed", "Operator")
        (p / "site/fixture.txt").write_text("changed")
        with self.assertRaises(Problem):
            self.w.approve("batch", "reviewed", "Operator")

    def test_missing_codex_is_explicit_blocker(self):
        self.register()
        out = self.home / "runs/ai"
        write(
            out / "state.json",
            {
                "id": "ai",
                "project": "alpha",
                "action": "propose",
                "status": "running",
                "checkpoint": "queued",
            },
        )
        with patch("d11lib.workflow.shutil.which", return_value=None):
            self.w.work("ai")
        state = self.w.run("ai")[1]
        self.assertEqual(state["status"], "blocked")
        self.assertIn("unavailable", state["error"])

    def test_malformed_api(self):
        c = TestClient(create_app(self.home), base_url="http://localhost:8765")
        r = c.post("/api/runs", json={}, headers={"Origin": "http://localhost:8765"})
        self.assertEqual(r.status_code, 400)

    def test_unattended_requires_confirmed_tracking(self):
        from d11lib.effort import unattended_seconds

        entries = [
            {
                "person": "A",
                "category": "engineering",
                "operation": "start",
                "at": "2026-09-08T00:00:00+00:00",
            },
            {
                "person": "A",
                "category": "engineering",
                "operation": "stop",
                "at": "2026-09-08T00:01:00+00:00",
            },
        ]
        commands = [
            {"startedAt": "2026-09-08T00:00:00+00:00", "finishedAt": "2026-09-08T00:02:00+00:00"}
        ] * 2
        self.assertIsNone(unattended_seconds(commands, effort_totals(entries), entries))
        entries.append({"operation": "confirm", "person": "A", "reason": "All work recorded"})
        self.assertEqual(unattended_seconds(commands, effort_totals(entries), entries), 60)

    def test_stopped_command(self):
        stop = self.home / "stop"
        stop.touch()
        with patch.dict(os.environ, {"D11_STOP_FILE": str(stop)}):
            r = command([sys.executable, "-c", "import time;time.sleep(5)"], self.home, 10)
        self.assertNotEqual(r["exitCode"], 0)

    def test_handoff_api_endpoints(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        r_prev = client.get("/api/projects/alpha/handoff/preview")
        self.assertEqual(r_prev.status_code, 200)
        self.assertIn("candidates", r_prev.json())
        with patch(
            "d11lib.handoff.sync_to_git_branch",
            return_value={"success": True, "branch": "upgrade/drupal-11", "filesChanged": []},
        ):
            r_post = client.post(
                "/api/projects/alpha/handoff",
                json={"branch": "upgrade/drupal-11"},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_post.status_code, 200)
            self.assertTrue(r_post.json()["success"])

    def test_capture_baseline_endpoint_and_summary(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/audit_run"
        write(
            out / "state.json",
            {
                "id": "audit_run",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "report_and_decisions",
            },
        )
        write(out / "gate.json", {"risk": {"score": 10, "recommendation": "Go"}})
        write(out / "result/result.json", {"checks": []})

        r_sum = client.get("/api/runs/audit_run/quick-summary")
        self.assertEqual(r_sum.status_code, 200)
        self.assertIn("baselineCaptured", r_sum.json())
        self.assertFalse(r_sum.json()["baselineCaptured"])

        with patch("d11lib.workflow.Workflow.capture") as mock_capture:
            r_cap = client.post(
                "/api/runs/audit_run/capture-baseline",
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_cap.status_code, 200)
            mock_capture.assert_called_once()
            self.assertTrue(r_cap.json()["baselineCaptured"])

    def test_terminal_api_endpoints(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        r_bad = client.post(
            "/api/terminal/run",
            json={"subcommand": "invalid_cmd"},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(r_bad.status_code, 409)
        r_active_init = client.get("/api/terminal/active")
        self.assertEqual(r_active_init.status_code, 200)

        with patch("d11lib.web_runner.subprocess.Popen") as mock_popen:
            import threading
            from unittest.mock import MagicMock

            release_read = threading.Event()

            def slow_readline():
                if not release_read.is_set():
                    release_read.wait(timeout=2.0)
                return ""

            mock_proc = MagicMock()
            mock_proc.stdout.readline.side_effect = slow_readline
            mock_proc.wait.return_value = 0
            mock_proc.stdin = MagicMock()
            mock_popen.return_value = mock_proc
            r_run = client.post(
                "/api/terminal/run",
                json={"subcommand": "status"},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_run.status_code, 200)
            job_id = r_run.json()["id"]

            r_active_running = client.get("/api/terminal/active")
            self.assertEqual(r_active_running.status_code, 200)
            self.assertTrue(r_active_running.json()["active"])
            self.assertEqual(r_active_running.json()["job"]["id"], job_id)

            release_read.set()
            r_get = client.get(f"/api/terminal/{job_id}")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.json()["id"], job_id)
            r_stop = client.post(
                f"/api/terminal/{job_id}/stop", headers={"Origin": "http://127.0.0.1:8765"}
            )
            self.assertEqual(r_stop.status_code, 200)

            r_active_after = client.get("/api/terminal/active")
            self.assertEqual(r_active_after.status_code, 200)
            self.assertFalse(r_active_after.json()["active"])

    def test_web_runner_job_and_stream(self):
        import asyncio
        import time

        from d11lib.web_runner import create_job, stream_job_events

        job = create_job(
            [sys.executable, "-c", 'print("terminal stream line"); import sys; sys.stdout.flush()']
        )
        job.start()
        for _ in range(50):
            if job.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job.status, "completed")
        self.assertIn("terminal stream line", "".join(job.output_lines))

        async def _consume():
            events = []
            async for evt in stream_job_events(job):
                events.append(evt)
            return events

        events = asyncio.run(_consume())
        self.assertTrue(any("terminal stream line" in e for e in events))
        self.assertTrue(any("finished" in e for e in events))

    def test_settings_api_and_connection(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        r_get = client.get("/api/settings")
        self.assertEqual(r_get.status_code, 200)
        self.assertIn("geminiModel", r_get.json())
        payload = {
            "defaultProvider": "gemini",
            "geminiApiKey": "AIzaSyTest1234567890",
            "geminiModel": "gemini-3.6-flash",
        }
        from d11lib.ai_providers import save_ai_settings

        with patch(
            "d11lib.ai_providers.save_ai_settings",
            side_effect=lambda b: save_ai_settings(b, env_path=self.home / ".env"),
        ):
            r_post = client.post(
                "/api/settings", json=payload, headers={"Origin": "http://127.0.0.1:8765"}
            )
        self.assertEqual(r_post.status_code, 200)
        self.assertEqual(r_post.json()["status"], "saved")
        self.assertTrue(r_post.json()["settings"]["hasGeminiKey"])
        self.assertIn("••••", r_post.json()["settings"]["geminiApiKey"])
        with patch("d11lib.ai_providers.urllib.request.urlopen") as mock_url:
            from unittest.mock import MagicMock

            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp
            r_test = client.post(
                "/api/settings/test",
                json={"provider": "gemini", "apiKey": "AIzaSyFakeKey"},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_test.status_code, 200)
            self.assertTrue(r_test.json()["ok"])

    def test_standalone_html_report(self):
        self.register()
        out = self.home / "runs/test_rep"
        write(
            out / "state.json",
            {
                "id": "test_rep",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "completed",
            },
        )
        write(
            out / "evidence.json",
            {
                "run": {
                    "id": "test_rep",
                    "project": "alpha",
                    "action": "guided-audit",
                    "status": "completed",
                },
                "readiness": "GREEN",
                "assessment": {
                    "checks": [{"id": "php", "status": "passed", "message": "PHP 8.3 OK"}]
                },
                "compatibility": {
                    "summary": {"total": 1, "ready": 1},
                    "extensions": [
                        {
                            "name": "my_mod",
                            "type": "module",
                            "status": "ready",
                            "selectedAction": "keep",
                            "risk": "low",
                        }
                    ],
                },
            },
        )
        (out / "visual/reference").mkdir(parents=True, exist_ok=True)
        (out / "visual/reference/desktop_route_1.png").write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        write(out / "hashes.json", {})
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        r_html = client.get("/api/runs/test_rep/report.html")
        self.assertEqual(r_html.status_code, 200)
        self.assertIn("<!doctype html>", r_html.text)
        self.assertIn("data:image/png;base64,", r_html.text)
        self.assertIn("my_mod", r_html.text)
        self.assertTrue((out / "summary.html").is_file())

    def test_quick_add_and_quick_summary_and_one_click_upgrade(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/test_audit"
        write(
            out / "state.json",
            {
                "id": "test_audit",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "checkpoint": "completed",
            },
        )
        write(
            out / "gate.json",
            {
                "approvalEligible": True,
                "approvalDigest": "dig123",
                "risk": {"score": 15, "recommendation": "Go", "hardBlockers": []},
                "baseline": {"selected": 3, "headerCount": 2, "footerCount": 1},
                "targetCore": "11.4.6",
            },
        )
        write(
            out / "result/context.json",
            {"composer": {"packages": [{"name": "drupal/core", "version": "10.4.5"}]}},
        )
        r_sum = client.get("/api/runs/test_audit/quick-summary")
        self.assertEqual(r_sum.status_code, 200)
        sum_data = r_sum.json()
        self.assertEqual(sum_data["targetCore"], "11.4.6")
        self.assertEqual(sum_data["currentCore"], "10.4.5")
        self.assertEqual(sum_data["baselineRoutes"], 3)
        self.assertEqual(sum_data["headerCount"], 2)
        self.assertEqual(sum_data["footerCount"], 1)
        self.assertEqual(sum_data["timeline"]["automatedDuration"], "5–10 minutes")
        self.assertEqual(sum_data["timeline"]["manualReviewHours"], 0)

        with (
            patch("d11lib.workflow.Workflow.auto_decide") as mock_decide,
            patch("d11lib.workflow.Workflow.approve_gate") as mock_approve,
            patch("d11lib.workflow.Workflow.start") as mock_start,
        ):
            mock_decide.return_value = {"decisions": []}
            mock_approve.return_value = {"approved": True, "reviewer": "Auto Tester"}
            mock_start.return_value = {
                "id": "run_upgrade_1",
                "status": "running",
                "action": "guided-upgrade",
            }
            r_upgrade = client.post(
                "/api/runs/test_audit/one-click-upgrade",
                json={"reviewer": "Auto Tester"},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_upgrade.status_code, 200)
            up_res = r_upgrade.json()
            self.assertEqual(up_res["status"], "started")
            self.assertEqual(up_res["run"]["id"], "run_upgrade_1")
            self.assertEqual(up_res["run"]["status"], "running")

        # Verify rollback is prevented when upgrade is currently running
        write(
            self.home / "runs/run_upgrade_1/state.json",
            {
                "id": "run_upgrade_1",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "running",
            },
        )
        r_rollback = client.post(
            "/api/runs/run_upgrade_1/rollback",
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(r_rollback.status_code, 409)
        self.assertIn(
            "Cannot roll back while upgrade rehearsal is actively running",
            r_rollback.json().get("error", ""),
        )

        with tempfile.TemporaryDirectory() as src_dir:
            src = Path(src_dir).resolve() / "source"
            src.mkdir()
            write(src / "composer.json", {"require": {"drupal/core-recommended": "^10"}})
            (src / "web").mkdir(parents=True)
            (src / ".ddev").mkdir()
            with patch("d11lib.guided_setup.Setup.scan") as mock_scan:
                mock_scan.return_value = {
                    "id": "run_scan_1",
                    "status": "running",
                    "action": "guided-audit",
                }
                payload = {
                    "id": "quick-test",
                    "name": "Quick Test",
                    "source": str(src),
                    "sourceUrl": "https://quick.ddev.site",
                }
                r_quick = client.post(
                    "/api/setup/quick-add",
                    json=payload,
                    headers={"Origin": "http://127.0.0.1:8765"},
                )
                self.assertEqual(r_quick.status_code, 200)
                q_res = r_quick.json()
                self.assertEqual(q_res["project"]["id"], "quick-test")
                self.assertEqual(q_res["run"]["id"], "run_scan_1")

    def test_guardrails_api_and_safe_handoff(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        p_dir = self.home / "projects/alpha/site"
        p_dir.mkdir(parents=True, exist_ok=True)
        write(p_dir / "composer.json", {"name": "drupal/alpha", "require": {"drupal/core": "^11"}})
        mod_dir = p_dir / "web/modules/custom/alpha_mod"
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "alpha_mod.module").write_text(
            "<?php\nfunction alpha_mod_test() {\n\t$a = array();\n  drupal_set_message('hi');\n  return $a;\n}\n?>\n"
        )

        # 1. GET guardrails
        r_g = client.get("/api/projects/alpha/guardrails")
        self.assertEqual(r_g.status_code, 200)
        g_data = r_g.json()
        self.assertIn("summary", g_data)
        self.assertIn("phpcs", g_data["guardrails"])
        self.assertFalse(g_data["summary"]["phpcsPassed"])

        # 2. POST guardrails fix
        r_fix = client.post(
            "/api/projects/alpha/guardrails/fix", headers={"Origin": "http://127.0.0.1:8765"}
        )
        self.assertEqual(r_fix.status_code, 200)
        fix_data = r_fix.json()
        self.assertEqual(fix_data["status"], "fixed")
        self.assertTrue(any("alpha_mod.module" in f for f in fix_data["fixedFiles"]))

        # 3. Safe Handoff (Strict Guardrail: never auto-commit, never auto-push)
        with tempfile.TemporaryDirectory() as src_dir:
            src = Path(src_dir).resolve() / "source"
            src.mkdir()
            (src / ".git").mkdir()

            def fake_cmd(argv, cwd, timeout, **kwargs):
                if "rev-parse" in argv and "--is-inside-work-tree" in argv:
                    return {"exitCode": 0, "stdout": "true\n", "stderr": ""}
                elif "rev-parse" in argv and "--abbrev-ref" in argv:
                    return {"exitCode": 0, "stdout": "main\n", "stderr": ""}
                elif "checkout" in argv:
                    return {"exitCode": 0, "stdout": "", "stderr": ""}
                elif "status" in argv:
                    return {"exitCode": 0, "stdout": "", "stderr": ""}
                return {"exitCode": 0, "stdout": "", "stderr": ""}

            with patch("d11lib.handoff.command", side_effect=fake_cmd):
                r_hand = client.post(
                    "/api/projects/alpha/handoff",
                    json={"branch": "upgrade/drupal-11", "commit": True, "source": str(src)},
                    headers={"Origin": "http://127.0.0.1:8765"},
                )
                self.assertEqual(r_hand.status_code, 200)
                h_res = r_hand.json()
                self.assertTrue(h_res["success"])
                self.assertFalse(h_res["committed"])
                self.assertFalse(h_res["autoPushed"])
                self.assertIn("guardrail", h_res["guardrailNotice"].lower())

    def test_delete_project_removes_managed_copy_and_preserves_source(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        with tempfile.TemporaryDirectory() as src_dir:
            src = Path(src_dir).resolve() / "real_drupal_site"
            src.mkdir()
            (src / "composer.json").write_text('{"name":"drupal/real-site"}')
            (src / "web").mkdir()
            (src / "web/index.php").write_text("<?php // keep safe")
            before_src_files = sorted([str(p.relative_to(src)) for p in src.rglob("*")])

            # Associate source in draft
            draft_dir = self.home / "drafts/alpha"
            draft_dir.mkdir(parents=True, exist_ok=True)
            write(draft_dir / "draft.json", {"id": "alpha", "source": str(src)})

            # Create an associated run
            run_dir = self.home / "runs/run_alpha_audit"
            run_dir.mkdir(parents=True, exist_ok=True)
            write(
                run_dir / "state.json",
                {"id": "run_alpha_audit", "project": "alpha", "status": "completed"},
            )

            # Verify everything is present before deletion
            self.assertTrue((self.home / "projects/alpha").is_dir())
            self.assertTrue(draft_dir.is_dir())
            self.assertTrue(run_dir.is_dir())

            # DELETE project via API
            r_del = client.delete(
                "/api/projects/alpha", headers={"Origin": "http://127.0.0.1:8765"}
            )
            self.assertEqual(r_del.status_code, 200)
            res_data = r_del.json()
            self.assertEqual(res_data["status"], "removed")
            self.assertEqual(res_data["projectId"], "alpha")
            self.assertEqual(res_data["sourcePath"], str(src))

            # Verify managed artifacts are gone
            self.assertFalse((self.home / "projects/alpha").exists())
            self.assertFalse(draft_dir.exists())
            self.assertFalse(run_dir.exists())

            # STRICT SAFETY: Verify user source repo is 100% intact and untouched
            self.assertTrue(src.is_dir())
            self.assertTrue((src / "composer.json").is_file())
            self.assertEqual((src / "composer.json").read_text(), '{"name":"drupal/real-site"}')
            self.assertTrue((src / "web/index.php").is_file())
            after_src_files = sorted([str(p.relative_to(src)) for p in src.rglob("*")])
            self.assertEqual(before_src_files, after_src_files)

            # Verify 409 on deleting non-existent or already deleted project (Problem exception)
            r_del_again = client.delete(
                "/api/projects/alpha", headers={"Origin": "http://127.0.0.1:8765"}
            )
            self.assertEqual(r_del_again.status_code, 409)

    def test_dashboard_csp_and_layout_no_top_gap(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        csp = r.headers.get("Content-Security-Policy", "")
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)
        body_idx = r.text.find("<body>")
        layout_idx = r.text.find('<div class="layout">')
        self.assertTrue(body_idx > 0 and layout_idx > body_idx)
        between = r.text[body_idx + len("<body>") : layout_idx].strip()
        self.assertEqual(
            between, "", msg='There must be no elements between <body> and <div class="layout">'
        )
        self.assertIn('id="icon-sprite"', r.text)

    def test_project_activity_endpoint(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        # 1. Check empty activity
        r = client.get("/api/projects/alpha/activity")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["events"], [])

        # 2. Add event to draft
        draft_dir = self.home / "drafts/alpha"
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / "events.jsonl").write_text(
            json.dumps({"at": now(), "type": "checkpoint", "checkpoint": "copying_code"}) + "\n"
        )
        r2 = client.get("/api/projects/alpha/activity")
        self.assertEqual(r2.status_code, 200)
        events = r2.json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["checkpoint"], "copying_code")

    def test_quick_add_retry_unfinalized_draft_overwrites_cleanly(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        with tempfile.TemporaryDirectory() as src_dir:
            src = Path(src_dir).resolve() / "drupal_site"
            src.mkdir()
            (src / "composer.json").write_text('{"name":"drupal/test"}')
            (src / ".docksal").mkdir()
            draft_dir = self.home / "drafts" / "retry-site"
            draft_dir.mkdir(parents=True, exist_ok=True)
            write(
                draft_dir / "draft.json",
                {"id": "retry-site", "status": "blocked", "source": str(src)},
            )
            with patch("d11lib.guided_setup.Setup.scan", return_value={"run": None}):
                r = client.post(
                    "/api/setup/quick-add",
                    json={"id": "retry-site", "source": str(src), "name": "Retry Site"},
                    headers={"Origin": "http://127.0.0.1:8765"},
                )
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertEqual(data["pid"], "retry-site")
            self.register()
            r_dup = client.post(
                "/api/setup/quick-add",
                json={"id": "alpha", "source": str(src), "name": "Alpha"},
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r_dup.status_code, 409)
    def test_rollback_allowed_when_reconciliation_required(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/interrupted_upgrade"
        out.mkdir(parents=True, exist_ok=True)
        (out / "recovery-checkpoint").mkdir(parents=True, exist_ok=True)
        write(
            out / "recovery-checkpoint/manifest.json",
            {
                "checkpointId": "chk123",
                "project": "alpha",
                "gitHead": "HEAD",
                "hashes": {"database": "abc"},
            },
        )
        write(
            out / "state.json",
            {
                "id": "interrupted_upgrade",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "reconciliation_required",
                "checkpoint": "automatic_rollback",
            },
        )
        # Verify normal actions are blocked by reconciliation_required
        with self.assertRaises(Problem):
            self.w.start("alpha", "assess")

        # But rollback action is allowed to start
        with patch("subprocess.Popen") as mock_pop:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_pop.return_value = mock_proc
            r = client.post(
                "/api/runs/interrupted_upgrade/rollback",
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(r.status_code, 200)
            res = r.json()
            self.assertEqual(res["status"], "running")
            self.assertEqual(res["action"], "guided-rollback")

    def test_reset_workflow_lock_clears_reconciliation_required(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/interrupted_upgrade_2"
        out.mkdir(parents=True, exist_ok=True)
        write(
            out / "state.json",
            {
                "id": "interrupted_upgrade_2",
                "project": "alpha",
                "action": "guided-upgrade",
                "status": "reconciliation_required",
                "checkpoint": "automatic_rollback",
            },
        )
        r_reset = client.post(
            "/api/workflow/reset-lock",
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(r_reset.status_code, 200)
        self.assertTrue(r_reset.json()["ok"])
        self.assertGreaterEqual(r_reset.json()["clearedRuns"], 1)
        # Check run state was updated and reconciled
        s = json.loads((out / "state.json").read_text())
        self.assertEqual(s["status"], "interrupted")
        self.assertIn("reconciliation", s)
        self.assertEqual(s["reconciliation"]["reviewer"], "Operator Reset")

    def test_post_compatibility_decisions(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/audit_decisions"
        out.mkdir(parents=True, exist_ok=True)
        write(
            out / "state.json",
            {
                "id": "audit_decisions",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "fingerprint": self.w.fingerprint(self.home / "projects/alpha", self.w.project("alpha")[1]),
            },
        )
        write(
            out / "compatibility-report.json",
            {
                "digest": "compat-1",
                "extensions": [
                    {
                        "name": "token",
                        "status": "update_available",
                        "currentVersion": "1.0",
                        "targetVersion": "2.0",
                        "releaseCandidates": [{"version": "2.0", "stability": "stable"}],
                        "upgradeStatus": {"issueCount": 0},
                        "rector": {"fixableCount": 0},
                    }
                ],
            },
        )
        write(out / "batch-config.json", self.cfg)
        write(out / "plan.json", {"planId": "p", "steps": [], "blockers": []})
        write(out / "gate.json", {"approvalEligible": True, "baseline": {"passed": True}, "risk": {"recommendation": "Go"}})
        write(out / "audit-tools.json", {"checks": []})
        write(out / "patch-candidates.json", {})
        write(out / "route-selection.json", {"routes": []})
        write(out / "result/result.json", {"checks": []})
        write(
            out / "result/context.json",
            {
                "roots": {"composer": str(self.home / "projects/alpha/site"), "drupal": str(self.home / "projects/alpha/site")},
                "extensions": [{"name": "token", "package": "drupal/token"}],
                "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
                "deployment": {"status": "unknown", "observations": []},
                "removedCoreDependencies": [],
                "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
            },
        )
        write(
            self.home / "projects/alpha/site/composer.json",
            {"name": "test/site", "require": {"drupal/token": "^1.0"}},
        )

        with patch("d11lib.solver.resolve") as mock_resolve:
            mock_resolve.return_value = {
                "status": "passed",
                "exactCoreVersion": "11.1.0",
                "lockPackages": {"drupal/token": "2.0.0"},
                "changedFiles": [],
                "changes": [],
            }
            res = client.post(
                "/api/runs/audit_decisions/compatibility-decisions",
                json={
                    "decisions": [
                        {
                            "name": "token",
                            "action": "compatible_release",
                            "candidateVersion": "2.0",
                            "acceptRisk": False,
                        }
                    ]
                },
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("gate", data)
            saved = read(out / "compatibility-decisions.json")
            token_dec = saved["token"] if isinstance(saved, dict) else next(d for d in saved if d.get("name") == "token")
            self.assertEqual(token_dec["action"], "compatible_release")
            self.assertEqual(token_dec["candidateVersion"], "2.0")

    def test_one_click_upgrade_sanitizer_rewrites_unclean_keep(self):
        self.register()
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        out = self.home / "runs/audit_sanitize"
        out.mkdir(parents=True, exist_ok=True)
        write(
            out / "state.json",
            {
                "id": "audit_sanitize",
                "project": "alpha",
                "action": "guided-audit",
                "status": "completed",
                "fingerprint": self.w.fingerprint(self.home / "projects/alpha", self.w.project("alpha")[1]),
            },
        )
        # Extension with issues: keep should be rewritten to compatible_release
        write(
            out / "compatibility-report.json",
            {
                "digest": "compat-sanitize",
                "extensions": [
                    {
                        "name": "dirty_mod",
                        "status": "patch_available",
                        "currentVersion": "1.0",
                        "targetVersion": "2.0",
                        "upgradeStatus": {"issueCount": 3},
                        "rector": {"fixableCount": 1},
                    },
                    {
                        "name": "clean_mod",
                        "status": "ready",
                        "currentVersion": "1.0",
                        "targetVersion": "1.0",
                        "upgradeStatus": {"issueCount": 0},
                        "rector": {"fixableCount": 0},
                    },
                ],
            },
        )
        write(out / "batch-config.json", self.cfg)
        write(out / "plan.json", {"planId": "p", "steps": [], "blockers": []})
        write(
            out / "gate.json",
            {
                "approvalEligible": True,
                "approvalDigest": "digest",
                "compatibility": {"summary": {"unresolved": 0}},
                "risk": {"recommendation": "Go"},
            },
        )
        write(out / "audit-tools.json", {"checks": []})
        write(out / "patch-candidates.json", {})
        write(
            out / "result/context.json",
            {
                "roots": {"composer": str(self.home / "projects/alpha/site"), "drupal": str(self.home / "projects/alpha/site")},
                "extensions": [],
            },
        )

        with patch("d11lib.solver.resolve") as mock_resolve, patch("subprocess.Popen") as mock_pop:
            mock_resolve.return_value = {
                "status": "passed",
                "lockPackages": {},
                "changedFiles": [],
            }
            mock_proc = MagicMock()
            mock_proc.pid = 9999
            mock_pop.return_value = mock_proc

            res = client.post(
                "/api/runs/audit_sanitize/one-click-upgrade",
                json={
                    "decisions": [
                        {"name": "dirty_mod", "action": "keep"},
                        {"name": "clean_mod", "action": "keep"},
                    ],
                    "reportDigest": "compat-sanitize",
                    "autoRemediate": False,
                },
                headers={"Origin": "http://127.0.0.1:8765"},
            )
            # Sanitizer rewrote dirty_mod to compatible_release
            saved = read(out / "compatibility-decisions.json")
            dec_map = saved if isinstance(saved, dict) else {d["name"]: d for d in saved}
            self.assertEqual(dec_map["dirty_mod"]["action"], "compatible_release")
            self.assertTrue(dec_map["dirty_mod"]["acceptRisk"])
            self.assertIn("Auto-aligned", dec_map["dirty_mod"]["note"])
            # clean_mod kept as keep
            self.assertEqual(dec_map["clean_mod"]["action"], "keep")

    def test_api_knowledge(self):
        client = TestClient(create_app(self.home), base_url="http://127.0.0.1:8765")
        res = client.get("/api/knowledge", headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["targetVersion"], "11")
        self.assertIn("requirements", data)

        res10 = client.get("/api/knowledge/10", headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(res10.status_code, 200)
        data10 = res10.json()
        self.assertEqual(data10["targetVersion"], "10")



if __name__ == "__main__":
    unittest.main()

