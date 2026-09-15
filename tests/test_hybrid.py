import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.ai_providers import choose, inventory, propose
from d11lib.audit_tools import _rector_record, _upgrade_record
from d11lib.audit_tools import run as run_audit_tools
from d11lib.browser_reports import markdown, sections
from d11lib.common import Problem, file_hash, read, write
from d11lib.compatibility import build, safe_upgrade, validate_decisions
from d11lib.dashboard import create_app
from d11lib.execution import validate_steps
from d11lib.patches import _release_candidates
from d11lib.solver import prepare_manifest
from d11lib.workflow import Workflow
from fastapi.testclient import TestClient


class HybridCompatibilityTests(unittest.TestCase):
    def test_rector_config_is_noninteractive_and_errors_are_not_findings(self):
        from d11lib.audit_tools import _copy_analysis, _run_rector

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            analysis = root / "analysis"
            (analysis / "site").mkdir(parents=True)
            assets = source / "web/sites/default/files"
            assets.mkdir(parents=True)
            (assets / "asset").write_text("asset")
            _copy_analysis(source, root / "copy")
            self.assertFalse((root / "copy/web/sites/default/files").exists())
            rec = {"stdout": '{"file_diffs":[],"errors":[]}', "exitCode": 0, "stderr": ""}
            with patch("d11lib.audit_tools.command", return_value=rec) as call:
                result = _run_rector(
                    ["docker", "compose"],
                    analysis,
                    {
                        "roots": {
                            "drupal": str(source / "web"),
                            "custom": [str(source / "web/modules/custom")],
                        }
                    },
                    source,
                )
                self.assertEqual(result["status"], "passed")
                self.assertNotIn("--no-interaction", call.call_args.args[0])
                self.assertIn("--config=d11-analysis-rector.php", call.call_args.args[0])
                self.assertIn(
                    "Drupal11SetList::DRUPAL_11",
                    (analysis / "site/d11-analysis-rector.php").read_text(),
                )
            self.assertEqual(
                _rector_record({**rec, "stdout": '{"file_diffs":[],"errors":["bad"]}'})["status"],
                "tool_failure",
            )

    def test_installed_inventory_package_counts_and_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            base = context["extensions"][0]
            context["extensions"] += [
                {**base, "name": "token_sub"},
                {**base, "name": "unused", "installed": False},
                {
                    **base,
                    "name": "fixture",
                    "installed": False,
                    "path": "web/modules/contrib/token/tests/modules/fixture/fixture.info.yml",
                },
                {
                    **base,
                    "name": "active_test",
                    "path": "web/modules/custom/tests/active_test/active_test.info.yml",
                },
                dict(base),
            ]
            checks = [
                {"id": "upgrade_status", "status": "passed"},
                {"id": "drupal_rector", "status": "passed"},
            ]
            report = build(
                context,
                checks,
                {"status": "passed", "versions": {"drupal/token": "2.0.0"}},
                {"packages": []},
                root,
            )
            self.assertEqual(report["summary"]["installed"], 3)
            self.assertEqual(report["summary"]["presentNotInstalled"], 1)
            self.assertEqual(report["summary"]["excludedFixtures"], 1)
            self.assertEqual(report["summary"]["updatePackages"], 1)
            self.assertEqual(report["presentNotInstalled"][0]["name"], "unused")
            self.assertNotIn("unused", report["decisions"])
            self.assertTrue(
                next(row for row in report["extensions"] if row["name"] == "active_test")[
                    "blockers"
                ]
            )
            model = {
                "run": {"id": "r", "project": "p", "status": "completed", "action": "guided-audit"},
                "compatibility": report,
            }
            self.assertIn("Present but not installed", markdown(model))
            self.assertIn("unused", markdown(model))

    def test_equal_older_and_newer_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, "2.0.4")
            checks = [
                {"id": "upgrade_status", "status": "passed"},
                {"id": "drupal_rector", "status": "passed"},
            ]
            for version, status in [
                ("2.0.2", "blocked"),
                ("2.0.4", "ready"),
                ("2.0.5", "update_available"),
                ("2.1.0-beta1", "unknown"),
            ]:
                row = build(
                    context,
                    checks,
                    {"status": "passed", "versions": {"drupal/token": version}},
                    {"packages": []},
                    root,
                )["extensions"][0]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["safeUpgradeEligible"], version == "2.0.5")

    def test_attention_classification_and_complete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            checks = [
                {"id": "upgrade_status", "status": "passed", "issues": []},
                {"id": "drupal_rector", "status": "passed", "changes": []},
            ]
            solver = {"status": "passed", "versions": {"drupal/token": "2.0.0"}}
            result = build(context, checks, solver, {"packages": []}, root)
            row = result["extensions"][0]
            self.assertEqual(row["reviewDisposition"], "automatically_planned")
            self.assertEqual(row["workCategory"], "Routine updates")
            self.assertEqual(result["decisions"]["token"]["origin"], "automatic")
            rebuilt = build(context, checks, solver, {"packages": []}, root, result["decisions"])
            self.assertEqual(rebuilt["extensions"][0]["reviewDisposition"], "automatically_planned")
            selected = validate_decisions(result, [{"name": "token", "action": "defer"}])
            changed = build(context, checks, solver, {"packages": []}, root, selected)
            self.assertEqual(changed["extensions"][0]["reviewDisposition"], "needs_decision")
            self.assertEqual(changed["decisions"]["token"]["origin"], "operator")
            blocked = build(context, [], {"status": "blocked"}, {"packages": []}, root)
            self.assertEqual(blocked["extensions"][0]["reviewDisposition"], "verification_blocked")
            self.assertEqual(len(blocked["sharedBlockers"]), 3)
            self.assertFalse(blocked["extensions"][0]["safeUpgradeEligible"])
            from d11lib.two_gate import delivery_forecast

            self.assertIsNone(delivery_forecast(blocked, {"status": "blocked"}, True))
            retained = build(
                context,
                checks,
                solver,
                {"packages": []},
                root,
                {
                    "token": {
                        "action": "compatible_release",
                        "candidateVersion": "2.0.0",
                        "origin": "operator",
                        "requiresRevalidation": True,
                    }
                },
            )
            self.assertEqual(retained["extensions"][0]["reviewDisposition"], "needs_decision")
            self.assertTrue(
                any(
                    "Retained operator choice" in message
                    for message in retained["extensions"][0]["blockers"]
                )
            )
            model = {
                "run": {"id": "r", "project": "p", "status": "blocked", "action": "guided-audit"},
                "compatibility": result,
            }
            self.assertIn("Routine updates", markdown(model))
            self.assertIn("token", markdown(model))

    def test_core_excluded_from_decisions_but_dependency_impact_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            context["extensions"].append(
                {
                    "name": "core_example",
                    "type": "module",
                    "path": "web/core/modules/example/example.info.yml",
                    "dependencies": ["token"],
                }
            )
            result = build(context, [], {"status": "blocked"}, {"packages": []}, root)
            self.assertEqual([row["name"] for row in result["extensions"]], ["token"])
            self.assertEqual(result["summary"]["total"], 1)
            self.assertEqual(
                result["extensions"][0]["impact"]["reverseDependencies"], ["core_example"]
            )
            with self.assertRaises(ValueError):
                validate_decisions(
                    {"extensions": [{"name": "system", "source": "core"}]},
                    [{"name": "system", "action": "remove"}],
                )

    def test_bulk_save_rejects_stale_report_and_source(self):
        from d11lib.two_gate import decide

        w = Mock()
        w.run.return_value = (
            Path("/unused"),
            {
                "action": "guided-audit",
                "status": "completed",
                "project": "example",
                "fingerprint": "old",
            },
        )
        w.project.return_value = (Path("/unused"), {})
        w.fingerprint.return_value = "new"
        with patch("d11lib.two_gate.compatibility_report", return_value={"digest": "current"}):
            with self.assertRaisesRegex(Problem, "evidence changed"):
                decide(w, "run", {"reportDigest": "stale", "decisions": []})
            with self.assertRaisesRegex(Problem, "inputs changed"):
                decide(w, "run", {"reportDigest": "current", "decisions": [{"bulkSafe": True}]})

    def test_safe_bulk_evidence_and_version_rules(self):
        checks = {name: {"status": "passed"} for name in ("upgrade_status", "drupal_rector")}
        row = {
            "status": "update_available",
            "releaseKind": "stable",
            "currentVersion": "2.0.4",
            "targetVersion": "2.0.5",
            "blockers": [],
        }
        self.assertTrue(safe_upgrade(row, checks)["safeUpgradeEligible"])
        for version in ("2.0.2", "2.0.4", "2.1.0-beta1", None, "dev-main"):
            self.assertFalse(
                safe_upgrade({**row, "targetVersion": version}, checks)["safeUpgradeEligible"]
            )
        self.assertFalse(safe_upgrade(row, {})["safeUpgradeEligible"])
        self.assertFalse(
            safe_upgrade({**row, "blockers": ["Missing checks"]}, checks)["safeUpgradeEligible"]
        )

    def test_bulk_validation_and_shared_package_conflicts(self):
        row = {
            "name": "a",
            "package": "drupal/a",
            "safeUpgradeEligible": True,
            "safeUpgradeVersion": "2.0.0",
            "releaseCandidates": [{"version": "2.0.0"}, {"version": "3.0.0"}],
        }
        report = {"extensions": [row, {**row, "name": "b"}]}
        choice = {
            "name": "a",
            "action": "compatible_release",
            "candidateVersion": "2.0.0",
            "bulkSafe": True,
        }
        self.assertEqual(validate_decisions(report, [choice])["a"]["candidateVersion"], "2.0.0")
        with self.assertRaisesRegex(ValueError, "eligibility"):
            validate_decisions({"extensions": [{**row, "safeUpgradeEligible": False}]}, [choice])
        with self.assertRaisesRegex(ValueError, "Conflicting versions"):
            validate_decisions(
                report,
                [
                    choice,
                    {"name": "b", "action": "compatible_release", "candidateVersion": "3.0.0"},
                ],
            )

    def test_null_dependencies_preserve_unknown_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            context["extensions"][0]["dependencies"] = None
            result = build(
                context,
                [{"id": "upgrade_status", "status": "tool_failure"}],
                {"status": "blocked"},
                {"packages": []},
                root,
            )
            self.assertEqual(result["extensions"][0]["status"], "unknown")
            self.assertEqual(result["extensions"][0]["impact"]["reverseDependencies"], [])
            self.assertTrue(result["extensions"][0]["blockers"])

    def context(self, root, version="1.0.0"):
        return {
            "roots": {"config": None},
            "composer": {"packages": [{"name": "drupal/token", "version": version}]},
            "extensions": [
                {
                    "name": "token",
                    "type": "module",
                    "path": str(root / "web/modules/contrib/token/token.info.yml"),
                    "package": "drupal/token",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10",
                    "dependencies": [],
                }
            ],
        }

    def test_scanner_findings_empty_and_malformed_are_distinct(self):
        record = lambda stdout, code=0: {
            "stdout": stdout,
            "stderr": "",
            "exitCode": code,
            "status": "passed",
        }
        self.assertEqual(_upgrade_record(record("[]"))["status"], "passed")
        issue = [
            {
                "type": "issue",
                "description": "Deprecated API",
                "location": {"path": "web/modules/contrib/token/token.module"},
            }
        ]
        finding = _upgrade_record(record(json.dumps(issue), 1))
        self.assertEqual(finding["status"], "findings")
        self.assertEqual(finding["issues"], issue)
        malformed = _upgrade_record(record("{bad"))
        self.assertEqual(malformed["failureCategory"], "malformed_output")
        rector = _rector_record(
            record(json.dumps({"file_diffs": [{"file": "web/modules/custom/a/a.module"}]}), 2)
        )
        self.assertEqual(rector["status"], "findings")
        self.assertEqual(len(rector["changes"]), 1)

    def test_disposable_scanner_install_does_not_change_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            site = root / "site"
            out = root / "out"
            site.mkdir()
            out.mkdir()
            (site / "composer.json").write_text(
                json.dumps(
                    {
                        "require": {"drupal/core-recommended": "^10.3"},
                        "require-dev": {
                            "drupal/core-dev": "^10.3",
                            "phpstan/phpstan": "^1.12",
                            "mglaman/drupal-check": "1.4",
                        },
                    }
                )
            )
            (site / "composer.lock").write_text(
                json.dumps(
                    {
                        "packages": [{"name": "drupal/core-recommended", "version": "10.4.8"}],
                        "packages-dev": [
                            {
                                "name": "drupal/core-dev",
                                "require": {"phpunit/phpunit": "^9.6", "phpstan/phpstan": "^1.12"},
                            }
                        ],
                    }
                )
            )
            database = root / "database.sql.gz"
            import gzip

            with gzip.open(database, "wb") as stream:
                stream.write(b"-- empty fixture")
            before = (site / "composer.json").read_text()

            def fake(argv, cwd, timeout=120, **kwargs):
                if "upgrade_status:analyze" in argv:
                    self.assertIn("--ignore-uninstalled", argv)
                if "update" in argv:
                    analysis_manifest = read(Path(cwd) / "site/composer.json")
                    self.assertNotIn("phpstan/phpstan", analysis_manifest["require-dev"])
                    self.assertNotIn("mglaman/drupal-check", analysis_manifest["require-dev"])
                    self.assertNotIn("drupal/core-dev", analysis_manifest["require-dev"])
                    self.assertEqual(analysis_manifest["require-dev"]["phpunit/phpunit"], "^9.6")
                    self.assertEqual(
                        analysis_manifest["require"]["drupal/core-recommended"], "10.4.8"
                    )
                    self.assertEqual(analysis_manifest["require"]["webflo/drupal-finder"], "^1.3.1")
                    self.assertTrue(
                        analysis_manifest["config"]["allow-plugins"]["composer/installers"]
                    )
                    self.assertNotIn("*", analysis_manifest["config"]["allow-plugins"])
                stdout = (
                    json.dumps({"upgrade_status": {"version": "4.3.8"}})
                    if "pm:list" in argv
                    else "[]"
                    if "upgrade_status:analyze" in argv
                    else json.dumps({"modules": {}})
                    if "php:eval" in argv
                    else ""
                )
                if "php:eval" in argv and "SELECT DATABASE()" in argv[argv.index("php:eval") + 1]:
                    stdout = json.dumps({"database": "analysis", "root": "/var/www"})
                return {
                    "argv": argv,
                    "exitCode": 0,
                    "stdout": stdout,
                    "stderr": "",
                    "status": "passed",
                    "elapsedSeconds": 0.01,
                }

            imported = {
                "exitCode": 0,
                "status": "passed",
                "stdout": "",
                "stderr": "",
                "elapsedSeconds": 0.01,
            }
            cfg = {"site": {"uri": "http://managed"}, "recovery": {"database": str(database)}}
            context = {"roots": {"composer": str(site), "custom": []}}
            with (
                patch("d11lib.audit_tools.command", side_effect=fake),
                patch("d11lib.audit_tools._import_database", return_value=imported),
            ):
                checks = run_audit_tools(cfg, context, out, object(), "site")
            self.assertEqual([x["status"] for x in checks], ["passed", "passed", "passed"])
            self.assertEqual((site / "composer.json").read_text(), before)
            self.assertFalse((out / "analysis").exists())
            self.assertTrue(read(out / "analysis-runtime.json")["candidateUnchanged"])

    def test_analysis_network_avoids_existing_subnets(self):
        import ipaddress

        from d11lib.audit_tools import _analysis_compose

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            def fake(argv, *args, **kwargs):
                return {
                    "exitCode": 0,
                    "stdout": "existing"
                    if "ls" in argv
                    else json.dumps([{"IPAM": {"Config": [{"Subnet": "10.240.0.0/17"}]}}]),
                }

            with patch("d11lib.audit_tools.command", side_effect=fake):
                _analysis_compose(root / "site", root / "runtime", "analysis-fixture")
            network = read(root / "runtime/compose.json")["networks"]["backend"]
            self.assertTrue(network["internal"])
            self.assertFalse(
                ipaddress.ip_network(network["ipam"]["config"][0]["subnet"]).overlaps(
                    ipaddress.ip_network("10.240.0.0/17")
                )
            )

    def test_upgrade_status_findings_exit_and_embedded_tool_failures(self):
        issue = {
            "type": "issue",
            "description": "Deprecated API",
            "location": {"path": "web/modules/custom/example/example.module"},
        }
        rec = {"exitCode": 3, "stdout": json.dumps([issue]), "stderr": ""}
        self.assertEqual(_upgrade_record(rec)["status"], "findings")
        failure = {**issue, "description": "PHPStan command failed: duplicate function"}
        rec["stdout"] = json.dumps([issue, failure])
        result = _upgrade_record(rec)
        self.assertEqual(result["status"], "findings")
        self.assertEqual(result["issues"], [issue])
        self.assertEqual(len(result["scannerFailures"]), 1)

    def test_analysis_copy_excludes_only_named_inactive_theme(self):
        from d11lib.audit_tools import _copy_analysis

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            for name in ("active", "inactive"):
                path = source / "web/themes/custom" / name
                path.mkdir(parents=True)
                (path / (name + ".theme")).write_text("<?php")
            _copy_analysis(source, root / "analysis", ["web/themes/custom/inactive"])
            self.assertTrue((root / "analysis/web/themes/custom/active/active.theme").exists())
            self.assertFalse((root / "analysis/web/themes/custom/inactive").exists())
            self.assertTrue((source / "web/themes/custom/inactive/inactive.theme").exists())

    def test_audit_tools_inventory_accepts_git_repository(self):
        from d11lib.audit_tools import inventory

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "COMMIT_EDITMSG").write_text("commit message")
            (root / "composer.json").write_text("{}")
            # Should not raise Problem('Remove credentials...: .git/COMMIT_EDITMSG')
            result = inventory(root)
            self.assertIsInstance(result, dict)
            self.assertIn("git_head", result)

    def test_module_identifier_and_patch_decision_survive_normalization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            context = self.context(root)
            issue = {
                "type": "issue",
                "description": "D11 API",
                "location": {"path": "web/modules/contrib/token/token.module"},
            }
            checks = [
                {
                    "id": "upgrade_status",
                    "status": "findings",
                    "issues": [issue],
                    "findingCount": 1,
                },
                {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
                {
                    "id": "module_impact",
                    "status": "passed",
                    "modules": {"token": {"populatedContent": False}},
                },
            ]
            solver = {"status": "blocked", "versions": {}}
            candidate = {
                "id": "token-mr-1-abc",
                "approvalEligible": True,
                "changedFiles": 1,
                "runtimeCodeChange": True,
            }
            patches = {
                "status": "candidates_found",
                "packages": [{"package": "drupal/token", "candidates": [candidate]}],
            }
            report = build(
                context,
                checks,
                solver,
                patches,
                out,
                {"token": {"action": "available_patch", "candidateId": candidate["id"]}},
            )
            row = report["extensions"][0]
            self.assertEqual(row["name"], "token")
            self.assertEqual(row["selectedAction"], "available_patch")
            self.assertEqual(row["blockers"], [])
            self.assertEqual(
                read(out / "compatibility-report.json")["extensions"][0]["name"], "token"
            )

    def test_release_prerelease_ai_and_removal_rules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            context = self.context(root)
            checks = [
                {"id": "upgrade_status", "status": "passed", "issues": []},
                {"id": "drupal_rector", "status": "passed", "changes": []},
                {
                    "id": "module_impact",
                    "status": "passed",
                    "modules": {"token": {"populatedContent": None}},
                },
            ]
            patches = {"status": "not_needed", "packages": []}
            pre = build(
                context,
                checks,
                {"status": "passed", "versions": {"drupal/token": "2.0.0-beta1"}},
                patches,
                out,
                {"token": {"action": "compatible_release", "acceptRisk": False}},
            )["extensions"][0]
            self.assertIn("Prerelease", pre["blockers"][0])
            accepted = build(
                context,
                checks,
                {"status": "passed", "versions": {"drupal/token": "2.0.0-beta1"}},
                patches,
                out,
                {"token": {"action": "compatible_release", "acceptRisk": True}},
            )["extensions"][0]
            self.assertEqual(accepted["blockers"], [])
            ai = build(
                context,
                checks,
                {"status": "blocked", "versions": {}},
                patches,
                out,
                {"token": {"action": "ai_manual_patch"}},
            )["extensions"][0]
            self.assertIn("AI patch", ai["blockers"][0])
            remove = build(
                context,
                checks,
                {"status": "blocked", "versions": {}},
                patches,
                out,
                {"token": {"action": "remove"}},
            )["extensions"][0]
            self.assertTrue(any("Populated-content" in x for x in remove["blockers"]))

    def test_mutually_exclusive_decision_validation(self):
        report = {"extensions": [{"name": "token"}]}
        value = validate_decisions(report, [{"name": "token", "action": "defer"}])
        self.assertEqual(value["token"]["action"], "defer")
        with self.assertRaises(ValueError):
            validate_decisions(
                report,
                [{"name": "token", "action": "remove"}, {"name": "token", "action": "defer"}],
            )

    def test_safe_decisions_are_preselected_and_saved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            context = self.context(root, version="1.0.0")
            checks = [
                {"id": "upgrade_status", "status": "passed", "issues": []},
                {"id": "drupal_rector", "status": "passed", "changes": []},
                {"id": "module_impact", "status": "passed", "modules": {}},
            ]
            report = build(
                context,
                checks,
                {"status": "passed", "versions": {"drupal/token": "2.0.0"}},
                {"status": "not_needed", "packages": []},
                out,
            )
            row = report["extensions"][0]
            self.assertEqual(row["selectedAction"], "compatible_release")
            self.assertTrue(row["autoSelected"])
            self.assertEqual(report["decisions"]["token"]["candidateVersion"], "2.0.0")
            self.assertEqual(report["summary"]["actions"]["compatible_release"], 1)

            context["composer"]["packages"][0]["version"] = "2.0.0"
            context["extensions"][0]["coreConstraint"] = "^10 || ^11"
            ready = build(
                context,
                checks,
                {"status": "passed", "versions": {"drupal/token": "2.0.0"}},
                {"status": "not_needed", "packages": []},
                out,
            )
            self.assertEqual(ready["extensions"][0]["selectedAction"], "keep")
            self.assertEqual(ready["summary"]["actions"]["keep"], 1)

    def test_solver_replaces_d10_constraints(self):
        source = {
            "require": {
                "drupal/core": "^10",
                "drupal/core-recommended": "^10.3",
                "drupal/token": "^1",
                "symfony/filesystem": "^6.4",
                "kporras07/composer-symlinks": "v1.2",
            },
            "require-dev": {
                "drupal/core-dev": "^10.3",
                "drush/drush": "^12",
                "mglaman/drupal-check": "1.4",
            },
            "config": {},
        }
        result = prepare_manifest(source)
        self.assertNotIn("drupal/core", result["require"])
        self.assertEqual(result["require"]["drupal/core-recommended"], "^11")
        self.assertEqual(result["require"]["symfony/filesystem"], "^6.4 || ^7")
        self.assertEqual(result["require"]["kporras07/composer-symlinks"], "^1.2")
        self.assertEqual(result["require-dev"]["mglaman/drupal-check"], "^1.5")
        self.assertEqual(result["require-dev"]["drupal/core-dev"], "^11")
        self.assertEqual(result["require-dev"]["drush/drush"], "^12.5 || ^13")
        self.assertEqual(source["require"]["drupal/core"], "^10")

    def test_solver_resolve_uses_no_check_lock(self):
        from d11lib.solver import resolve

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            root.mkdir()
            out = Path(td) / "out"
            out.mkdir()
            write(root / "composer.json", {"require": {"drupal/core-recommended": "^10"}})
            commands = []

            def fake_command(argv, cwd, timeout, **kwargs):
                commands.append(argv)
                if "update" in argv:
                    write(
                        cwd / "composer.lock",
                        {"packages": [{"name": "drupal/core-recommended", "version": "11.0.0"}]},
                    )
                return {"exitCode": 0, "stdout": "", "stderr": ""}

            with patch("d11lib.solver.command", side_effect=fake_command):
                res = resolve(root, out)
            self.assertTrue(any("validate" in c and "--no-check-lock" in c for c in commands))
            self.assertTrue(
                any("update" in c and "--ignore-platform-req=ext-*" in c for c in commands)
            )
            self.assertEqual(res["status"], "passed")

    def test_resolve_with_manifest_override_positional(self):
        from d11.solver import resolve
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            out = Path(td) / "out"
            out.mkdir()
            write(root / "composer.json", {"require": {"drupal/core-recommended": "^10"}})
            manifest_override = {
                "name": "promet/provus-drupal",
                "repositories": [{"type": "composer", "url": "https://packages.drupal.org/8"}],
                "require": {
                    "drupal/core-recommended": "^10",
                    "drupal/better_exposed_filters": "7.1.3",
                },
            }
            commands = []

            def fake_command(argv, cwd, timeout, **kwargs):
                commands.append(argv)
                if "update" in argv:
                    write(
                        cwd / "composer.lock",
                        {"packages": [{"name": "drupal/core-recommended", "version": "11.1.0"}]},
                    )
                return {"exitCode": 0, "stdout": "", "stderr": ""}

            with patch("d11lib.solver.command", side_effect=fake_command):
                # Passing manifest as 3rd positional argument (as two_gate did)
                res = resolve(root, out, manifest_override)
            self.assertEqual(res["status"], "passed")
            self.assertEqual(res["exactCoreVersion"], "11.1.0")
            solver_composer = read(out / "solver/composer.json")
            self.assertIn("drupal/better_exposed_filters", solver_composer["require"])

    def test_drupal_org_release_candidates_prefer_stable(self):
        xml = b"""<project><releases>
          <release><version>2.0.0-beta1</version><status>published</status><core_compatibility>^10.3 || ^11</core_compatibility></release>
          <release><version>8.x-1.9</version><status>published</status><core_compatibility>^9 || ^10 || ^11</core_compatibility></release>
          <release><version>1.8</version><status>published</status><core_compatibility>^10</core_compatibility></release>
        </releases></project>"""

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return xml

        values = _release_candidates("token", lambda *args, **kwargs: Response())
        self.assertEqual(values[0]["version"], "1.9")
        self.assertEqual(values[0]["stability"], "stable")
        self.assertEqual(values[1]["stability"], "prerelease")

    def test_controlled_removal_requires_digest_evidence(self):
        base = {
            "id": "remove_token",
            "stage": "C-remediation",
            "cwd": ".",
            "mutates": True,
            "description": "remove",
            "argv": ["fin", "drush", "pm:uninstall", "token", "--yes"],
            "preconditions": [{"argv": ["fin", "drush", "status"]}],
            "postconditions": [{"argv": ["fin", "drush", "status"]}],
            "destructive": True,
        }
        with self.assertRaises(Problem):
            validate_steps([base])
        validate_steps([{**base, "reviewedRemovalDigest": "abc"}])

    def test_provider_inventory_and_three_json_envelopes(self):
        def discover(argv, cwd, timeout, **kwargs):
            if "--version" in argv:
                return {"exitCode": 0, "stdout": "1.2.3", "stderr": ""}
            return {
                "exitCode": 0,
                "stdout": " ".join(
                    (
                        " --output-schema",
                        " --sandbox",
                        " --output-format",
                        " --permission-mode",
                        " --strict-mcp-config",
                        " --approval-mode",
                    )
                ),
                "stderr": "",
            }

        with patch("d11lib.ai_providers.shutil.which", side_effect=lambda name: "/bin/" + name):
            found = inventory(discover)
        self.assertEqual(
            [x["id"] for x in found], ["gemini", "claude", "codex", "openai", "ollama"]
        )
        selected = choose(
            None,
            [
                {"id": "gemini", "available": True, "safeInterface": True},
                {"id": "claude", "available": True, "safeInterface": True},
                {"id": "codex", "available": False, "safeInterface": False},
            ],
        )
        self.assertEqual(selected["id"], "gemini")
        schema = {"type": "object"}
        payload = {
            "summary": "fix",
            "findings": ["x"],
            "changes": [],
            "steps": [],
            "limitations": ["review"],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            def fake(argv, cwd, timeout, **kwargs):
                if "-o" in argv:
                    Path(argv[argv.index("-o") + 1]).write_text(json.dumps(payload))
                    stdout = "{}"
                elif argv[0] == "tool" and "-p" in argv:
                    stdout = json.dumps(
                        {
                            "result": json.dumps(payload),
                            "model": "claude-test",
                            "usage": {"input_tokens": 1},
                        }
                    )
                else:
                    stdout = json.dumps(
                        {"response": payload, "model": "gemini-test", "stats": {"tokens": 1}}
                    )
                return {"exitCode": 0, "stdout": stdout, "stderr": "", "elapsedSeconds": 0.1}

            for provider in ("codex", "claude", "gemini"):
                with patch(
                    "d11lib.ai_providers.choose",
                    return_value={"id": provider, "executable": "tool", "version": "1"},
                ):
                    result, record = propose(
                        provider,
                        "prompt",
                        root / "source",
                        root / provider,
                        schema,
                        run_command=fake,
                    )
                self.assertEqual(result["summary"], "fix")
                self.assertEqual(record["provider"], provider)

            # Test multi-model REST API proposal mode
            def fake_api(provider_id, prompt):
                return json.dumps(payload), f"{provider_id}-model", {"tokens": 10}

            for api_provider in ("gemini", "openai", "claude", "ollama"):
                with patch(
                    "d11lib.ai_providers.choose",
                    return_value={
                        "id": api_provider,
                        "mode": "api",
                        "model": f"{api_provider}-api",
                        "version": "1",
                        "available": True,
                        "safeInterface": True,
                    },
                ):
                    result, record = propose(
                        api_provider,
                        "prompt",
                        root / "source",
                        root / f"api_{api_provider}",
                        schema,
                        api_caller=fake_api,
                    )
                self.assertEqual(result["summary"], "fix")
                self.assertEqual(record["provider"], api_provider)

    def test_malformed_provider_output_is_preserved_as_failure(self):
        with (
            tempfile.TemporaryDirectory() as td,
            patch(
                "d11lib.ai_providers.choose",
                return_value={"id": "claude", "executable": "tool", "version": "1"},
            ),
        ):
            root = Path(td)

            def fake(*args, **kwargs):
                return {"exitCode": 0, "stdout": json.dumps({"result": "not-json"}), "stderr": ""}

            with self.assertRaises(Problem):
                propose(
                    "claude",
                    "prompt",
                    root / "source",
                    root / "out",
                    {"type": "object"},
                    run_command=fake,
                )
            self.assertEqual(
                read(root / "out/ai-provider.json")["failureCategory"], "malformed_output"
            )

    def test_provider_authentication_and_rate_limit_categories(self):
        for message, category in [
            ("Login required", "authentication"),
            ("HTTP 429 quota exceeded", "rate_limit"),
        ]:
            with (
                self.subTest(category=category),
                tempfile.TemporaryDirectory() as td,
                patch(
                    "d11lib.ai_providers.choose",
                    return_value={"id": "claude", "executable": "tool", "version": "1"},
                ),
            ):
                root = Path(td)

                def fake(*args, **kwargs):
                    return {"exitCode": 1, "stdout": "", "stderr": message}

                with self.assertRaises(Problem):
                    propose(
                        "claude",
                        "prompt",
                        root / "source",
                        root / "out",
                        {"type": "object"},
                        run_command=fake,
                    )
                self.assertEqual(read(root / "out/ai-provider.json")["failureCategory"], category)

    def test_browser_markdown_uses_same_compatibility_model(self):
        row = {
            "name": "token",
            "type": "module",
            "currentVersion": "1.0",
            "targetVersion": "2.0",
            "status": "update_available",
            "selectedAction": "compatible_release",
            "risk": "medium",
        }
        model = {
            "run": {
                "id": "run",
                "project": "site",
                "status": "completed",
                "action": "guided-audit",
            },
            "compatibility": {
                "summary": {"total": 1, "update_available": 1, "unresolved": 0},
                "extensions": [row],
            },
            "assessment": {},
        }
        compatibility = next(s for s in sections(model) if s["id"] == "compatibility")
        self.assertEqual(compatibility["compatibility"], [row])
        self.assertIn("| token | module | 1.0 | 2.0 |", markdown(model))

    def test_local_compatibility_and_provider_apis(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            out = home / "runs/audit"
            write(
                out / "state.json",
                {"id": "audit", "project": "site", "action": "guided-audit", "status": "completed"},
            )
            write(
                out / "compatibility-report.json",
                {"schemaVersion": "1.0", "extensions": [], "summary": {"total": 0}, "digest": "x"},
            )
            client = TestClient(create_app(home), base_url="http://127.0.0.1:8765")
            self.assertEqual(client.get("/api/runs/audit/compatibility").json()["digest"], "x")
            with patch(
                "d11lib.ai_providers.inventory",
                return_value=[{"id": "codex", "available": False, "safeInterface": False}],
            ):
                self.assertEqual(client.get("/api/providers").json()[0]["id"], "codex")

    def test_decision_change_regenerates_digest_and_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            w = Workflow(home)
            project = home / "projects/site"
            site = project / "site"
            site.mkdir(parents=True)
            (site / "composer.json").write_text(
                json.dumps({"require": {"drupal/core-recommended": "^10.3", "drupal/token": "^1"}})
            )
            (site / "composer.lock").write_text(json.dumps({"packages": []}))
            cfg = {
                "schemaVersion": "1.0",
                "repository": "site",
                "environment": {"id": "local", "kind": "local", "authorized": True},
                "site": {"uri": "http://127.0.0.1:9999"},
                "roots": {"composer": ".", "drupal": "web"},
                "runtime": {"wrapper": "fin"},
                "identity": {
                    "argv": ["fin", "exec", "printenv", "MYSQL_DATABASE"],
                    "expected": "upgrade_test",
                },
                "recovery": {
                    "code": "c",
                    "database": "d",
                    "files": "f",
                    "owner": "A",
                    "procedure": "restore",
                    "verifiedAt": "2026-09-08T00:00:00Z",
                },
                "steps": [],
                "checks": [],
            }
            write(project / "project.json", cfg)
            write(project / "registration.json", {"id": "site", "fixture": False})
            out = home / "runs/audit"
            context = {
                "roots": {"composer": str(site), "custom": [], "config": None},
                "composer": {"packages": [{"name": "drupal/token", "version": "1.0"}]},
                "extensions": [
                    {
                        "name": "token",
                        "type": "module",
                        "path": str(site / "web/modules/contrib/token/token.info.yml"),
                        "package": "drupal/token",
                        "installed": False,
                        "exported": False,
                        "coreConstraint": "^11",
                        "dependencies": [],
                    }
                ],
                "runtime": {
                    "commands": {
                        "status": {"data": {"drupal-version": "10.4.0", "php-version": "8.3.0"}}
                    }
                },
                "deployment": {"status": "unknown", "observations": []},
                "removedCoreDependencies": [],
            }
            context["extensions"][0]["installed"] = True
            checks = [
                {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
                {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
                {"id": "module_impact", "status": "passed", "modules": {}},
            ]
            solver = {
                "status": "passed",
                "exactCoreVersion": "11.2.0",
                "versions": {"drupal/token": "2.0.0"},
                "changes": [
                    {
                        "path": "composer.json",
                        "beforeSha256": file_hash(site / "composer.json"),
                        "after": json.dumps(
                            {"require": {"drupal/core-recommended": "^11", "drupal/token": "^1"}}
                        ),
                        "finding": "composer_resolution",
                        "rationale": "solver",
                        "verificationCheckIds": ["composer_validate"],
                    }
                ],
            }
            patches = {"status": "not_needed", "packages": []}
            initial = build(context, checks, solver, patches, out)
            write(
                out / "state.json",
                {
                    "id": "audit",
                    "project": "site",
                    "action": "guided-audit",
                    "status": "completed",
                    "fingerprint": "f",
                },
            )
            write(out / "result/context.json", context)
            write(out / "result/result.json", {"checks": []})
            write(out / "audit-tools.json", {"checks": checks})
            write(out / "patch-candidates.json", patches)
            write(out / "composer-resolution.json", solver)
            write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
            write(
                out / "gate.json",
                {
                    "baseline": {"passed": True, "selected": 1, "routeDigest": "routes"},
                    "risk": {"recommendation": "No-Go"},
                    "approvalDigest": "old",
                },
            )
            write(out / "gate-approval.json", {"old": True})
            plan_result = {
                "planId": "new-plan",
                "environment": cfg["environment"],
                "site": cfg["site"],
                "source": "10.4.0",
                "target": "11.2.0",
                "steps": [],
                "blockers": [],
                "backup": cfg["recovery"],
                "inputs": {},
            }
            with (
                patch("d11lib.solver.resolve", return_value=solver) as resolve_mock,
                patch("d11lib.execution.plan", return_value=plan_result),
            ):
                result = w.compatibility_decisions(
                    "audit", {"decisions": [{"name": "token", "action": "compatible_release"}]}
                )
            self.assertNotEqual(result["compatibility"]["digest"], initial["digest"])
            self.assertFalse((out / "gate-approval.json").exists())
            self.assertEqual(result["compatibility"]["summary"]["unresolved"], 0)
            self.assertEqual(resolve_mock.call_args.args[2]["require"]["drupal/token"], "2.0.0")

    def test_tune_analysis_tools_parallel(self):
        from d11lib.audit_tools import _tune_analysis_tools

        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            module_dir = site / "web/modules/contrib/upgrade_status"
            module_dir.mkdir(parents=True)
            neon = module_dir / "deprecation_testing_template.neon"
            neon.write_text(
                "parameters:\n\tparallel:\n\t\tmaximumNumberOfProcesses: 0\n\tcustomRulesetUsed: true\n",
                encoding="utf-8",
            )
            _tune_analysis_tools(site)
            tuned = neon.read_text(encoding="utf-8")
            self.assertNotIn("maximumNumberOfProcesses: 0", tuned)
            self.assertIn("maximumNumberOfProcesses:", tuned)
            self.assertIn("processTimeout: 300.0", tuned)

    def test_audit_tools_fast_and_full_mode_flags(self):
        from d11lib.audit_tools import _installed_run

        commands = []

        def fake_command(argv, cwd, timeout=120, **kwargs):
            commands.append(argv)
            return {
                "argv": argv,
                "exitCode": 0,
                "stdout": "[]",
                "stderr": "",
                "status": "passed",
                "elapsedSeconds": 0.01,
            }

        cfg = {"site": {"uri": "http://test.local"}, "runtime": {"wrapper": "fin"}}
        context = {
            "roots": {"composer": "/tmp"},
            "runtime": {
                "wrapper": "fin",
                "commands": {
                    "capabilities": {"stdout": json.dumps({"commands": [{"name": "upgrade_status:analyze"}]})},
                    "drush": ["fin", "drush"],
                },
            },
        }

        with patch("d11lib.audit_tools.command", side_effect=fake_command):
            # Fast mode (default)
            _installed_run(cfg, context, fast=True)
            self.assertTrue(any("--ignore-contrib" in cmd for cmd in commands))
            self.assertTrue(any("--skip-existing" in cmd for cmd in commands))
            self.assertTrue(any("--phpstan-memory-limit=2048M" in cmd for cmd in commands))

            commands.clear()
            # Full mode
            _installed_run(cfg, context, fast=False)
            self.assertFalse(any("--ignore-contrib" in cmd for cmd in commands))
            self.assertTrue(any("--skip-existing" in cmd for cmd in commands))
            self.assertTrue(any("--phpstan-memory-limit=2048M" in cmd for cmd in commands))


if __name__ == "__main__":
    unittest.main()
