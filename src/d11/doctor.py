"""Environment, prerequisite, and diagnostic verification for Drupal 11 Upgrade Toolkit."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .ai_providers import inventory
from .common import ROOT, get_d11_home, schema


def check_python_runtime() -> dict[str, Any]:
    version_str = sys.version.split()[0]
    py_info = sys.version_info
    if py_info >= (3, 10):
        return {
            "id": "python_runtime",
            "name": "Python Runtime",
            "status": "ok",
            "message": f"{version_str} ({sys.executable})",
        }
    elif py_info >= (3, 9):
        return {
            "id": "python_runtime",
            "name": "Python Runtime",
            "status": "warning",
            "message": f"{version_str} (Python 3.10+ recommended for optimal performance)",
        }
    else:
        return {
            "id": "python_runtime",
            "name": "Python Runtime",
            "status": "error",
            "message": f"{version_str} (Python 3.9+ required)",
        }


def check_d11_home(home_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        home_dir = Path(home_dir or get_d11_home())
        home_dir.mkdir(parents=True, exist_ok=True)
        probe_file = home_dir / ".d11-probe"
        probe_file.write_text("ok")
        probe_file.unlink()
        return {
            "id": "d11_home",
            "name": "D11_HOME Storage",
            "status": "ok",
            "message": f"{home_dir} (Ready, Writable)",
        }
    except Exception as e:
        return {
            "id": "d11_home",
            "name": "D11_HOME Storage",
            "status": "error",
            "message": f"{home_dir} not writable: {e}",
        }


def check_disk_headroom(home_dir: Path, min_gb: float = 20.0) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(home_dir)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        if free_gb >= min_gb:
            return {
                "id": "disk_headroom",
                "name": "Disk Headroom",
                "status": "ok",
                "message": f"{free_gb:.1f} GB free of {total_gb:.1f} GB (>= {int(min_gb)} GB recommended)",
            }
        else:
            return {
                "id": "disk_headroom",
                "name": "Disk Headroom",
                "status": "warning",
                "message": f"{free_gb:.1f} GB free (Recommended >= {int(min_gb)} GB free space on D11_HOME volume)",
            }
    except Exception as e:
        return {
            "id": "disk_headroom",
            "name": "Disk Headroom",
            "status": "warning",
            "message": f"Unable to determine disk usage for {home_dir}: {e}",
        }


def check_docker_daemon() -> dict[str, Any]:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return {
            "id": "docker_daemon",
            "name": "Docker Daemon",
            "status": "warning",
            "message": "Docker CLI not found in PATH",
        }
    try:
        res = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            server_ver = res.stdout.strip()
            return {
                "id": "docker_daemon",
                "name": "Docker Daemon",
                "status": "ok",
                "message": f"Connected (Docker Server v{server_ver})",
            }
        else:
            err = (
                res.stderr.strip().splitlines()[-1]
                if res.stderr.strip()
                else "Cannot connect to Docker daemon socket"
            )
            return {
                "id": "docker_daemon",
                "name": "Docker Daemon",
                "status": "warning",
                "message": f"Docker CLI available, but daemon unreachable: {err}",
            }
    except Exception as e:
        return {
            "id": "docker_daemon",
            "name": "Docker Daemon",
            "status": "warning",
            "message": f"Docker daemon probe failed: {e}",
        }


def check_ai_provider() -> dict[str, Any]:
    try:
        providers = inventory()
        available = [p for p in providers if p.get("available") and p.get("safeInterface")]
        if available:
            primary = available[0]
            model_info = primary.get("model") or primary.get("version") or "Ready"
            return {
                "id": "ai_connectivity",
                "name": "AI Provider",
                "status": "ok",
                "message": f"{primary['name']} ({primary['mode'].upper()}) - {model_info}",
            }
        else:
            return {
                "id": "ai_connectivity",
                "name": "AI Provider",
                "status": "warning",
                "message": "No AI providers configured (Running in offline heuristic remediation mode)",
            }
    except Exception as e:
        return {
            "id": "ai_connectivity",
            "name": "AI Provider",
            "status": "warning",
            "message": f"AI provider inspection error: {e}",
        }


def check_tool(id_: str, name: str, binary: str, required: bool = True) -> dict[str, Any]:
    found = shutil.which(binary)
    if found:
        return {
            "id": id_,
            "name": name,
            "status": "ok",
            "message": "Available",
        }
    else:
        return {
            "id": id_,
            "name": name,
            "status": "error" if required else "ok",
            "message": "Not found in PATH" if required else "Not installed (optional)",
        }


def check_trusted_hosts_status(project_path: Path | str | None = None) -> dict[str, Any] | None:
    """Diagnostic check verifying whether Drupal trusted_host_patterns match the project URL."""
    if not project_path:
        return None
    source = Path(project_path).resolve()
    if not source.is_dir():
        return None

    try:
        from .init_project import probe_project
        from .trusted_hosts import check_trusted_hosts

        probe = probe_project(source)
        uri = probe.get("uri", "")
        root = probe.get("drupal_root", "web")
        res = check_trusted_hosts(source, uri, drupal_root=root)

        if res["matches"]:
            count = res.get("patternsCount", 0)
            host = res.get("hostname", "local host")
            return {
                "id": "trusted_hosts",
                "name": "Drupal Trusted Hosts",
                "status": "ok",
                "message": f"Configured ({count} patterns cover {host})",
            }
        elif not res["configured"]:
            return {
                "id": "trusted_hosts",
                "name": "Drupal Trusted Hosts",
                "status": "warning",
                "message": f"No patterns configured in settings for {res.get('hostname')}",
            }
        else:
            return {
                "id": "trusted_hosts",
                "name": "Drupal Trusted Hosts",
                "status": "warning",
                "message": f"Configured patterns do not match {res.get('hostname')}",
            }
    except Exception as e:
        return {
            "id": "trusted_hosts",
            "name": "Drupal Trusted Hosts",
            "status": "warning",
            "message": f"Trusted hosts check skipped: {e}",
        }


def check_project_runtime_php(project_path: Path | str | None = None) -> dict[str, Any] | None:
    """Diagnostic check verifying project PHP platform configuration for Drupal 11 compatibility."""
    if not project_path:
        return None
    source = Path(project_path).resolve()
    if not source.is_dir():
        return None

    composer_file = source / "composer.json"
    ddev_config = source / ".ddev" / "config.yaml"
    docksal_env = source / ".docksal" / "docksal.env"
    lando_yml = source / ".lando.yml"
    lando_yaml = source / ".lando.yaml"

    if not composer_file.is_file() and not ddev_config.is_file() and not docksal_env.is_file():
        return None

    try:
        import re
        from .common import read

        messages = []
        is_warning = False

        if composer_file.is_file():
            manifest = read(composer_file)
            platform_php = manifest.get("config", {}).get("platform", {}).get("php")
            if platform_php:
                parts = [int(x) for x in re.findall(r"\d+", str(platform_php))[:2]]
                if parts and (parts[0] < 8 or (parts[0] == 8 and parts[1] < 3)):
                    messages.append(
                        f"composer.json platform.php is {platform_php} (relaxed to 8.3 during solver resolution)"
                    )
                    is_warning = True
                else:
                    messages.append(f"Platform PHP {platform_php}")

        if ddev_config.is_file():
            try:
                import yaml

                data = yaml.safe_load(ddev_config.read_text(errors="replace")) or {}
                ddev_php = data.get("php_version")
                if ddev_php:
                    parts = [int(x) for x in re.findall(r"\d+", str(ddev_php))[:2]]
                    if parts and (parts[0] < 8 or (parts[0] == 8 and parts[1] < 3)):
                        messages.append(
                            f"DDEV is configured for PHP {ddev_php}; Drupal 11 requires PHP 8.3+ (run: ddev config --php-version 8.3)"
                        )
                        is_warning = True
                    else:
                        messages.append(f"DDEV PHP {ddev_php}")
            except Exception:
                pass

        if docksal_env.is_file():
            try:
                content = docksal_env.read_text(errors="replace")
                m = re.search(r"CLI_IMAGE=.*?(?:php)?(\d+\.\d+)", content)
                if m:
                    ver = m.group(1)
                    parts = [int(x) for x in ver.split(".")]
                    if parts and (parts[0] < 8 or (parts[0] == 8 and parts[1] < 3)):
                        messages.append(
                            f"Docksal CLI image uses PHP {ver}; Drupal 11 requires PHP 8.3+ (update CLI_IMAGE in .docksal/docksal.env)"
                        )
                        is_warning = True
                    else:
                        messages.append(f"Docksal PHP {ver}")
            except Exception:
                pass

        lando_target = lando_yml if lando_yml.is_file() else (lando_yaml if lando_yaml.is_file() else None)
        if lando_target:
            try:
                import yaml

                ldata = yaml.safe_load(lando_target.read_text(errors="replace")) or {}
                lphp = ldata.get("config", {}).get("php")
                if lphp:
                    parts = [int(x) for x in re.findall(r"\d+", str(lphp))[:2]]
                    if parts and (parts[0] < 8 or (parts[0] == 8 and parts[1] < 3)):
                        messages.append(
                            f"Lando is configured for PHP {lphp}; Drupal 11 requires PHP 8.3+ (set config.php: '8.3' in {lando_target.name})"
                        )
                        is_warning = True
                    else:
                        messages.append(f"Lando PHP {lphp}")
            except Exception:
                pass

        if not messages:
            return {
                "id": "runtime_php",
                "name": "Drupal 11 PHP Compatibility",
                "status": "ok",
                "message": "Project PHP configuration satisfies Drupal 11 (PHP 8.3+)",
            }

        return {
            "id": "runtime_php",
            "name": "Drupal 11 PHP Compatibility",
            "status": "warning" if is_warning else "ok",
            "message": "; ".join(messages),
        }
    except Exception as e:
        return {
            "id": "runtime_php",
            "name": "Drupal 11 PHP Compatibility",
            "status": "warning",
            "message": f"PHP check skipped: {e}",
        }


def check_project_runtime_database(project_path: Path | str | None = None) -> dict[str, Any] | None:
    """Diagnostic check verifying project database version for Drupal 11 compatibility."""
    if not project_path:
        return None
    source = Path(project_path).resolve()
    if not source.is_dir():
        return None

    ddev_config = source / ".ddev" / "config.yaml"
    docksal_env = source / ".docksal" / "docksal.env"
    lando_yml = source / ".lando.yml"
    lando_yaml = source / ".lando.yaml"

    if not ddev_config.is_file() and not docksal_env.is_file() and not lando_yml.is_file() and not lando_yaml.is_file():
        return None

    try:
        import re

        messages = []
        is_error = False
        is_warning = False

        if ddev_config.is_file():
            try:
                import yaml

                data = yaml.safe_load(ddev_config.read_text(errors="replace")) or {}
                db_info = data.get("database", {})
                db_type = db_info.get("type", "").lower()
                db_ver = str(db_info.get("version", ""))
                if db_type and db_ver:
                    parts = [int(x) for x in re.findall(r"\d+", db_ver)[:2]]
                    if "mariadb" in db_type:
                        if parts and (parts[0] < 10 or (parts[0] == 10 and parts[1] < 6)):
                            messages.append(
                                f"DDEV MariaDB {db_ver} is unsupported; Drupal 11 requires MariaDB 10.6+ (run: ddev config --database mariadb:10.11)"
                            )
                            is_error = True
                        else:
                            messages.append(f"DDEV MariaDB {db_ver}")
                    elif "mysql" in db_type:
                        if parts and parts[0] < 8:
                            messages.append(
                                f"DDEV MySQL {db_ver} is unsupported; Drupal 11 requires MySQL 8.0+ (run: ddev config --database mysql:8.0)"
                            )
                            is_error = True
                        else:
                            messages.append(f"DDEV MySQL {db_ver}")
            except Exception:
                pass

        if docksal_env.is_file():
            try:
                content = docksal_env.read_text(errors="replace")
                m = re.search(r"DB_IMAGE=.*?(mariadb|mysql):(\d+\.\d+)", content, re.IGNORECASE)
                if m:
                    engine, ver = m.group(1).lower(), m.group(2)
                    parts = [int(x) for x in ver.split(".")]
                    if engine == "mariadb" and parts and (parts[0] < 10 or (parts[0] == 10 and parts[1] < 6)):
                        messages.append(f"Docksal MariaDB {ver} is unsupported; Drupal 11 requires MariaDB 10.6+")
                        is_error = True
                    elif engine == "mysql" and parts and parts[0] < 8:
                        messages.append(f"Docksal MySQL {ver} is unsupported; Drupal 11 requires MySQL 8.0+")
                        is_error = True
                    else:
                        messages.append(f"Docksal {engine.capitalize()} {ver}")
            except Exception:
                pass

        lando_target = lando_yml if lando_yml.is_file() else (lando_yaml if lando_yaml.is_file() else None)
        if lando_target:
            try:
                import yaml

                ldata = yaml.safe_load(lando_target.read_text(errors="replace")) or {}
                services = ldata.get("services", {})
                for sname, sdata in services.items():
                    stype = str(sdata.get("type", "")).lower()
                    m = re.search(r"(mariadb|mysql):(\d+\.\d+)", stype)
                    if m:
                        engine, ver = m.group(1), m.group(2)
                        parts = [int(x) for x in ver.split(".")]
                        if engine == "mariadb" and parts and (parts[0] < 10 or (parts[0] == 10 and parts[1] < 6)):
                            messages.append(f"Lando service '{sname}' ({engine} {ver}) is unsupported; Drupal 11 requires MariaDB 10.6+")
                            is_error = True
                        elif engine == "mysql" and parts and parts[0] < 8:
                            messages.append(f"Lando service '{sname}' ({engine} {ver}) is unsupported; Drupal 11 requires MySQL 8.0+")
                            is_error = True
                        else:
                            messages.append(f"Lando {engine.capitalize()} {ver}")
            except Exception:
                pass

        if not messages:
            return {
                "id": "runtime_database",
                "name": "Drupal 11 Database Compatibility",
                "status": "ok",
                "message": "Configured database satisfies Drupal 11 requirements (MariaDB 10.6+ / MySQL 8.0+)",
            }

        status = "error" if is_error else ("warning" if is_warning else "ok")
        return {
            "id": "runtime_database",
            "name": "Drupal 11 Database Compatibility",
            "status": status,
            "message": "; ".join(messages),
        }
    except Exception as e:
        return {
            "id": "runtime_database",
            "name": "Drupal 11 Database Compatibility",
            "status": "warning",
            "message": f"Database check skipped: {e}",
        }


def check_project_drush_version(project_path: Path | str | None = None) -> dict[str, Any] | None:
    """Diagnostic check verifying Drush version for Drupal 11 compatibility."""
    if not project_path:
        return None
    source = Path(project_path).resolve()
    if not source.is_dir():
        return None

    lock_file = source / "composer.lock"
    json_file = source / "composer.json"

    if not lock_file.is_file() and not json_file.is_file():
        return None

    try:
        import re
        from .common import read

        drush_ver = None
        if lock_file.is_file():
            lock_data = read(lock_file)
            packages = lock_data.get("packages", []) + lock_data.get("packages-dev", [])
            for p in packages:
                if p.get("name") == "drush/drush":
                    drush_ver = p.get("version", "").lstrip("v")
                    break

        if not drush_ver and json_file.is_file():
            json_data = read(json_file)
            reqs = {**json_data.get("require", {}), **json_data.get("require-dev", {})}
            drush_ver = reqs.get("drush/drush")

        if not drush_ver:
            return None

        m = re.search(r"(\d+)(?:\.(\d+))?", str(drush_ver))
        if m:
            major = int(m.group(1))
            minor = int(m.group(2) or 0)
            if major < 12 or (major == 12 and minor < 5):
                return {
                    "id": "project_drush",
                    "name": "Drush Version",
                    "status": "warning",
                    "message": f"Drush {drush_ver} configured; Drupal 11 requires Drush 13+ (or 12.5+ for transition)",
                }
            else:
                return {
                    "id": "project_drush",
                    "name": "Drush Version",
                    "status": "ok",
                    "message": f"Drush {drush_ver} satisfies Drupal 11 requirements",
                }

        return {
            "id": "project_drush",
            "name": "Drush Version",
            "status": "ok",
            "message": f"Drush {drush_ver} configured",
        }
    except Exception as e:
        return {
            "id": "project_drush",
            "name": "Drush Version",
            "status": "warning",
            "message": f"Drush check skipped: {e}",
        }


def run_doctor(
    home_dir: Path | str | None = None,
    project_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run all environment diagnostic checks and return structured report."""
    target_home = Path(home_dir).resolve() if home_dir else get_d11_home()

    checks = [
        check_python_runtime(),
        check_d11_home(target_home),
        check_disk_headroom(target_home),
        check_docker_daemon(),
        check_ai_provider(),
        check_tool("git_cli", "Git CLI", "git", required=True),
        check_tool("composer_cli", "Composer CLI", "composer", required=False),
        check_tool("ddev_cli", "DDEV CLI", "ddev", required=False),
    ]

    th_check = check_trusted_hosts_status(project_path)
    if th_check is not None:
        checks.append(th_check)

    php_check = check_project_runtime_php(project_path)
    if php_check is not None:
        checks.append(php_check)

    db_check = check_project_runtime_database(project_path)
    if db_check is not None:
        checks.append(db_check)

    drush_check = check_project_drush_version(project_path)
    if drush_check is not None:
        checks.append(drush_check)

    total = len(checks)
    errors = sum(1 for c in checks if c["status"] == "error")
    warnings = sum(1 for c in checks if c["status"] == "warning")
    passed_count = sum(1 for c in checks if c["status"] == "ok")
    overall_passed = errors == 0

    report = {
        "schemaVersion": "1.0",
        "toolkitVersion": "2.0.0",
        "toolkitRoot": str(ROOT),
        "home": str(target_home),
        "passed": overall_passed,
        "summary": {
            "total": total,
            "passed": passed_count,
            "warnings": warnings,
            "errors": errors,
        },
        "checks": checks,
    }

    schema(report, "doctor")
    return report


def doctor_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for d11 doctor."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="d11 doctor",
        description="Verify local environment, tools, and prerequisites",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable diagnostic report in JSON",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Override D11_HOME path for verification",
    )
    parser.add_argument(
        "-p",
        "--project",
        default=None,
        help="Path to Drupal project directory for project-specific diagnostic checks",
    )

    args = parser.parse_args(argv)
    report = run_doctor(home_dir=args.home, project_path=args.project)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1

    print("\033[1;36m=== Drupal 11 Upgrade Toolkit Doctor ===\033[0m")
    print(f"  • Toolkit:     v{report['toolkitVersion']} ({report['toolkitRoot']})")
    print(f"  • D11_HOME:    {report['home']}")

    for c in report["checks"]:
        if c["status"] == "ok":
            icon = "\033[1;32m✔\033[0m"
        elif c["status"] == "warning":
            icon = "\033[1;33m⚠\033[0m"
        else:
            icon = "\033[1;31m✖\033[0m"
        print(f"  • {c['name']:<16} {icon} {c['message']}")

    summary = report["summary"]
    if report["passed"]:
        if summary["warnings"] > 0:
            print(
                f"\n\033[1;33m⚠ Toolkit environment verified with {summary['warnings']} warning(s).\033[0m\n"
            )
        else:
            print("\n\033[1;32m✔ Toolkit environment fully verified.\033[0m\n")
        return 0
    else:
        print(
            f"\n\033[1;31m✖ Toolkit environment check failed with {summary['errors']} error(s).\033[0m\n"
        )
        return 1
