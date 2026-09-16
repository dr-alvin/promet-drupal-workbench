"""Invariant 2: ``keep`` is permitted only for clean extensions.

These tests pin the single shared definition of "clean", the default-decision
guard in the report builder, the recommendation for clean custom modules, the
scan-mode source of truth, and decision provenance recorded by the engine.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.auto_decide import auto_decide as auto_decide_run
from d11.common import read, write
from d11.compatibility import (
    _safe_default,
    build,
    is_clean_extension,
    validate_decisions,
)


def _context(root, extensions, custom=()):
    return {
        "roots": {"composer": str(root), "custom": list(custom), "config": None},
        "composer": {
            "packages": [
                {"name": e["package"], "version": e.get("currentVersion", "1.0.0")}
                for e in extensions
                if e.get("package")
            ]
        },
        "extensions": extensions,
        "runtime": {"status": "collected", "commands": {"status": {"data": {"drupal-version": "10.4.0"}}}},
        "deployment": {"status": "unknown", "observations": []},
        "removedCoreDependencies": [],
        "git": {"head": {"stdout": "abc"}, "dirty": {"stdout": ""}},
    }


def _row(report, name):
    """Rows for uninstalled extensions live in presentNotInstalled, not extensions."""
    rows = report.get("extensions", []) + report.get("presentNotInstalled", [])
    return next(r for r in rows if r["name"] == name)


CLEAN_CHECKS = [
    {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0, "command": {"argv": []}},
    {"id": "drupal_rector", "status": "passed", "changes": [], "findingCount": 0},
    {"id": "module_impact", "status": "passed", "modules": {}},
]
SOLVER_OK = {"status": "passed", "exactCoreVersion": "11.4.0", "versions": {}, "changes": []}
NO_PATCHES = {"status": "not_needed", "packages": []}


class IsCleanExtensionTests(unittest.TestCase):
    def test_ready_without_findings_is_clean(self):
        row = {"status": "ready", "upgradeStatus": {"issueCount": 0}, "rector": {"fixableCount": 0}}
        self.assertTrue(is_clean_extension(row))

    def test_ready_with_upgrade_status_issues_is_not_clean(self):
        row = {"status": "ready", "upgradeStatus": {"issueCount": 2}, "rector": {"fixableCount": 0}}
        self.assertFalse(is_clean_extension(row))

    def test_ready_with_rector_fixes_is_not_clean(self):
        row = {"status": "ready", "upgradeStatus": {"issueCount": 0}, "rector": {"fixableCount": 1}}
        self.assertFalse(is_clean_extension(row))

    def test_not_ready_is_not_clean(self):
        row = {"status": "update_available", "upgradeStatus": {"issueCount": 0}, "rector": {"fixableCount": 0}}
        self.assertFalse(is_clean_extension(row))

    def test_incompatible_core_constraint_is_not_clean(self):
        row = {"status": "ready", "coreConstraint": "^9 || ^10", "upgradeStatus": {}, "rector": {}}
        self.assertFalse(is_clean_extension(row))
        row["coreConstraint"] = "^10 || ^11"
        self.assertTrue(is_clean_extension(row))

    def test_missing_counts_and_non_dict_are_handled(self):
        self.assertTrue(is_clean_extension({"status": "ready"}))
        self.assertFalse(is_clean_extension(None))


class SafeDefaultTests(unittest.TestCase):
    def test_clean_ready_defaults_to_keep(self):
        decision = _safe_default("ready", [], "1.0.0", "contrib", [], issues=[], recommended="keep")
        self.assertEqual(decision["action"], "keep")
        self.assertTrue(decision["autoSelected"])

    def test_ready_with_issues_does_not_default_to_keep(self):
        decision = _safe_default("ready", [], "1.0.0", "contrib", [], issues=[{"description": "x"}])
        self.assertIsNone(decision)

    def test_ready_with_rector_fixes_does_not_default_to_keep(self):
        decision = _safe_default("ready", [], "1.0.0", "custom", [{"file": "a.php"}], issues=[])
        self.assertIsNone(decision)

    def test_ready_recommended_remove_does_not_default_to_keep(self):
        decision = _safe_default("ready", [], None, "contrib", [], issues=[], recommended="remove")
        self.assertIsNone(decision)

    def test_stable_update_still_defaults_to_compatible_release(self):
        candidates = [{"version": "2.0.0", "stability": "stable"}]
        decision = _safe_default("update_available", candidates, "2.0.0", "contrib", [], issues=[])
        self.assertEqual(decision["action"], "compatible_release")
        self.assertEqual(decision["candidateVersion"], "2.0.0")


class BuildInvariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "web/modules/custom/acme/").mkdir(parents=True)
        (self.root / "web/modules/contrib/unused/").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _ext_custom(self):
        return {
            "name": "acme",
            "type": "module",
            "path": str(self.root / "web/modules/custom/acme/acme.info.yml"),
            "package": None,
            "installed": True,
            "exported": True,
            "coreConstraint": "^10 || ^11",
            "dependencies": [],
        }

    def _ext_unused_contrib(self):
        return {
            "name": "unused",
            "type": "module",
            "path": str(self.root / "web/modules/contrib/unused/unused.info.yml"),
            "package": "drupal/unused",
            "installed": False,
            "exported": False,
            "coreConstraint": "^10 || ^11",
            "dependencies": [],
        }

    def test_clean_custom_module_recommends_and_selects_keep(self):
        context = _context(self.root, [self._ext_custom()], custom=[str(self.root / "web/modules/custom")])
        report = build(context, CLEAN_CHECKS, SOLVER_OK, NO_PATCHES, self.root / "run")
        row = report["extensions"][0]
        self.assertEqual(row["status"], "ready")
        # A custom module has no upstream release to move to; the recommendation must be keep.
        self.assertEqual(row["recommendedAction"], "keep")
        self.assertEqual(row["selectedAction"], "keep")
        self.assertEqual(row["blockers"], [])

    def test_unused_contrib_recommended_remove_is_not_auto_kept(self):
        context = _context(self.root, [self._ext_unused_contrib()])
        report = build(context, CLEAN_CHECKS, SOLVER_OK, NO_PATCHES, self.root / "run")
        row = _row(report, "unused")
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["recommendedAction"], "remove")
        # Layer 1: the default-decision guard itself never proposes keep against a remove recommendation.
        self.assertIsNone(_safe_default(row["status"], row["releaseCandidates"], row["targetVersion"], "contrib", [], issues=[], recommended=row["recommendedAction"]))
        # Layer 2: the report leaves uninstalled rows undecided for the operator or the decision engine.
        self.assertIsNone(row["selectedAction"])
        self.assertIsNone(row["decision"])
        self.assertEqual(row["reviewDisposition"], "present_not_installed")

    def test_no_row_is_ever_kept_with_findings(self):
        custom = self._ext_custom()
        fix = {"file": str(self.root / "web/modules/custom/acme/acme.module"), "diff": "--- a\n+++ b\n"}
        checks = [
            {"id": "upgrade_status", "status": "passed", "issues": [], "findingCount": 0, "command": {"argv": []}},
            {"id": "drupal_rector", "status": "findings", "changes": [fix], "findingCount": 1},
            {"id": "module_impact", "status": "passed", "modules": {}},
        ]
        context = _context(self.root, [custom], custom=[str(self.root / "web/modules/custom")])
        report = build(context, checks, SOLVER_OK, NO_PATCHES, self.root / "run")
        row = report["extensions"][0]
        self.assertEqual(row["rector"]["fixableCount"], 1)
        self.assertNotEqual(row["selectedAction"], "keep")
        self.assertFalse(is_clean_extension(row))
        for r in report["extensions"]:
            if r["selectedAction"] == "keep":
                self.assertFalse(r["upgradeStatus"]["issueCount"] or r["rector"]["fixableCount"])


class ScanModeSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "web/modules/contrib/foo").mkdir(parents=True)
        self.ext = {
            "name": "foo",
            "type": "module",
            "path": str(self.root / "web/modules/contrib/foo/foo.info.yml"),
            "package": "drupal/foo",
            "installed": True,
            "exported": True,
            "coreConstraint": "^10 || ^11",
            "dependencies": [],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _checks(self, argv):
        checks = [dict(c) for c in CLEAN_CHECKS]
        checks[0] = dict(checks[0], command={"argv": argv})
        return checks

    def _row(self, scan_mode, argv):
        context = _context(self.root, [self.ext])
        if scan_mode is not None:
            context["scanMode"] = scan_mode
        return build(context, self._checks(argv), SOLVER_OK, NO_PATCHES, self.root / "run")["extensions"][0]

    def test_recorded_scan_mode_wins_over_argv(self):
        # Recorded full scan: contrib was scanned even though argv carries the flag.
        row = self._row("full", ["drush", "upgrade_status:analyze", "--ignore-contrib"])
        self.assertEqual(row["scanStatus"], "scanned")
        # Recorded fast scan: contrib was not scanned even if the flag were renamed away.
        row = self._row("fast", ["drush", "upgrade_status:analyze"])
        self.assertEqual(row["scanStatus"], "not_scanned")

    def test_argv_is_only_a_fallback_for_older_runs(self):
        row = self._row(None, ["drush", "upgrade_status:analyze", "--ignore-contrib"])
        self.assertEqual(row["scanStatus"], "not_scanned")
        row = self._row(None, ["drush", "upgrade_status:analyze"])
        self.assertEqual(row["scanStatus"], "scanned")


class DecisionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "web/modules/contrib/unused").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_decisions_preserves_provenance_keys(self):
        report = {"extensions": [{"name": "foo", "source": "contrib", "releaseCandidates": []}]}
        normalized = validate_decisions(
            report,
            [
                {
                    "name": "foo",
                    "action": "keep",
                    "origin": "automatic",
                    "decidedBy": "rule_clean_extension",
                    "recommendedAction": "keep",
                    "overrodeRecommendation": False,
                    "unrelated": "dropped",
                }
            ],
        )
        self.assertEqual(normalized["foo"]["decidedBy"], "rule_clean_extension")
        self.assertEqual(normalized["foo"]["recommendedAction"], "keep")
        self.assertIs(normalized["foo"]["overrodeRecommendation"], False)
        self.assertNotIn("unrelated", normalized["foo"])
        plain = validate_decisions(report, [{"name": "foo", "action": "keep"}])
        self.assertNotIn("decidedBy", plain["foo"])

    def test_auto_decide_records_which_rule_decided(self):
        (self.root / "web/modules/contrib/clean_mod").mkdir(parents=True)
        (self.root / "web/modules/contrib/devmod").mkdir(parents=True)
        clean_mod = {
            "name": "clean_mod",
            "type": "module",
            "path": str(self.root / "web/modules/contrib/clean_mod/clean_mod.info.yml"),
            "package": "drupal/clean_mod",
            "currentVersion": "2.1.0",
            "installed": True,
            "exported": True,
            "coreConstraint": "^10 || ^11",
            "dependencies": [],
        }
        # A dev checkout cannot be version-compared, so the report recommends defer;
        # the engine resolves it to the solver's stable target and must say so.
        devmod = dict(clean_mod, name="devmod", package="drupal/devmod", currentVersion="dev-3.x",
                      path=str(self.root / "web/modules/contrib/devmod/devmod.info.yml"))
        solver = dict(SOLVER_OK, versions={"drupal/clean_mod": "2.1.0", "drupal/devmod": "3.2.0"})
        context = _context(self.root, [clean_mod, devmod])
        out = self.root / "run"
        out.mkdir()
        report = build(context, CLEAN_CHECKS, solver, NO_PATCHES, out)
        self.assertEqual(_row(report, "clean_mod")["recommendedAction"], "keep")
        self.assertEqual(_row(report, "devmod")["recommendedAction"], "defer")
        self.assertIsNone(_row(report, "devmod")["selectedAction"])
        write(out / "compatibility-report.json", report)
        write(out / "result/context.json", context)
        write(out / "result/result.json", {"checks": [{"id": "compatibility", "status": "passed"}]})
        write(out / "audit-tools.json", {"checks": CLEAN_CHECKS})
        write(out / "composer-resolution.json", solver)
        write(out / "patch-candidates.json", NO_PATCHES)
        write(out / "route-selection.json", {"routes": ["/"], "digest": "routes"})
        write(out / "gate.json", {"baseline": {"passed": True}})
        (self.root / "composer.json").write_text(json.dumps({"require": {
            "drupal/core-recommended": "^10.3", "drupal/clean_mod": "^2.1", "drupal/devmod": "3.x-dev"}}))
        (self.root / "composer.lock").write_text(json.dumps({"packages": []}))

        root = self.root

        class MockWorkflow:
            def fingerprint(self, p, cfg):
                return "fp"

            def event(self, *args, **kwargs):
                pass

            def report(self, *args, **kwargs):
                pass

            def run(self, rid):
                return out, {"action": "guided-audit", "status": "completed", "project": "proj", "scanMode": "fast"}

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

        with patch("d11.solver.resolve", return_value=solver):
            result = auto_decide_run(MockWorkflow(), "run-1", auto_remediate=False)
        self.assertEqual(result["status"], "passed")
        decisions = read(out / "compatibility-decisions.json")

        clean = decisions["clean_mod"]
        self.assertEqual(clean["action"], "keep")
        self.assertEqual(clean["decidedBy"], "rule_clean_extension")
        self.assertEqual(clean["recommendedAction"], "keep")
        self.assertIs(clean["overrodeRecommendation"], False)

        dev = decisions["devmod"]
        self.assertEqual(dev["action"], "compatible_release")
        self.assertEqual(dev["candidateVersion"], "3.2.0")
        self.assertEqual(dev["decidedBy"], "rule_compatible_release")
        self.assertEqual(dev["recommendedAction"], "defer")
        self.assertIs(dev["overrodeRecommendation"], True)

        # Provenance survives the rebuild into the report the Gate 1 reviewer sees.
        rebuilt = read(out / "compatibility-report.json")
        self.assertEqual(_row(rebuilt, "devmod")["decision"]["decidedBy"], "rule_compatible_release")
        self.assertIs(_row(rebuilt, "devmod")["decision"]["overrodeRecommendation"], True)


if __name__ == "__main__":
    unittest.main()
