import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import *
from d11lib.discovery import config_diff, discover
from d11lib.execution import execute, execution_lock, plan
from d11lib.reporting import checks, reports


class ToolkitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "site"
        self.root.mkdir()
        self.out = self.base / "out"
        self.cfg = {
            "schemaVersion": "1.0",
            "repository": "site",
            "environment": {"id": "fixture", "kind": "local", "authorized": True},
            "site": {"uri": "https://fixture.test"},
            "runtime": {
                "wrapper": "local",
                "commands": {
                    "php": [sys.executable, "fake.py", "php"],
                    "composer": [sys.executable, "fake.py", "composer"],
                    "drush": [sys.executable, "fake.py", "drush"],
                },
            },
            "_root": str(self.root),
            "_config": str(self.base / "project.json"),
        }

    def tearDown(self):
        self.temp.cleanup()

    def layout(self, nested=False, docroot=False):
        cr = self.root / "docroot" if nested else self.root
        cr.mkdir(exist_ok=True)
        dr = cr if nested else self.root / ("docroot" if docroot else "web")
        (dr / "sites/default").mkdir(parents=True)
        (dr / "core/lib").mkdir(parents=True)
        (dr / "core/lib/Drupal.php").write_text("<?php")
        write(
            cr / "composer.json",
            {
                "require": {"drupal/core-recommended": "^10.3", "drupal/example": "^1"},
                "extra": {
                    "drupal-scaffold": {"locations": {"web-root": str(dr.relative_to(cr)) or "."}}
                },
            },
        )
        write(
            cr / "composer.lock",
            {
                "packages": [
                    {"name": "drupal/core", "version": "10.3.0"},
                    {"name": "drupal/example", "version": "1.0.0"},
                ]
            },
        )
        return cr, dr

    def test_layouts(self):
        for nested, docroot in [(False, False), (False, True), (True, False)]:
            with self.subTest(nested=nested, docroot=docroot):
                import shutil

                shutil.rmtree(self.root)
                self.root.mkdir()
                cr, dr = self.layout(nested, docroot)
                data = discover(self.cfg)
                self.assertEqual(data["roots"]["composer"], str(cr))
                self.assertEqual(data["roots"]["drupal"], str(dr))

    def test_wrapper_uses_repository_not_path(self):
        self.layout()
        self.cfg.pop("runtime")
        (self.root / ".ddev").mkdir()
        (self.root / ".ddev/config.yaml").write_text("name: fixture")
        self.assertEqual(discover(self.cfg)["wrapper"]["selected"], "ddev")
        (self.root / ".docksal").mkdir()
        self.assertIsNone(discover(self.cfg)["wrapper"]["selected"])

    def test_missing_runtime_not_disabled(self):
        cr, dr = self.layout()
        m = dr / "modules/contrib/example/sub"
        m.mkdir(parents=True)
        (m / "sub.info.yml").write_text(
            "name: Sub\ntype: module\ncore_version_requirement: ^10 || ^11\n"
        )
        data = discover(self.cfg)
        self.assertIsNone(data["extensions"][0]["installed"])
        self.assertEqual(data["extensions"][0]["compatibility"], "unknown")

    def test_package_maps_multiple_modules(self):
        cr, dr = self.layout()
        m = dr / "modules/contrib/example"
        m.mkdir(parents=True)
        for name in ["one", "two"]:
            (m / (name + ".info.yml")).write_text("type: module\n")
        write(
            cr / "vendor/composer/installed.json",
            {
                "packages": [
                    {"name": "drupal/example", "install-path": "../../web/modules/contrib/example"}
                ]
            },
        )
        self.assertEqual(
            [x["package"] for x in discover(self.cfg)["extensions"]], ["drupal/example"] * 2
        )

    def test_multisite_requires_uri(self):
        self.layout()
        self.cfg.pop("site")
        with self.assertRaises(Problem):
            discover(self.cfg, True)

    def test_production_runtime_blocked(self):
        self.layout()
        self.cfg["environment"]["kind"] = "production"
        with self.assertRaises(Problem):
            discover(self.cfg, True)

    def test_config_risks(self):
        a = self.base / "a"
        b = self.base / "b"
        a.mkdir()
        b.mkdir()
        (a / "field.storage.node.body.yml").write_text("type: text\n")
        (a / "core.extension.yml").write_text("module:\n  forum: 0\n")
        (b / "core.extension.yml").write_text("module: {}\n")
        (a / "system.site.yml").write_text("uuid: first")
        (b / "system.site.yml").write_text("uuid: second")
        risks = [r for c in config_diff(a, b) for r in c["risks"]]
        self.assertIn("field_or_storage_deletion", risks)
        self.assertIn("module_removal:forum", risks)
        self.assertIn("site_uuid_mismatch", risks)

    def test_deployment_observed_order_and_ci(self):
        self.layout()
        (self.root / ".ci").mkdir()
        (self.root / ".ci/deploy").write_text("drush deploy\ncomposer install\ndrush cim\n")
        rows = discover(self.cfg)["deployment"]["observations"]
        self.assertEqual([r["line"] for r in rows], [1, 2, 3])
        self.assertIn("drush deploy", rows[0]["text"])

    def approved(self, fail=False):
        cr, dr = self.layout()
        (self.root / "fake.py").write_text((ROOT / "tests/fixtures/runtime.py").read_text())
        token = dr / "modules/token"
        token.mkdir(parents=True)
        (token / "token.info.yml").write_text("type: module\n")
        self.cfg["deliveryBudget"] = {
            "reportedHumanHours": 1,
            "forecast": {"lowHours": 10, "highHours": 20, "basis": "fixture scope"},
        }
        self.cfg.update(
            target="^11.2",
            identity={"argv": [sys.executable, "-c", 'print("fixture")'], "expected": "fixture"},
            recovery={
                k: "verified fixture reference"
                for k in ["code", "database", "files", "owner", "procedure", "verifiedAt"]
            },
            requirementsEvidence="requirements.json",
            baselineEvidence="baseline.json",
        )
        write(
            self.base / "requirements.json",
            {
                "target": "^11.2",
                "sources": ["https://www.drupal.org/docs/upgrading-drupal"],
                "verifiedAt": now(),
                "readinessPassed": True,
                "earlierDatabaseUpdatesComplete": True,
                "removedCoreExtensionsReviewed": True,
            },
        )
        write(self.base / "capture.json", {"stable": True})
        write(
            self.base / "baseline.json",
            {
                "environment": self.cfg["environment"],
                "site": self.cfg["site"],
                "codeRevision": "fixture revision",
                "installedExtensions": "fixture inventory",
                "activeConfiguration": "fixture config",
                "publicScenarios": ["public"],
                "authenticatedScenarios": ["editor"],
                "captureSettings": "capture.json",
                "captureSettingsHash": file_hash(self.base / "capture.json"),
            },
        )
        self.cfg["steps"] = [
            {
                "id": "first",
                "stage": "D-composer",
                "argv": [
                    sys.executable,
                    "-c",
                    "import sys;sys.exit(7)"
                    if fail
                    else 'from pathlib import Path;Path("changed.txt").write_text("approved")',
                ],
                "cwd": ".",
                "mutates": True,
                "description": "Fixture mutation",
                "preconditions": [
                    {"argv": [sys.executable, "-c", 'print("before")'], "stdoutEquals": "before"}
                ],
                "postconditions": [
                    {
                        "argv": [
                            sys.executable,
                            "-c",
                            'from pathlib import Path;assert Path("changed.txt").exists()',
                        ]
                    }
                ],
            },
            {
                "id": "later",
                "stage": "E-deployment",
                "argv": [
                    sys.executable,
                    "-c",
                    'from pathlib import Path;Path("later.txt").touch()',
                ],
                "cwd": ".",
                "mutates": True,
                "description": "Later fixture step",
                "dependsOn": ["first"],
                "preconditions": [{"argv": [sys.executable, "-c", "pass"]}],
                "postconditions": [{"argv": [sys.executable, "-c", "pass"]}],
            },
        ]
        ctx = discover(self.cfg)
        ctx["runtime"]["status"] = "collected"
        ctx["runtime"]["commands"]["status"] = {"data": {"drupal-version": "10.3.0"}}
        p = plan(self.cfg, ctx, self.out)
        a = {
            "schemaVersion": "1.0",
            "planId": p["planId"],
            "environment": p["environment"],
            "site": p["site"],
            "approver": "Fixture tester",
            "approvedAt": now(),
            "steps": ["first", "later"],
            "recoveryDigest": digest(p["backup"]),
        }
        return p, a

    def test_failure_stops_later_steps_and_resume(self):
        p, a = self.approved(True)
        run, code = execute(self.cfg, p, a, self.out)
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "later.txt").exists())
        self.assertEqual(run["steps"][0]["command"]["exitCode"], 7)
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out, True)
        reports(self.out, {**run, "checks": [{"id": "first", "status": "failed"}]}, self.cfg)
        self.assertTrue((self.out / "client-report.md").exists())

    def test_success_and_checkpoint_resume(self):
        p, a = self.approved()
        run, code = execute(self.cfg, p, a, self.out)
        self.assertEqual(code, 0)
        _, code = execute(self.cfg, p, a, self.out, True)
        self.assertEqual(code, 0)
        (self.root / "changed.txt").write_text("unexpected")
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out, True)

    def test_stale_dirty_input(self):
        p, a = self.approved()
        (self.root / "dirty.txt").write_text("uncommitted")
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out)

    def test_environment_and_approval_mismatch(self):
        p, a = self.approved()
        a["environment"] = {**a["environment"], "id": "other"}
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out)

    def test_concurrent_lock(self):
        with execution_lock(self.cfg):
            with self.assertRaises(Problem):
                with execution_lock(self.cfg):
                    pass

    def test_scanner_findings_vs_crash(self):
        self.layout()
        self.cfg["checks"] = [
            {
                "id": "scan",
                "argv": [
                    sys.executable,
                    "-c",
                    'import json,sys;print(json.dumps([{"type":"issue","description":"deprecated","location":{"path":"file.php"}}]));sys.exit(1)',
                    "--format=codeclimate",
                    "--all",
                ],
                "cwd": ".",
                "kind": "upgrade_status",
                "required": True,
            }
        ]
        self.assertEqual(checks(self.cfg, discover(self.cfg))[0]["status"], "findings")
        self.cfg["checks"][0]["argv"][2] = "import sys;sys.exit(1)"
        self.assertEqual(checks(self.cfg, discover(self.cfg))[0]["status"], "tool_failure")

    def test_cli_invalid_usage(self):
        p = subprocess.run([str(ROOT / "bin/d11"), "--wrong"], capture_output=True)
        self.assertEqual(p.returncode, 64)

    def test_no_git_stage_or_commit(self):
        self.layout()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        before = command(["git", "ls-files", "--stage"], self.root)["stdout"]
        discover(self.cfg)
        self.assertEqual(command(["git", "ls-files", "--stage"], self.root)["stdout"], before)
        self.assertNotEqual(command(["git", "rev-parse", "HEAD"], self.root)["exitCode"], 0)

    def test_missing_backup_blocks(self):
        p, a = self.approved()
        p["backup"]["database"] = ""
        p["planId"] = digest({k: v for k, v in p.items() if k != "planId"})
        a["planId"] = p["planId"]
        a["recoveryDigest"] = digest(p["backup"])
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out)

    def test_missing_baseline_blocks(self):
        p, a = self.approved()
        (self.base / "capture.json").unlink()
        with self.assertRaises(Problem):
            execute(self.cfg, p, a, self.out)

    def test_shell_entrypoint_blocked(self):
        from d11lib.execution import validate_steps

        p, a = self.approved()
        p["steps"][0]["argv"] = ["bash", "-c", "echo unsafe"]
        with self.assertRaises(Problem):
            validate_steps(p["steps"])

    def test_timeout_is_failure(self):
        r = command([sys.executable, "-c", "import time;time.sleep(5)"], self.root, 0.05)
        self.assertEqual(r["status"], "tool_failure")
        self.assertIsNone(r["exitCode"])

    def test_multiple_wrappers_on_path(self):
        from unittest.mock import patch

        self.layout()
        self.cfg.pop("runtime")
        (self.root / ".ddev").mkdir()
        (self.root / ".ddev/config.yaml").write_text("name: fixture")
        bindir = self.base / "bin"
        bindir.mkdir()
        for name in ["ddev", "fin", "lando"]:
            f = bindir / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(bindir) + ":" + os.environ["PATH"]}):
            self.assertEqual(discover(self.cfg)["wrapper"]["selected"], "ddev")

    def test_cli_plan_execution_and_report_from_other_directory(self):
        self.approved()
        cfg_file = Path(self.cfg["_config"])
        write(cfg_file, {k: v for k, v in self.cfg.items() if not k.startswith("_")})
        argv = [
            str(ROOT / "bin/d11"),
            "plan",
            "--config",
            str(cfg_file),
            "--output",
            str(self.out),
            "--runtime",
        ]
        env = {**os.environ, "D11_HOME": str(self.base / ".d11")}
        p = subprocess.run(argv, cwd="/tmp", capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 3, p.stderr)
        generated = read(self.out / "plan.json")
        self.assertFalse(generated["blockers"])
        a = read(self.out / "approval.template.json")
        a.update(approver="Fixture reviewer", approvedAt=now())
        write(self.base / "approval.json", a)
        p = subprocess.run(
            [
                str(ROOT / "bin/d11"),
                "execute",
                "--config",
                str(cfg_file),
                "--output",
                str(self.out),
                "--plan-file",
                str(self.out / "plan.json"),
                "--approval-file",
                str(self.base / "approval.json"),
            ],
            cwd="/tmp",
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.root / "later.txt").exists())
        result = read(self.out / "result.json")
        self.assertEqual(result["status"], "passed")
        self.assertTrue((self.out / "client-report.md").exists())

    def test_deploy_requires_configuration_review(self):
        self.approved()
        self.cfg["steps"][1]["argv"] = ["ddev", "drush", "deploy"]
        ctx = discover(self.cfg)
        ctx["runtime"]["status"] = "collected"
        ctx["runtime"]["commands"]["status"] = {"data": {"drupal-version": "10.3.0"}}
        p = plan(self.cfg, ctx, self.out)
        self.assertTrue(any("configComparison" in x for x in p["blockers"]))

    def test_destructive_alias_blocked(self):
        from d11lib.execution import validate_steps

        p, a = self.approved()
        p["steps"][1]["argv"] = ["ddev", "drush", "pmu", "example"]
        with self.assertRaises(Problem):
            validate_steps(p["steps"])

    def test_defaults_cleanup_rules(self):
        from d11.compatibility import OBSOLETE_PERFORMANCE_MODULES, build

        self.assertIn("advagg", OBSOLETE_PERFORMANCE_MODULES)
        self.assertIn("fastclick", OBSOLETE_PERFORMANCE_MODULES)
        checks = [{"id": "upgrade_status", "status": "passed"}, {"id": "drupal_rector", "status": "passed"}]
        solver = {"status": "passed", "versions": {}}

        # 1. Unused contrib extension defaults to remove
        disco_unused = {
            "extensions": [
                {
                    "name": "unused_mod",
                    "type": "module",
                    "installed": False,
                    "exported": False,
                    "package": "drupal/unused_mod",
                    "path": "modules/contrib/unused_mod",
                }
            ]
        }
        report = build(
            disco_unused,
            checks,
            solver,
            {"packages": []},
            self.out,
        )
        ext = next(e for e in report["presentNotInstalled"] if e["name"] == "unused_mod")
        self.assertEqual(ext["recommendedAction"], "remove")

        # 2. Contrib theme with no stable release defaults to remove instead of patch
        disco_theme = {
            "extensions": [
                {
                    "name": "old_theme",
                    "type": "theme",
                    "installed": True,
                    "exported": True,
                    "package": "drupal/old_theme",
                    "path": "themes/contrib/old_theme",
                }
            ]
        }
        report_theme = build(
            disco_theme,
            checks,
            solver,
            {"packages": []},
            self.out,
        )
        ext_theme = next(e for e in report_theme["extensions"] if e["name"] == "old_theme")
        self.assertEqual(ext_theme["recommendedAction"], "remove")

        # 3. Performance module (advagg) defaults to remove
        disco_advagg = {
            "extensions": [
                {
                    "name": "advagg",
                    "type": "module",
                    "installed": True,
                    "exported": True,
                    "package": "drupal/advagg",
                    "path": "modules/contrib/advagg",
                }
            ]
        }
        report_advagg = build(
            disco_advagg,
            checks,
            solver,
            {"packages": []},
            self.out,
        )
        ext_advagg = next(e for e in report_advagg["extensions"] if e["name"] == "advagg")
        self.assertEqual(ext_advagg["recommendedAction"], "remove")


if __name__ == "__main__":
    unittest.main()
