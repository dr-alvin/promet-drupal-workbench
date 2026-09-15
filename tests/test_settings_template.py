"""Unit tests for settings.php template rendering."""

from __future__ import annotations

import unittest

from d11.settings_template import DEFAULT_CONFIG_OVERRIDES, render_settings_php


class SettingsTemplateTests(unittest.TestCase):
    def test_default_rendering_matches_golden_literal(self):
        project = "d11-prometweb-test"
        salt = "4f8a3c2b1e0d" * 5 + "1234"
        config_sync = "config/sync"

        # The literal previously hardcoded in provision.py:157
        expected = (
            "<?php\n$databases['default']['default']=['driver'=>'mysql','namespace'=>'Drupal\\\\mysql\\\\Driver\\\\Database\\\\mysql','autoload'=>'core/modules/mysql/src/Driver/Database/mysql/','database'=>getenv('MYSQL_DATABASE'),'username'=>getenv('MYSQL_USER'),'password'=>getenv('MYSQL_PASSWORD'),'host'=>'db','port'=>3306];\n"
            + "$settings['hash_salt']='"
            + salt
            + "';\n"
            + "$settings['trusted_host_patterns']=['^127\\.0\\.0\\.1$','^"
            + project
            + "$'];\n"
            + "$settings['config_sync_directory']='/var/www/"
            + config_sync
            + "';\n"
            + "$config['automated_cron.settings']['interval']=0;\n$config['system.mail']['interface']['default']='test_mail_collector';\n$config['mailsystem.settings']['defaults']['sender']='test_mail_collector';\n$config['smtp.settings']['smtp_on']=FALSE;\n$config['pantheon_advanced_page_cache.settings']['surrogate_key_header_limit']=4096;\n"
        )

        rendered = render_settings_php(
            project=project,
            drupal_root="web",
            config_sync_directory=config_sync,
            hash_salt=salt,
            config_overrides=None,
        )

        self.assertEqual(rendered, expected)

    def test_custom_overrides_omits_smtp(self):
        project = "d11-clean-client"
        salt = "abcdef123456"

        custom_overrides = [
            "$config['automated_cron.settings']['interval']=0;",
            "$config['system.mail']['interface']['default']='test_mail_collector';",
        ]

        rendered = render_settings_php(
            project=project,
            drupal_root="web",
            config_sync_directory="config/default",
            hash_salt=salt,
            config_overrides=custom_overrides,
        )

        self.assertIn("$config['automated_cron.settings']['interval']=0;", rendered)
        self.assertIn("$config['system.mail']['interface']['default']='test_mail_collector';", rendered)
        self.assertNotIn("smtp", rendered)
        self.assertNotIn("mailsystem", rendered)
        self.assertNotIn("pantheon_advanced_page_cache", rendered)

    def test_dict_overrides(self):
        project = "d11-dict-client"
        salt = "123456abcdef"

        rendered = render_settings_php(
            project=project,
            drupal_root="web",
            config_sync_directory="config/sync",
            hash_salt=salt,
            config_overrides={
                "system.logging": {"error_level": "verbose"},
                "system.performance": {"css.preprocess": False},
            },
        )

        self.assertIn("$config['system.logging']='{'error_level': 'verbose'}';", rendered)
        self.assertNotIn("smtp", rendered)

    def test_private_files_path(self):
        rendered = render_settings_php(
            project="d11-test",
            drupal_root="web",
            config_sync_directory="config/sync",
            hash_salt="salt",
            private_files_path="/var/private",
        )
        self.assertIn("$settings['file_private_path']='/var/private';\n", rendered)


if __name__ == "__main__":
    unittest.main()
