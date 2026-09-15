"""Unit tests for centralized ignores and exclusion sets."""

from __future__ import annotations

import unittest
from pathlib import Path

from d11.handoff import _is_excluded
from d11.ignores import (
    DEPENDENCIES,
    DISALLOWED_SECRET_EXTENSIONS,
    DISALLOWED_SECRET_FILENAMES,
    HANDOFF_EXCLUDE_DIRS,
    HANDOFF_EXCLUDE_EXTENSIONS,
    HANDOFF_EXCLUDE_FILES,
    SECRETS,
    SETUP_EXCLUDED,
    SETUP_SECRET_NAMES,
    STATE_HASHES_IGNORED_DIRS,
    USER_FILES,
    VCS,
)


class IgnoresTests(unittest.TestCase):
    def test_core_ignore_categories(self):
        # VCS
        self.assertIn(".git", VCS)
        self.assertIn(".ddev", VCS)
        self.assertIn(".docksal", VCS)

        # DEPENDENCIES
        self.assertIn("vendor", DEPENDENCIES)
        self.assertIn("node_modules", DEPENDENCIES)
        self.assertIn(".venv", DEPENDENCIES)
        self.assertIn("core", DEPENDENCIES)

        # USER_FILES
        self.assertIn("files", USER_FILES)
        self.assertIn("private", USER_FILES)
        self.assertIn("artifacts", USER_FILES)

        # SECRETS
        self.assertIn(".env", SECRETS)
        self.assertIn("settings.local.php", SECRETS)
        self.assertIn("auth.json", SECRETS)

    def test_derived_sets_consistency(self):
        # Setup exclusions contain VCS and workspace cache items
        self.assertIn(".git", SETUP_EXCLUDED)
        self.assertIn("node_modules", SETUP_EXCLUDED)
        self.assertIn("artifacts", SETUP_EXCLUDED)
        self.assertIn(".env", SETUP_SECRET_NAMES)

        # Handoff exclusions contain core directories and secrets
        self.assertIn("vendor", HANDOFF_EXCLUDE_DIRS)
        self.assertIn("core", HANDOFF_EXCLUDE_DIRS)
        self.assertIn(".sql", HANDOFF_EXCLUDE_EXTENSIONS)
        self.assertIn(".env", HANDOFF_EXCLUDE_FILES)

        # State hashes ignores
        self.assertIn(".git", STATE_HASHES_IGNORED_DIRS)
        self.assertIn("vendor", STATE_HASHES_IGNORED_DIRS)
        self.assertIn("core", STATE_HASHES_IGNORED_DIRS)

        # Guardrails disallowed secrets
        self.assertIn(".env", DISALLOWED_SECRET_FILENAMES)
        self.assertIn(".key", DISALLOWED_SECRET_EXTENSIONS)

    def test_handoff_is_excluded_integration(self):
        self.assertTrue(_is_excluded(Path(".git/config")))
        self.assertTrue(_is_excluded(Path("vendor/autoload.php")))
        self.assertTrue(_is_excluded(Path("web/core/lib/Drupal.php")))
        self.assertTrue(_is_excluded(Path("web/sites/default/files/styles/test.jpg")))
        self.assertTrue(_is_excluded(Path("dump.sql")))
        self.assertTrue(_is_excluded(Path("backup.sql.gz")))
        self.assertTrue(_is_excluded(Path(".env")))
        self.assertTrue(_is_excluded(Path("web/sites/default/settings.local.php")))

        self.assertFalse(_is_excluded(Path("composer.json")))
        self.assertFalse(_is_excluded(Path("web/modules/custom/my_mod/my_mod.info.yml")))


if __name__ == "__main__":
    unittest.main()
