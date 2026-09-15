"""Read-only inspection/export adapters for existing local runtimes."""

from __future__ import annotations

import abc
import gzip
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Optional
from urllib.parse import urlsplit

from .common import Problem, digest, file_hash, now
from .profile import get_profile, validate_profile_database, validate_profile_php


def validate_drupal_requirements(php: str, db_version: str, drush: str = "") -> None:
    if php:
        m = re.search(r"(\d+)\.(\d+)", str(php))
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            if (major, minor) < (8, 3):
                raise Problem(f"Drupal 11 requires PHP 8.3+; detected {php}")
    if db_version:
        ver_str = str(db_version).strip()
        ver_lower = ver_str.lower()
        m = re.search(r"(\d+(?:\.\d+)+)", ver_str)
        if m:
            parts = tuple(int(x) for x in m.group(1).split("."))
            if "mariadb" in ver_lower or (parts[0] in (10, 11) and "mysql" not in ver_lower):
                if parts < (10, 6):
                    raise Problem(f"Drupal 11 requires MariaDB 10.6+; detected {db_version}")
            elif "mysql" in ver_lower or "percona" in ver_lower or parts[0] == 8 or parts[0] == 5:
                if parts < (8, 0):
                    raise Problem(f"Drupal 11 requires MySQL 8.0+; detected {db_version}")
    if drush:
        m = re.search(r"(\d+)(?:\.(\d+))?", str(drush))
        if m:
            d_major = int(m.group(1))
            d_minor = int(m.group(2) or 0)
            if d_major < 12 or (d_major == 12 and d_minor < 5):
                raise Problem(f"Drupal 11 requires Drush 13+ (or 12.5+); detected {drush}")


def output(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise Problem(
            "Local runtime probe unavailable or timed out; keep the source runtime running"
        )
    if r.returncode:
        raise Problem(
            "Local runtime probe failed: " + " ".join(argv[:2]) + "; inspect the selected runtime"
        )
    return r.stdout


def containers():
    ids = output(["docker", "ps", "-q"]).split()
    return json.loads(output(["docker", "inspect", *ids])) if ids else []


def labels(c):
    return c.get("Config", {}).get("Labels") or {}


def env(c):
    return dict(x.split("=", 1) for x in c.get("Config", {}).get("Env", []) if "=" in x)


def host_path(value):
    # Docker Desktop may expose macOS bind mounts with this host prefix.
    if value.startswith("/host_mnt/") and Path("/Users").is_dir():
        value = value[len("/host_mnt") :]
    return Path(value).resolve()


def _matches_host(hostname: str, allowed_hosts: set[str]) -> bool:
    if hostname in allowed_hosts:
        return True
    import fnmatch

    for pattern in allowed_hosts:
        if pattern and fnmatch.fnmatch(hostname, pattern):
            return True
    return False


def verify_url(url, siblings, objects=()):
    try:
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        raise Problem("Enter a valid local HTTP(S) URL")
    if (
        u.scheme not in ("http", "https")
        or not u.hostname
        or u.username
        or u.password
        or u.query
        or u.fragment
        or u.path not in ("", "/")
    ):
        raise Problem(
            "Use the local site origin only, without credentials, path, query or fragment"
        )
    hosts = set()
    ports = set()
    direct_ports = set()
    for c in siblings:
        if labels(c).get("com.docker.compose.service") not in ("web", "appserver", "cli"):
            continue
        e = env(c)
        lbl = labels(c)
        for key in (
            "VIRTUAL_HOST",
            "DDEV_HOSTNAME",
            "DDEV_PRIMARY_URL",
            "LANDO_APP_NAME",
            "DRUSH_OPTIONS_URI",
        ):
            if key == "LANDO_APP_NAME" and e.get(key):
                hosts.add(f"{e[key]}.lndo.site")
                continue
            for value in e.get(key, "").split(","):
                val = value.strip()
                if val:
                    hosts.add(urlsplit(val).hostname if "://" in val else val)
        for key in ("io.docksal.virtual-host", "virtual-host"):
            for value in lbl.get(key, "").split(","):
                val = value.strip()
                if val:
                    hosts.add(urlsplit(val).hostname if "://" in val else val)
        if lbl.get("com.ddev.site-name"):
            hosts.add(f"{lbl['com.ddev.site-name']}.ddev.site")
        for bindings in c.get("NetworkSettings", {}).get("Ports", {}).values():
            for b in bindings or []:
                if b.get("HostIp") in ("127.0.0.1", "0.0.0.0", "::", "::1"):
                    ports.add(int(b["HostPort"]))
                    direct_ports.add(int(b["HostPort"]))
        # DDEV publishes router port expectations in the project web environment.
        for key in (
            ("DDEV_ROUTER_HTTPS_PORT",) if u.scheme == "https" else ("DDEV_ROUTER_HTTP_PORT",)
        ):
            if e.get(key, "").isdigit():
                ports.add(int(e[key]))
        if e.get("LANDO_WEB_PORT") and e["LANDO_WEB_PORT"].isdigit():
            ports.add(int(e["LANDO_WEB_PORT"]))
    for c in objects:
        image_name = c.get("Config", {}).get("Image", "")
        if "docksal/vhost-proxy" in image_name or any(
            proxy_img in image_name for proxy_img in ("lando/proxy", "traefik")
        ):
            for binding in (
                c.get("NetworkSettings", {})
                .get("Ports", {})
                .get("443/tcp" if u.scheme == "https" else "80/tcp", [])
                or []
            ):
                ports.add(int(binding["HostPort"]))
    if u.hostname in ("localhost", "127.0.0.1", "::1"):
        if port not in direct_ports:
            owners = set()
            for c in objects:
                for bindings in c.get("NetworkSettings", {}).get("Ports", {}).values():
                    if any(int(b.get("HostPort", 0)) == port for b in bindings or []):
                        owner = labels(c).get("com.docker.compose.project")
                        if owner:
                            owners.add(owner)
            selected = next(
                (
                    labels(c).get("com.docker.compose.project")
                    for c in siblings
                    if labels(c).get("com.docker.compose.project")
                ),
                None,
            )
            available = ", ".join(str(value) for value in sorted(direct_ports)) or "none"
            detail = (
                f"; it belongs to Docker project {', '.join(sorted(owners))}"
                if owners and owners != {selected}
                else ""
            )
            raise Problem(
                f"Local URL port {port} is not published by selected project {selected or 'unknown'}{detail}. Detected published port(s): {available}"
            )
    elif not _matches_host(u.hostname, hosts):
        raise Problem("Local URL hostname does not match the selected project runtime")
    elif port not in ports:
        if u.hostname.endswith(".lndo.site") and port in (80, 443):
            pass
        else:
            raise Problem("Local URL port does not match the runtime router")
    return u.scheme + "://" + u.netloc


def map_path(value, cli, mount, drupal, source):
    import posixpath

    from .guided_setup import selected_path

    if not value:
        return ""
    raw = posixpath.normpath(
        str(PurePosixPath(mount) / drupal / value)
        if not PurePosixPath(value).is_absolute()
        else value
    )
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise Problem("Detected files path contains parent traversal; select a reviewed override")
    matches = []
    for m in cli.get("Mounts", []):
        if m.get("Type") == "bind":
            root = PurePosixPath(m["Destination"])
            if path.is_relative_to(root):
                matches.append((len(root.parts), host_path(m["Source"]) / str(path.relative_to(root))))
        elif m.get("Type") == "volume" and m.get("Name"):
            resolved = ContainerRuntime._volume_device_path(m["Name"])
            if resolved is not None:
                root = PurePosixPath(m["Destination"])
                if path.is_relative_to(root):
                    matches.append((len(root.parts), resolved / str(path.relative_to(root))))
    if not matches:
        raise Problem(
            "Detected files are in an unmapped container volume; provide a reviewed local files folder"
        )
    best = max(matches, key=lambda x: x[0])[1]
    if not best.exists() and (best == source / "private" or str(best).startswith(str(source))):
        try:
            best.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return str(selected_path(str(best)))


class SourceRuntime(abc.ABC):
    """Abstract base class for source runtime adapters."""

    wrapper: ClassVar[str] = "unknown"
    name: ClassVar[str] = "unknown"

    def __init__(self, source: Path | str = "", **kwargs: Any):
        self.source = Path(source).resolve() if source else Path.cwd()

    @classmethod
    @abc.abstractmethod
    def detect(cls, path: Path | str) -> bool:
        """Detect whether the given path uses this runtime."""
        ...

    @abc.abstractmethod
    def exec(self, cmd: list[str], **kwargs: Any) -> str:
        """Execute a command in the runtime environment."""
        ...

    @abc.abstractmethod
    def dump_db(self, target: Path | str, expected: Optional[dict[str, Any]] = None) -> None:
        """Export a gzipped database dump to the target path."""
        ...

    @abc.abstractmethod
    def files_path(self, container_files_path: str = "") -> Path:
        """Resolve the host files path."""
        ...

    @abc.abstractmethod
    def url(self) -> str:
        """Detect or return the local site URL."""
        ...

    @abc.abstractmethod
    def drush_prefix(self) -> list[str]:
        """Command prefix for executing drush."""
        ...

    @abc.abstractmethod
    def composer_prefix(self) -> list[str]:
        """Command prefix for executing composer."""
        ...

    @abc.abstractmethod
    def php_prefix(self) -> list[str]:
        """Command prefix for executing php."""
        ...

    @abc.abstractmethod
    def inspect(
        self, url: str, files_override: str = "", profile: Optional[str] = None
    ) -> dict[str, Any]:
        """Inspect the running runtime environment and verify identity."""
        ...


class ContainerRuntime(SourceRuntime):
    """Base runtime adapter for Docker-backed environments."""

    cli_roles: ClassVar[list[str]] = ["cli"]
    db_roles: ClassVar[list[str]] = ["db"]
    default_mount: ClassVar[Optional[str]] = None

    @staticmethod
    def _volume_device_path(volume_name: str) -> Optional[Path]:
        """Resolve the host device path for a named Docker volume backed by a bind mount."""
        try:
            raw = subprocess.run(
                ["docker", "volume", "inspect", volume_name, "--format", "{{json .Options}}"],
                capture_output=True, text=True, timeout=10,
            )
            if raw.returncode == 0 and raw.stdout.strip():
                opts = json.loads(raw.stdout.strip())
                if isinstance(opts, dict) and opts.get("type") == "none" and opts.get("o") == "bind":
                    device = opts.get("device", "")
                    if device:
                        return host_path(device)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        return None

    def _mount_matches_source(self, m: dict[str, Any]) -> bool:
        """Check if a container mount (bind or named volume) points to self.source."""
        if m.get("Type") == "bind":
            hp = host_path(m.get("Source", "/"))
            if hp == self.source:
                return True
            if (
                self.wrapper == "ddev"
                and hp == self.source / ".ddev"
                and m.get("Destination") == "/mnt/ddev_config"
            ):
                return True
        elif m.get("Type") == "volume" and m.get("Name"):
            resolved = self._volume_device_path(m["Name"])
            if resolved is not None and resolved == self.source:
                return True
        return False

    def select_containers(
        self, objects: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
        candidates: list[dict[str, Any]] = []
        for role in self.cli_roles:
            candidates = [
                c
                for c in objects
                if labels(c).get("com.docker.compose.service") == role
                and any(self._mount_matches_source(m) for m in c.get("Mounts", []))
            ]
            if candidates:
                break
        if len(candidates) != 1:
            raise Problem(
                "Source runtime is stopped or ambiguous: expected one "
                + self.wrapper
                + " container mounted to the selected root"
            )
        cli = candidates[0]
        project = labels(cli).get("com.docker.compose.project", "")
        is_ddev = project.startswith("ddev-") or bool(labels(cli).get("com.ddev.site-name"))
        if (self.wrapper == "ddev") != is_ddev:
            raise Problem("Selected runtime does not match the project container")
        siblings = [c for c in objects if labels(c).get("com.docker.compose.project") == project]
        db: list[dict[str, Any]] = []
        for db_role in self.db_roles:
            db = [c for c in siblings if labels(c).get("com.docker.compose.service") == db_role]
            if db:
                break
        if len(db) != 1:
            raise Problem("Cannot uniquely identify the source database container")
        mount = next(
            (
                m["Destination"]
                for m in cli["Mounts"]
                if (
                    (m.get("Type") == "bind" and host_path(m.get("Source", "/")) == self.source)
                    or (
                        m.get("Type") == "volume"
                        and m.get("Name")
                        and self._volume_device_path(m["Name"]) == self.source
                    )
                )
            ),
            self.default_mount,
        )
        if not mount:
            raise Problem("Cannot resolve the runtime code mount")
        return cli, db[0], siblings, mount

    def exec(self, cmd: list[str], **kwargs: Any) -> str:
        cli, _, _, mount = self.select_containers(containers())
        workdir = kwargs.get("workdir", mount)
        return output(["docker", "exec", "-w", str(workdir), cli["Id"], *cmd])

    def dump_db(self, target: Path | str, expected: Optional[dict[str, Any]] = None) -> None:
        target = Path(target)
        cli, db, _, _ = self.select_containers(containers())
        if expected and (
            db["Id"] != expected["databaseContainer"] or cli["Id"] != expected["container"]
        ):
            raise Problem("Source runtime changed after inspection; inspect again")
        database = expected["databaseName"] if expected else env(db).get("MYSQL_DATABASE", "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise Problem("Source database name is unknown")
        shell = 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD:-root}"; export MYSQL_PWD; dump=$(command -v mariadb-dump || command -v mysqldump); "$dump" -uroot --single-transaction --skip-lock-tables --skip-triggers --hex-blob "$1"'
        with tempfile.TemporaryFile() as errors, gzip.open(target, "wb") as out:
            proc = subprocess.Popen(
                ["docker", "exec", db["Id"], "sh", "-c", shell, "d11-export", database],
                stdout=subprocess.PIPE,
                stderr=errors,
            )
            try:
                for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                    if (
                        os.environ.get("D11_STOP_FILE")
                        and Path(os.environ["D11_STOP_FILE"]).exists()
                    ):
                        raise Problem("Export stopped; partial copy requires reconciliation")
                    out.write(chunk)
                if proc.wait():
                    raise Problem(
                        "Read-only database export failed; source database was not modified"
                    )
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()

    def files_path(self, container_files_path: str = "") -> Path:
        cli, _, _, mount = self.select_containers(containers())
        resolved = map_path(container_files_path, cli, mount, "", self.source)
        return Path(resolved) if resolved else (self.source / "web/sites/default/files")

    def inspect(
        self, url: str, files_override: str = "", profile: Optional[str] = None
    ) -> dict[str, Any]:
        prof = get_profile(profile)
        objects = containers()
        cli, db, siblings, mount = self.select_containers(objects)
        uri = verify_url(url, siblings, objects)
        manifest_hashes = {}
        for name in ("composer.json", "composer.lock"):
            if not (self.source / name).is_file():
                raise Problem("Composer manifests and lockfile are required")
            remote = output(
                [
                    "docker",
                    "exec",
                    "-w",
                    mount,
                    cli["Id"],
                    "php",
                    "-r",
                    'echo hash_file("sha256", $argv[1]);',
                    name,
                ]
            ).strip()
            manifest_hashes[name] = remote
            if remote != file_hash(self.source / name):
                raise Problem(
                    "Source and running container manifests differ; synchronize the local runtime first"
                )
        raw = output(
            [
                "docker",
                "exec",
                "-w",
                mount,
                cli["Id"],
                "php",
                "vendor/bin/drush.php",
                "status",
                "--format=json",
                "--uri=" + uri,
            ]
        )
        try:
            s = json.loads(raw)
        except ValueError:
            raise Problem("Drush status returned malformed output; runtime identity is unknown")
        if not isinstance(s, dict):
            raise Problem("Drush status did not return an object; runtime identity is unknown")
        root = PurePosixPath(s.get("root", ""))
        if not root.is_absolute() or not root.is_relative_to(PurePosixPath(mount)):
            raise Problem("Drupal root is outside the selected runtime mount")
        drupal = str(root.relative_to(PurePosixPath(mount)))
        if (
            not (self.source / drupal / "core/lib/Drupal.php").is_file()
            or not (self.source / "vendor/autoload.php").is_file()
        ):
            raise Problem(
                "Installed Drupal dependencies are missing; install the source lockfile before setup"
            )
        database = s.get("db-name", "")
        host = s.get("db-hostname", s.get("db-host", ""))
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise Problem(
                "Database selection is unknown or unsupported; inspect multisite settings"
            )
        aliases = {"db", "database", db.get("Name", "").lstrip("/")}
        for network in db.get("NetworkSettings", {}).get("Networks", {}).values():
            aliases.update(network.get("Aliases") or [])
        if host not in aliases:
            raise Problem("Drupal database host does not match the isolated local database service")
        php = str(s.get("php-version", ""))
        validate_profile_php(prof, php)
        # Password stays inside the container; database is a positional shell argument.
        sql = 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD:-root}"; export MYSQL_PWD; client=$(command -v mariadb || command -v mysql); "$client" -uroot -Nse "SELECT VERSION()" "$1"'
        version = output(
            ["docker", "exec", db["Id"], "sh", "-c", sql, "d11-inspect", database]
        ).strip()
        validate_profile_database(prof, version)
        validate_drupal_requirements(php, version, str(s.get("drush-version", "")))
        files = files_override or map_path(s.get("files", ""), cli, mount, drupal, self.source)
        if not files:
            raise Problem(
                "Drush did not identify public files; provide the local folder under Advanced"
            )
        try:
            private = map_path(
                s.get("private", s.get("files-private", "")), cli, mount, drupal, self.source
            )
        except Problem:
            raise Problem(
                "Configured private files are not available through a local bind mount. Provide a reviewed bundle including those files; no private data will be silently omitted."
            )
        site_path = s.get("site", "sites/default")
        if site_path != "sites/default":
            raise Problem(
                "Multisite requires a reviewed advanced bundle; detected " + str(site_path)
            )
        result = {
            "source": str(self.source),
            "wrapper": self.wrapper,
            "sourceUrl": uri,
            "project": labels(cli)["com.docker.compose.project"],
            "container": cli["Id"],
            "databaseContainer": db["Id"],
            "databaseName": database,
            "drupal": drupal,
            "files": files,
            "privateFiles": private,
            "phpVersion": php,
            "databaseVersion": version,
            "manifestHashes": manifest_hashes,
            "dependenciesInstalled": True,
            "destination": prof.get(
                "destination", "Managed Docksal images · PHP 8.3 / MariaDB 10.11"
            ),
            "inspectedAt": now(),
        }
        result["identityHash"] = digest({k: v for k, v in result.items() if k != "inspectedAt"})
        return result


class DdevRuntime(ContainerRuntime):
    wrapper = "ddev"
    name = "ddev"
    cli_roles = ["web"]
    db_roles = ["db"]
    default_mount = "/var/www/html"

    @classmethod
    def detect(cls, path: Path | str) -> bool:
        return (Path(path) / ".ddev").is_dir()

    def drush_prefix(self) -> list[str]:
        return ["ddev", "drush"]

    def composer_prefix(self) -> list[str]:
        return ["ddev", "composer"]

    def php_prefix(self) -> list[str]:
        return ["ddev", "exec", "php"]

    def url(self) -> str:
        cfg_file = self.source / ".ddev/config.yaml"
        if cfg_file.is_file():
            try:
                import yaml

                ddev_cfg = yaml.safe_load(cfg_file.read_text()) or {}
                ddev_name = ddev_cfg.get("name") or self.source.name
                tld = ddev_cfg.get("project_tld", "ddev.site")
                return f"https://{ddev_name}.{tld}"
            except Exception:
                pass
        return ""


class FinRuntime(ContainerRuntime):
    wrapper = "fin"
    name = "fin"
    cli_roles = ["cli"]
    db_roles = ["db"]
    default_mount = None

    @classmethod
    def detect(cls, path: Path | str) -> bool:
        return (Path(path) / ".docksal").is_dir()

    def drush_prefix(self) -> list[str]:
        return ["fin", "drush"]

    def composer_prefix(self) -> list[str]:
        return ["fin", "composer"]

    def php_prefix(self) -> list[str]:
        return ["fin", "exec", "php"]

    def url(self) -> str:
        for env_name in (".docksal/docksal-local.env", ".docksal/docksal.env"):
            env_file = self.source / env_name
            if env_file.is_file():
                try:
                    for line in env_file.read_text().splitlines():
                        if line.startswith("VIRTUAL_HOST="):
                            vhost = line.split("=", 1)[1].strip().strip('"').strip("'")
                            return f"http://{vhost}"
                except Exception:
                    pass
        return ""


class LandoRuntime(ContainerRuntime):
    wrapper = "lando"
    name = "lando"
    cli_roles = ["appserver", "web", "cli"]
    db_roles = ["database", "db"]
    default_mount = "/app"

    @classmethod
    def detect(cls, path: Path | str) -> bool:
        p = Path(path)
        return any(
            (p / f).is_file()
            for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml")
        )

    def drush_prefix(self) -> list[str]:
        return ["lando", "drush"]

    def composer_prefix(self) -> list[str]:
        return ["lando", "composer"]

    def php_prefix(self) -> list[str]:
        return ["lando", "php"]

    def url(self) -> str:
        for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml"):
            p = self.source / f
            if p.is_file():
                try:
                    import yaml

                    cfg = yaml.safe_load(p.read_text()) or {}
                    name = cfg.get("name") or self.source.name
                    return f"https://{name}.lndo.site"
                except Exception:
                    pass
        return ""


class ComposeRuntime(ContainerRuntime):
    wrapper = "compose"
    name = "compose"
    cli_roles = ["cli", "web", "app", "drupal", "php"]
    db_roles = ["db", "database", "mariadb", "mysql"]
    default_mount = "/var/www/html"

    @classmethod
    def detect(cls, path: Path | str) -> bool:
        p = Path(path)
        return any(
            (p / f).is_file()
            for f in (
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yaml",
                "compose.yml",
            )
        )

    def drush_prefix(self) -> list[str]:
        return ["docker", "compose", "exec", "-T", "cli", "drush"]

    def composer_prefix(self) -> list[str]:
        return ["docker", "compose", "exec", "-T", "cli", "composer"]

    def php_prefix(self) -> list[str]:
        return ["docker", "compose", "exec", "-T", "cli", "php"]

    def url(self) -> str:
        return "http://127.0.0.1:8080"


class LocalRuntime(SourceRuntime):
    wrapper = "local"
    name = "local"

    @classmethod
    def detect(cls, path: Path | str) -> bool:
        return (Path(path) / "composer.json").is_file()

    def drush_prefix(self) -> list[str]:
        return ["vendor/bin/drush"]

    def composer_prefix(self) -> list[str]:
        return ["composer"]

    def php_prefix(self) -> list[str]:
        return ["php"]

    def url(self) -> str:
        return "http://127.0.0.1:8080"

    def exec(self, cmd: list[str], **kwargs: Any) -> str:
        return output(cmd)

    def dump_db(self, target: Path | str, expected: Optional[dict[str, Any]] = None) -> None:
        target = Path(target)
        database = (expected or {}).get("databaseName", "")
        if not database:
            raise Problem("Source database name is unknown")
        shell = 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD:-root}"; export MYSQL_PWD; dump=$(command -v mariadb-dump || command -v mysqldump); "$dump" -uroot --single-transaction --skip-lock-tables --skip-triggers --hex-blob "$1"'
        with tempfile.TemporaryFile() as errors, gzip.open(target, "wb") as out:
            proc = subprocess.Popen(
                ["sh", "-c", shell, "d11-export", database],
                stdout=subprocess.PIPE,
                stderr=errors,
            )
            try:
                for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                    if (
                        os.environ.get("D11_STOP_FILE")
                        and Path(os.environ["D11_STOP_FILE"]).exists()
                    ):
                        raise Problem("Export stopped; partial copy requires reconciliation")
                    out.write(chunk)
                if proc.wait():
                    raise Problem(
                        "Read-only database export failed; source database was not modified"
                    )
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()

    def files_path(self, container_files_path: str = "") -> Path:
        if container_files_path:
            p = Path(container_files_path)
            return p if p.is_absolute() else (self.source / p)
        for cand in ("web/sites/default/files", "sites/default/files"):
            if (self.source / cand).is_dir():
                return self.source / cand
        return self.source / "web/sites/default/files"

    def inspect(
        self, url: str, files_override: str = "", profile: Optional[str] = None
    ) -> dict[str, Any]:
        prof = get_profile(profile)
        for name in ("composer.json", "composer.lock"):
            if not (self.source / name).is_file():
                raise Problem("Composer manifests and lockfile are required")
        manifest_hashes = {
            name: file_hash(self.source / name) for name in ("composer.json", "composer.lock")
        }
        autoload = self.source / "vendor/autoload.php"
        if not autoload.is_file():
            raise Problem(
                "Installed Drupal dependencies are missing; install the source lockfile before setup"
            )
        raw = output(
            [
                "php",
                "vendor/bin/drush.php",
                "status",
                "--format=json",
                "--uri=" + url,
            ]
        )
        try:
            s = json.loads(raw)
        except ValueError:
            raise Problem("Drush status returned malformed output; runtime identity is unknown")
        if not isinstance(s, dict):
            raise Problem("Drush status did not return an object; runtime identity is unknown")
        root = Path(s.get("root", ""))
        if not root.is_dir() or not (root / "core/lib/Drupal.php").is_file():
            raise Problem("Drupal root is outside the selected runtime mount")
        try:
            drupal = str(root.relative_to(self.source))
        except ValueError:
            drupal = "web" if (self.source / "web/core/lib/Drupal.php").is_file() else ""
        database = s.get("db-name", "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise Problem(
                "Database selection is unknown or unsupported; inspect multisite settings"
            )
        php = str(s.get("php-version", ""))
        validate_profile_php(prof, php)
        sql = 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD:-root}"; export MYSQL_PWD; client=$(command -v mariadb || command -v mysql); "$client" -uroot -Nse "SELECT VERSION()" "$1"'
        version = output(["sh", "-c", sql, "d11-inspect", database]).strip()
        validate_profile_database(prof, version)
        validate_drupal_requirements(php, version, str(s.get("drush-version", "")))
        files = files_override or str(self.source / drupal / s.get("files", "sites/default/files"))
        private = (
            str(self.source / drupal / s.get("private", s.get("files-private", "")))
            if s.get("private") or s.get("files-private")
            else ""
        )
        result = {
            "source": str(self.source),
            "wrapper": "local",
            "sourceUrl": url,
            "project": self.source.name,
            "container": "local",
            "databaseContainer": "local",
            "databaseName": database,
            "drupal": drupal,
            "files": files,
            "privateFiles": private,
            "phpVersion": php,
            "databaseVersion": version,
            "manifestHashes": manifest_hashes,
            "dependenciesInstalled": True,
            "destination": prof.get(
                "destination", "Managed Docksal images · PHP 8.3 / MariaDB 10.11"
            ),
            "inspectedAt": now(),
        }
        result["identityHash"] = digest({k: v for k, v in result.items() if k != "inspectedAt"})
        return result


RUNTIME_ADAPTERS: dict[str, type[SourceRuntime]] = {
    "ddev": DdevRuntime,
    "fin": FinRuntime,
    "lando": LandoRuntime,
    "compose": ComposeRuntime,
    "local": LocalRuntime,
}


def get_runtime_adapter(wrapper: str, source: Path | str = "") -> SourceRuntime:
    adapter_cls = RUNTIME_ADAPTERS.get(wrapper)
    if not adapter_cls:
        raise Problem(
            f"Unsupported runtime wrapper: '{wrapper}'. Supported: {', '.join(RUNTIME_ADAPTERS)}"
        )
    return adapter_cls(source)


def get_runtime_prefixes(wrapper: str, source: Path | str = "") -> tuple[list[str], list[str]]:
    """Return (drush_prefix, composer_prefix) for the given runtime wrapper."""
    try:
        adapter = get_runtime_adapter(wrapper, source)
        return adapter.drush_prefix(), adapter.composer_prefix()
    except Exception:
        if wrapper == "ddev":
            return ["ddev", "drush"], ["ddev", "composer"]
        if wrapper == "lando":
            return ["lando", "drush"], ["lando", "composer"]
        if wrapper == "compose":
            return ["docker", "compose", "exec", "-T", "cli", "drush"], [
                "docker",
                "compose",
                "exec",
                "-T",
                "cli",
                "composer",
            ]
        if wrapper == "local":
            return ["vendor/bin/drush"], ["composer"]
        return ["fin", "drush"], ["fin", "composer"]


def select(source, wrapper, objects):
    source = Path(source).resolve()
    adapter = get_runtime_adapter(wrapper, source)
    if isinstance(adapter, ContainerRuntime):
        return adapter.select_containers(objects)
    raise Problem(f"Runtime '{wrapper}' does not use containers")


def inspect(source, wrapper, url, files_override="", profile=None):
    source = Path(source).resolve()
    adapter = get_runtime_adapter(wrapper, source)
    return adapter.inspect(url=url, files_override=files_override, profile=profile)


def export_database(source, target, wrapper="fin", expected=None):
    source = Path(source).resolve()
    adapter = get_runtime_adapter(wrapper, source)
    return adapter.dump_db(target, expected=expected)


def detect_runtime(source_path):
    source = Path(source_path).resolve()
    if not (source / "composer.json").is_file():
        raise Problem(f"No composer.json found in {source}")
    project_name = source.name
    detected_url = ""
    wrapper = "auto"
    if (source / ".ddev/config.yaml").is_file():
        try:
            import yaml

            ddev_cfg = yaml.safe_load((source / ".ddev/config.yaml").read_text()) or {}
            ddev_name = ddev_cfg.get("name") or project_name
            project_name = ddev_name
            tld = ddev_cfg.get("project_tld", "ddev.site")
            detected_url = f"https://{ddev_name}.{tld}"
            wrapper = "ddev"
        except Exception:
            pass
    elif (source / ".docksal/docksal.env").is_file():
        try:
            for line in (source / ".docksal/docksal.env").read_text().splitlines():
                if line.startswith("VIRTUAL_HOST="):
                    vhost = line.split("=", 1)[1].strip().strip('"').strip("'")
                    detected_url = f"http://{vhost}"
                    wrapper = "fin"
                    break
        except Exception:
            pass
    elif any(
        (source / f).is_file()
        for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml")
    ):
        wrapper = "lando"
        for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml"):
            if (source / f).is_file():
                try:
                    import yaml

                    lando_cfg = yaml.safe_load((source / f).read_text()) or {}
                    lando_name = lando_cfg.get("name") or project_name
                    project_name = lando_name
                    detected_url = f"https://{lando_name}.lndo.site"
                    break
                except Exception:
                    pass
    elif any(
        (source / f).is_file()
        for f in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yaml",
            "compose.yml",
        )
    ):
        wrapper = "compose"
    try:
        objs = containers()
        cli, _, siblings, _ = select(
            source, wrapper if wrapper in ("fin", "ddev", "lando", "compose") else "ddev", objs
        )
        for c in siblings:
            if labels(c).get("com.docker.compose.service") in ("web", "appserver", "cli"):
                e = env(c)
                lbl = labels(c)
                if e.get("DDEV_PRIMARY_URL"):
                    detected_url = e["DDEV_PRIMARY_URL"]
                elif e.get("LANDO_APP_NAME"):
                    detected_url = f"https://{e['LANDO_APP_NAME']}.lndo.site"
                elif lbl.get("io.docksal.virtual-host"):
                    detected_url = f"http://{lbl['io.docksal.virtual-host'].split(',')[0].strip()}"
                elif e.get("VIRTUAL_HOST"):
                    detected_url = f"http://{e['VIRTUAL_HOST'].split(',')[0].strip()}"
                elif e.get("DRUSH_OPTIONS_URI"):
                    detected_url = e["DRUSH_OPTIONS_URI"]
        if wrapper == "fin" and detected_url and ":" not in urlsplit(detected_url).netloc:
            for c in objs:
                if "docksal/vhost-proxy" in c.get("Config", {}).get("Image", ""):
                    for b in c.get("NetworkSettings", {}).get("Ports", {}).get("80/tcp", []) or []:
                        hp = int(b.get("HostPort", 0))
                        if hp and hp != 80:
                            detected_url = f"{detected_url}:{hp}"
                            break
    except Exception:
        pass
    pid = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")[:40]
    return {
        "source": str(source),
        "name": project_name,
        "id": pid,
        "wrapper": wrapper,
        "sourceUrl": detected_url,
    }
