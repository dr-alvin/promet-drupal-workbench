import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import read, schema, write
from d11.visual_audit import compose_project_name, derive_visual_config, image_tag
import visual_audit


class VisualAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_compose_project_name(self):
        # Explicit project
        self.assertEqual(compose_project_name(explicit_project="my-site"), "d11-my-site")
        self.assertEqual(compose_project_name(explicit_project="d11-already"), "d11-already")

        # From config
        self.assertEqual(compose_project_name({"projectId": "client_app"}), "d11-client_app")
        self.assertEqual(
            compose_project_name({"environment": {"id": "zero-copy-site"}}),
            "d11-zero-copy-site",
        )

        # Fallback
        self.assertEqual(compose_project_name({}), "d11-audit")

    def test_derive_visual_config_ddev(self):
        project_cfg = {
            "schemaVersion": "1.0",
            "repository": "site",
            "id": "my-ddev-site",
            "environment": {"id": "my-ddev-site", "kind": "local", "authorized": True},
            "site": {"uri": "https://my-ddev-site.ddev.site:8443"},
            "runtime": {"wrapper": "ddev"},
            "routes": ["/", "/about", "/user/login"],
        }
        visual_cfg = derive_visual_config(project_cfg)

        self.assertEqual(visual_cfg["referenceUrl"], "https://my-ddev-site.ddev.site:8443")
        self.assertEqual(visual_cfg["testUrl"], "https://my-ddev-site.ddev.site:8443")
        self.assertEqual(visual_cfg["network"]["shared"], "ddev-my-ddev-site_default")
        self.assertIn("my-ddev-site.ddev.site:host-gateway", visual_cfg["network"]["extraHosts"])
        self.assertIn("my-ddev-site.ddev.site", visual_cfg["localTlsExceptions"])
        self.assertTrue(len(visual_cfg["scenarios"]) >= 3)
        schema(visual_cfg, "scenarios")

    def test_derive_visual_config_fin_and_lando(self):
        fin_cfg = {
            "id": "my-fin-site",
            "environment": {"id": "my-fin-site", "kind": "local", "authorized": True},
            "site": {"uri": "http://my-fin-site.docksal.site"},
            "runtime": {"wrapper": "fin"},
        }
        v_fin = derive_visual_config(fin_cfg)
        self.assertEqual(v_fin["network"]["shared"], "my-fin-site_default")
        schema(v_fin, "scenarios")

        lando_cfg = {
            "id": "my-lando-site",
            "environment": {"id": "my-lando-site", "kind": "local", "authorized": True},
            "site": {"uri": "https://my-lando-site.lndo.site"},
            "runtime": {"wrapper": "lando"},
        }
        v_lando = derive_visual_config(lando_cfg)
        self.assertEqual(v_lando["network"]["shared"], "my-lando-site_default")
        schema(v_lando, "scenarios")

    def test_cli_generate_command(self):
        project_json = self.root / "project.json"
        write(
            project_json,
            {
                "schemaVersion": "1.0",
                "id": "test-site",
                "environment": {"id": "test-site", "kind": "local", "authorized": True},
                "site": {"uri": "http://127.0.0.1:8080"},
                "runtime": {"wrapper": "compose"},
                "routes": ["/", "/test-route"],
            },
        )
        visual_out = self.root / "visual.json"

        # Invoke visual_audit main with generate command
        argv = [
            "visual_audit.py",
            "generate",
            "--config",
            str(project_json),
            "--output",
            str(visual_out),
        ]
        from unittest.mock import patch

        with patch.object(sys, "argv", argv):
            exit_code = visual_audit.main()
            self.assertEqual(exit_code, 0)

        self.assertTrue(visual_out.is_file())
        saved = read(visual_out)
        self.assertEqual(saved["projectId"], "test-site")
        schema(saved, "scenarios")


if __name__ == "__main__":
    unittest.main()
