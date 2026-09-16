"""Unit tests for runtime profile loading and validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from d11.common import Problem
from d11.profile import (
    DEFAULT_PROFILE,
    get_profile,
    list_profiles,
    load_profile,
    validate_profile_database,
    validate_profile_php,
)
from d11.source_runtime import inspect


class ProfileTests(unittest.TestCase):
    def test_load_default_profile(self):
        p = load_profile()
        self.assertEqual(p["id"], "docksal-php83")
        self.assertEqual(p["php"]["version"], "8.3")
        self.assertEqual(p["database"]["version"], "10.11")
        self.assertEqual(p["images"]["cli"], "docksal/cli:php8.3-3.12")
        self.assertEqual(p["images"]["db"], "docksal/mariadb:10.11-1.4")
        self.assertEqual(p["images"]["web"], "docksal/apache:2.4-2.5")
        self.assertIn("Managed Docksal images", p["destination"])

    def test_list_and_get_profiles(self):
        profiles = list_profiles()
        self.assertIn("docksal-php83", profiles)
        self.assertIn("docksal-php82", profiles)

        p83 = get_profile("docksal-php83")
        self.assertEqual(p83["id"], "docksal-php83")

        p82 = get_profile("docksal-php82.json")
        self.assertEqual(p82["id"], "docksal-php82")
        self.assertEqual(p82["php"]["version"], "8.2")

        # None defaults to DEFAULT_PROFILE
        self.assertEqual(get_profile(None)["id"], DEFAULT_PROFILE)

    def test_unknown_profile_raises_informative_error(self):
        with self.assertRaises(Problem) as ctx:
            load_profile("nonexistent-profile")
        self.assertIn("Unknown runtime profile 'nonexistent-profile'", str(ctx.exception))
        self.assertIn("Available profiles:", str(ctx.exception))

    def test_php_validation(self):
        p83 = get_profile("docksal-php83")
        # Valid source versions
        validate_profile_php(p83, "8.1.20")
        validate_profile_php(p83, "8.2.14")
        validate_profile_php(p83, "8.3.3")

        # Invalid versions
        with self.assertRaises(Problem) as ctx:
            validate_profile_php(p83, "7.4.33")
        self.assertIn("Source PHP version is unsupported by the managed PHP 8.3 destination: 7.4.33", str(ctx.exception))

        p82 = get_profile("docksal-php82")
        validate_profile_php(p82, "8.1.20")
        validate_profile_php(p82, "8.2.14")
        with self.assertRaises(Problem) as ctx:
            validate_profile_php(p82, "8.3.3")
        self.assertIn("Source PHP version is unsupported by the managed PHP 8.2 destination: 8.3.3", str(ctx.exception))

    def test_database_validation(self):
        p83 = get_profile("docksal-php83")
        # Supported MariaDB versions
        validate_profile_database(p83, "10.11.7-MariaDB")
        validate_profile_database(p83, "10.4.12-MariaDB")

        # Supported MySQL 8.0+ and Percona versions
        validate_profile_database(p83, "8.0.40")
        validate_profile_database(p83, "8.0.32-MySQL")
        validate_profile_database(p83, "8.0.35-Percona Server")
        validate_profile_database(p83, "8.4.0")

        # Unsupported MySQL version (5.5 is unsupported)
        with self.assertRaises(Problem) as ctx:
            validate_profile_database(p83, "5.5.44-MySQL")
        self.assertIn("Source database version is unsupported by the managed destination: 5.5.44-MySQL", str(ctx.exception))

        # Unsupported MariaDB version (10.2 is unsupported)
        with self.assertRaises(Problem) as ctx:
            validate_profile_database(p83, "10.2.36-MariaDB")
        self.assertIn("Source database version is unsupported by the managed destination: 10.2.36-MariaDB", str(ctx.exception))

        # Unsupported database engine
        with self.assertRaises(Problem) as ctx:
            validate_profile_database(p83, "PostgreSQL 16.2")
        self.assertIn("Automatic destination currently supports MariaDB, MySQL sources; detected PostgreSQL 16.2", str(ctx.exception))

    def test_inspect_negative_profile_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td).resolve()
            for name in (
                "composer.json",
                "composer.lock",
                "vendor/autoload.php",
                "web/core/lib/Drupal.php",
            ):
                p = src / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("{}")
            (src / "web/sites/default/files").mkdir(parents=True)
            (src / ".docksal").mkdir()

            objects = [
                {
                    "Id": "app",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "cli",
                            "com.docker.compose.project": "example",
                        },
                        "Env": ["VIRTUAL_HOST=example.docksal.site", "MYSQL_DATABASE=db"],
                    },
                    "Mounts": [{"Type": "bind", "Source": str(src), "Destination": "/var/www"}],
                    "NetworkSettings": {"Networks": {"test": {"Aliases": ["cli"]}}},
                },
                {
                    "Id": "db",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "db",
                            "com.docker.compose.project": "example",
                        },
                    },
                    "NetworkSettings": {"Networks": {"test": {"Aliases": ["db"]}}},
                },
                {
                    "Id": "web",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "web",
                            "com.docker.compose.project": "example",
                        },
                    },
                    "NetworkSettings": {
                        "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8097"}]},
                    },
                },
            ]

            from d11.common import file_hash

            def probe(argv):
                if "hash_file" in " ".join(argv):
                    return file_hash(src / argv[-1])
                if "status" in argv:
                    return json.dumps(
                        {
                            "root": "/var/www/web",
                            "site": "sites/default",
                            "db-hostname": "db",
                            "db-name": "db",
                            "db-password": "PASS",
                            "php-version": "8.3.15",
                            "files": "sites/default/files",
                        }
                    )
                return "10.11.7-MariaDB"

            # Passing docksal-php83 succeeds
            with (
                patch("d11.source_runtime.containers", return_value=objects),
                patch("d11.source_runtime.output", side_effect=probe),
            ):
                res = inspect(src, "fin", "http://127.0.0.1:8097", profile="docksal-php83")
                self.assertEqual(res["phpVersion"], "8.3.15")
                self.assertEqual(res["destination"], "Managed Docksal images · PHP 8.3 / MariaDB 10.11")

            # Deliberate mismatch: running PHP 8.3 under docksal-php82 profile must block informatively
            with (
                patch("d11.source_runtime.containers", return_value=objects),
                patch("d11.source_runtime.output", side_effect=probe),
            ):
                with self.assertRaises(Problem) as ctx:
                    inspect(src, "fin", "http://127.0.0.1:8097", profile="docksal-php82")
                self.assertIn("Source PHP version is unsupported by the managed PHP 8.2 destination: 8.3.15", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
