import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.auto_decide import auto_decide as auto_decide_run
from d11.common import write, read
from d11.compatibility import build, validate_decisions
from d11.two_gate import decide


class AutoDecideAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "composer.json").write_text(json.dumps({"require": {"drupal/core-recommended": "^10.3"}}))
        (self.root / "composer.lock").write_text(json.dumps({"packages": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_extension_without_upstream_release_auto_decides_keep(self):
        """Extensions like better_normalizers (custom fork, clean AST, ^10 || ^11) must auto-decide to keep."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupaloverride/better_normalizers", "version": "dev-master"}]},
            "extensions": [
                {
                    "name": "better_normalizers",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/better_normalizers/better_normalizers.info.yml"),
                    "package": "drupaloverride/better_normalizers",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10 || ^11",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        ext = report["extensions"][0]
        self.assertEqual(ext["name"], "better_normalizers")
        self.assertEqual(ext["status"], "ready")
        self.assertEqual(ext["recommendedAction"], "keep")
        self.assertEqual(ext["blockers"], [])

        # Write run files for auto_decide
        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})
        (self.root / "composer.json").write_text(json.dumps({"require": {"drupal/core-recommended": "^10.3"}}))
        (self.root / "composer.lock").write_text(json.dumps({"packages": []}))

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            result = auto_decide_run(w, "run-1", auto_remediate=False)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["breakdown"].get("keep"), 1)
        self.assertEqual(result["breakdown"].get("defer", 0), 0)

        decisions = read(out / "compatibility-decisions.json")
        self.assertEqual(decisions["better_normalizers"]["action"], "keep")
        self.assertEqual(decisions["better_normalizers"]["origin"], "automatic")

    def test_clean_extension_with_matching_current_and_target_auto_decides_keep(self):
        """Extensions like better_exposed_filters (7.1.3 -> 7.1.3, clean AST) must auto-decide to keep."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupal/better_exposed_filters", "version": "7.1.3"}]},
            "extensions": [
                {
                    "name": "better_exposed_filters",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/better_exposed_filters/better_exposed_filters.info.yml"),
                    "package": "drupal/better_exposed_filters",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10.3 || ^11",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {"drupal/better_exposed_filters": "7.1.3"}}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        ext = report["extensions"][0]
        self.assertEqual(ext["name"], "better_exposed_filters")
        self.assertEqual(ext["status"], "ready")
        self.assertEqual(ext["currentVersion"], "7.1.3")
        self.assertEqual(ext["targetVersion"], "7.1.3")
        self.assertEqual(ext["recommendedAction"], "keep")
        self.assertEqual(ext["blockers"], [])

    def test_prior_clean_decisions_do_not_get_tainted_with_requires_revalidation(self):
        """When an audit inherits prior decisions, clean decisions must not get requiresRevalidation."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupal/token", "version": "2.0.0"}]},
            "extensions": [
                {
                    "name": "token",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/token/token.info.yml"),
                    "package": "drupal/token",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^11",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {"drupal/token": "2.0.0"}}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run"
        out.mkdir(parents=True)

        prior_decisions = {
            "token": {
                "action": "keep",
                "candidateId": None,
                "candidateVersion": "2.0.0",
                "acceptRisk": False,
                "origin": "operator",
            }
        }
        report = build(context, checks, solver, patches, out, decisions=prior_decisions)
        ext = report["extensions"][0]

        self.assertFalse(any("Retained operator choice" in b for b in ext["blockers"]))
        self.assertEqual(ext["blockers"], [])
        self.assertNotEqual(ext["risk"], "critical")



    def test_developer_operator_override_is_preserved(self):
        """If a developer manually chose an action (origin: operator), auto_decide respects the developer decision."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupal/some_module", "version": "1.0.0"}]},
            "extensions": [
                {
                    "name": "some_module",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/some_module/some_module.info.yml"),
                    "package": "drupal/some_module",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10 || ^11",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run_override"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})
        
        # Developer explicitly overrode some_module to remove in compatibility-decisions.json
        write(out / "compatibility-decisions.json", {
            "some_module": {
                "action": "remove",
                "candidateId": None,
                "candidateVersion": None,
                "acceptRisk": False,
                "note": "Developer chosen removal",
                "origin": "operator"
            }
        })

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            result = auto_decide_run(w, "run-1", auto_remediate=False)

        decisions = read(out / "compatibility-decisions.json")
        # Developer override preserved!
        self.assertEqual(decisions["some_module"]["action"], "remove")
        self.assertEqual(decisions["some_module"]["origin"], "operator")
        self.assertEqual(decisions["some_module"]["note"], "Developer chosen removal")

    def test_obsolete_performance_modules_auto_decide_remove(self):
        """Obsolete performance modules like advagg must auto-decide to remove."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupal/advagg", "version": "4.0.0"}]},
            "extensions": [
                {
                    "name": "advagg",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/advagg/advagg.info.yml"),
                    "package": "drupal/advagg",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^9 || ^10",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run_advagg"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            result = auto_decide_run(w, "run-1", auto_remediate=False)

        decisions = read(out / "compatibility-decisions.json")
        self.assertEqual(decisions["advagg"]["action"], "remove")
        self.assertEqual(decisions["advagg"]["origin"], "automatic")

    def test_stale_remediation_on_clean_extension_normalizes_to_keep(self):
        """Clean extension with stale manual_remediation or same-version compatible_release normalizes to keep."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": []},
            "project": "proj",
            "extensions": [
                {
                    "name": "better_normalizers",
                    "type": "module",
                    "source": "contrib",
                    "package": "drupal/better_normalizers",
                    "currentVersion": "dev-master",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10 || ^11",
                },
                {
                    "name": "better_exposed_filters",
                    "type": "module",
                    "source": "contrib",
                    "package": "drupal/better_exposed_filters",
                    "currentVersion": "7.1.3",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10 || ^11",
                },
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run_stale_test"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        # Pre-seed stale operator decisions
        stale_decisions = {
            "better_normalizers": {
                "action": "manual_remediation",
                "origin": "operator",
                "candidateVersion": None,
                "note": "Automatically generated validated remediation proposal",
            },
            "better_exposed_filters": {
                "action": "compatible_release",
                "origin": "operator",
                "candidateVersion": "7.1.3",
                "note": "Prior choice",
            },
        }
        write(out / "compatibility-decisions.json", stale_decisions)
        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            result = auto_decide_run(w, "run-1", auto_remediate=False)

        decisions = read(out / "compatibility-decisions.json")
        self.assertEqual(decisions["better_normalizers"]["action"], "keep")
        self.assertEqual(decisions["better_normalizers"]["candidateVersion"], "dev-master")
        self.assertEqual(decisions["better_normalizers"]["origin"], "automatic")

        self.assertEqual(decisions["better_exposed_filters"]["action"], "keep")
        self.assertEqual(decisions["better_exposed_filters"]["candidateVersion"], "7.1.3")

    def test_custom_module_with_findings_does_not_decide_keep(self):
        """Custom modules with code findings must never be assigned 'keep' (enforcing Keep Invariant)."""
        context = {
            "roots": {"composer": str(self.root), "custom": ["web/modules/custom"], "config": None},
            "composer": {"packages": []},
            "extensions": [
                {
                    "name": "my_custom_module",
                    "type": "module",
                    "path": str(self.root / "web/modules/custom/my_custom_module/my_custom_module.info.yml"),
                    "package": None,
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {
                "id": "upgrade_status",
                "status": "findings",
                "issues": [
                    {
                        "location": {"path": "modules/custom/my_custom_module/src/Plugin.php"},
                        "description": "Call to deprecated method",
                    }
                ],
                "findingCount": 1,
            },
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run_custom_test"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            auto_decide_run(w, "run-custom", auto_remediate=False)

        decisions = read(out / "compatibility-decisions.json")
        self.assertNotEqual(
            decisions["my_custom_module"]["action"],
            "keep",
            "Custom module with findings must NOT be marked as keep",
        )
        self.assertEqual(decisions["my_custom_module"]["action"], "manual_remediation")

    def test_removed_core_module_bridged_to_contrib(self):
        """Active removed core module (e.g. action) must be transitioned to contrib package."""
        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": []},
            "extensions": [
                {
                    "name": "action",
                    "type": "module",
                    "path": str(self.root / "core/modules/action/action.info.yml"),
                    "package": None,
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^10",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [{"name": "action", "active": True, "exported": True}],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {"status": "not_needed", "packages": []}

        out = self.root / "run_core_test"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        ext = report["extensions"][0]
        self.assertEqual(ext["name"], "action")
        self.assertEqual(ext["package"], "drupal/action")
        self.assertEqual(ext["recommendedAction"], "compatible_release")

        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": checks})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", patches)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})

        root = self.root
        class MockWorkflow:
            def fingerprint(self, p, cfg): return "fp"
            def event(self, *args, **kwargs): pass
            def report(self, *args, **kwargs): pass
            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj"}
            def project(self, pid):
                return root, {
                    "environment": {"id": "local", "kind": "local"},
                    "site": {"uri": "http://127.0.0.1"},
                    "roots": {"composer": ".", "drupal": "web"},
                    "runtime": {"wrapper": "fin"},
                    "recovery": {"procedure": "restore"},
                    "_config": str(root / "project.json"),
                    "_root": str(root),
                }

        w = MockWorkflow()
        from unittest.mock import patch
        with patch("d11.solver.resolve", return_value=solver):
            auto_decide_run(w, "run-core", auto_remediate=False)

        decisions = read(out / "compatibility-decisions.json")
        self.assertEqual(decisions["action"]["action"], "compatible_release")
        self.assertEqual(decisions["action"]["candidateVersion"], "^1.0")

    def test_release_lookup_failure_defers_with_blocker(self):
        """When release lookup fails due to network error, auto_decide must defer with a blocker, never remove."""
        from d11.auto_decide import DECISION_RULES

        ext = {
            "name": "network_fail_mod",
            "source": "contrib",
            "status": "ready",
            "installed": True,
            "exported": True,
            "releaseLookup": "failed",
            "releaseCandidates": [],
            "targetVersion": None,
            "patches": [],
        }
        ctx = {
            "existing": None,
            "is_clean": False,
            "manual_proposal": Path("/nonexistent"),
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "defer")
        self.assertIn("Release lookup failed", decision["note"])

    def test_known_replacement_rule(self):
        """Extensions with known community replacements (e.g. swiftmailer) should transition via rule_known_replacement."""
        from d11.auto_decide import DECISION_RULES

        ext = {
            "name": "swiftmailer",
            "package": "drupal/swiftmailer",
            "source": "contrib",
            "status": "incompatible",
            "installed": True,
            "exported": True,
            "targetVersion": None,
            "patches": [],
        }
        ctx = {
            "existing": None,
            "is_clean": False,
            "manual_proposal": Path("/nonexistent"),
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {"drupal/swiftmailer": "drupal/symfony_mailer"},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "compatible_release")
        self.assertIn("drupal/symfony_mailer", decision["note"])


class ProvusEcosystemProtectionTests(unittest.TestCase):
    """Verify that Provus ecosystem modules are never auto-assigned action:'remove'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Provus project composer.json
        (self.root / "composer.json").write_text(
            json.dumps({
                "name": "promet/provus-drupal",
                "require": {
                    "drupal/core-recommended": "^10.3",
                    "drupal/blazy": "4.0.x-dev",
                    "drupal/layout_builder_block_clone": "^1.0",
                    "drupal/lb_copy_section": "^1.0",
                    "drupal/webform_spam_words": "^1.0",
                    "drupal/layout_builder_reorder": "^1.0",
                }
            })
        )
        (self.root / "composer.lock").write_text(json.dumps({"packages": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def _make_uninstalled_ext(self, name, target_version="1.0.1"):
        return {
            "name": name,
            "type": "module",
            "source": "contrib",
            "package": f"drupal/{name}",
            "installed": False,
            "exported": False,
            "status": "update_available",
            "currentVersion": "1.0.0",
            "targetVersion": target_version,
            "releaseCandidates": [{"version": target_version, "stability": "stable"}],
            "dependencies": [],
        }

    def _build_report_and_run_rules(self, ext_name, is_provus=True):
        from d11.auto_decide import DECISION_RULES
        from d11.compatibility import is_provus_project, PROVUS_ECOSYSTEM_MODULES

        ext = self._make_uninstalled_ext(ext_name)
        is_clean = (
            ext.get("status") == "ready"
            and not ext.get("upgradeStatus", {})
            and not ext.get("rector", {})
        )
        ctx = {
            "existing": None,
            "is_clean": is_clean,
            "is_provus": is_provus,
            "manual_proposal": self.root / "no-proposal.json",
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break
        return decision

    def test_provus_detection_by_composer_name(self):
        """is_provus_project returns True for promet/provus-drupal composer.json."""
        from d11.compatibility import is_provus_project
        result = is_provus_project(site_root=self.root, context={})
        self.assertTrue(result, "Expected Provus project detection via composer.json name")

    def test_non_provus_not_detected(self):
        """is_provus_project returns False for a generic Drupal project."""
        from d11.compatibility import is_provus_project
        (self.root / "composer.json").write_text(
            json.dumps({"name": "my-org/my-drupal-site", "require": {"drupal/core-recommended": "^10.3"}})
        )
        result = is_provus_project(site_root=self.root, context={})
        self.assertFalse(result, "Generic project should not be detected as Provus")

    def test_provus_ecosystem_module_not_removed_when_uninstalled(self):
        """Provus ecosystem modules must never get action:'remove' even when uninstalled."""
        for mod in ("layout_builder_block_clone", "lb_copy_section", "webform_spam_words",
                    "layout_builder_reorder", "blazy", "slick", "slick_ui"):
            with self.subTest(module=mod):
                decision = self._build_report_and_run_rules(mod, is_provus=True)
                self.assertIsNotNone(decision, f"No decision produced for {mod}")
                self.assertNotEqual(
                    decision["action"], "remove",
                    f"Provus module '{mod}' must not be auto-assigned 'remove', got: {decision}"
                )
                self.assertIn(
                    decision["action"], ("keep", "compatible_release", "defer"),
                    f"Provus module '{mod}' should be keep/compatible_release/defer, got: {decision['action']}"
                )

    def test_non_provus_uninstalled_module_still_removed(self):
        """On a non-Provus project, uninstalled modules without releases still get 'remove'."""
        ext = {
            "name": "some_random_module",
            "type": "module",
            "source": "contrib",
            "package": "drupal/some_random_module",
            "installed": False,
            "exported": False,
            "status": "not_installed",
            "currentVersion": "1.0.0",
            "targetVersion": None,
            "releaseCandidates": [],
            "dependencies": [],
        }
        from d11.auto_decide import DECISION_RULES
        ctx = {
            "existing": None,
            "is_clean": False,
            "is_provus": False,
            "manual_proposal": self.root / "no-proposal.json",
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "remove",
                         "Non-Provus uninstalled module without release should be 'remove'")

    def test_operator_override_still_takes_priority(self):
        """An operator 'remove' override for a Provus module must be respected."""
        from d11.auto_decide import DECISION_RULES
        ext = self._make_uninstalled_ext("blazy")
        is_clean = False
        ctx = {
            "existing": {"origin": "operator", "action": "remove"},
            "is_clean": is_clean,
            "is_provus": True,
            "manual_proposal": self.root / "no-proposal.json",
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break
        # rule_operator_override fires first and must win
        self.assertEqual(decision["action"], "remove",
                         "Operator override 'remove' must take priority over Provus protection")

    def test_config_split_module_not_removed_when_uninstalled(self):
        """Modules in config split (e.g. 'live') must never get action:'remove' when uninstalled."""
        from d11.auto_decide import DECISION_RULES

        # 1. Compatible release candidate available
        ext_with_candidate = {
            "name": "acquia_purge",
            "type": "module",
            "source": "contrib",
            "package": "drupal/acquia_purge",
            "installed": False,
            "exported": True,
            "configSplits": ["live"],
            "inConfigSplit": True,
            "status": "update_available",
            "currentVersion": "1.0.0",
            "targetVersion": "^2.0.0",
            "releaseCandidates": [{"version": "^2.0.0", "stability": "stable"}],
            "dependencies": [],
        }
        ctx = {
            "existing": None,
            "is_clean": False,
            "is_provus": False,
            "manual_proposal": self.root / "no-proposal.json",
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext_with_candidate, ctx)
            if decision is not None:
                break
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "compatible_release")
        self.assertIn("live", decision["note"])

        # 2. Already clean module
        ext_clean = dict(ext_with_candidate)
        ctx_clean = dict(ctx, is_clean=True)
        decision_clean = None
        for rule in DECISION_RULES:
            decision_clean = rule(ext_clean, ctx_clean)
            if decision_clean is not None:
                break
        self.assertIsNotNone(decision_clean)
        self.assertEqual(decision_clean["action"], "keep")
        self.assertIn("live", decision_clean["note"])

        # 3. No release candidate found -> defer (never remove)
        ext_no_release = dict(ext_with_candidate, targetVersion=None, releaseCandidates=[])
        decision_defer = None
        for rule in DECISION_RULES:
            decision_defer = rule(ext_no_release, ctx)
            if decision_defer is not None:
                break
        self.assertIsNotNone(decision_defer)
        self.assertEqual(decision_defer["action"], "defer")
        self.assertNotEqual(decision_defer["action"], "remove")

    def test_config_split_discovery(self):
        """discover_config_splits finds config_split definitions and modules."""
        from d11.discovery import discover_config_splits
        config_dir = self.root / "config" / "sync"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config_split.config_split.live.yml").write_text(
            "id: live\nlabel: Live\nstatus: false\nmodule:\n  acquia_purge: 0\n  shield: 0\n"
        )
        splits_info = discover_config_splits(config_dir, self.root)
        self.assertIn("live", splits_info["splits"])
        self.assertEqual(sorted(splits_info["splits"]["live"]["modules"]), ["acquia_purge", "shield"])
        self.assertIn("acquia_purge", splits_info["extensions"])
        self.assertEqual(splits_info["extensions"]["acquia_purge"], ["live"])

    def test_version_tuple_parsing(self):
        """semver_tuple handles prereleases, core prefixes, constraints; version_tuple stays strict."""
        from d11.compatibility import semver_tuple, version_tuple

        # semver_tuple handles prereleases, core prefixes, constraints, and plain semver.
        self.assertEqual(semver_tuple("3.0.0-alpha5"), (3, 0, 0))
        self.assertEqual(semver_tuple("^3.0@alpha"), (3, 0, 0))
        self.assertEqual(semver_tuple("8.x-1.10"), (1, 10, 0))
        self.assertEqual(semver_tuple("^1.10"), (1, 10, 0))
        self.assertEqual(semver_tuple("1.10.0"), (1, 10, 0))
        self.assertEqual(semver_tuple("v2.4.1"), (2, 4, 1))
        self.assertIsNone(semver_tuple("dev-main"))
        self.assertIsNone(semver_tuple(""))

        # version_tuple strictly handles standard semver (preserving test_hybrid expectations)
        self.assertEqual(version_tuple("1.10.0"), (1, 10, 0))
        self.assertEqual(version_tuple("2.4.1"), (2, 4, 1))
        self.assertEqual(version_tuple("v2.4"), (2, 4, 0))
        self.assertIsNone(version_tuple("2.1.0-beta1"))
        self.assertIsNone(version_tuple("3.0.0-alpha5"))

    def test_tb_megamenu_v3_defaults_to_keep_and_never_downgrades_to_v1(self):
        """tb_megamenu 3.0.0-alpha5 / 3.x must default to keep and never downgrade to 1.x."""
        from d11.compatibility import build
        from d11.auto_decide import auto_decide as auto_decide_run, DECISION_RULES

        context = {
            "roots": {"composer": str(self.root), "custom": [], "config": None},
            "composer": {"packages": [{"name": "drupal/tb_megamenu", "version": "3.0.0-alpha5"}]},
            "extensions": [
                {
                    "name": "tb_megamenu",
                    "type": "module",
                    "path": str(self.root / "web/modules/contrib/tb_megamenu/tb_megamenu.info.yml"),
                    "package": "drupal/tb_megamenu",
                    "installed": True,
                    "exported": True,
                    "coreConstraint": "^8 || ^9 || ^10 || ^11",
                    "dependencies": [],
                }
            ],
            "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
            "deployment": {"status": "unknown", "observations": []},
            "removedCoreDependencies": [],
            "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
        }
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0},
            {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        solver = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
        patches = {
            "status": "searched",
            "packages": [
                {
                    "package": "drupal/tb_megamenu",
                    "releaseCandidates": [
                        {"version": "1.10", "drupalOrgVersion": "8.x-1.10", "stability": "stable"},
                        {"version": "3.0.0-alpha5", "drupalOrgVersion": "3.0.0-alpha5", "stability": "prerelease"},
                    ],
                }
            ],
        }

        out = self.root / "run_tb"
        out.mkdir(parents=True)
        report = build(context, checks, solver, patches, out)

        ext = report["extensions"][0]
        self.assertEqual(ext["name"], "tb_megamenu")
        self.assertEqual(ext["status"], "ready")
        self.assertEqual(ext["recommendedAction"], "keep")
        self.assertEqual(ext["decision"]["action"], "keep")
        self.assertNotIn("1.10", [rc["version"] for rc in ext["releaseCandidates"]])

        # Verify auto_decide rules for tb_megamenu
        ctx = {
            "existing": None,
            "is_clean": True,
            "is_provus": True,
            "manual_proposal": out / "missing.json",
            "patches": [],
            "accept_prereleases": True,
            "obsolete_modules": set(),
            "replacements": {},
        }
        dec = None
        for rule in DECISION_RULES:
            dec = rule(ext, ctx)
            if dec is not None:
                break
        self.assertIsNotNone(dec)
        self.assertEqual(dec["action"], "keep")
        self.assertEqual(dec["candidateVersion"], "3.0.0-alpha5")

        # Verify stale operator decision with 1.x is corrected to keep
        ctx_stale_op = dict(ctx, existing={"origin": "operator", "action": "compatible_release", "candidateVersion": "1.10"})
        dec_fixed = None
        for rule in DECISION_RULES:
            dec_fixed = rule(ext, ctx_stale_op)
            if dec_fixed is not None:
                break
        self.assertIsNotNone(dec_fixed)
        self.assertEqual(dec_fixed["action"], "keep")
        self.assertEqual(dec_fixed["candidateVersion"], "3.0.0-alpha5")


if __name__ == "__main__":
    unittest.main()


