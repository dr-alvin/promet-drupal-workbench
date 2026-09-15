import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from d11lib.common import write
from d11lib.guided_setup import Setup
from d11lib.recovery import create as recovery_create
from d11lib.recovery import restore as recovery_restore
from d11lib.workflow import Workflow


class ZeroCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name).resolve()
        self.workflow = Workflow(self.base / "managed")
        self.setup = Setup(self.workflow)

        # Setup mock Git repository with composer.json and fake media
        self.src = self.base / "mock-git-repo"
        self.src.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.src, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tester@example.com"],
            cwd=self.src,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"], cwd=self.src, capture_output=True, check=True
        )
        (self.src / ".ddev").mkdir()

        # Composer manifests
        write(
            self.src / "composer.json",
            {"name": "drupal/test-site", "require": {"drupal/core-recommended": "^10.3"}},
        )
        write(self.src / "composer.lock", {"packages": []})

        # Drupal structure with custom module and media files
        custom_mod = self.src / "web/modules/custom/test_mod"
        custom_mod.mkdir(parents=True)
        (custom_mod / "test_mod.info.yml").write_text(
            "name: Test Mod\ntype: module\ncore_version_requirement: ^10 || ^11\n"
        )
        (custom_mod / "test_mod.module").write_text("<?php\n")

        media_dir = self.src / "web/sites/default/files"
        media_dir.mkdir(parents=True)
        for i in range(20):
            (media_dir / f"image_{i}.png").write_bytes(b"fake_png_data" * 100)

        # Commit baseline to Git
        subprocess.run(["git", "add", "."], cwd=self.src, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial baseline commit"],
            cwd=self.src,
            capture_output=True,
            check=True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_auto_detect_zero_copy_on_git_source(self):
        body = {"id": "test-zero-copy-site", "source": str(self.src), "name": "Test Zero Copy Site"}
        draft = self.setup.create(body)
        self.assertTrue(draft.get("zeroCopy"), "Should auto-detect zero-copy for Git repository")
        self.assertEqual(draft.get("gitBranch"), "upgrade/drupal-11")

    def test_zero_copy_work_skips_media_copy_and_creates_branch(self):
        body = {
            "id": "zero-copy-proj",
            "source": str(self.src),
            "name": "Zero Copy Project",
            "exportAuthorized": True,
        }
        self.setup.create(body)
        self.setup.work("zero-copy-proj")

        draft = self.setup.get("zero-copy-proj")
        self.assertEqual(draft["status"], "registered")
        self.assertEqual(draft["originalBranch"], "main")
        self.assertEqual(draft["upgradeBranch"], "upgrade/drupal-11")

        # Verify git branch switched to upgrade/drupal-11
        b_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.src,
            capture_output=True,
            text=True,
        )
        self.assertEqual(b_res.stdout.strip(), "upgrade/drupal-11")

        # Verify no massive file bundle was duplicated
        bundle_dir = self.setup.path("zero-copy-proj") / "bundle"
        self.assertFalse(
            (bundle_dir / "files").exists(), "Media files should NOT be copied in zero-copy mode"
        )

        # Verify site is symlinked
        site_link = self.workflow.home / "projects" / "zero-copy-proj" / "site"
        self.assertTrue(
            site_link.is_symlink(), "Project site should be a symlink to source in zero-copy mode"
        )
        self.assertEqual(site_link.resolve(), self.src.resolve())

        # Verify project.json has zeroCopy metadata
        proj_p, cfg = self.workflow.project("zero-copy-proj")
        self.assertTrue(cfg.get("zeroCopy"))
        self.assertEqual(cfg.get("originalBranch"), "main")
        self.assertEqual(cfg.get("upgradeBranch"), "upgrade/drupal-11")

    def test_fast_zero_copy_fingerprint(self):
        body = {
            "id": "fingerprint-site",
            "source": str(self.src),
            "name": "Fingerprint Site",
            "exportAuthorized": True,
        }
        self.setup.create(body)
        self.setup.work("fingerprint-site")

        p, cfg = self.workflow.project("fingerprint-site")
        fp1 = self.workflow.fingerprint(p, cfg)
        self.assertTrue(fp1)

        # Repeating fingerprint should yield identical result
        fp2 = self.workflow.fingerprint(p, cfg)
        self.assertEqual(fp1, fp2)

        # Modifying composer.json changes the fingerprint instantly
        (self.src / "composer.json").write_text('{"modified": true}')
        fp3 = self.workflow.fingerprint(p, cfg)
        self.assertNotEqual(fp1, fp3)

    def test_zero_copy_recovery_create_and_restore(self):
        body = {
            "id": "recovery-site",
            "source": str(self.src),
            "name": "Recovery Site",
            "exportAuthorized": True,
        }
        self.setup.create(body)
        self.setup.work("recovery-site")

        p, cfg = self.workflow.project("recovery-site")

        # 1. Create recovery checkpoint
        checkpoint_out = self.base / "run-out"
        checkpoint_out.mkdir()
        manifest = recovery_create(self.workflow, "recovery-site", checkpoint_out)

        self.assertTrue(manifest.get("zeroCopy"))
        self.assertEqual(manifest.get("originalBranch"), "main")
        self.assertEqual(manifest.get("upgradeBranch"), "upgrade/drupal-11")
        self.assertTrue((checkpoint_out / "recovery-checkpoint/database.sql.gz").is_file())
        self.assertFalse(
            (checkpoint_out / "recovery-checkpoint/code").exists(),
            "Code should NOT be duplicated in recovery checkpoint",
        )

        # 2. Simulate upgrade mutations in the source repo
        (self.src / "upgrade_modified.txt").write_text("drupal 11 mutated file")
        (self.src / "composer.json").write_text('{"drupal/core": "^11"}')

        # 3. Restore / Rollback
        rollback_evidence = recovery_restore(self.workflow, "recovery-site", checkpoint_out)
        self.assertTrue(rollback_evidence.get("zeroCopy"))
        self.assertTrue(rollback_evidence.get("databaseRestored"))
        self.assertEqual(rollback_evidence.get("gitBranchRestored"), "main")

        # Verify source Git repo is back on 'main' and uncommitted changes cleaned
        b_res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.src,
            capture_output=True,
            text=True,
        )
        self.assertEqual(b_res.stdout.strip(), "main")
        self.assertFalse(
            (self.src / "upgrade_modified.txt").exists(),
            "Mutated file should be cleaned on rollback",
        )


if __name__ == "__main__":
    unittest.main()
