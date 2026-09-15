import json
import tempfile
import unittest
from pathlib import Path

from d11.auto_remediate import generate_remediation_proposals
from d11.solver import prepare_manifest
from d11.two_gate import _batch_config, _apply_patch_decisions


class TestReusabilityEnhancements(unittest.TestCase):
    def test_removed_core_dependencies_cleared_when_decisions_resolved(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "project"
            p.mkdir()
            out = Path(td) / "run_out"
            out.mkdir()

            cfg = {
                "_config": str(p / "project.json"),
                "repository": "site",
                "environment": {"id": "test_env"},
                "site": {"uri": "http://127.0.0.1:8888"},
            }
            context = {
                "removedCoreDependencies": [
                    {"name": "action", "active": True, "exported": False},
                    {"name": "tour", "active": False, "exported": True},
                    {"name": "book", "active": False, "exported": False},
                ],
                "deployment": {"status": "unknown", "observations": []},
            }
            solver = {"exactCoreVersion": "11.4.6", "status": "passed", "changes": [], "installed": {}}

            # Case 1: No decisions file -> removedCoreExtensionsReviewed is False
            _batch_config(p, cfg, out, context, [], True, solver)
            req1 = json.loads((p / "target-requirements.json").read_text())
            self.assertFalse(req1["removedCoreExtensionsReviewed"])

            # Case 2: Decisions unresolved/deferred -> still False
            decisions = {
                "action": {"action": "defer"},
                "tour": {"action": "remove"},
            }
            (out / "compatibility-decisions.json").write_text(json.dumps(decisions))
            _batch_config(p, cfg, out, context, [], True, solver)
            req2 = json.loads((p / "target-requirements.json").read_text())
            self.assertFalse(req2["removedCoreExtensionsReviewed"])

            # Case 3: Decisions fully resolved for active/exported extensions -> True
            decisions = {
                "action": {"action": "compatible_release"},
                "tour": {"action": "remove"},
            }
            (out / "compatibility-decisions.json").write_text(json.dumps(decisions))
            _batch_config(p, cfg, out, context, [], True, solver)
            req3 = json.loads((p / "target-requirements.json").read_text())
            self.assertTrue(req3["removedCoreExtensionsReviewed"])

    def test_solver_manifest_auto_adds_composer_patches_plugin(self):
        source = {
            "require": {"drupal/core": "^10.3"},
            "extra": {
                "patches": {
                    "drupal/token": {
                        "Fix D11 deprecation": "https://www.drupal.org/files/issues/token.patch"
                    }
                }
            },
            "config": {},
        }
        result = prepare_manifest(source)
        self.assertIn("cweagans/composer-patches", result["require"])
        self.assertEqual(result["require"]["cweagans/composer-patches"], "^1.7 || ^2.0")
        self.assertTrue(result["config"]["allow-plugins"]["cweagans/composer-patches"])

    def test_apply_patch_decisions_auto_adds_cweagans_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            patch_file = out / "test.patch"
            patch_file.write_text("diff --git a/test b/test\n")

            composer_data = {
                "require": {"drupal/core-recommended": "^11.0"},
                "config": {"allow-plugins": {"phpstan/extension-installer": True}},
            }
            proposal = {
                "changes": [
                    {
                        "path": "composer.json",
                        "after": json.dumps(composer_data, indent=2),
                    }
                ],
                "findings": [],
            }
            compatibility = {
                "extensions": [
                    {
                        "name": "example_mod",
                        "package": "drupal/example_mod",
                        "patches": [
                            {
                                "id": "patch_1",
                                "title": "D11 compatibility fix",
                                "patch": "test.patch",
                                "approvalEligible": True,
                            }
                        ],
                    }
                ]
            }
            decisions = {
                "example_mod": {
                    "action": "community_patch",
                    "candidateId": "patch_1",
                }
            }

            _apply_patch_decisions(proposal, compatibility, decisions, out)

            composer_after = json.loads(proposal["changes"][0]["after"])
            self.assertIn("cweagans/composer-patches", composer_after["require"])
            self.assertTrue(
                composer_after["config"]["allow-plugins"]["cweagans/composer-patches"]
            )
            self.assertEqual(
                composer_after["extra"]["patches"]["drupal/example_mod"]["D11 compatibility fix"],
                "patches/d11/test.patch",
            )

    def test_auto_remediate_dynamic_docroot_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            site_root = Path(td)
            # Create custom docroot structure 'custom_web'
            info_file = (
                site_root
                / "custom_web"
                / "modules"
                / "custom"
                / "my_module"
                / "my_module.info.yml"
            )
            info_file.parent.mkdir(parents=True)
            info_file.write_text("name: My Module\ntype: module\ncore_version_requirement: ^10\n")

            run_out = site_root / "out"
            run_out.mkdir()

            report = {
                "extensions": [
                    {
                        "name": "my_module",
                        "source": "custom",
                        "status": "fixable",
                        "upgradeStatus": {
                            "issues": [
                                {
                                    "location": {
                                        "path": "modules/custom/my_module/my_module.module"
                                    }
                                }
                            ]
                        },
                        "rector": {"changes": []},
                    }
                ]
            }
            context = {"roots": {"drupal": "custom_web"}}

            result = generate_remediation_proposals(site_root, run_out, report, context)
            self.assertEqual(result["remediatedCount"], 1)
            self.assertEqual(result["modules"][0]["name"], "my_module")

    def test_removal_precondition_uses_core_status_not_failing_eval(self):
        # Verify that uninstall steps use core:status for safe idempotency
        import inspect
        from d11 import two_gate
        source = inspect.getsource(two_gate._rebuild_after_decisions)
        self.assertIn('"core:status"', source)
        self.assertNotIn('throw new \\Exception("Module {name} not installed")', source)


if __name__ == "__main__":
    unittest.main()
