import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.common import Problem
from d11lib.handoff import _is_excluded, inspect_sync_candidates, sync_to_git_branch


class GitHandoffTests(unittest.TestCase):
    def test_exclusion_rules(self):
        # Excluded directories and files
        self.assertTrue(_is_excluded(Path(".git/config")))
        self.assertTrue(_is_excluded(Path("vendor/autoload.php")))
        self.assertTrue(_is_excluded(Path("web/core/lib/Drupal.php")))
        self.assertTrue(_is_excluded(Path("web/sites/default/files/styles/test.jpg")))
        self.assertTrue(_is_excluded(Path("dump.sql")))
        self.assertTrue(_is_excluded(Path("backup.sql.gz")))
        self.assertTrue(_is_excluded(Path(".env")))
        self.assertTrue(_is_excluded(Path("web/sites/default/settings.local.php")))

        # Allowed files
        self.assertFalse(_is_excluded(Path("composer.json")))
        self.assertFalse(_is_excluded(Path("composer.lock")))
        self.assertFalse(_is_excluded(Path("web/modules/custom/my_module/my_module.info.yml")))
        self.assertFalse(_is_excluded(Path("web/modules/custom/my_module/src/Controller/Test.php")))
        self.assertFalse(_is_excluded(Path("web/themes/custom/my_theme/my_theme.info.yml")))
        self.assertFalse(_is_excluded(Path("patches/module-fix.patch")))
        self.assertFalse(_is_excluded(Path("config/sync/system.site.yml")))

    def test_inspect_sync_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed"
            source = root / "source"
            managed.mkdir()
            source.mkdir()

            # Managed has updated composer.json and a new custom module file
            (managed / "composer.json").write_text(json.dumps({"require": {"drupal/core": "^11"}}))
            (source / "composer.json").write_text(json.dumps({"require": {"drupal/core": "^10"}}))

            (managed / "web/modules/custom/my_mod").mkdir(parents=True)
            (managed / "web/modules/custom/my_mod/my_mod.info.yml").write_text(
                "name: My Mod\ncore_version_requirement: ^10 || ^11\n"
            )

            candidates = inspect_sync_candidates(managed, source)
            paths = [c["path"] for c in candidates]
            self.assertIn("composer.json", paths)
            self.assertIn("web/modules/custom/my_mod/my_mod.info.yml", paths)

            comp_candidate = next(c for c in candidates if c["path"] == "composer.json")
            self.assertEqual(comp_candidate["action"], "modified")

            mod_candidate = next(
                c for c in candidates if c["path"] == "web/modules/custom/my_mod/my_mod.info.yml"
            )
            self.assertEqual(mod_candidate["action"], "added")

    def test_sync_to_git_branch_success(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed"
            source = root / "source"
            managed.mkdir()
            source.mkdir()

            (managed / "composer.json").write_text(json.dumps({"require": {"drupal/core": "^11"}}))
            (source / "composer.json").write_text(json.dumps({"require": {"drupal/core": "^10"}}))

            calls = []
            status_call_count = 0

            def fake_command(argv, cwd, timeout, **kwargs):
                nonlocal status_call_count
                calls.append((argv, cwd))
                if "rev-parse" in argv and "--is-inside-work-tree" in argv:
                    return {"exitCode": 0, "stdout": "true\n", "stderr": ""}
                elif "rev-parse" in argv and "--abbrev-ref" in argv:
                    return {"exitCode": 0, "stdout": "main\n", "stderr": ""}
                elif "checkout" in argv:
                    return {"exitCode": 0, "stdout": "", "stderr": ""}
                elif "status" in argv:
                    status_call_count += 1
                    # Clean working tree on pre-check, dirty on target branch
                    return {
                        "exitCode": 0,
                        "stdout": "" if status_call_count == 1 else " M composer.json\n",
                        "stderr": "",
                    }
                elif "add" in argv or "commit" in argv:
                    return {"exitCode": 0, "stdout": "", "stderr": ""}
                return {"exitCode": 0, "stdout": "", "stderr": ""}

            res = sync_to_git_branch(
                managed_dir=managed,
                source_dir=source,
                branch_name="upgrade/drupal-11",
                commit=True,
                run_command=fake_command,
            )

            self.assertTrue(res["success"])
            self.assertEqual(res["branch"], "upgrade/drupal-11")
            self.assertEqual(res["originalBranch"], "main")
            # Strict safety guardrails: never auto-commit, never auto-push
            self.assertFalse(res["committed"])
            self.assertFalse(res["autoPushed"])
            self.assertIn("guardrail", res["guardrailNotice"].lower())
            self.assertEqual(len(res["filesChanged"]), 1)
            self.assertEqual(res["filesChanged"][0]["path"], "composer.json")

            # Verify file was copied
            self.assertIn("^11", (source / "composer.json").read_text())

    def test_protected_branch_blocked_by_guardrail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed"
            source = root / "source"
            managed.mkdir()
            source.mkdir()

            def fake_cmd(argv, cwd, timeout, **kwargs):
                return {"exitCode": 0, "stdout": "true\n", "stderr": ""}

            with self.assertRaises(Problem) as ctx:
                sync_to_git_branch(managed, source, branch_name="main", run_command=fake_cmd)
            self.assertIn("protected branch", str(ctx.exception).lower())

    def test_dirty_working_tree_blocked_by_guardrail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed"
            source = root / "source"
            managed.mkdir()
            source.mkdir()

            def fake_cmd(argv, cwd, timeout, **kwargs):
                if "rev-parse" in argv and "--is-inside-work-tree" in argv:
                    return {"exitCode": 0, "stdout": "true\n", "stderr": ""}
                elif "rev-parse" in argv and "--abbrev-ref" in argv:
                    return {"exitCode": 0, "stdout": "feature\n", "stderr": ""}
                elif "status" in argv:
                    return {"exitCode": 0, "stdout": " M uncommitted.txt\n", "stderr": ""}
                return {"exitCode": 0, "stdout": "", "stderr": ""}

            with self.assertRaises(Problem) as ctx:
                sync_to_git_branch(
                    managed, source, branch_name="upgrade/drupal-11", run_command=fake_cmd
                )
            self.assertIn("uncommitted change", str(ctx.exception).lower())

    def test_sync_non_git_source_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed"
            source = root / "source"
            managed.mkdir()
            source.mkdir()

            def fail_git(argv, cwd, timeout, **kwargs):
                return {"exitCode": 128, "stdout": "", "stderr": "fatal: not a git repository"}

            with self.assertRaises(Problem) as ctx:
                sync_to_git_branch(managed, source, run_command=fail_git)
            self.assertIn("not a Git repository", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
