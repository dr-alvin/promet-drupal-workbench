from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from d11.common import load_config, read, schema, write
from d11.init_project import (
    generate_project_config,
    init_cli,
    init_project,
    probe_config_root,
    probe_core_version,
    probe_custom_roots,
    probe_db_credentials,
    probe_drupal_root,
    probe_project,
    probe_runtime,
)


class InitProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_probe_drupal_root(self):
        # 1. web directory with core/lib/Drupal.php
        (self.root / "web/core/lib").mkdir(parents=True)
        (self.root / "web/core/lib/Drupal.php").write_text("<?php\n")
        self.assertEqual(probe_drupal_root(self.root), "web")

        # 2. docroot directory
        docroot_proj = self.root / "docroot_proj"
        (docroot_proj / "docroot/sites").mkdir(parents=True)
        self.assertEqual(probe_drupal_root(docroot_proj), "docroot")

        # 3. composer.json scaffold override
        scaffold_proj = self.root / "scaffold_proj"
        scaffold_proj.mkdir(parents=True)
        (scaffold_proj / "custom_web").mkdir(parents=True)
        (scaffold_proj / "composer.json").write_text(
            json.dumps({"extra": {"drupal-scaffold": {"locations": {"web-root": "custom_web/"}}}})
        )
        self.assertEqual(probe_drupal_root(scaffold_proj), "custom_web")

        # 4. Flat / root structure
        flat_proj = self.root / "flat_proj"
        flat_proj.mkdir(parents=True)
        (flat_proj / "index.php").write_text("<?php\n")
        self.assertEqual(probe_drupal_root(flat_proj), ".")

    def test_probe_core_version(self):
        proj = self.root / "version_proj"
        proj.mkdir(parents=True)

        # 1. From composer.lock (drupal/core-recommended)
        (proj / "composer.lock").write_text(
            json.dumps({"packages": [{"name": "drupal/core-recommended", "version": "v10.3.2"}]})
        )
        self.assertEqual(probe_core_version(proj, "web"), "10.3.2")

        # 2. From core/lib/Drupal.php
        (proj / "composer.lock").unlink()
        (proj / "web/core/lib").mkdir(parents=True)
        (proj / "web/core/lib/Drupal.php").write_text(
            "<?php\nclass Drupal {\n  const VERSION = '10.2.7';\n}\n"
        )
        self.assertEqual(probe_core_version(proj, "web"), "10.2.7")

        # 3. From composer.json
        (proj / "web/core/lib/Drupal.php").unlink()
        (proj / "composer.json").write_text(json.dumps({"require": {"drupal/core": "^10.3"}}))
        self.assertEqual(probe_core_version(proj, "web"), "^10.3")

    def test_probe_custom_roots(self):
        proj = self.root / "custom_proj"
        (proj / "web/modules/custom").mkdir(parents=True)
        (proj / "web/themes/custom").mkdir(parents=True)
        (proj / "web/profiles/custom").mkdir(parents=True)

        roots = probe_custom_roots(proj, "web")
        self.assertEqual(
            roots,
            ["web/modules/custom", "web/themes/custom", "web/profiles/custom"],
        )

        # When directories don't exist yet on disk
        empty_proj = self.root / "empty_proj"
        empty_proj.mkdir(parents=True)
        self.assertEqual(
            probe_custom_roots(empty_proj, "web"),
            ["web/modules/custom", "web/themes/custom"],
        )
        self.assertEqual(
            probe_custom_roots(empty_proj, "."),
            ["modules/custom", "themes/custom"],
        )

    def test_probe_config_root(self):
        proj = self.root / "config_proj"
        (proj / "config/sync").mkdir(parents=True)
        (proj / "config/sync/core.extension.yml").write_text("module: {}\n")
        self.assertEqual(probe_config_root(proj, "web"), "config/sync")

        # From settings.php
        proj2 = self.root / "config_proj2"
        (proj2 / "web/sites/default").mkdir(parents=True)
        (proj2 / "custom_config_dir").mkdir(parents=True)
        (proj2 / "web/sites/default/settings.php").write_text(
            "$settings['config_sync_directory'] = '../custom_config_dir';"
        )
        self.assertEqual(probe_config_root(proj2, "web"), "custom_config_dir")

    def test_probe_db_credentials(self):
        # DDEV default
        proj = self.root / "ddev_proj"
        proj.mkdir()
        db = probe_db_credentials(proj, "web", "ddev")
        self.assertEqual(db["host"], "db")
        self.assertEqual(db["database"], "db")
        self.assertEqual(db["username"], "db")

        # Docksal with docksal.env
        fin_proj = self.root / "fin_proj"
        (fin_proj / ".docksal").mkdir(parents=True)
        (fin_proj / ".docksal/docksal.env").write_text(
            "MYSQL_DATABASE=myclient_db\nMYSQL_USER=user1\n"
        )
        db_fin = probe_db_credentials(fin_proj, "web", "fin")
        self.assertEqual(db_fin["database"], "myclient_db")
        self.assertEqual(db_fin["username"], "user1")

        # Parsed from settings.php
        settings_proj = self.root / "settings_proj"
        (settings_proj / "web/sites/default").mkdir(parents=True)
        (settings_proj / "web/sites/default/settings.local.php").write_text("""<?php
$databases['default']['default'] = [
  'database' => 'custom_site_db',
  'username' => 'custom_user',
  'password' => 'secret',
  'host' => 'mariadb.internal',
  'port' => '3307',
  'driver' => 'mysql',
];
""")
        db_custom = probe_db_credentials(settings_proj, "web", "local")
        self.assertEqual(db_custom["host"], "mariadb.internal")
        self.assertEqual(db_custom["database"], "custom_site_db")
        self.assertEqual(db_custom["username"], "custom_user")
        self.assertEqual(db_custom["port"], 3307)

    def test_probe_runtime(self):
        # DDEV
        ddev_proj = self.root / "ddev_site"
        (ddev_proj / ".ddev").mkdir(parents=True)
        (ddev_proj / ".ddev/config.yaml").write_text("name: ddev-site\nproject_tld: ddev.site\n")
        (ddev_proj / "composer.json").write_text('{"name": "test/site"}')
        r = probe_runtime(ddev_proj)
        self.assertEqual(r["wrapper"], "ddev")
        self.assertEqual(r["name"], "ddev-site")
        self.assertEqual(r["sourceUrl"], "https://ddev-site.ddev.site")

        # Docksal
        fin_proj = self.root / "fin_site"
        (fin_proj / ".docksal").mkdir(parents=True)
        (fin_proj / ".docksal/docksal.env").write_text("VIRTUAL_HOST=fin-site.docksal.site\n")
        (fin_proj / "composer.json").write_text('{"name": "test/site"}')
        r_fin = probe_runtime(fin_proj)
        self.assertEqual(r_fin["wrapper"], "fin")
        self.assertEqual(r_fin["sourceUrl"], "http://fin-site.docksal.site")

        # Lando
        lando_proj = self.root / "lando_site"
        lando_proj.mkdir(parents=True)
        (lando_proj / ".lando.yml").write_text("name: lando-app\nrecipe: drupal10\n")
        (lando_proj / "composer.json").write_text('{"name": "test/site"}')
        r_lando = probe_runtime(lando_proj)
        self.assertEqual(r_lando["wrapper"], "lando")
        self.assertEqual(r_lando["sourceUrl"], "https://lando-app.lndo.site")

    def test_generate_project_config_and_schema_validation(self):
        # Create full mock Drupal project
        mock_proj = self.root / "my-client-site"
        (mock_proj / "web/core/lib").mkdir(parents=True)
        (mock_proj / "web/core/lib/Drupal.php").write_text(
            "<?php\nclass Drupal { const VERSION = '10.3.1'; }\n"
        )
        (mock_proj / "web/modules/custom").mkdir(parents=True)
        (mock_proj / "web/themes/custom").mkdir(parents=True)
        (mock_proj / "config/sync").mkdir(parents=True)
        (mock_proj / "config/sync/core.extension.yml").write_text("module: {}\n")
        (mock_proj / ".ddev").mkdir(parents=True)
        (mock_proj / ".ddev/config.yaml").write_text("name: my-client-site\n")
        (mock_proj / "composer.json").write_text('{"name": "client/site"}')
        (mock_proj / "composer.lock").write_text(
            json.dumps({"packages": [{"name": "drupal/core-recommended", "version": "v10.3.1"}]})
        )

        probe = probe_project(mock_proj)
        self.assertEqual(probe["drupal_root"], "web")
        self.assertEqual(probe["core_version"], "10.3.1")
        self.assertEqual(probe["wrapper"], "ddev")
        self.assertEqual(probe["config_root"], "config/sync")

        # Generate config
        out_file = mock_proj / "project.json"
        cfg = generate_project_config(probe, out_file)
        self.assertEqual(cfg["schemaVersion"], "1.0")
        self.assertEqual(cfg["repository"], ".")
        self.assertEqual(cfg["environment"]["id"], "my-client-site-local")
        self.assertEqual(cfg["site"]["uri"], "https://my-client-site.ddev.site")
        self.assertEqual(cfg["roots"]["drupal"], "web")
        self.assertEqual(cfg["roots"]["config"], "config/sync")
        self.assertEqual(cfg["runtime"]["wrapper"], "ddev")

        # Validate with schema
        schema(cfg, "project")

        # Validate with load_config
        write(out_file, cfg)
        loaded = load_config(out_file)
        self.assertEqual(loaded["_root"], str(mock_proj.resolve()))

    def test_init_project_end_to_end(self):
        site_dir = self.root / "sample-drupal"
        (site_dir / "web/modules/custom").mkdir(parents=True)
        (site_dir / "web/themes/custom").mkdir(parents=True)
        (site_dir / "composer.json").write_text('{"name": "test/drupal-app"}')

        res = init_project(target_path=site_dir, non_interactive=True)
        self.assertFalse(res["cancelled"])
        self.assertTrue(Path(res["output_path"]).is_file())

        cfg = read(Path(res["output_path"]))
        schema(cfg, "project")
        self.assertEqual(cfg["roots"]["drupal"], "web")

    def test_init_cli(self):
        site_dir = self.root / "cli-drupal"
        (site_dir / "web").mkdir(parents=True)
        (site_dir / "composer.json").write_text('{"name": "test/cli-app"}')

        # 1. Successful non-interactive init
        code = init_cli([str(site_dir), "-y"])
        self.assertEqual(code, 0)
        self.assertTrue((site_dir / "project.json").is_file())

        # 2. Custom output path
        custom_out = self.root / "custom_output/proj.json"
        code2 = init_cli([str(site_dir), "-y", "-o", str(custom_out)])
        self.assertEqual(code2, 0)
        self.assertTrue(custom_out.is_file())

        # 3. Non-existent path returns 1
        code3 = init_cli([str(self.root / "nonexistent_dir"), "-y"])
        self.assertEqual(code3, 1)

    def test_interactive_confirmation(self):
        site_dir = self.root / "interactive-drupal"
        (site_dir / "web").mkdir(parents=True)
        (site_dir / "composer.json").write_text('{"name": "test/interactive"}')

        # User confirms with 'y'
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="y"):
            res = init_project(target_path=site_dir, non_interactive=False)
            self.assertFalse(res["cancelled"])
            self.assertTrue((site_dir / "project.json").is_file())

        (site_dir / "project.json").unlink()

        # User cancels with 'n'
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"):
            res2 = init_project(target_path=site_dir, non_interactive=False)
            self.assertTrue(res2["cancelled"])
            self.assertFalse((site_dir / "project.json").is_file())


if __name__ == "__main__":
    unittest.main()

