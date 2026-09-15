"""Unit tests for automated remediation, decision synthesis, and obsolete package detection."""

import tempfile
import unittest
from pathlib import Path

from d11lib.auto_remediate import (
    _apply_known_rector_and_deprecations,
    _update_composer_json,
    _update_info_yml,
)
from d11lib.budget import scaffold_delivery_budget
from d11lib.common import write
from d11lib.obsolete import inspect_obsolete_packages


class TestAutoWorkflow(unittest.TestCase):
    def test_update_info_yml(self):
        # 1. Update ^9 || ^10 to ^10 || ^11
        original = "name: Test Module\ntype: module\ncore_version_requirement: ^9 || ^10\n"
        updated = _update_info_yml(original)
        self.assertIn("core_version_requirement: ^10 || ^11", updated)

        # 2. Leave already compliant requirement alone
        compliant = "name: Test Module\ntype: module\ncore_version_requirement: ^10 || ^11\n"
        self.assertEqual(_update_info_yml(compliant), compliant)

        # 3. Add requirement if missing
        missing = "name: Test Module\ntype: module\n"
        self.assertIn("core_version_requirement: ^10 || ^11", _update_info_yml(missing))

    def test_update_composer_json(self):
        original = (
            '{\n  "name": "drupal/test",\n  "require": {\n    "drupal/core": "^9 || ^10"\n  }\n}'
        )
        updated = _update_composer_json(original)
        self.assertIn('"drupal/core": "^10 || ^11"', updated)

    def test_apply_known_rector_transformations(self):
        code = "FileSystemInterface::EXISTS_REPLACE;"
        transformed = _apply_known_rector_and_deprecations(code)
        self.assertIn("DeprecationHelper::backwardsCompatibleCall", transformed)
        self.assertIn("FileExists::Replace", transformed)

        code_err = "FileSystemInterface::EXISTS_ERROR;"
        transformed_err = _apply_known_rector_and_deprecations(code_err)
        self.assertIn("FileExists::Error", transformed_err)

        css = "#drupal-off-canvas { color: red; }"
        self.assertIn("#drupal-off-canvas-wrapper", _apply_known_rector_and_deprecations(css))

    def test_remediation_idempotent_and_comment_string_safe(self):
        fixture_code = """<?php
// FileSystemInterface::EXISTS_REPLACE in a single-line comment
/* FileSystemInterface::EXISTS_REPLACE in a block comment */
$msg = 'Do not replace FileSystemInterface::EXISTS_REPLACE in single-quoted string';
$str = "Do not replace FileSystemInterface::EXISTS_REPLACE in double-quoted string";
$status = FileSystemInterface::EXISTS_REPLACE;
$err = FileSystemInterface::EXISTS_ERROR;
"""
        pass1 = _apply_known_rector_and_deprecations(fixture_code)
        # Assert comment is untouched
        self.assertIn("// FileSystemInterface::EXISTS_REPLACE in a single-line comment", pass1)
        self.assertIn("/* FileSystemInterface::EXISTS_REPLACE in a block comment */", pass1)
        # Assert string literals are untouched
        self.assertIn(
            "'Do not replace FileSystemInterface::EXISTS_REPLACE in single-quoted string'", pass1
        )
        self.assertIn(
            '"Do not replace FileSystemInterface::EXISTS_REPLACE in double-quoted string"', pass1
        )
        # Assert real code is transformed
        self.assertIn("DeprecationHelper::backwardsCompatibleCall", pass1)
        self.assertIn("FileExists::Replace", pass1)
        self.assertIn("FileExists::Error", pass1)

        # Assert second pass is a strict NO-OP (idempotent)
        pass2 = _apply_known_rector_and_deprecations(pass1)
        self.assertEqual(pass1, pass2)

        # Assert a third pass remains identical
        pass3 = _apply_known_rector_and_deprecations(pass2)
        self.assertEqual(pass2, pass3)

    def test_inspect_obsolete_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "name": "test/site",
                "require": {
                    "drupal/core": "^10",
                    "drupal/installed_mod": "^1.0",
                    "drupal/uninstalled_mod": "^2.0",
                },
            }
            write(root / "composer.json", manifest)

            res = inspect_obsolete_packages(root, active_extensions=["installed_mod"])
            self.assertEqual(res["status"], "passed")
            self.assertEqual(res["uninstalledCount"], 1)
            self.assertEqual(res["uninstalledPackages"][0]["package"], "drupal/uninstalled_mod")
            self.assertEqual(res["recommendedCommand"], "composer remove drupal/uninstalled_mod")

    def test_scaffold_delivery_budget(self):
        cfg = {}
        b = scaffold_delivery_budget(cfg)
        self.assertEqual(b["targetHours"], 20)
        self.assertEqual(b["checkpointHours"], 3)
        self.assertEqual(b["reportedHumanHours"], 2.5)
        self.assertIn("forecast", b)
        self.assertEqual(b["forecast"]["scope"], "total")
        self.assertLessEqual(b["forecast"]["highHours"], 20)


if __name__ == "__main__":
    unittest.main()
