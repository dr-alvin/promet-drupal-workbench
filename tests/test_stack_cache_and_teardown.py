"""Analysis stack cache reuse and detached teardown (Phase 2 speed, zero-findings-change)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.audit_tools import (  # noqa: E402
    ANALYSIS_PACKAGES,
    STACK_MANIFEST_MARKER,
    _analysis_manifest,
    _copy_analysis,
    _detach_teardown,
    _finish_previous_teardown,
    _stack_cache_available,
    _stack_cache_restore,
    _stack_cache_store,
)
from d11.common import digest, read, write  # noqa: E402

MANIFEST = {"require": {"drupal/core-recommended": "10.4.8"}, "require-dev": {"drupal/upgrade_status": "^4.3"}}


def _resolved_site(root):
    site = root / "site"
    (site / "vendor/bin").mkdir(parents=True)
    (site / "vendor/bin/rector").write_text("#!/bin/sh")
    (site / "vendor/autoload.php").write_text("<?php")
    (site / "composer.lock").write_text(json.dumps({"packages": [{"name": "drupal/upgrade_status"}]}))
    return site


class CopyAnalysisTests(unittest.TestCase):
    def _source(self, root):
        source = root / "source"
        (source / "vendor/composer").mkdir(parents=True)
        (source / "vendor/composer/installed.json").write_text("{}")
        (source / "web/modules/custom/x").mkdir(parents=True)
        (source / "web/modules/custom/x/x.module").write_text("<?php")
        return source

    def test_vendor_is_copied_by_default(self):
        # The delta-update path (application vendor present) is the one proven to work;
        # a fresh install of ~500 packages into an empty bind-mounted vendor/ failed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_analysis(self._source(root), root / "copy")
            self.assertTrue((root / "copy/vendor/composer/installed.json").is_file())
            self.assertTrue((root / "copy/web/modules/custom/x/x.module").is_file())

    def test_vendor_is_skipped_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_analysis(self._source(root), root / "copy", include_vendor=False)
            self.assertFalse((root / "copy/vendor").exists())
            self.assertTrue((root / "copy/web/modules/custom/x/x.module").is_file())


class AnalysisManifestTests(unittest.TestCase):
    def _source(self, root):
        source = root / "source"
        source.mkdir()
        (source / "composer.json").write_text(json.dumps({
            "require": {"drupal/core-recommended": "^10.3", "drupal/token": "^1.9", "phpstan/phpstan": "^1.12"},
            "require-dev": {"drupal/core-dev": "^10.3", "mglaman/drupal-check": "1.4"},
        }))
        (source / "composer.lock").write_text(json.dumps({
            "packages": [
                {"name": "drupal/core-recommended", "version": "10.4.8"},
                {"name": "drupal/token", "version": "1.15.0"},
                {"name": "cweagans/composer-patches", "version": "1.7.3", "type": "composer-plugin"},
            ],
            "packages-dev": [
                {"name": "drupal/core-dev", "require": {"phpunit/phpunit": "^9.6", "phpstan/phpstan": "^1.12"}},
            ],
        }))
        return source

    def test_manifest_is_derived_from_source_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source(Path(tmp))
            version, manifest = _analysis_manifest(source)
            self.assertEqual(version, "10.4.8")
            self.assertEqual(manifest["require"]["drupal/core-recommended"], "10.4.8")
            self.assertEqual(manifest["require"]["drupal/token"], "1.15.0")  # pinned to the lock
            self.assertEqual(manifest["require"]["composer/installers"], "^2.3")
            self.assertEqual(manifest["require-dev"]["phpunit/phpunit"], "^9.6")
            self.assertEqual(manifest["require-dev"]["drupal/upgrade_status"], "^4.3")
            for package in ANALYSIS_PACKAGES:
                self.assertNotIn(package, manifest["require"])
                self.assertNotIn(package, {k: v for k, v in manifest["require-dev"].items() if k not in ("drupal/upgrade_status", "palantirnet/drupal-rector")})
            self.assertEqual(manifest["config"]["allow-plugins"], {"cweagans/composer-patches": False, "composer/installers": True})
            self.assertEqual(digest(manifest), digest(_analysis_manifest(source)[1]))
            # The source itself is untouched.
            self.assertIn('"^10.3"', (source / "composer.json").read_text())

    def test_cache_available_requires_matching_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = _resolved_site(root)
            stack = root / "stack"
            self.assertFalse(_stack_cache_available(stack, MANIFEST))
            _stack_cache_store(site, stack, MANIFEST)
            self.assertTrue(_stack_cache_available(stack, MANIFEST))
            self.assertFalse(_stack_cache_available(stack, {**MANIFEST, "require": {"x/y": "1"}}))


class StackCacheTests(unittest.TestCase):
    def test_store_then_restore_is_a_hit_for_the_same_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = _resolved_site(root)
            stack = root / "stack"
            self.assertTrue(_stack_cache_store(site, stack, MANIFEST))
            self.assertEqual((stack / STACK_MANIFEST_MARKER).read_text().strip(), digest(MANIFEST))

            fresh = root / "fresh"
            fresh.mkdir()
            (fresh / "composer.lock").write_text("{}")  # the copy of the source lock
            self.assertTrue(_stack_cache_restore(fresh, stack, MANIFEST))
            self.assertTrue((fresh / "vendor/bin/rector").is_file())
            # The resolved lock replaces the source lock so `composer install` is a no-op.
            self.assertIn("drupal/upgrade_status", (fresh / "composer.lock").read_text())

    def test_restore_misses_when_manifest_differs_or_vendor_exists_or_cache_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = _resolved_site(root)
            stack = root / "stack"
            self.assertFalse(_stack_cache_restore(root / "nothing", stack, MANIFEST))
            _stack_cache_store(site, stack, MANIFEST)
            other = root / "other"
            other.mkdir()
            self.assertFalse(_stack_cache_restore(other, stack, {**MANIFEST, "require": {"x/y": "1"}}))
            self.assertFalse((other / "vendor").exists())
            occupied = root / "occupied"
            (occupied / "vendor").mkdir(parents=True)
            self.assertFalse(_stack_cache_restore(occupied, stack, MANIFEST))

    def test_store_refuses_incomplete_toolchains_and_keeps_other_stack_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stack = root / "stack"
            stack.mkdir()
            write(stack / "db_ready.json", {"importedAt": "x"})
            empty = root / "empty"
            empty.mkdir()
            self.assertFalse(_stack_cache_store(empty, stack, MANIFEST))
            site = _resolved_site(root)
            self.assertTrue(_stack_cache_store(site, stack, MANIFEST))
            self.assertTrue((stack / "db_ready.json").is_file())  # DB import marker survives


class DetachedTeardownTests(unittest.TestCase):
    def _runtime(self, root):
        runtime = root / "analysis/runtime"
        runtime.mkdir(parents=True)
        (runtime / "runtime.env").write_text("MYSQL_PASSWORD=secret\n")
        write(runtime / "compose.json", {
            "services": {
                "db": {"image": "mariadb", "env_file": [str(runtime / "runtime.env")]},
                "cli": {"image": "cli", "env_file": [str(runtime / "runtime.env")]},
            },
            "networks": {"backend": {}},
        })
        return runtime

    def test_detach_relocates_compose_and_records_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self._runtime(root)
            stack = root / "stack"
            with patch("d11.audit_tools._spawn_detached", return_value=777) as spawn:
                record = _detach_teardown(runtime, stack, "d11-stack-abc-0123456789")
            self.assertEqual(record["status"], "detached")
            self.assertEqual(record["pid"], 777)
            argv = spawn.call_args.args[0]
            self.assertEqual(argv[:3], ["docker", "compose", "-p"])
            self.assertEqual(argv[3], "d11-stack-abc-0123456789")
            self.assertEqual(argv[-2:], ["down", "--remove-orphans"])
            relocated = Path(argv[5])
            self.assertEqual(relocated.parent, stack / "teardown")
            compose = read(relocated)
            for service in compose["services"].values():
                self.assertEqual(service["env_file"], [str(stack / "teardown/runtime.env")])
            self.assertTrue((stack / "teardown/runtime.env").is_file())
            self.assertEqual(read(stack / "teardown/pending.json")["pid"], 777)

    def test_next_run_finishes_a_pending_teardown_once_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stack = root / "stack"
            self.assertIsNone(_finish_previous_teardown(stack))
            runtime = self._runtime(root)
            project = "d11-stack-prometwe-b7ec063294"
            with patch("d11.audit_tools._spawn_detached", return_value=1):
                _detach_teardown(runtime, stack, project)
            # Tamper with the stored argv: it must not be what gets executed.
            marker = stack / "teardown/pending.json"
            info = read(marker)
            info["argv"] = ["rm", "-rf", "/"]
            write(marker, info)
            with patch("d11.audit_tools.command", return_value={"status": "passed"}) as cmd:
                record = _finish_previous_teardown(stack)
            self.assertEqual(record["status"], "passed")
            self.assertEqual(record["project"], project)
            argv = cmd.call_args.args[0]
            self.assertEqual(argv[:4], ["docker", "compose", "-p", project])
            self.assertEqual(argv[-2:], ["down", "--remove-orphans"])
            # Relocated compose/env (which holds DB credentials) are gone afterwards.
            self.assertFalse((stack / "teardown").exists())
            self.assertIsNone(_finish_previous_teardown(stack))

    def test_unusable_pending_marker_never_fails_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = Path(tmp) / "stack"
            (stack / "teardown").mkdir(parents=True)
            (stack / "teardown/pending.json").write_text("{not json")
            with patch("d11.audit_tools.command") as cmd:
                record = _finish_previous_teardown(stack)
            cmd.assert_not_called()
            self.assertEqual(record["status"], "tool_failure")
            self.assertIn("error", record)
            self.assertFalse((stack / "teardown").exists())
            # A well-formed marker with a project name outside the expected shape is refused too.
            (stack / "teardown").mkdir(parents=True)
            write(stack / "teardown/pending.json", {"project": "evil; rm -rf /"})
            write(stack / "teardown/compose.json", {"services": {}})
            with patch("d11.audit_tools.command") as cmd:
                record = _finish_previous_teardown(stack)
            cmd.assert_not_called()
            self.assertEqual(record["status"], "tool_failure")


if __name__ == "__main__":
    unittest.main()
