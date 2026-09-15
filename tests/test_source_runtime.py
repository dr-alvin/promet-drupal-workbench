import gzip
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.common import Problem, file_hash, read, write
from d11lib.guided_setup import Setup
from d11lib.source_runtime import (
    ComposeRuntime,
    LandoRuntime,
    LocalRuntime,
    export_database,
    get_runtime_adapter,
    inspect,
    select,
    verify_url,
)
from d11lib.workflow import Workflow


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.src = self.root / "example"
        for name in (
            "composer.json",
            "composer.lock",
            "vendor/autoload.php",
            "web/core/lib/Drupal.php",
        ):
            p = self.src / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}")
        (self.src / "web/sites/default/files").mkdir(parents=True)
        (self.src / ".docksal").mkdir()
        self.s = Setup(Workflow(self.root / "managed"))

    def tearDown(self):
        self.temp.cleanup()

    def objects(self, wrapper="fin"):
        project = "ddev-example" if wrapper == "ddev" else "example"

        def c(cid, role, mounts=[]):
            return {
                "Id": cid,
                "Config": {
                    "Labels": {
                        "com.docker.compose.service": role,
                        "com.docker.compose.project": project,
                    },
                    "Env": [
                        "VIRTUAL_HOST=example.ddev.site",
                        "DDEV_ROUTER_HTTPS_PORT=8443",
                        "MYSQL_DATABASE=db",
                    ],
                },
                "Mounts": mounts,
                "NetworkSettings": {
                    "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8097"}]},
                    "Networks": {"test": {"Aliases": [role]}},
                },
            }

        return [
            c(
                "app",
                "web" if wrapper == "ddev" else "cli",
                [{"Type": "bind", "Source": str(self.src), "Destination": "/var/www"}],
            ),
            c("db", "db"),
        ] + ([] if wrapper == "ddev" else [c("web", "web")])

    def probe(self, argv):
        if "hash_file" in " ".join(argv):
            return file_hash(self.src / argv[-1])
        if "status" in argv:
            return json.dumps(
                {
                    "root": "/var/www/web",
                    "site": "sites/default",
                    "db-hostname": "db",
                    "db-name": "db",
                    "db-password": "NEVER_RETURN",
                    "php-version": "8.3.30",
                    "files": "sites/default/files",
                }
            )
        return "10.11.7-MariaDB"

    def test_both_adapters_and_redaction(self):
        for wrapper in ("fin", "ddev"):
            with (
                patch("d11lib.source_runtime.containers", return_value=self.objects(wrapper)),
                patch("d11lib.source_runtime.output", side_effect=self.probe),
            ):
                result = inspect(self.src, wrapper, "http://127.0.0.1:8097")
            self.assertEqual(result["databaseName"], "db")
            self.assertEqual(result["files"], str(self.src / "web/sites/default/files"))
            self.assertNotIn("NEVER_RETURN", json.dumps(result))

    def test_generated_names_and_draft_inspection(self):
        a = self.s.create({"source": str(self.src), "sourceUrl": "http://127.0.0.1:8097"})
        b = self.s.create({"source": str(self.src)})
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(a["name"], "example")
        with patch("d11lib.source_runtime.inspect", side_effect=Problem("Stopped")):
            d = self.s.inspect(a["id"])
        self.assertEqual(d["status"], "blocked")
        self.assertEqual(self.s.list()[0]["draft"], True)
        with self.assertRaises(Problem):
            self.s.start(a["id"], True)

    def test_identity_bound_and_stale_inputs(self):
        d = self.s.create({"source": str(self.src), "sourceUrl": "http://127.0.0.1:8097"})
        with (
            patch("d11lib.source_runtime.containers", return_value=self.objects()),
            patch("d11lib.source_runtime.output", side_effect=self.probe),
        ):
            d = self.s.inspect(d["id"])
        self.assertEqual(d["checkpoint"], "source_verified")
        self.assertFalse(d["exportAuthorized"])
        self.s.update_inputs(d["id"], {"sourceUrl": "http://127.0.0.1:8888"})
        self.assertNotIn("inspection", self.s.get(d["id"]))

    def test_runtime_and_url_mismatch(self):
        for objects in ([], self.objects() + [self.objects()[0]], self.objects("ddev")):
            with self.assertRaises(Problem):
                select(self.src, "fin", objects)
        for url in (
            "https://production.example",
            "http://user:pass@example.ddev.site",
            "file:///tmp/test",
            "http://127.0.0.1:8097/path",
        ):
            with self.assertRaises(Problem):
                verify_url(url, self.objects())
        with self.assertRaisesRegex(
            Problem, r"port 8888.*selected project example.*published port\(s\): 8097"
        ):
            verify_url("http://127.0.0.1:8888", self.objects(), self.objects())
        other = self.objects()[2]
        other["Config"]["Labels"]["com.docker.compose.project"] = "another-copy"
        other["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"] = "60605"
        with self.assertRaisesRegex(
            Problem, r"port 60605.*belongs to Docker project another-copy.*8097"
        ):
            verify_url("http://127.0.0.1:60605", self.objects(), [*self.objects(), other])

    def test_malformed_dependencies_and_versions(self):
        with patch("d11lib.source_runtime.containers", return_value=self.objects()):
            for problem in ("malformed", "php", "database", "host", "private", "multisite", "sync"):

                def output(argv):
                    v = self.probe(argv)
                    if problem == "sync" and "hash_file" in " ".join(argv):
                        return "wrong"
                    if "status" in argv:
                        if problem == "malformed":
                            return "bad json"
                        d = json.loads(v)
                        if problem == "php":
                            d["php-version"] = "7.4.0"
                        if problem == "host":
                            d["db-hostname"] = "production.example"
                        if problem == "private":
                            d["private"] = "/unmapped/private"
                        if problem == "multisite":
                            d["site"] = "sites/another"
                        return json.dumps(d)
                    if problem == "database" and "SELECT VERSION()" in " ".join(argv):
                        return "5.7.44-MySQL"
                    return v

                with (
                    patch("d11lib.source_runtime.output", side_effect=output),
                    self.assertRaises(Problem),
                ):
                    inspect(self.src, "fin", "http://127.0.0.1:8097")
            (self.src / "vendor/autoload.php").unlink()
            with (
                patch("d11lib.source_runtime.output", side_effect=self.probe),
                self.assertRaises(Problem),
            ):
                inspect(self.src, "fin", "http://127.0.0.1:8097")

    def test_scoped_read_only_export_and_changed_identity(self):
        for wrapper in ("fin", "ddev"):
            target = self.root / (wrapper + ".gz")
            proc = Mock(stdout=io.BytesIO(b"SQL COPY"))
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            expected = {"container": "app", "databaseContainer": "db", "databaseName": "db"}
            with (
                patch("d11lib.source_runtime.containers", return_value=self.objects(wrapper)),
                patch("d11lib.source_runtime.subprocess.Popen", return_value=proc) as run,
            ):
                export_database(self.src, target, wrapper, expected)
                argv = run.call_args.args[0]
                self.assertEqual(argv[2], "db")
                self.assertIn("--single-transaction --skip-lock-tables", argv[5])
                self.assertEqual(argv[-1], "db")
                self.assertEqual(gzip.open(target).read(), b"SQL COPY")
            with (
                patch("d11lib.source_runtime.containers", return_value=self.objects(wrapper)),
                self.assertRaises(Problem),
            ):
                export_database(self.src, target, wrapper, {**expected, "container": "stale"})

    def test_ddev_synchronized_mount_identity(self):
        objects = self.objects("ddev")
        objects[0]["Mounts"] = [
            {"Type": "bind", "Source": str(self.src / ".ddev"), "Destination": "/mnt/ddev_config"}
        ]
        self.assertEqual(select(self.src, "ddev", objects)[3], "/var/www/html")

    def test_private_copy_and_source_preservation(self):
        private = self.src / "private"
        private.mkdir()
        (private / "document.txt").write_text("test content")
        (private / "test-login.json").write_text("secret")
        (private / "old.sql.gz").write_text("dump")
        (self.src / "web/sites/default/files/example.txt").write_text("public")
        before = file_hash(self.src / "composer.json")
        d = self.s.create({"source": str(self.src), "id": "private-test", "exportAuthorized": True})
        d["privateFiles"] = str(private)
        write(self.s.path(d["id"]) / "draft.json", d)

        def export(source, target, *args):
            target.write_bytes(b"SQL fixture")

        def provision(setup, p, d, bundle):
            self.assertEqual((bundle / "private/document.txt").read_text(), "test content")
            self.assertFalse((bundle / "private/test-login.json").exists())
            self.assertFalse((bundle / "private/old.sql.gz").exists())
            return {"site": {"uri": "http://127.0.0.1:9999"}}

        with (
            patch("d11lib.provision.export_local", side_effect=export),
            patch("d11lib.provision.provision", side_effect=provision),
        ):
            self.s.work(d["id"])
        final = self.s.get(d["id"])
        self.assertEqual(final["status"], "review_required", final.get("error"))
        self.assertEqual(file_hash(self.src / "composer.json"), before)
        self.assertTrue((private / "test-login.json").exists())
        self.assertIn("private", read(self.s.path(d["id"]) / "preparation.json")["hashes"])

    def test_lando_adapter(self):
        adapter = get_runtime_adapter("lando", self.src)
        self.assertIsInstance(adapter, LandoRuntime)
        self.assertEqual(adapter.drush_prefix(), ["lando", "drush"])
        self.assertEqual(adapter.php_prefix(), ["lando", "php"])

        self.assertFalse(LandoRuntime.detect(self.src))
        (self.src / ".lando.yml").write_text("name: my-lando-app\n")
        self.assertTrue(LandoRuntime.detect(self.src))
        self.assertEqual(adapter.url(), "https://my-lando-app.lndo.site")

        def lando_objects():
            return [
                {
                    "Id": "lando-app",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "appserver",
                            "com.docker.compose.project": "example",
                        },
                        "Env": [
                            "LANDO_APP_NAME=example",
                            "MYSQL_DATABASE=db",
                        ],
                    },
                    "Mounts": [{"Type": "bind", "Source": str(self.src), "Destination": "/app"}],
                    "NetworkSettings": {
                        "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "80"}]},
                        "Networks": {"test": {"Aliases": ["appserver"]}},
                    },
                },
                {
                    "Id": "lando-db",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "database",
                            "com.docker.compose.project": "example",
                        },
                        "Env": ["MYSQL_DATABASE=db"],
                    },
                    "Mounts": [],
                    "NetworkSettings": {
                        "Ports": {},
                        "Networks": {"test": {"Aliases": ["database"]}},
                    },
                },
            ]

        def lando_probe(argv):
            if "status" in argv:
                return json.dumps(
                    {
                        "root": "/app/web",
                        "site": "sites/default",
                        "db-hostname": "database",
                        "db-name": "db",
                        "php-version": "8.3.30",
                        "files": "sites/default/files",
                    }
                )
            return self.probe(argv)

        with (
            patch("d11lib.source_runtime.containers", return_value=lando_objects()),
            patch("d11lib.source_runtime.output", side_effect=lando_probe),
        ):
            res = inspect(self.src, "lando", "https://example.lndo.site")
            self.assertEqual(res["databaseName"], "db")
            self.assertEqual(res["wrapper"], "lando")

        target = self.root / "lando.gz"
        proc = Mock(stdout=io.BytesIO(b"LANDO SQL"))
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        expected = {
            "container": "lando-app",
            "databaseContainer": "lando-db",
            "databaseName": "db",
        }
        with (
            patch("d11lib.source_runtime.containers", return_value=lando_objects()),
            patch("d11lib.source_runtime.subprocess.Popen", return_value=proc) as run,
        ):
            export_database(self.src, target, "lando", expected)
            argv = run.call_args.args[0]
            self.assertEqual(argv[2], "lando-db")
            self.assertEqual(gzip.open(target).read(), b"LANDO SQL")

    def test_compose_adapter(self):
        adapter = get_runtime_adapter("compose", self.src)
        self.assertIsInstance(adapter, ComposeRuntime)
        self.assertEqual(
            adapter.drush_prefix(), ["docker", "compose", "exec", "-T", "cli", "drush"]
        )
        self.assertEqual(adapter.php_prefix(), ["docker", "compose", "exec", "-T", "cli", "php"])

        self.assertFalse(ComposeRuntime.detect(self.src))
        (self.src / "docker-compose.yml").write_text("services: {}\n")
        self.assertTrue(ComposeRuntime.detect(self.src))

        def compose_objects():
            return [
                {
                    "Id": "compose-cli",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "cli",
                            "com.docker.compose.project": "example",
                        },
                        "Env": [
                            "MYSQL_DATABASE=db",
                        ],
                    },
                    "Mounts": [
                        {"Type": "bind", "Source": str(self.src), "Destination": "/var/www/html"}
                    ],
                    "NetworkSettings": {
                        "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]},
                        "Networks": {"test": {"Aliases": ["cli"]}},
                    },
                },
                {
                    "Id": "compose-db",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.service": "db",
                            "com.docker.compose.project": "example",
                        },
                        "Env": ["MYSQL_DATABASE=db"],
                    },
                    "Mounts": [],
                    "NetworkSettings": {
                        "Ports": {},
                        "Networks": {"test": {"Aliases": ["db"]}},
                    },
                },
            ]

        def compose_probe(argv):
            if "status" in argv:
                return json.dumps(
                    {
                        "root": "/var/www/html/web",
                        "site": "sites/default",
                        "db-hostname": "db",
                        "db-name": "db",
                        "php-version": "8.3.30",
                        "files": "sites/default/files",
                    }
                )
            return self.probe(argv)

        with (
            patch("d11lib.source_runtime.containers", return_value=compose_objects()),
            patch("d11lib.source_runtime.output", side_effect=compose_probe),
        ):
            res = inspect(self.src, "compose", "http://127.0.0.1:8080")
            self.assertEqual(res["databaseName"], "db")
            self.assertEqual(res["wrapper"], "compose")

        target = self.root / "compose.gz"
        proc = Mock(stdout=io.BytesIO(b"COMPOSE SQL"))
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        expected = {
            "container": "compose-cli",
            "databaseContainer": "compose-db",
            "databaseName": "db",
        }
        with (
            patch("d11lib.source_runtime.containers", return_value=compose_objects()),
            patch("d11lib.source_runtime.subprocess.Popen", return_value=proc) as run,
        ):
            export_database(self.src, target, "compose", expected)
            argv = run.call_args.args[0]
            self.assertEqual(argv[2], "compose-db")
            self.assertEqual(gzip.open(target).read(), b"COMPOSE SQL")

    def test_local_adapter_and_registry(self):
        local_adapter = get_runtime_adapter("local", self.src)
        self.assertIsInstance(local_adapter, LocalRuntime)
        self.assertEqual(local_adapter.drush_prefix(), ["vendor/bin/drush"])
        self.assertEqual(local_adapter.php_prefix(), ["php"])
        self.assertTrue(local_adapter.detect(self.src))
        self.assertEqual(local_adapter.files_path(), self.src / "web/sites/default/files")

        with self.assertRaises(Problem):
            get_runtime_adapter("unsupported_wrapper")

    def test_docksal_virtual_host_label_and_proxy_port(self):
        objs = self.objects("fin")
        web = objs[2]
        web["Config"]["Labels"]["io.docksal.virtual-host"] = (
            "mysite.docksal.site,*.mysite.docksal.site"
        )
        web["Config"]["Env"] = [
            e for e in web["Config"]["Env"] if not e.startswith("VIRTUAL_HOST=")
        ]

        proxy = {
            "Id": "vhost-proxy",
            "Config": {"Image": "docksal/vhost-proxy:1.8", "Labels": {}},
            "NetworkSettings": {
                "Ports": {
                    "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8085"}],
                    "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "4433"}],
                }
            },
        }
        all_objs = [*objs, proxy]

        self.assertEqual(
            verify_url("http://mysite.docksal.site:8085", objs, all_objs),
            "http://mysite.docksal.site:8085",
        )
        self.assertEqual(
            verify_url("http://sub.mysite.docksal.site:8085", objs, all_objs),
            "http://sub.mysite.docksal.site:8085",
        )
        self.assertEqual(
            verify_url("http://mysite.docksal.site:8097", objs, all_objs),
            "http://mysite.docksal.site:8097",
        )
        with self.assertRaisesRegex(Problem, "hostname does not match"):
            verify_url("http://other.docksal.site:8085", objs, all_objs)


if __name__ == "__main__":
    unittest.main()
