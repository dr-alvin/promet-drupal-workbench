"""Unit tests for Drupal trusted_host_patterns management."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from d11.trusted_hosts import (
    MARKER_END,
    MARKER_START,
    check_trusted_hosts,
    detect_settings_file,
    ensure_trusted_hosts,
    extract_host_patterns,
    generate_trusted_hosts_block,
)


class TrustedHostsTests(unittest.TestCase):
    def test_extract_host_patterns_docksal_with_port(self):
        url = "http://prometweb-upgrade-test-new.docksal.site:8085"
        patterns = extract_host_patterns(url, wrapper="fin")
        self.assertIn(r"^prometweb-upgrade-test-new\.docksal\.site$", patterns)
        self.assertIn(r"^.+\.docksal\.site$", patterns)
        self.assertIn(r"^.+\.docksal$", patterns)
        self.assertIn(r"^localhost$", patterns)
        self.assertIn(r"^127\.0\.0\.1$", patterns)
        self.assertIn(r"^web$", patterns)
        self.assertIn(r"^appserver$", patterns)

    def test_extract_host_patterns_ddev(self):
        url = "https://client-project.ddev.site"
        patterns = extract_host_patterns(url, wrapper="ddev")
        self.assertIn(r"^client-project\.ddev\.site$", patterns)
        self.assertIn(r"^.+\.ddev\.site$", patterns)
        self.assertIn(r"^localhost$", patterns)

    def test_extract_host_patterns_lando(self):
        url = "https://client-project.lndo.site"
        patterns = extract_host_patterns(url, wrapper="lando")
        self.assertIn(r"^client-project\.lndo\.site$", patterns)
        self.assertIn(r"^.+\.lndo\.site$", patterns)

    def test_extract_host_patterns_localhost(self):
        url = "http://localhost:8080"
        patterns = extract_host_patterns(url, wrapper="local")
        self.assertIn(r"^localhost$", patterns)
        self.assertIn(r"^127\.0\.0\.1$", patterns)
        self.assertIn(r"^web$", patterns)

    def test_detect_settings_file_with_existing_local(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            local_php = sites_def / "settings.local.php"
            local_php.write_text("<?php // local\n")

            target, kind = detect_settings_file(root, "web")
            self.assertEqual(target.resolve(), local_php.resolve())
            self.assertEqual(kind, "local")

    def test_detect_settings_file_with_include_in_main(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            main_php = sites_def / "settings.php"
            main_php.write_text("<?php\ninclude $app_root . '/sites/default/settings.local.php';\n")

            target, kind = detect_settings_file(root, "web")
            self.assertEqual(target.resolve(), (sites_def / "settings.local.php").resolve())
            self.assertEqual(kind, "local")

    def test_detect_settings_file_main_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            main_php = sites_def / "settings.php"
            main_php.write_text("<?php\n// No local settings include\n")

            target, kind = detect_settings_file(root, "web")
            self.assertEqual(target.resolve(), main_php.resolve())
            self.assertEqual(kind, "main")

    def test_ensure_trusted_hosts_creates_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            main_php = sites_def / "settings.php"
            main_php.write_text("<?php\ninclude $site_path . '/settings.local.php';\n")

            res = ensure_trusted_hosts(root, "http://myproject.docksal.site:8085", "web", "fin")
            self.assertEqual(res["status"], "created")
            local_php = sites_def / "settings.local.php"
            self.assertTrue(local_php.is_file())
            content = local_php.read_text()
            self.assertIn(MARKER_START, content)
            self.assertIn(MARKER_END, content)
            self.assertIn(r"$settings['trusted_host_patterns'][] = '^myproject\.docksal\.site$';", content)

    def test_ensure_trusted_hosts_appends_to_existing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            local_php = sites_def / "settings.local.php"
            local_php.write_text("<?php\n$databases['default']['default'] = [];\n")

            res = ensure_trusted_hosts(root, "http://myproject.docksal.site:8085", "web", "fin")
            self.assertEqual(res["status"], "updated")
            content = local_php.read_text()
            self.assertIn("$databases['default']['default'] = [];", content)
            self.assertIn(MARKER_START, content)
            self.assertIn(r"^myproject\.docksal\.site$", content)

    def test_ensure_trusted_hosts_idempotent_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            local_php = sites_def / "settings.local.php"
            local_php.write_text("<?php\n$databases['default']['default'] = [];\n")

            # First run
            res1 = ensure_trusted_hosts(root, "http://first.docksal.site", "web", "fin")
            self.assertEqual(res1["status"], "updated")
            self.assertIn(r"^first\.docksal\.site$", local_php.read_text())

            # Second run with different URL replaces the marked block
            res2 = ensure_trusted_hosts(root, "http://second.docksal.site", "web", "fin")
            self.assertEqual(res2["status"], "updated")
            content = local_php.read_text()
            self.assertIn(r"^second\.docksal\.site$", content)
            # The marked block should appear exactly once
            self.assertEqual(content.count(MARKER_START), 1)
            self.assertEqual(content.count(MARKER_END), 1)

    def test_check_trusted_hosts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            local_php = sites_def / "settings.local.php"
            local_php.write_text(
                "<?php\n$settings['trusted_host_patterns'] = ['^example\\.com$', '^.+\\.docksal\\.site$'];\n"
            )

            # Matching host
            check1 = check_trusted_hosts(root, "http://promet.docksal.site:8080", "web")
            self.assertTrue(check1["configured"])
            self.assertTrue(check1["matches"])

            # Non-matching host
            check2 = check_trusted_hosts(root, "http://unrelated-domain.org", "web")
            self.assertTrue(check2["configured"])
            self.assertFalse(check2["matches"])

    def test_read_only_permission_handling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sites_def = root / "web/sites/default"
            sites_def.mkdir(parents=True)
            local_php = sites_def / "settings.local.php"
            local_php.write_text("<?php\n")

            # Make file and directory read-only
            os.chmod(local_php, 0o444)
            os.chmod(sites_def, 0o555)

            try:
                res = ensure_trusted_hosts(root, "http://myproject.docksal.site", "web", "fin")
                self.assertEqual(res["status"], "updated")
                self.assertIn(r"^myproject\.docksal\.site$", local_php.read_text())
            finally:
                os.chmod(sites_def, 0o777)
                os.chmod(local_php, 0o666)

    def test_discover_project_hostnames_docksal_multi_vhost(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docksal_dir = root / ".docksal"
            docksal_dir.mkdir()
            (docksal_dir / "docksal.env").write_text(
                "VIRTUAL_HOST=\"acme.docksal.site,acme-alias.docksal.site,acme.test\"\n"
            )
            from d11.trusted_hosts import discover_project_hostnames
            hosts = discover_project_hostnames(root, "http://acme.docksal.site:8080")
            self.assertIn("acme.docksal.site", hosts)
            self.assertIn("acme-alias.docksal.site", hosts)
            self.assertIn("acme.test", hosts)

    def test_discover_project_hostnames_ddev(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ddev_dir = root / ".ddev"
            ddev_dir.mkdir()
            (ddev_dir / "config.yaml").write_text(
                "name: cool-project\nadditional_hostnames: [cool-alias]\nadditional_fqdns: [custom.cool.org]\n"
            )
            from d11.trusted_hosts import discover_project_hostnames
            hosts = discover_project_hostnames(root)
            self.assertIn("cool-project.ddev.site", hosts)
            self.assertIn("cool-alias.ddev.site", hosts)
            self.assertIn("custom.cool.org", hosts)

    def test_discover_project_hostnames_lando(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".lando.yml").write_text(
                "name: lando-app\nproxy:\n  appserver:\n    - custom.app.test\n"
            )
            from d11.trusted_hosts import discover_project_hostnames
            hosts = discover_project_hostnames(root)
            self.assertIn("lando-app.lndo.site", hosts)
            self.assertIn("custom.app.test", hosts)

    def test_custom_domain_wildcards(self):
        patterns = extract_host_patterns("https://dev.client-site.org")
        self.assertIn(r"^dev\.client-site\.org$", patterns)
        self.assertIn(r"^.+\.client-site\.org$", patterns)


if __name__ == "__main__":
    unittest.main()
