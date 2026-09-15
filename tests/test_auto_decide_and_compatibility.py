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


if __name__ == "__main__":
    unittest.main()

