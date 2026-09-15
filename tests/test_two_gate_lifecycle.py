import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.common import digest, file_hash, now, read, write
from d11lib.two_gate import approve, audit, decide, rollback, upgrade
from d11lib.workflow import Workflow


class TwoGateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "d11_home"
        self.w = Workflow(self.home)

        # Setup mock Git repository with synthetic Drupal 10 site
        self.src = self.base / "mock-d10-site"
        self.src.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.src, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.src, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.src, capture_output=True, check=True)

        (self.src / ".ddev").mkdir(parents=True)
        (self.src / "web/sites/default").mkdir(parents=True)
        (self.src / "web/sites/default/settings.php").write_text("<?php\n$databases = [];\n")

        # Custom module
        custom_mod = self.src / "web/modules/custom/my_feature"
        custom_mod.mkdir(parents=True)
        (custom_mod / "my_feature.info.yml").write_text("name: My Feature\ntype: module\ncore_version_requirement: ^10 || ^11\n")
        (custom_mod / "my_feature.module").write_text("<?php\n")

        # Composer manifest
        self.composer_json = {
            "name": "vendor/site",
            "type": "project",
            "require": {
                "drupal/core-recommended": "10.3.0",
                "drupal/token": "1.12.0",
            },
            "extra": {
                "installer-paths": {
                    "web/core": ["type:drupal-core"],
                    "web/modules/contrib/{$name}": ["type:drupal-module"],
                }
            },
        }
        write(self.src / "composer.json", self.composer_json)
        self.composer_lock = {
            "_readme": ["This is locked."],
            "packages": [
                {
                    "name": "drupal/core-recommended",
                    "version": "10.3.0",
                    "type": "metapackage",
                },
                {
                    "name": "drupal/token",
                    "version": "1.12.0",
                    "type": "drupal-module",
                },
            ],
            "packages-dev": [],
        }
        write(self.src / "composer.lock", self.composer_lock)

        subprocess.run(["git", "add", "."], cwd=self.src, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.src, capture_output=True, check=True)

        self.pid = "d10-site"

    def tearDown(self):
        self.tmp.cleanup()

    def _mock_command(self, argv, cwd, timeout=1800):
        cmd_str = " ".join(str(x) for x in argv)
        # Assess subcommand
        if "bin/d11" in cmd_str and "assess" in argv:
            out_idx = argv.index("--output")
            res_dir = Path(argv[out_idx + 1])
            res_dir.mkdir(parents=True, exist_ok=True)
            write(
                res_dir / "result.json",
                {"status": "passed", "checks": [{"id": "d10_check", "status": "passed"}]},
            )
            write(
                res_dir / "context.json",
                {
                    "roots": {"composer": str(self.src), "drupal": str(self.src / "web"), "config": None},
                    "extensions": [
                        {
                            "name": "token",
                            "type": "module",
                            "package": "drupal/token",
                            "installed": True,
                            "path": "web/modules/contrib/token",
                            "currentVersion": "1.12.0",
                        },
                        {
                            "name": "my_feature",
                            "type": "module",
                            "installed": True,
                            "path": "web/modules/custom/my_feature",
                            "currentVersion": "1.0.0",
                            "coreConstraint": "^10 || ^11",
                        },
                    ],
                    "composer": {"packages": self.composer_lock["packages"]},
                    "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.3.0", "php-version": "8.3.0"}}}},
                    "deployment": {"status": "unknown", "observations": []},
                    "removedCoreDependencies": [],
                    "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
                },
            )
            return {"exitCode": 0, "status": "passed", "stdout": "", "stderr": ""}

        # Identity probe
        if "php" in cmd_str and ('echo "ok"' in cmd_str or "echo 'ok'" in cmd_str or 'echo \\"ok\\"' in cmd_str or 'print("ok")' in cmd_str or "ok" in argv):
            return {"exitCode": 0, "status": "passed", "stdout": "ok\n", "stderr": ""}

        # Drush / DDEV wrapper calls
        if "core:status" in cmd_str or "updatedb:status" in cmd_str:
            return {
                "exitCode": 0,
                "status": "passed",
                "stdout": json.dumps({"bootstrap": "Successful", "drupal-version": "11.0.0"}),
                "stderr": "",
            }

        if "cache:rebuild" in cmd_str or "cr" in cmd_str or "updatedb" in cmd_str:
            return {"exitCode": 0, "status": "passed", "stdout": "Cache rebuild ok", "stderr": ""}

        if "export-db" in cmd_str or "dump" in cmd_str:
            return {"exitCode": 0, "status": "passed", "stdout": "", "stderr": ""}

        if "composer" in cmd_str and "install" in cmd_str:
            return {"exitCode": 0, "status": "passed", "stdout": "Composer install ok", "stderr": ""}

        if "composer" in cmd_str and "validate" in cmd_str:
            return {"exitCode": 0, "status": "passed", "stdout": "composer.json is valid", "stderr": ""}

        if "composer" in cmd_str and "check-platform-reqs" in cmd_str:
            return {"exitCode": 0, "status": "passed", "stdout": "platform reqs ok", "stderr": ""}

        if "helper" in cmd_str or ("apply" in argv and "check" in argv):
            return {"exitCode": 0, "status": "passed", "stdout": "ok", "stderr": ""}

        return {"exitCode": 0, "status": "passed", "stdout": "", "stderr": ""}

    def test_full_two_gate_lifecycle(self):
        # 1. Audit Phase
        audit_out = self.home / "runs/audit_run"
        audit_out.mkdir(parents=True, exist_ok=True)
        audit_state = {
            "id": "audit_run",
            "project": self.pid,
            "action": "guided-audit",
            "status": "running",
            "options": {"fast": True, "capture_baseline": False},
        }

        with patch("d11lib.two_gate.command", side_effect=self._mock_command), \
             patch("d11lib.audit_tools.command", side_effect=self._mock_command), \
             patch("d11lib.solver.command", side_effect=self._mock_command), \
             patch("d11lib.execution.command", side_effect=self._mock_command), \
             patch("d11lib.execution.execute", return_value=({"status": "passed"}, 0)), \
             patch.object(self.w, "capture", return_value=None), \
             patch("d11lib.recovery.subprocess.run") as mock_sub_run, \
             patch("d11lib.solver.resolve") as mock_resolve, \
             patch("d11lib.audit_tools.run") as mock_run_audit_tools, \
             patch("d11lib.patches.discover") as mock_patches:

            mock_sub_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            def mock_audit_tools_impl(cfg, context, out, w, pid, fast=True):
                checks = [
                    {"id": "upgrade_status", "status": "passed", "scanner": "upgrade_status"},
                    {"id": "drupal_rector", "status": "passed", "scanner": "drupal_rector"},
                ]
                write(Path(out) / "audit-tools.json", {"mode": "mock", "checks": checks})
                return checks

            mock_run_audit_tools.side_effect = mock_audit_tools_impl

            from d11lib.guided_setup import Setup
            setup = Setup(self.w)
            setup.create({
                "id": self.pid,
                "source": str(self.src),
                "name": "D10 Site",
                "exportAuthorized": True,
            })
            setup.work(self.pid)

            # Mock solver resolution
            mock_resolve.return_value = {
                "status": "passed",
                "exactCoreVersion": "11.1.0",
                "lockPackages": {
                    "drupal/core-recommended": "11.1.0",
                    "drupal/token": "2.0.0",
                },
                "versions": {
                    "drupal/core-recommended": "11.1.0",
                    "drupal/token": "2.0.0",
                },
                "changedFiles": [
                    {
                        "path": "composer.json",
                        "beforeSha256": file_hash(self.src / "composer.json"),
                        "after": json.dumps(self.composer_json),
                        "finding": "composer_resolution",
                        "rationale": "Exact Drupal 11 resolution",
                        "verificationCheckIds": ["composer_validate"],
                    }
                ],
                "changes": [
                    {
                        "path": "composer.json",
                        "beforeSha256": file_hash(self.src / "composer.json"),
                        "after": json.dumps(self.composer_json),
                        "finding": "composer_resolution",
                        "rationale": "Exact Drupal 11 resolution",
                        "verificationCheckIds": ["composer_validate"],
                    }
                ],
            }

            # Mock patch discovery
            def mock_patches_impl(*args, **kwargs):
                out_path = kwargs.get("out") or (args[1] if len(args) > 1 else audit_out)
                res = {
                    "status": "passed",
                    "packages": [
                        {
                            "package": "drupal/token",
                            "machine": "token",
                            "candidates": [],
                            "releaseCandidates": [{"version": "2.0.0", "stability": "stable"}],
                        }
                    ],
                    "limitations": [],
                }
                write(Path(out_path) / "patch-candidates.json", res)
                return res

            mock_patches.side_effect = mock_patches_impl

            # Run Audit
            audit_result = audit(self.w, self.pid, audit_out, audit_state)
            self.assertIn("gate", audit_result)
            audit_state["status"] = "completed"
            write(audit_out / "state.json", audit_state)
            self.assertEqual(read(audit_out / "state.json")["status"], "completed")
            self.assertTrue((audit_out / "gate.json").is_file())
            self.assertTrue((audit_out / "plan.json").is_file())
            self.assertTrue((audit_out / "batch-config.json").is_file())

            gate = read(audit_out / "gate.json")
            self.assertIn("approvalDigest", gate)

            # 2. Decide Phase
            decisions_payload = {
                "decisions": [
                    {
                        "name": "token",
                        "action": "compatible_release",
                        "candidateVersion": "2.0.0",
                        "acceptRisk": False,
                    },
                    {
                        "name": "my_feature",
                        "action": "keep",
                        "candidateVersion": "1.0.0",
                        "acceptRisk": False,
                    },
                ]
            }
            decide_res = decide(self.w, "audit_run", decisions_payload)
            self.assertIn("gate", decide_res)
            self.assertIn("compatibility", decide_res)
            self.assertTrue((audit_out / "compatibility-decisions.json").is_file())

            # Read fresh gate after decision update
            gate = read(audit_out / "gate.json")
            plan = read(audit_out / "plan.json")

            # 3. Approve Gate 1
            approval_body = {
                "planId": plan["planId"],
                "reviewer": "CI Automated Engineer",
                "privacyReviewed": True,
                "acceptRisks": True,
                "approvalDigest": gate["approvalDigest"],
            }
            approve_res = approve(self.w, "audit_run", approval_body)
            self.assertEqual(approve_res["approvalDigest"], gate["approvalDigest"])
            self.assertTrue((audit_out / "gate-approval.json").is_file())
            self.assertTrue((audit_out / "approved-batch.json").is_file())

            # 4. Upgrade Execution Phase
            upgrade_out = self.home / "runs/upgrade_run"
            upgrade_out.mkdir(parents=True, exist_ok=True)
            upgrade_state = {
                "id": "upgrade_run",
                "project": self.pid,
                "action": "guided-upgrade",
                "status": "running",
            }

            with patch("d11lib.recovery.create") as mock_recovery_create:
                mock_recovery_create.return_value = {
                    "checkpointId": "chk_001",
                    "gitHead": "head_001",
                    "databaseDump": str(upgrade_out / "pre-upgrade.sql.gz"),
                }
                (upgrade_out / "pre-upgrade.sql.gz").write_bytes(b"fakedb")
                (upgrade_out / "recovery-checkpoint").mkdir(parents=True, exist_ok=True)
                write(upgrade_out / "recovery-checkpoint/manifest.json", {"checkpointId": "chk_001"})

                upg_res = upgrade(self.w, self.pid, "audit_run", upgrade_out, upgrade_state)
                self.assertTrue(upg_res["visualPassed"])
                self.assertEqual(read(upgrade_out / "state.json")["status"], "completed")

            # 5. Rollback Phase
            rollback_out = self.home / "runs/rollback_run"
            rollback_out.mkdir(parents=True, exist_ok=True)
            rollback_state = {
                "id": "rollback_run",
                "project": self.pid,
                "action": "guided-rollback",
                "status": "running",
            }

            with patch("d11lib.recovery.restore") as mock_recovery_restore:
                mock_recovery_restore.return_value = {
                    "checkpointId": "chk_001",
                    "databaseRestored": True,
                    "runtimeVerified": True,
                    "drupalBootstrap": True,
                    "criticalRoutes": [{"route": "/", "passed": True, "status": 200}],
                }
                rb_res = rollback(self.w, self.pid, "upgrade_run", rollback_out, rollback_state)
                self.assertEqual(rb_res["checkpointId"], "chk_001")
                self.assertEqual(read(upgrade_out / "state.json")["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
