"""Disposable Drupal 10 analysis runtime for Upgrade Status and Drupal Rector."""

from __future__ import annotations

import base64
import gzip
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .common import Problem, command, digest, file_hash, get_d11_home, now, write
from .profile import get_profile
from .source_runtime import get_runtime_adapter


def inventory(source):
    source = Path(source)
    if (source / ".git").is_dir():
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=source, capture_output=True, text=True
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True
        )
        lines = [
            l
            for l in status.stdout.splitlines()
            if not any(
                l.endswith(" " + x) or (" " + x + "/" in l) or l.endswith("/" + x)
                for x in ("private", ".docksal", ".ddev", "artifacts", "sites/default/files")
            )
        ]
        return {"git_head": head.stdout.strip(), "git_status": "\n".join(lines)}
    from .guided_setup import source_inventory

    return source_inventory(source)


def _failure(identifier, message, category, rec=None):
    result = {
        "id": identifier,
        "status": "tool_failure",
        "required": True,
        "message": message,
        "failureCategory": category,
        "findingCount": None,
    }
    if rec is not None:
        result["command"] = rec
    return result


def _record(identifier, rec, parser, version=None, accepted_codes=(0, 1, 2)):
    raw = rec.get("stdout", "").strip()
    value = None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        decoder = json.JSONDecoder()
        idx = 0
        candidate = None
        while idx < len(raw):
            while idx < len(raw) and raw[idx].isspace():
                idx += 1
            if idx >= len(raw):
                break
            try:
                obj, end = decoder.raw_decode(raw, idx)
                try:
                    valid, _, _ = parser(obj)
                    if valid:
                        candidate = obj
                        break
                except Exception:
                    pass
                if candidate is None:
                    candidate = obj
                idx = end
            except Exception:
                idx += 1
        if candidate is not None:
            value = candidate
        else:
            start_bracket = raw.find("[")
            start_brace = raw.find("{")
            if start_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
                end_bracket = raw.rfind("]")
                if end_bracket > start_bracket:
                    try:
                        value = json.loads(raw[start_bracket : end_bracket + 1])
                    except Exception:
                        pass
            elif start_brace != -1:
                end_brace = raw.rfind("}")
                if end_brace > start_brace:
                    try:
                        value = json.loads(raw[start_brace : end_brace + 1])
                    except Exception:
                        pass
    if value is None:
        return _failure(
            identifier, "Scanner returned malformed structured output", "malformed_output", rec
        )
    try:
        valid, count, details = parser(value)
    except (ValueError, TypeError, KeyError):
        return _failure(
            identifier, "Scanner returned malformed structured output", "malformed_output", rec
        )
    if not valid:
        return _failure(
            identifier,
            "Scanner returned an unsupported structured response",
            "malformed_output",
            rec,
        )
    if rec.get("exitCode") not in accepted_codes:
        return _failure(identifier, "Scanner process failed", "execution_failure", rec)
    return {
        "id": identifier,
        "status": "findings" if count else "passed",
        "required": True,
        "message": identifier.replace("_", " ") + " disposable read-only scan",
        "command": rec,
        "findingCount": count,
        "failureCategory": "findings" if count else None,
        "version": version,
        **details,
    }


def _copy_analysis(source, target, excluded_paths=()):
    ignored = {".git", ".ddev", ".docksal", "node_modules", "sites/default/files", "private", "artifacts", "tools"}
    ignored_extensions = (".sql", ".sql.gz", ".tar.gz", ".tgz", ".zip")

    def ignore(path, names):
        rel = Path(path).resolve().relative_to(Path(source).resolve())
        return [
            name
            for name in names
            if name in ignored
            or any(name.endswith(ext) for ext in ignored_extensions)
            or str(rel / name).replace("\\", "/") in ignored
            or str(rel / name) in excluded_paths
            or ("sites" in rel.parts and name == "files")
        ]

    shutil.copytree(source, target, ignore=ignore, symlinks=True, ignore_dangling_symlinks=True)


def _core_version(site):
    lock = site / "composer.lock"
    if not lock.is_file():
        return None
    data = json.loads(lock.read_text())
    for item in data.get("packages", []):
        if item.get("name") in ("drupal/core-recommended", "drupal/core"):
            return str(item.get("version", "")).lstrip("v") or None


def read_locked_packages(site):
    return json.loads((site / "composer.lock").read_text()).get("packages", [])


def _configure_analysis_settings(site_dir, document_root):
    doc_dir = site_dir / document_root if str(document_root) != "." else site_dir
    sites_dir = doc_dir / "sites"
    if not sites_dir.exists():
        sites_dir.mkdir(parents=True, exist_ok=True)
    target_dirs = [sites_dir / "default"]
    if sites_dir.is_dir():
        for p in sites_dir.iterdir():
            if p.is_dir() and p != sites_dir / "default" and (p / "settings.php").exists():
                target_dirs.append(p)

    snippet = r"""
// D11 disposable analysis container database configuration
if (getenv('MYSQL_DATABASE')) {
    $db_driver = 'mysql';
    $app_root_dir = defined('DRUPAL_ROOT') ? DRUPAL_ROOT : ($app_root ?? '');
    $has_mysql_module = file_exists($app_root_dir . '/core/modules/mysql');
    $databases['default']['default'] = [
        'driver' => $db_driver,
        'database' => getenv('MYSQL_DATABASE'),
        'username' => getenv('MYSQL_USER'),
        'password' => getenv('MYSQL_PASSWORD'),
        'host' => 'db',
        'port' => 3306,
        'prefix' => '',
    ];
    if ($has_mysql_module) {
        $databases['default']['default']['namespace'] = 'Drupal\\mysql\\Driver\\Database\\mysql';
        $databases['default']['default']['autoload'] = 'core/modules/mysql/src/Driver/Database/mysql/';
    } else {
        $databases['default']['default']['namespace'] = 'Drupal\\Core\\Database\\Driver\\mysql';
        $databases['default']['default']['autoload'] = 'core/lib/Drupal/Core/Database/Driver/mysql/';
    }
    if (empty($settings['hash_salt'])) {
        $settings['hash_salt'] = 'd11_disposable_analysis_salt_rehearsal';
    }
    $settings['trusted_host_patterns'][] = '.*';
    $settings['cache']['default'] = 'cache.backend.database';
    $settings['cache']['bins']['render'] = 'cache.backend.database';
    $settings['cache']['bins']['page'] = 'cache.backend.database';
    $settings['cache']['bins']['dynamic_page_cache'] = 'cache.backend.database';
    $settings['config_readonly'] = FALSE;
}
"""
    for settings_dir in target_dirs:
        settings_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(settings_dir, 0o777)
        except Exception:
            pass
        for target in settings_dir.glob("settings*.php"):
            try:
                os.chmod(target, 0o666)
            except Exception:
                pass
            content = target.read_text(encoding="utf-8", errors="replace")
            if "// D11 disposable analysis container database configuration" not in content:
                target.write_text(content + "\n" + snippet + "\n", encoding="utf-8")
        settings_php = settings_dir / "settings.php"
        if not settings_php.exists():
            settings_php.write_text("<?php\n" + snippet + "\n", encoding="utf-8")


def _tune_analysis_tools(site_dir: Path) -> None:
    """Tune scanner configurations for parallel multi-core performance."""
    cpu_count = min(os.cpu_count() or 4, 8)
    for neon_file in site_dir.rglob("deprecation_testing_template.neon"):
        try:
            content = neon_file.read_text(encoding="utf-8")
            if "maximumNumberOfProcesses:" in content:
                tuned = re.sub(
                    r"maximumNumberOfProcesses:\s*0",
                    f"maximumNumberOfProcesses: {cpu_count}\n\t\tprocessTimeout: 300.0",
                    content,
                )
                neon_file.write_text(tuned, encoding="utf-8")
        except Exception:
            pass


def _analysis_compose(site, runtime, project, profile=None):
    prof = get_profile(profile)
    runtime.mkdir(parents=True, exist_ok=True)
    env = {
        "MYSQL_DATABASE": "analysis",
        "MYSQL_USER": "analysis",
        "MYSQL_PASSWORD": digest(project)[:32],
        "MYSQL_ROOT_PASSWORD": digest(project + "-root")[:32],
    }
    env_file = runtime / "runtime.env"
    env_file.write_text("\n".join(k + "=" + v for k, v in env.items()) + "\n")
    os.chmod(env_file, 0o600)
    network_ids = (
        _checked(["docker", "network", "ls", "--format", "{{.ID}}"], runtime, 30)
        .get("stdout", "")
        .split()
    )
    networks = (
        json.loads(_checked(["docker", "network", "inspect", *network_ids], runtime, 30)["stdout"])
        if network_ids
        else []
    )
    occupied = [
        ipaddress.ip_network(config["Subnet"], strict=False)
        for network in networks
        for config in (network.get("IPAM", {}).get("Config") or [])
        if config.get("Subnet")
    ]
    candidates = list(ipaddress.ip_network("10.240.0.0/16").subnets(new_prefix=28))
    start = int(digest(project)[:8], 16) % len(candidates)
    subnet = next(
        (
            candidate
            for candidate in candidates[start:] + candidates[:start]
            if not any(
                candidate.version == used.version and candidate.overlaps(used) for used in occupied
            )
        ),
        None,
    )
    if subnet is None:
        raise Problem("No unused private subnet is available for the disposable analysis network")
    compose = {
        "services": {
            "db": {
                "image": prof["images"]["db"],
                "env_file": [str(env_file)],
                "volumes": ["analysis_db:/var/lib/mysql"],
                "networks": ["backend"],
            },
            "cli": {
                "image": prof["images"]["cli"],
                "working_dir": "/var/www",
                "env_file": [str(env_file)],
                "volumes": [f"{site}:/var/www"],
                "networks": ["backend"],
                "entrypoint": ["tail", "-f", "/dev/null"],
            },
        },
        "networks": {"backend": {"internal": True, "ipam": {"config": [{"subnet": str(subnet)}]}}},
        "volumes": {"analysis_db": {}},
    }
    path = runtime / "compose.json"
    write(path, compose)
    return ["docker", "compose", "-p", project, "-f", str(path)]


def _checked(argv, cwd, timeout=600, input_text=None):
    rec = command(argv, cwd, timeout, input_text=input_text)
    if rec.get("exitCode") != 0:
        raise Problem(rec.get("stderr") or "Analysis command failed")
    return rec


def _run_rector(prefix, analysis, context, source):
    mapped = _custom_paths(context, source)
    if not mapped:
        return {
            "id": "drupal_rector",
            "status": "passed",
            "required": True,
            "message": "No configured custom-code roots require Rector coverage",
            "findingCount": 0,
            "changes": [],
            "failureCategory": None,
        }
    version = command(
        prefix + ["exec", "-T", "cli", "php", "vendor/bin/rector", "--version"], analysis, 120
    )
    site = analysis / "site"
    root = (
        Path(context.get("roots", {}).get("drupal", source))
        .resolve()
        .relative_to(source.resolve())
        .as_posix()
    )
    config = site / "d11-analysis-rector.php"
    encoded = base64.b64encode(("/" + root + "/core").encode()).decode()
    config.write_text(
        "<?php\nreturn static function (\\Rector\\Config\\RectorConfig $config): void {\n$config->sets([\\DrupalRector\\Set\\Drupal10SetList::DRUPAL_10, \\DrupalRector\\Set\\Drupal11SetList::DRUPAL_11]);\n$config->fileExtensions(['php','module','theme','install','profile','inc','engine']);\n$config->autoloadPaths([__DIR__ . base64_decode('"
        + encoded
        + "')]);\n};\n"
    )
    record = command(
        prefix
        + [
            "exec",
            "-T",
            "cli",
            "php",
            "vendor/bin/rector",
            "process",
            *mapped,
            "--config=d11-analysis-rector.php",
            "--dry-run",
            "--output-format=json",
            "--no-progress-bar",
        ],
        analysis,
        1800,
    )
    return _rector_record(record, version.get("stdout", "")[:300])


def _import_database(prefix, cwd, database):
    argv = prefix + [
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE"',
    ]
    started = now()
    started_monotonic = time.monotonic()
    source = gzip.open(database, "rb") if str(database).endswith(".gz") else open(database, "rb")
    with source:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if os.environ.get("D11_STOP_FILE") and Path(os.environ["D11_STOP_FILE"]).exists():
                    proc.terminate()
                    raise Problem("Disposable analysis import stopped")
                proc.stdin.write(chunk)
            proc.stdin.close()
            stderr = proc.stderr.read().decode(errors="replace")
            stdout = proc.stdout.read().decode(errors="replace")
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
    return {
        "argv": argv,
        "cwd": str(cwd),
        "startedAt": started,
        "finishedAt": now(),
        "exitCode": code,
        "status": "passed" if code == 0 else "tool_failure",
        "stdout": stdout,
        "stderr": stderr,
        "elapsedSeconds": round(time.monotonic() - started_monotonic, 6),
        "failureCategory": None if code == 0 else "execution_failure",
    }


def _custom_paths(context, root):
    result = []
    for value in context.get("roots", {}).get("custom", []):
        try:
            result.append(str(Path(value).resolve().relative_to(root.resolve())))
        except ValueError:
            pass
    return result


def _upgrade_record(rec, version=None):
    def parse(value):
        valid = isinstance(value, list) and all(
            isinstance(x, dict)
            and x.get("type") == "issue"
            and x.get("description")
            and isinstance(x.get("location"), dict)
            for x in value
        )
        return valid, len(value) if valid else None, {"issues": value if valid else []}

    result = _record("upgrade_status", rec, parse, version, accepted_codes=(0, 1, 2, 3))
    failures = [
        issue
        for issue in result.get("issues", [])
        if "PHPStan command failed" in issue.get("description", "")
    ]
    if failures:
        result.update(
            status="findings",
            failureCategory="findings",
            message=f"PHPStan reported {len(failures)} analysis errors on project entries; inspect scanner evidence",
            scannerFailures=failures,
        )
        result["issues"] = [issue for issue in result["issues"] if issue not in failures]
        result["findingCount"] = len(result["issues"])
    return result


def _rector_record(rec, version=None):
    def parse(value):
        if isinstance(value, list):
            return True, len(value), {"changes": value}
        if not isinstance(value, dict):
            return False, None, {}
        if not any(key in value for key in ("file_diffs", "changed_files", "totals")):
            return False, None, {}
        changes = value.get("file_diffs", value.get("changed_files", []))
        if not isinstance(changes, (dict, list)):
            changes = []
        values = list(changes.values()) if isinstance(changes, dict) else changes
        return True, len(values), {"changes": values}

    raw = rec.get("stdout", "")
    parsed = None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(raw):
            while idx < len(raw) and raw[idx].isspace():
                idx += 1
            if idx >= len(raw):
                break
            try:
                obj, end = decoder.raw_decode(raw, idx)
                if isinstance(obj, dict) and any(k in obj for k in ("file_diffs", "changed_files", "totals")):
                    parsed = obj
                    break
                idx = end
            except Exception:
                idx += 1

    if isinstance(parsed, dict) and parsed.get("errors"):
        changes = parsed.get("file_diffs", parsed.get("changed_files", []))
        if not changes:
            return _failure(
                "drupal_rector",
                "Rector reported analysis errors; inspect its structured command output",
                "execution_failure",
                rec,
            )

    return _record("drupal_rector", rec, parse, version)


def _impact_record(rec):
    raw = rec.get("stdout", "").strip()
    value = None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
            except Exception:
                pass
    if value is None or not isinstance(value, dict) or not isinstance(value.get("modules"), dict):
        return _failure(
            "module_impact", "Module impact probe returned malformed JSON", "malformed_output", rec
        )
    if rec.get("exitCode") != 0:
        return _failure("module_impact", "Module impact probe failed", "execution_failure", rec)
    populated = sum(1 for module in value["modules"].values() if module.get("populatedContent"))
    return {
        "id": "module_impact",
        "status": "findings" if populated else "passed",
        "required": True,
        "message": "Field-provider and populated-content impact probe",
        "failureCategory": "findings" if populated else None,
        "findingCount": populated,
        "modules": value["modules"],
        "command": rec,
    }


def _extension_version(rec, name):
    raw = rec.get("stdout", "").strip()
    value = None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
            except Exception:
                value = None
    if isinstance(value, dict):
        candidates = [value.get(name), *value.values()]
        for item in candidates:
            if isinstance(item, dict) and item.get("version"):
                return str(item["version"])
    return None


def _installed_run(cfg, context, fast: bool = True):
    """Compatibility fallback for fixtures and legacy projects without a managed snapshot."""
    root = Path(context["roots"]["composer"])
    uri = "--uri=" + cfg["site"]["uri"]
    checks = []
    try:
        catalog = json.loads(context["runtime"]["commands"]["capabilities"].get("stdout", ""))
        names = {item["name"] for item in catalog.get("commands", [])}
    except (ValueError, TypeError, KeyError):
        names = set()
    wrapper = (
        context.get("runtime", {}).get("wrapper")
        or cfg.get("runtime", {}).get("wrapper", "fin")
    )
    drush_cmd = context.get("runtime", {}).get("commands", {}).get("drush")
    if not drush_cmd:
        try:
            drush_cmd = get_runtime_adapter(wrapper).drush_prefix()
        except Problem:
            drush_cmd = ["fin", "drush"]
    if "upgrade_status:analyze" in names:
        us_args = [
            *drush_cmd,
            "upgrade_status:analyze",
            "--all",
            "--ignore-uninstalled",
            "--skip-existing",
            "--phpstan-memory-limit=2048M",
            "--format=codeclimate",
        ]
        if fast:
            us_args.append("--ignore-contrib")
        us_args.append(uri)
        checks.append(_upgrade_record(command(us_args, root, 1200)))
    else:
        checks.append(
            {
                "id": "upgrade_status",
                "status": "unknown",
                "required": True,
                "message": "Upgrade Status is unavailable and no disposable managed snapshot was supplied",
                "failureCategory": "missing_capability",
                "findingCount": None,
            }
        )
    custom = _custom_paths(context, root)
    rector = root / "vendor/bin/rector"
    if not custom:
        checks.append(
            {
                "id": "drupal_rector",
                "status": "passed",
                "required": True,
                "message": "No configured custom-code roots require Rector coverage",
                "findingCount": 0,
                "changes": [],
                "failureCategory": None,
            }
        )
    elif rector.is_file():
        php_cmd = context.get("runtime", {}).get("commands", {}).get("php")
        if not php_cmd:
            try:
                php_cmd = get_runtime_adapter(wrapper).php_prefix()
            except Problem:
                php_cmd = ["fin", "exec", "php"]
        checks.append(
            _rector_record(
                command(
                    [
                        *php_cmd,
                        "vendor/bin/rector",
                        "process",
                        *custom,
                        "--dry-run",
                        "--output-format=json",
                        "--no-progress-bar",
                    ],
                    root,
                    1200,
                )
            )
        )
    else:
        checks.append(
            {
                "id": "drupal_rector",
                "status": "unknown",
                "required": True,
                "message": "Drupal Rector is unavailable and no disposable managed snapshot was supplied",
                "failureCategory": "missing_capability",
                "findingCount": None,
            }
        )
    return checks


def compute_stack_key(context: dict, cfg: dict, source: Path) -> str:
    """Derive deterministic hash for analysis stack reuse across runs."""
    import hashlib
    parts = []
    source = Path(source)
    lock = source / "composer.lock"
    if lock.is_file():
        parts.append(file_hash(lock))
    else:
        cj = source / "composer.json"
        if cj.is_file():
            parts.append(file_hash(cj))
    db_str = cfg.get("recovery", {}).get("database", "")
    if db_str:
        db = Path(db_str)
        if db.is_file():
            parts.append(file_hash(db))
    parts.append(str(cfg.get("runtimeProfile", "")))
    custom_roots = sorted(str(r) for r in context.get("roots", {}).get("custom", []))
    parts.extend(custom_roots)
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def run(cfg, context, out, workflow=None, pid=None, fast: bool = True):
    """Install and run scanners in a disposable copy; never mutate the candidate."""
    out = Path(out)
    source = Path(context["roots"]["composer"])
    if workflow is None or pid is None:
        checks = _installed_run(cfg, context, fast=fast)
        write(out / "audit-tools.json", {"mode": "installed-fallback", "checks": checks})
        return checks
    analysis = out / "analysis"
    site = analysis / "site"
    runtime = analysis / "runtime"
    stack_key = compute_stack_key(context, cfg, source)
    clean_pid = re.sub(r"[^a-zA-Z0-9_-]", "", str(pid))[:8] or "run"
    project = f"d11-stack-{clean_pid}-{stack_key[:10]}"
    prof = get_profile(cfg.get("runtimeProfile"))
    before = inventory(source)
    checks = []
    lifecycle = []
    prefix = None
    try:
        excluded = []
        for extension in context.get("extensions", []):
            if (
                extension.get("type") == "theme"
                and extension.get("installed") is False
                and "/themes/custom/" in extension.get("path", "").replace("\\", "/")
            ):
                try:
                    excluded.append(
                        str(Path(extension["path"]).parent.resolve().relative_to(source.resolve()))
                    )
                except ValueError:
                    pass
        _copy_analysis(source, site, excluded)
        lifecycle.append(
            {
                "step": "copy",
                "status": "passed",
                "at": now(),
                "sourceHash": digest(before),
                "excludedUninstalledCustomThemes": excluded,
            }
        )
        version = _core_version(site)
        if not version:
            raise Problem("Cannot determine the Drupal 10 core version from composer.lock")
        manifest = json.loads((site / "composer.json").read_text())
        lock = json.loads((site / "composer.lock").read_text())
        core_dev = next(
            (
                item
                for item in lock.get("packages-dev", [])
                if item.get("name") == "drupal/core-dev"
            ),
            {},
        )
        for package, constraint in core_dev.get("require", {}).items():
            if (
                package.startswith("phpstan/")
                or package == "mglaman/phpstan-drupal"
                or package in manifest.get("require", {})
            ):
                continue
            manifest.setdefault("require-dev", {}).setdefault(package, constraint)
        # The disposable scanner toolchain must not inherit obsolete PHPStan
        # development pins from the application. Upgrade Status/Rector resolve
        # their compatible analysis dependencies together below.
        ANALYSIS_PACKAGES = (
            "phpstan/phpstan",
            "mglaman/phpstan-drupal",
            "mglaman/drupal-check",
            "drupal/core-dev",
            "drupal/upgrade_status",
            "palantirnet/drupal-rector",
        )
        for package in ANALYSIS_PACKAGES:
            manifest.get("require-dev", {}).pop(package, None)
            manifest.get("require", {}).pop(package, None)
        for package in ("drupal/core", "drupal/core-recommended"):
            if package in manifest.get("require", {}):
                manifest["require"][package] = version
        for package in read_locked_packages(site):
            if package["name"] in manifest.get("require", {}) and package["name"] not in ANALYSIS_PACKAGES:
                manifest["require"][package["name"]] = package["version"]
        for package in ANALYSIS_PACKAGES:
            manifest.get("require", {}).pop(package, None)
        # Drupal Finder is part of the analysis toolchain as well as Drush.
        # Old application pins can prevent PHPStan Drupal 2 from resolving.
        manifest.setdefault("require", {})["webflo/drupal-finder"] = "^1.3.1"
        manifest["require"]["composer/installers"] = "^2.3"
        manifest.setdefault("require-dev", {}).update(
            {"drupal/upgrade_status": "^4.3", "palantirnet/drupal-rector": "^1.1"}
        )
        plugins = {
            item["name"]: False
            for item in lock.get("packages", []) + lock.get("packages-dev", [])
            if item.get("type") == "composer-plugin"
        }
        plugins["composer/installers"] = True
        # Do not add a wildcard: canonical JSON sorts "*" first and Composer
        # applies the first matching rule, which would disable the installer.
        manifest.setdefault("config", {})["allow-plugins"] = plugins
        write(site / "composer.json", manifest)
        stack_dir = get_d11_home() / "cache" / "stacks" / stack_key
        cache_vendor = stack_dir / "vendor"
        cache_lock = stack_dir / "composer.lock"
        refresh = cfg.get("refresh", False) or cfg.get("refresh_stack", False)
        if not refresh and cache_vendor.is_dir() and cache_lock.is_file():
            if not (site / "vendor").exists():
                try:
                    shutil.copytree(cache_vendor, site / "vendor", symlinks=True)
                except Exception:
                    pass

        composer_cache = get_d11_home() / "cache" / "composer"
        composer_cache.mkdir(parents=True, exist_ok=True)
        install = command(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "composer",
                "-u",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{site}:/var/www",
                "-v",
                f"{composer_cache}:/tmp/composer-cache",
                "-e",
                "COMPOSER_CACHE_DIR=/tmp/composer-cache",
                "-w",
                "/var/www",
                prof["images"]["cli"],
                "update",
                "--with-all-dependencies",
                "--no-scripts",
                "--no-interaction",
            ],
            analysis,
            1800,
        )
        lifecycle.append(
            {"step": "install_tools", "status": install.get("status"), "command": install}
        )
        if install.get("exitCode") != 0:
            checks = [
                _failure(
                    "upgrade_status",
                    "Upgrade Status installation failed in the disposable analysis copy",
                    "tool_installation",
                    install,
                ),
                _failure(
                    "drupal_rector",
                    "Drupal Rector installation failed in the disposable analysis copy",
                    "tool_installation",
                    install,
                ),
            ]
            return checks
        if (site / "vendor").is_dir() and not cache_vendor.is_dir():
            try:
                stack_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(site / "vendor", cache_vendor, symlinks=True)
                if (site / "composer.lock").is_file():
                    shutil.copy(site / "composer.lock", cache_lock)
            except Exception:
                pass
        _tune_analysis_tools(site)
        prefix = _analysis_compose(site, runtime, project, profile=prof)
        _checked(prefix + ["up", "-d"], analysis, 600)
        # Reload the newly resolved installer plugin before regenerating paths;
        # copied vendor metadata can otherwise retain default library paths.
        _checked(
            prefix
            + [
                "exec",
                "-T",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "cli",
                "composer",
                "dump-autoload",
                "--no-scripts",
                "--no-interaction",
            ],
            analysis,
            120,
        )
        # Rector does not depend on Drupal bootstrap or the imported database.
        checks.append(_run_rector(prefix, analysis, context, source))
        for _ in range(60):
            ready = command(
                prefix
                + [
                    "exec",
                    "-T",
                    "db",
                    "sh",
                    "-c",
                    'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE" -Nse "SELECT 1"',
                ],
                analysis,
                10,
            )
            if ready.get("exitCode") == 0:
                break
            time.sleep(1)
        else:
            raise Problem("Disposable analysis database did not become ready")
        database = Path(cfg.get("recovery", {}).get("database", ""))
        if not database.is_file():
            raise Problem("Sanitized recovery database is unavailable for disposable analysis")
        db_ready_marker = stack_dir / "db_ready.json"
        table_check = command(
            prefix
            + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE" -Nse "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\'analysis\' AND table_name=\'users\'"',
            ],
            analysis,
            10,
        )
        already_imported = (
            not refresh
            and db_ready_marker.is_file()
            and table_check.get("exitCode") == 0
            and table_check.get("stdout", "").strip() not in ("", "0")
        )
        if already_imported:
            imported = {"status": "passed", "exitCode": 0, "stdout": "Reused existing database volume"}
            lifecycle.append(
                {"step": "import_sanitized_database", "status": "passed", "reused": True}
            )
        else:
            imported = _import_database(prefix, analysis, database)
            lifecycle.append(
                {"step": "import_sanitized_database", "status": imported["status"], "command": imported}
            )
            if imported.get("exitCode") == 0:
                try:
                    stack_dir.mkdir(parents=True, exist_ok=True)
                    write(db_ready_marker, {"stackKey": stack_key, "importedAt": now()})
                except Exception:
                    pass
        if imported["exitCode"] != 0:
            raise Problem("Disposable analysis database import failed")
        document_root = (
            Path(context.get("roots", {}).get("drupal", source))
            .resolve()
            .relative_to(source.resolve())
        )
        container_root = "/var/www" + (
            "/" + document_root.as_posix() if str(document_root) != "." else ""
        )
        _configure_analysis_settings(site, document_root)
        drush = prefix + [
            "exec",
            "-T",
            "-w",
            container_root,
            "cli",
            "php",
            "/var/www/vendor/bin/drush.php",
            "--root=" + container_root,
            "--uri=" + cfg["site"]["uri"],
        ]
        root_expression = (
            "base64_decode('" + base64.b64encode(container_root.encode()).decode() + "')"
        )
        autoload = command(
            prefix
            + [
                "exec",
                "-T",
                "cli",
                "php",
                "-r",
                'require "vendor/autoload.php"; echo json_encode(["corePath"=>\\Composer\\InstalledVersions::getInstallPath("drupal/core"),"rootFiles"=>array_map("file_exists",array_map(fn($file)=>'
                + root_expression
                + ' . $file,["/autoload.php","/core/includes/common.inc","/core/core.services.yml","/core/misc/drupal.js"]))]); exit(class_exists("Drupal\\Core\\DrupalKernel") ? 0 : 1);',
            ],
            analysis,
            120,
        )
        lifecycle.append(
            {"step": "analysis_autoload", "status": autoload["status"], "command": autoload}
        )
        if autoload.get("exitCode") != 0:
            raise Problem("Analysis Drupal autoload failed; inspect analysis-runtime.json")
        probe = command(
            drush
            + [
                "php:eval",
                'echo json_encode(["database"=>\\Drupal::database()->query("SELECT DATABASE()")->fetchField(),"root"=>DRUPAL_ROOT]);',
            ],
            analysis,
            120,
        )
        lifecycle.append(
            {"step": "analysis_bootstrap", "status": probe["status"], "command": probe}
        )
        raw_probe = probe.get("stdout", "").strip()
        try:
            identity = json.loads(raw_probe)
        except (ValueError, TypeError):
            m = re.search(r"\{[^{}]*\"database\"[^{}]*\}", raw_probe)
            if m:
                try:
                    identity = json.loads(m.group(0))
                except Exception:
                    identity = {}
            else:
                identity = {}
        if (
            probe.get("exitCode") != 0
            or identity.get("database") != "analysis"
            or identity.get("root") != container_root
        ):
            raise Problem(
                "Analysis Drupal bootstrap or database identity check failed; inspect analysis-runtime.json (analysis_bootstrap)"
            )
        _checked(
            drush + ["pm:enable", "upgrade_status", "--yes", "--uri=" + cfg["site"]["uri"]],
            analysis,
            600,
        )
        us_version = _checked(
            drush
            + [
                "pm:list",
                "--type=module",
                "--filter=upgrade_status",
                "--format=json",
                "--uri=" + cfg["site"]["uri"],
            ],
            analysis,
            120,
        )
        us_args = [
            *drush,
            "upgrade_status:analyze",
            "--all",
            "--ignore-uninstalled",
            "--skip-existing",
            "--phpstan-memory-limit=2048M",
            "--format=codeclimate",
        ]
        if fast:
            us_args.append("--ignore-contrib")
        us_args.append("--uri=" + cfg["site"]["uri"])
        checks.append(
            _upgrade_record(
                command(
                    us_args,
                    analysis,
                    1800,
                ),
                _extension_version(us_version, "upgrade_status"),
            )
        )
        impact_php = """$providers=[]; foreach (\\Drupal::service("plugin.manager.field.field_type")->getDefinitions() as $id=>$definition) {$providers[$id]=$definition["provider"]??NULL;} $modules=[]; foreach (\\Drupal::entityTypeManager()->getStorage("field_storage_config")->loadMultiple() as $storage) {$provider=$providers[$storage->getType()]??NULL; if (!$provider) continue; $entry=&$modules[$provider]; if (!$entry) $entry=["fieldProviders"=>[],"populatedContent"=>FALSE,"populatedCounts"=>[]]; $entity=$storage->getTargetEntityTypeId(); $field=$storage->getName(); $entry["fieldProviders"][]=$entity.".".$field; try {$query=\\Drupal::entityQuery($entity)->accessCheck(FALSE)->exists($field)->count(); $count=(int)$query->execute(); $entry["populatedCounts"][$entity.".".$field]=$count; if ($count>0) $entry["populatedContent"]=TRUE;} catch (\\Throwable $e) {$entry["populatedContent"]=NULL;}} echo json_encode(["modules"=>$modules]);"""
        checks.append(
            _impact_record(
                command(
                    drush + ["php:eval", impact_php, "--uri=" + cfg["site"]["uri"]], analysis, 600
                )
            )
        )
    except Exception as exc:
        if not any(item["id"] == "upgrade_status" for item in checks):
            checks.append(_failure("upgrade_status", str(exc), "analysis_runtime"))
        if not any(item["id"] == "drupal_rector" for item in checks):
            checks.append(
                {
                    "id": "drupal_rector",
                    "status": "unknown",
                    "required": True,
                    "message": "Rector was not started because analysis prerequisites failed",
                    "failureCategory": "skipped",
                    "findingCount": None,
                }
            )
    finally:
        refresh = cfg.get("refresh", False) or cfg.get("refresh_stack", False)
        if prefix:
            down_cmd = (
                prefix + ["down", "-v", "--remove-orphans"]
                if refresh
                else prefix + ["down", "--remove-orphans"]
            )
            lifecycle.append(
                {
                    "step": "teardown",
                    "status": command(down_cmd, analysis, 600).get("status"),
                }
            )
        if refresh:
            try:
                stack_dir = get_d11_home() / "cache" / "stacks" / stack_key
                if stack_dir.exists():
                    shutil.rmtree(stack_dir, ignore_errors=True)
            except Exception:
                pass
        after = inventory(source)
        unchanged = before == after
        lifecycle.append(
            {
                "step": "candidate_integrity",
                "status": "passed" if unchanged else "failed",
                "before": digest(before),
                "after": digest(after),
            }
        )
        if not unchanged:
            checks.append(
                _failure(
                    "analysis_candidate_integrity",
                    "Managed candidate changed during disposable analysis",
                    "candidate_contamination",
                )
            )
        write(
            out / "analysis-runtime.json",
            {
                "schemaVersion": "1.0",
                "project": project,
                "createdAt": now(),
                "scanMode": "fast" if fast else "full",
                "candidateUnchanged": unchanged,
                "lifecycle": lifecycle,
            },
        )
        if analysis.exists():
            shutil.rmtree(analysis, ignore_errors=True)
        write(out / "audit-tools.json", {"mode": "disposable", "checks": checks})
    return checks
