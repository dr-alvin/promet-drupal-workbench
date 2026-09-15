"""Unit tests for version knowledge loading and validation."""

from __future__ import annotations

import unittest

from d11.knowledge import (
    get_default_branch,
    get_deprecation_rules,
    get_knowledge,
    get_obsolete_modules,
    get_patch_search_term,
    get_removed_core,
    get_removed_packages,
    get_replacements,
    get_requirements,
    get_source_floor,
    get_target_constraint,
    get_target_info_yml,
    load_knowledge,
)


class KnowledgeTests(unittest.TestCase):
    def test_load_drupal_11_knowledge(self):
        k = load_knowledge("11")
        self.assertEqual(k["targetVersion"], "11")
        self.assertEqual(k["source"]["major"], 10)
        self.assertEqual(k["source"]["minimum"], "10.3.0")
        self.assertEqual(k["target"]["constraint"], "^11")
        self.assertEqual(k["target"]["infoYml"], "^10 || ^11")
        self.assertIn("action", k["removedCoreExtensions"])
        self.assertIn("book", k["removedCoreExtensions"])
        self.assertEqual(k["defaultBranch"], "upgrade/drupal-11")
        self.assertTrue(len(k["deprecationRules"]) > 0)

    def test_target_normalization(self):
        k1 = get_knowledge("11")
        k2 = get_knowledge("drupal-11")
        k3 = get_knowledge("drupal-11.json")
        self.assertEqual(k1["targetVersion"], k2["targetVersion"])
        self.assertEqual(k2["targetVersion"], k3["targetVersion"])

    def test_target_normalization_with_dict_and_composer_manifest(self):
        # A composer.json dict with packages.drupal.org/8 repository should not extract '8'
        manifest = {
            "name": "promet/provus-drupal",
            "description": "Install Provus Drupal kickstarter by Promet Source.",
            "repositories": [{"type": "composer", "url": "https://packages.drupal.org/8"}],
        }
        k = load_knowledge(manifest)
        self.assertEqual(k["targetVersion"], "11")
        self.assertEqual(get_target_constraint(manifest), "^11")

        # Dict with explicit targetVersion or target should be respected
        k_d10 = load_knowledge({"targetVersion": "10"})
        self.assertEqual(k_d10["targetVersion"], "10")

    def test_convenience_accessors(self):
        self.assertEqual(get_target_constraint("11"), "^11")
        self.assertEqual(get_target_info_yml("11"), "^10 || ^11")
        major, parts, min_str = get_source_floor("11")
        self.assertEqual(major, 10)
        self.assertEqual(parts, (10, 3, 0))
        self.assertEqual(min_str, "10.3.0")
        self.assertIn("tracker", get_removed_core("11"))
        self.assertEqual(get_default_branch("11"), "upgrade/drupal-11")
        self.assertIn("Drupal%2011", get_patch_search_term("11"))
        rules = get_deprecation_rules("11")
        patterns = [r["pattern"] for r in rules]
        self.assertTrue(any("EXISTS_REPLACE" in p for p in patterns))
        reqs = get_requirements("11")
        self.assertIn("php", reqs)
        self.assertIn("databases", reqs)
        self.assertIn("advagg", get_obsolete_modules("11"))
        self.assertIn("drupal/core-vendor-hardening", get_removed_packages("11"))
        self.assertEqual(get_replacements("11").get("drupal/swiftmailer"), "drupal/symfony_mailer")

    def test_load_drupal_10_knowledge_and_d9_fixture_plan(self):
        import tempfile
        from pathlib import Path

        from d11.common import Problem, digest, file_hash, write
        from d11.execution import prerequisites

        k = load_knowledge("10")
        self.assertEqual(k["targetVersion"], "10")
        self.assertEqual(k["source"]["major"], 9)
        self.assertEqual(k["source"]["minimum"], "9.4.0")
        self.assertEqual(k["target"]["constraint"], "^10")
        self.assertEqual(k["target"]["infoYml"], "^9 || ^10")
        self.assertIn("color", k["removedCoreExtensions"])
        self.assertIn("quickedit", k["removedCoreExtensions"])
        self.assertEqual(k["defaultBranch"], "upgrade/drupal-10")

        # Verify accessors with target="drupal-10"
        self.assertEqual(get_target_constraint("drupal-10"), "^10")
        self.assertEqual(get_target_info_yml("drupal-10"), "^9 || ^10")
        major, parts, min_str = get_source_floor("drupal-10")
        self.assertEqual(major, 9)
        self.assertEqual(parts, (9, 4, 0))
        self.assertEqual(min_str, "9.4.0")

        # Verify D9 source validity against D10 target in prerequisites
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            proc = base / "recovery.md"
            proc.write_text("recovery procedure")
            env = {"id": "local", "kind": "local", "authorized": True}
            site = {"uri": "https://example.com"}
            backup = {
                "code": "git",
                "database": "dump",
                "files": "sync",
                "owner": "dev",
                "procedure": "recovery.md",
                "verifiedAt": "2026-09-11T00:00:00Z",
                "procedureDigest": file_hash(proc),
            }
            cap = base / "capture.json"
            write(cap, {"stable": True})
            bl = base / "baseline.json"
            write(
                bl,
                {
                    "schemaVersion": "1.0",
                    "environment": env,
                    "site": site,
                    "codeRevision": "rev1",
                    "installedExtensions": "exts",
                    "activeConfiguration": "cfg",
                    "publicScenarios": ["home"],
                    "authenticatedScenarios": ["admin"],
                    "captureSettings": "capture.json",
                    "captureSettingsHash": file_hash(cap),
                },
            )
            req = base / "requirements.json"
            write(
                req,
                {
                    "schemaVersion": "1.0",
                    "target": "drupal-10",
                    "sources": ["https://drupal.org"],
                    "verifiedAt": "2026-09-11T00:00:00Z",
                    "readinessPassed": True,
                    "earlierDatabaseUpdatesComplete": True,
                    "removedCoreExtensionsReviewed": True,
                },
            )
            cfg = {
                "_config": str(base / "project.json"),
                "environment": env,
                "site": site,
                "target": "drupal-10",
                "baselineEvidence": "baseline.json",
                "requirementsEvidence": "requirements.json",
                "steps": [],
            }
            p = {
                "source": "9.5.10",
                "target": "drupal-10",
                "environment": env,
                "site": site,
                "steps": [],
                "backup": backup,
                "requirementsEvidence": "requirements.json",
                "requirementsHash": file_hash(req),
                "baselineEvidence": "baseline.json",
                "baselineHash": file_hash(bl),
                "before": {
                    "codeRevision": "rev1",
                    "installedExtensions": "exts",
                    "activeConfiguration": "cfg",
                    "publicScenarios": ["home"],
                    "authenticatedScenarios": ["admin"],
                },
            }
            p["planId"] = digest({k: v for k, v in p.items() if k != "planId"})
            approval = {
                "schemaVersion": "1.0",
                "planId": p["planId"],
                "environment": env,
                "site": site,
                "approver": "Dev",
                "approvedAt": "2026-09-11T00:00:00Z",
                "steps": [],
                "recoveryDigest": digest(backup),
            }

            # Prerequisites check should pass for D9 (9.5.10) targeting D10
            prerequisites(cfg, p, approval)

            # Incompatible D8 source (8.9.0) should fail
            p_bad = dict(p, source="8.9.0")
            p_bad["planId"] = digest({k: v for k, v in p_bad.items() if k != "planId"})
            a_bad = dict(approval, planId=p_bad["planId"])
            with self.assertRaises(Problem) as ctx:
                prerequisites(cfg, p_bad, a_bad)
            self.assertIn("verified Drupal 9.4.0 or later Drupal 9 release", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
