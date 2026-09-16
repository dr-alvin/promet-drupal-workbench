import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.drupal_health import (  # noqa: E402
    composer_source_fallbacks,
    parse_requirements,
    requirement_errors,
    source_install_findings,
)

# Verbatim shape of Composer 2 output when a dist download fails. Reproducing it
# exactly matters: the detector is only useful if it matches what Composer
# actually prints.
COMPOSER_FALLBACK = """
  - Downloading drupal/blazy (3.0.19)
    Failed to download drupal/blazy from dist: The "https://ftp.drupal.org/files/projects/blazy-3.0.19.zip" file could not be downloaded (HTTP/2 502)
    Now trying to download from source
  - Installing drupal/blazy (3.0.19): Cloning 6a1f2c3d4e
  - Installing drupal/admin_toolbar (3.6.3): Extracting archive
"""

CLEAN_INSTALL = """
  - Installing drupal/admin_toolbar (3.6.3): Extracting archive
  - Installing drupal/token (1.15.0): Extracting archive
Generating autoload files
"""

PACKAGED_INFO = """name: Blazy
type: module
core_version_requirement: '>=9.4'

# Information added by Drupal.org packaging script on 2026-08-26
version: '3.0.19'
project: 'blazy'
datestamp: 1787772245
"""

SOURCE_INFO = """name: Blazy
type: module
core_version_requirement: '>=9.4'
dependencies:
  - drupal:filter
"""


class ComposerFallbackTests(unittest.TestCase):
    def test_detects_dist_failure_and_clone(self):
        self.assertEqual(composer_source_fallbacks(COMPOSER_FALLBACK), ["drupal/blazy"])

    def test_clean_install_reports_nothing(self):
        self.assertEqual(composer_source_fallbacks(CLEAN_INSTALL), [])

    def test_clone_alone_is_enough(self):
        # However the fallback was reached, a clone means no packaging metadata.
        text = "  - Installing drupal/slick (3.0.8): Cloning abc123"
        self.assertEqual(composer_source_fallbacks(text), ["drupal/slick"])

    def test_multiple_packages_deduplicated_and_sorted(self):
        text = COMPOSER_FALLBACK + "\n  - Installing drupal/slick (3.0.8): Cloning abc"
        self.assertEqual(composer_source_fallbacks(text), ["drupal/blazy", "drupal/slick"])

    def test_empty_and_none_are_safe(self):
        self.assertEqual(composer_source_fallbacks(""), [])
        self.assertEqual(composer_source_fallbacks(None), [])

    def test_non_package_tokens_are_ignored(self):
        # Only vendor/package names count; bare words are noise.
        self.assertEqual(composer_source_fallbacks("Failed to download thing from dist"), [])


class SourceInstallTests(unittest.TestCase):
    def _site(self, modules):
        root = Path(tempfile.mkdtemp())
        base = root / "web" / "modules" / "contrib"
        for name, content in modules.items():
            d = base / name
            d.mkdir(parents=True)
            (d / f"{name}.info.yml").write_text(content)
        return root

    def test_packaged_module_is_clean(self):
        root = self._site({"blazy": PACKAGED_INFO})
        self.assertEqual(source_install_findings(root), [])

    def test_source_module_is_flagged(self):
        root = self._site({"blazy": SOURCE_INFO, "admin_toolbar": PACKAGED_INFO})
        findings = source_install_findings(root)
        self.assertEqual([f["extension"] for f in findings], ["blazy"])
        self.assertIn("version", findings[0]["reason"])

    def test_git_checkout_is_recorded(self):
        root = self._site({"blazy": SOURCE_INFO})
        (root / "web/modules/contrib/blazy/.git").mkdir()
        self.assertTrue(source_install_findings(root)[0]["fromGitSource"])

    def test_bare_version_key_counts_even_without_packaging_footer(self):
        # Some modules legitimately carry version without the footer.
        root = self._site({"custom_ish": "name: X\nversion: '1.2.3'\n"})
        self.assertEqual(source_install_findings(root), [])

    def test_missing_contrib_directory_is_not_an_error(self):
        self.assertEqual(source_install_findings(Path(tempfile.mkdtemp())), [])


class RequirementsTests(unittest.TestCase):
    def test_parses_keyed_object_and_bare_list(self):
        keyed = json.dumps({"a": {"title": "A", "severity": 2}})
        listed = json.dumps([{"title": "A", "severity": 2}])
        self.assertEqual(len(parse_requirements(keyed)), 1)
        self.assertEqual(len(parse_requirements(listed)), 1)

    def test_empty_and_malformed_output_yield_nothing(self):
        for raw in ("", "   ", "not json", "[]"):
            self.assertEqual(parse_requirements(raw), [])

    def test_only_errors_are_returned(self):
        entries = [
            {"title": "Unresolved dependency", "value": "Blazy", "severity": 2},
            {"title": "Cron", "value": "Last run 5 days ago", "severity": 1},
            {"title": "Fine", "value": "OK", "severity": 0},
        ]
        errors = requirement_errors(entries)
        self.assertEqual([e["title"] for e in errors], ["Unresolved dependency"])

    def test_textual_severity_labels_are_understood(self):
        entries = [
            {"title": "Broken", "value": "x", "severity": "Error"},
            {"title": "Meh", "value": "y", "severity": "Warning"},
        ]
        self.assertEqual([e["title"] for e in requirement_errors(entries)], ["Broken"])

    def test_uninterpretable_severity_does_not_manufacture_a_blocker(self):
        entries = [{"title": "Odd", "value": "x", "severity": {"weird": True}}]
        self.assertEqual(requirement_errors(entries), [])

    def test_html_is_stripped_from_descriptions(self):
        entries = [
            {
                "title": "Unresolved dependency",
                "value": "Blazy",
                "severity": 2,
                "description": "<em>Provus</em> requires <a href='#'>this</a>.",
            }
        ]
        self.assertEqual(requirement_errors(entries)[0]["description"], "Provus requires this.")


class SeveritySplitTests(unittest.TestCase):
    """Missing metadata must not block a site Drupal reports as healthy.

    A version key is only required once some extension declares a constrained
    dependency on the affected one. Drupal's requirements report is the
    authority on whether that has happened; blocking on the metadata alone
    fails healthy sites.
    """

    def _run(self, tmp, requirements_json, modules):
        from unittest.mock import patch

        import d11lib.drupal_health as dh

        base = Path(tmp) / "web" / "modules" / "contrib"
        for name, content in modules.items():
            d = base / name
            d.mkdir(parents=True)
            (d / f"{name}.info.yml").write_text(content)
        cfg = {"runtime": {"wrapper": "local"}, "roots": {"drupal": "web"}, "site": {"uri": ""}}
        with patch.object(
            dh, "command", return_value={"exitCode": 0, "stdout": requirements_json, "argv": []}
        ):
            return dh.check(cfg, tmp)

    def test_metadata_gap_alone_is_a_warning_not_a_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(tmp, "[]", {"blazy": SOURCE_INFO})
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["blockers"], [])
            self.assertEqual(len(result["warnings"]), 1)
            self.assertIn("composer reinstall --prefer-dist drupal/blazy", result["warnings"][0])

    def test_drupal_errors_block(self):
        payload = json.dumps(
            [{"title": "Unresolved dependency", "value": "Blazy (>= 4.x)", "severity": 2}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(tmp, payload, {"blazy": PACKAGED_INFO})
            self.assertEqual(result["status"], "failed")
            self.assertIn("Unresolved dependency", result["blockers"][0])

    def test_failed_probe_is_unknown_not_passed(self):
        # Absence of evidence must never read as evidence of health.
        from unittest.mock import patch

        import d11lib.drupal_health as dh

        cfg = {"runtime": {"wrapper": "local"}, "roots": {"drupal": "web"}, "site": {"uri": ""}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                dh, "command", return_value={"exitCode": 1, "stderr": "drush missing", "argv": []}
            ):
                result = dh.check(cfg, tmp)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("unverified", result["message"])


if __name__ == "__main__":
    unittest.main()
