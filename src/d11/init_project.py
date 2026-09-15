"""Automated project configuration initialization and probing for Drupal projects."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .common import Problem, schema, write
from .source_runtime import detect_runtime


def probe_drupal_root(source: Path) -> str:
    """Probe the Drupal root directory (e.g. 'web', 'docroot', or '.')."""
    # 1. Check composer.json configuration
    composer_file = source / "composer.json"
    if composer_file.is_file():
        try:
            manifest = json.loads(composer_file.read_text())
            extra = manifest.get("extra", {})
            scaffold_web = extra.get("drupal-scaffold", {}).get("locations", {}).get("web-root")
            if scaffold_web:
                cleaned = scaffold_web.strip("/").strip()
                if cleaned and (source / cleaned).is_dir():
                    return cleaned
            installer_paths = extra.get("installer-paths", {})
            for path_pattern, types in installer_paths.items():
                if "type:drupal-core" in types or "drupal/core" in types:
                    prefix = path_pattern.split("/core")[0].strip("/")
                    if prefix and (source / prefix).is_dir():
                        return prefix
        except Exception:
            pass

    # 2. Check conventional directory indicators
    for candidate in ("web", "docroot"):
        cand_dir = source / candidate
        if cand_dir.is_dir():
            if (
                (cand_dir / "core/lib/Drupal.php").is_file()
                or (cand_dir / "index.php").is_file()
                or (cand_dir / "sites").is_dir()
            ):
                return candidate

    # 3. Check root directly
    if (
        (source / "core/lib/Drupal.php").is_file()
        or (source / "index.php").is_file()
        or (source / "sites").is_dir()
    ):
        return "."

    # 4. Fallback to existing directory or 'web'
    if (source / "web").is_dir():
        return "web"
    if (source / "docroot").is_dir():
        return "docroot"
    return "."


def probe_core_version(source: Path, drupal_root: str) -> str | None:
    """Probe Drupal core version from composer.lock, core/lib/Drupal.php, or composer.json."""
    # 1. Check composer.lock
    lock_file = source / "composer.lock"
    if lock_file.is_file():
        try:
            data = json.loads(lock_file.read_text())
            for pkg in (*data.get("packages", []), *data.get("packages-dev", [])):
                if pkg.get("name") in ("drupal/core-recommended", "drupal/core"):
                    v = str(pkg.get("version", "")).lstrip("v").strip()
                    if v:
                        return v
        except Exception:
            pass

    # 2. Check core/lib/Drupal.php
    drupal_php = source / (drupal_root if drupal_root != "." else "") / "core/lib/Drupal.php"
    if drupal_php.is_file():
        try:
            text = drupal_php.read_text()
            m = re.search(r"const\s+VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
            if m:
                return m.group(1).strip()
        except Exception:
            pass

    # 3. Check composer.json requirements
    composer_file = source / "composer.json"
    if composer_file.is_file():
        try:
            data = json.loads(composer_file.read_text())
            reqs = {**data.get("require", {}), **data.get("require-dev", {})}
            for name in ("drupal/core-recommended", "drupal/core"):
                if name in reqs:
                    return str(reqs[name]).strip()
        except Exception:
            pass

    return None


def probe_custom_roots(source: Path, drupal_root: str) -> list[str]:
    """Identify custom modules and themes paths."""
    prefix = "" if drupal_root == "." else f"{drupal_root}/"
    candidates = [
        f"{prefix}modules/custom",
        f"{prefix}themes/custom",
        f"{prefix}profiles/custom",
    ]
    existing = [c for c in candidates if (source / c).is_dir()]
    if existing:
        return existing
    # Fallback to standard locations
    return [f"{prefix}modules/custom", f"{prefix}themes/custom"]


def probe_config_root(source: Path, drupal_root: str) -> str | None:
    """Identify configuration sync directory."""
    # 1. Check standard config directories with sync evidence
    for candidate in ("config/sync", "config/default", "config"):
        if (source / candidate / "core.extension.yml").is_file():
            return candidate

    # 2. Check settings.php for config_sync_directory
    settings_dir = source / (drupal_root if drupal_root != "." else "") / "sites"
    if settings_dir.is_dir():
        for settings_file in sorted(settings_dir.glob("*/settings*.php")):
            try:
                text = settings_file.read_text()
                m = re.search(
                    r"\$settings\[['\"]config_sync_directory['\"]\]\s*=\s*['\"]([^'\"]+)['\"]\s*;",
                    text,
                )
                if m:
                    sync_path = m.group(1)
                    dr_path = source / (drupal_root if drupal_root != "." else "")
                    candidates = [
                        (dr_path / sync_path).resolve(),
                        (source / sync_path).resolve(),
                        (settings_file.parent / sync_path).resolve(),
                    ]
                    for p in candidates:
                        if p.is_dir():
                            try:
                                return str(p.relative_to(source.resolve()))
                            except ValueError:
                                pass
            except Exception:
                pass

    # 3. Check directory existence fallback
    if (source / "config/sync").is_dir():
        return "config/sync"
    if (source / "config/default").is_dir():
        return "config/default"
    return "config/sync"


def probe_db_credentials(source: Path, drupal_root: str, wrapper: str) -> dict[str, Any]:
    """Probe database connection parameters from settings or dev environment defaults."""
    db_info: dict[str, Any] = {
        "host": "db",
        "port": 3306,
        "database": "db",
        "username": "db",
    }
    if wrapper == "fin":
        db_info.update({"database": "default", "username": "root"})
        docksal_env = source / ".docksal/docksal.env"
        if docksal_env.is_file():
            try:
                for line in docksal_env.read_text().splitlines():
                    if line.startswith("MYSQL_DATABASE="):
                        db_info["database"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("MYSQL_USER="):
                        db_info["username"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    elif wrapper == "lando":
        db_info.update({"host": "database", "database": "drupal10", "username": "drupal10"})
    elif wrapper == "local":
        db_info.update({"host": "127.0.0.1", "database": source.name, "username": "root"})
    elif wrapper == "compose":
        db_info.update({"database": "drupal", "username": "drupal"})

    # Inspect settings*.php
    sites_dir = source / (drupal_root if drupal_root != "." else "") / "sites"
    if sites_dir.is_dir():
        for sf in sorted(sites_dir.glob("*/settings*.php")):
            try:
                content = sf.read_text()
                host_m = re.search(r"['\"]host['\"]\s*=>\s*['\"]([^'\"]+)['\"]", content)
                db_m = re.search(r"['\"]database['\"]\s*=>\s*['\"]([^'\"]+)['\"]", content)
                user_m = re.search(r"['\"]username['\"]\s*=>\s*['\"]([^'\"]+)['\"]", content)
                port_m = re.search(r"['\"]port['\"]\s*=>\s*['\"]?([0-9]+)['\"]?", content)
                if host_m:
                    db_info["host"] = host_m.group(1)
                if db_m:
                    db_info["database"] = db_m.group(1)
                if user_m:
                    db_info["username"] = user_m.group(1)
                if port_m:
                    db_info["port"] = int(port_m.group(1))
            except Exception:
                pass

    return db_info


def probe_runtime(source: Path) -> dict[str, Any]:
    """Probe local dev runtime environment and local URL."""
    project_name = source.name
    pid = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")[:40]
    wrapper = "local"
    detected_url = ""

    if (source / "composer.json").is_file():
        try:
            detected = detect_runtime(source)
            wrapper = detected.get("wrapper", "local")
            if wrapper == "auto" or not wrapper:
                wrapper = "local"
            detected_url = detected.get("sourceUrl", "")
            project_name = detected.get("name") or project_name
            pid = detected.get("id") or pid
        except Exception:
            pass

    # Static detection fallback if detect_runtime didn't catch wrapper
    if wrapper == "local":
        if (source / ".ddev").is_dir():
            wrapper = "ddev"
        elif (source / ".docksal").is_dir():
            wrapper = "fin"
        elif any(
            (source / f).is_file()
            for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml")
        ):
            wrapper = "lando"
        elif any(
            (source / f).is_file()
            for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml")
        ):
            wrapper = "compose"

    # Construct default URL if not detected
    if not detected_url:
        if wrapper == "ddev":
            detected_url = f"https://{project_name}.ddev.site"
        elif wrapper == "fin":
            detected_url = f"http://{project_name}.docksal.site"
        elif wrapper == "lando":
            detected_url = f"https://{project_name}.lndo.site"
        else:
            detected_url = "http://127.0.0.1:8888"

    return {
        "wrapper": wrapper,
        "sourceUrl": detected_url,
        "name": project_name,
        "id": pid,
    }


def probe_project(source_path: Path | str) -> dict[str, Any]:
    """Probe a Drupal project directory and return complete discovered properties."""
    source = Path(source_path).resolve()
    if not source.is_dir():
        raise Problem(f"Target directory does not exist: {source}")

    drupal_root = probe_drupal_root(source)
    core_version = probe_core_version(source, drupal_root)
    custom_roots = probe_custom_roots(source, drupal_root)
    config_root = probe_config_root(source, drupal_root)
    runtime_info = probe_runtime(source)
    db_info = probe_db_credentials(source, drupal_root, runtime_info["wrapper"])

    return {
        "path": str(source),
        "name": runtime_info["name"],
        "id": runtime_info["id"],
        "drupal_root": drupal_root,
        "core_version": core_version,
        "custom_roots": custom_roots,
        "config_root": config_root,
        "wrapper": runtime_info["wrapper"],
        "uri": runtime_info["sourceUrl"],
        "database": db_info,
    }


def generate_project_config(
    probe_data: dict[str, Any], output_path: Path | str | None = None
) -> dict[str, Any]:
    """Generate and schema-validate a project.json configuration dictionary."""
    source = Path(probe_data["path"]).resolve()
    if output_path is not None:
        out_dir = Path(output_path).parent.resolve()
        repo_rel = os.path.relpath(source, out_dir)
    else:
        repo_rel = "."

    wrapper = probe_data["wrapper"]
    if wrapper == "ddev":
        identity = {"argv": ["ddev", "exec", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "fin":
        identity = {"argv": ["fin", "exec", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "lando":
        identity = {"argv": ["lando", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "compose":
        identity = {
            "argv": ["docker", "compose", "exec", "-T", "cli", "php", "-r", 'echo "ok";'],
            "expected": "ok",
        }
    else:
        identity = {"argv": [sys.executable, "-c", 'print("ok")'], "expected": "ok"}

    cfg: dict[str, Any] = {
        "schemaVersion": "1.0",
        "repository": repo_rel,
        "environment": {
            "id": f"{probe_data['id']}-local",
            "kind": "local",
            "authorized": True,
        },
        "site": {
            "uri": probe_data["uri"],
        },
        "roots": {
            "composer": ".",
            "drupal": probe_data["drupal_root"],
            "custom": probe_data["custom_roots"],
        },
        "runtime": {
            "wrapper": wrapper,
        },
        "identity": identity,
        "proposedChanges": [],
        "steps": [],
        "checks": [],
        "estimates": [],
    }

    if probe_data.get("config_root"):
        cfg["roots"]["config"] = probe_data["config_root"]

    # Validate against JSON schema
    schema(cfg, "project")
    return cfg


def init_project(
    target_path: Path | str = ".",
    output_path: Path | str | None = None,
    non_interactive: bool = False,
) -> dict[str, Any]:
    """Probe target project directory and initialize project.json."""
    source = Path(target_path).resolve()
    if not source.is_dir():
        raise Problem(f"Target directory does not exist: {source}")

    probe_data = probe_project(source)

    if output_path is not None:
        dest_file = Path(output_path).resolve()
    else:
        dest_file = source / "project.json"

    cfg = generate_project_config(probe_data, dest_file)

    # Print probe summary
    print("\n\033[1;36mDrupal 11 Upgrade Toolkit — Project Initialization\033[0m")
    print(f"Target: {source}")
    print(f"  • Drupal Root:     {probe_data['drupal_root']}")
    print(f"  • Core Version:    {probe_data['core_version'] or 'Not detected'}")
    print(f"  • Custom Roots:    {', '.join(probe_data['custom_roots']) or 'None'}")
    print(f"  • Config Sync:     {probe_data.get('config_root') or 'None'}")
    print(f"  • Dev Runtime:     {probe_data['wrapper']} ({probe_data['uri']})")
    db = probe_data["database"]
    print(
        f"  • Database:        {db['database']}@{db['host']}:{db['port']} (user: {db['username']})"
    )
    print(f"  • Output File:     {dest_file}\n")

    # Interactive confirmation
    if not non_interactive and sys.stdin.isatty():
        prompt = "Generate project.json with these settings? [Y/n]: "
        try:
            answer = input(prompt).strip().lower()
            if answer in ("n", "no"):
                print("\033[1;33mCancelled. No configuration file was written.\033[0m\n")
                return {"cancelled": True, "probe": probe_data, "output_path": str(dest_file)}
        except (KeyboardInterrupt, EOFError):
            print("\n\033[1;33mCancelled.\033[0m\n")
            return {"cancelled": True, "probe": probe_data, "output_path": str(dest_file)}

    dest_file.parent.mkdir(parents=True, exist_ok=True)
    write(dest_file, cfg)
    print("\033[1;32m✔ Configuration generated and validated against schema.\033[0m")
    print(f"\033[1;32m✔ Written to {dest_file}\033[0m\n")

    # Configure trusted_host_patterns so local dev requests are permitted
    try:
        from .trusted_hosts import ensure_trusted_hosts

        th_res = ensure_trusted_hosts(
            source_path=source,
            site_url=probe_data.get("uri", ""),
            drupal_root=probe_data.get("drupal_root", "web"),
            wrapper=probe_data.get("wrapper", "auto"),
        )
        if th_res.get("status") in ("created", "updated"):
            print(f"\033[1;32m✔ Configured trusted host patterns in {th_res.get('file')}\033[0m\n")
    except Exception:
        pass

    return {
        "cancelled": False,
        "config": cfg,
        "probe": probe_data,
        "output_path": str(dest_file),
    }


def init_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for d11 init."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="d11 init",
        description="Probe Drupal project directory and generate project.json configuration",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to Drupal project directory (default: current directory)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip interactive confirmation and write configuration",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Destination path for project.json (default: <PATH>/project.json)",
    )

    args = parser.parse_args(argv)
    try:
        res = init_project(
            target_path=args.path,
            output_path=args.output,
            non_interactive=args.yes,
        )
        return 0 if not res.get("cancelled") else 1
    except Problem as e:
        print(f"\033[1;31m✖ Error: {e}\033[0m\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\033[1;31m✖ Unexpected error: {e}\033[0m\n", file=sys.stderr)
        return 1
