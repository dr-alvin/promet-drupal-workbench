"""Repository-first discovery: absence of evidence never establishes compatibility."""

from __future__ import annotations

import copy
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import *


def find_roots(cfg):
    root = Path(cfg["_root"]).resolve()
    explicit = cfg.get("roots", {})
    if explicit.get("composer"):
        cp = relative(root, explicit["composer"]) / "composer.json"
        if not cp.is_file():
            raise Problem("Configured Composer root has no composer.json")
    else:
        candidates = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs if d not in {".git", "vendor", "node_modules", "files", ".venv"}
            ]
            if "composer.json" in files:
                p = Path(base, "composer.json")
                data = read(p)
                req = {**data.get("require", {}), **data.get("require-dev", {})}
                if any(n in req for n in ("drupal/core", "drupal/core-recommended")):
                    candidates.append(p)
        if len(candidates) != 1:
            raise Problem(
                f"Expected one Drupal Composer project; found {len(candidates)}. Set roots.composer"
            )
        cp = candidates[0]
    composer = read(cp)
    cr = cp.parent
    scaffold = (
        composer.get("extra", {}).get("drupal-scaffold", {}).get("locations", {}).get("web-root")
    )
    if explicit.get("drupal"):
        dr = relative(root, explicit["drupal"])
    elif scaffold:
        dr = relative(cr, scaffold)
    else:
        candidates = [
            p
            for p in [cr / "web", cr / "docroot", cr]
            if (p / "core/lib/Drupal.php").is_file() or (p / "sites").is_dir()
        ]
        if len(candidates) != 1:
            raise Problem("Drupal root ambiguous or absent; set roots.drupal")
        dr = candidates[0]
    vendor = (
        relative(root, explicit["vendor"])
        if explicit.get("vendor")
        else relative(cr, composer.get("config", {}).get("vendor-dir", "vendor"))
    )
    sync = relative(root, explicit["config"]) if explicit.get("config") else None
    sync_evidence = "configured" if sync else "unknown"
    if not sync:
        candidates = []
        for settings in sorted((dr / "sites").glob("*/settings*.php")):
            for match in re.finditer(
                r"\$settings\[['\"]config_sync_directory['\"]\]\s*=\s*['\"]([^'\"]+)['\"]\s*;",
                settings.read_text(),
            ):
                p = relative(dr, match.group(1))
                if p.is_dir():
                    candidates.append(p)
        candidates = sorted(set(candidates))
        if len(candidates) == 1:
            sync, sync_evidence = (
                candidates[0],
                "inferred_literal_settings; active value unverified",
            )
    custom = [str(relative(root, p)) for p in explicit.get("custom", [])]
    if not custom:
        custom = [
            str(p) for kind in ("modules", "themes") for p in [dr / kind / "custom"] if p.is_dir()
        ]
    return {
        "repository": str(root),
        "composer": str(cr),
        "drupal": str(dr),
        "vendor": str(vendor),
        "config": str(sync) if sync else None,
        "configEvidence": sync_evidence,
        "custom": custom,
    }, composer


def wrapper(cfg, roots):
    chosen = cfg.get("runtime", {}).get("wrapper")
    repo = Path(roots["repository"])
    marker_checks = [
        ("ddev", [".ddev/config.yaml"]),
        ("fin", [".docksal"]),
        ("lando", [".lando.yml", ".lando.yaml"]),
        ("compose", ["docker-compose.yml", "compose.yaml", "compose.yml", "docker-compose.yaml"]),
    ]
    found = [w for w, rel_paths in marker_checks if any((repo / p).exists() for p in rel_paths)]
    if not chosen:
        if len(found) != 1:
            return {"selected": None, "evidence": found, "status": "unknown"}
        chosen = found[0]
    return {
        "selected": chosen,
        "evidence": found,
        "status": "configured" if cfg.get("runtime", {}).get("wrapper") else "inferred",
    }


def runtime_argv(cfg, roots, selected, tool, args):
    explicit = cfg.get("runtime", {}).get("commands", {}).get(tool)
    if explicit:
        prefix = explicit
    elif selected == "fin" and tool == "php":
        prefix = ["fin", "exec", "php"]
    elif selected in ("ddev", "fin", "lando"):
        prefix = [selected, tool]
    elif selected == "compose":
        prefix = ["docker", "compose", "exec", tool]
    elif selected == "local":
        # Local tools must be explicitly configured; no implicit global PHP.
        raise Problem(f"Configure runtime.commands.{tool} for local execution")
    else:
        raise Problem("Runtime wrapper is unknown")
    return prefix + args


def deployment(root):
    records = []
    candidates = [
        ".ci",
        ".github",
        ".gitlab-ci.yml",
        "bitbucket-pipelines.yml",
        ".docksal",
        ".ddev",
        ".lando.yml",
        ".lando.yaml",
        "docker-compose.yml",
        "compose.yaml",
        ".tugboat",
        "scripts",
        "package.json",
        "composer.json",
    ]
    pattern = re.compile(
        r"\b(drush\s+(?:deploy|updb|updatedb|cim|config:import|cr|cache:rebuild)|composer\s+(?:install|update)|(?:npm|yarn|pnpm)\s+(?:run\s+)?build)\b"
    )
    for name in candidates:
        p = Path(root, name)
        files = [p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []
        for f in files:
            if not f.is_file() or f.is_symlink() or f.stat().st_size > 1_000_000:
                continue
            for line, text in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if pattern.search(text):
                    records.append(
                        {
                            "path": str(f.relative_to(root)),
                            "line": line,
                            "text": redact(text.strip()),
                            "evidenceQuality": "inferred",
                        }
                    )
    return {
        "status": "review_required" if records else "unknown",
        "observations": records,
        "note": "Textual order is evidence; review branches, includes and script invocation before approving execution.",
    }


def config_diff(before, after):
    old, new = Path(before), Path(after)
    if not old.is_dir() or not new.is_dir():
        raise Problem("Both configuration evidence directories must exist")
    if not any(old.rglob("*.yml")) or not any(new.rglob("*.yml")):
        raise Problem("Configuration comparison requires nonempty baseline and candidate evidence")

    def load(base):
        return {str(p.relative_to(base)): yaml_read(p) for p in sorted(base.rglob("*.yml"))}

    a, b = load(old), load(new)
    changes = []
    for name in sorted(a.keys() | b.keys()):
        if a.get(name) == b.get(name):
            continue
        risks = []
        if name not in b:
            risks.append("configuration_deletion")
        if name.startswith(("field.storage.", "field.field.")) and name not in b:
            risks.append("field_or_storage_deletion")
        if name == "core.extension.yml":
            for kind in ("module", "theme"):
                for item in sorted(
                    set(a.get(name, {}).get(kind, {})) - set(b.get(name, {}).get(kind, {}))
                ):
                    risks.append(f"{kind}_removal:{item}")
        if name.startswith("user.role."):
            risks.append("role_or_permission_change")
        if (
            name.startswith(("views.view.", "pathauto.pattern."))
            and b.get(name, {}).get("status") is False
        ):
            risks.append("disabled_behavior")
        if "config_split" in name:
            risks.append("environment_split")
        if name == "system.site.yml" and a.get(name, {}).get("uuid") != b.get(name, {}).get("uuid"):
            risks.append("site_uuid_mismatch")
        changes.append(
            {
                "path": name,
                "kind": "deleted" if name not in b else "added" if name not in a else "changed",
                "risks": risks,
                "requiresReview": True,
            }
        )
    return changes


def discover_config_splits(config_dir: str | Path | None, repo_root: str | Path | None = None) -> dict:
    """Discover Drupal config split definitions and their configured extensions."""
    splits = {}
    extensions = {}
    searched_dirs = set()

    candidate_dirs = []
    if config_dir:
        cd = Path(config_dir).resolve()
        if cd.is_dir():
            candidate_dirs.append(cd)
            if cd.parent.is_dir() and cd.parent != cd:
                candidate_dirs.append(cd.parent)
    if repo_root:
        rd = Path(repo_root).resolve()
        for sub in ("config", "config/sync", "config/splits", "config/envs"):
            p = rd / sub
            if p.is_dir():
                candidate_dirs.append(p)

    split_files = []
    for d in candidate_dirs:
        if d in searched_dirs:
            continue
        searched_dirs.add(d)
        for pattern in ("config_split.config_split.*.yml", "config_split.config_split.*.yaml"):
            for f in sorted(d.glob(pattern)):
                if f.is_file() and f not in split_files:
                    split_files.append(f)

    for sf in split_files:
        try:
            data = yaml_read(sf) or {}
            if not isinstance(data, dict):
                continue
            split_id = data.get("id")
            if not split_id:
                parts = sf.stem.split(".")
                split_id = parts[-1] if len(parts) >= 3 else sf.stem

            mod_data = data.get("module", {})
            if isinstance(mod_data, dict):
                modules = list(mod_data.keys())
            elif isinstance(mod_data, list):
                modules = [str(m) for m in mod_data if m]
            else:
                modules = []

            theme_data = data.get("theme", {})
            if isinstance(theme_data, dict):
                themes = list(theme_data.keys())
            elif isinstance(theme_data, list):
                themes = [str(t) for t in theme_data if t]
            else:
                themes = []

            folder_val = data.get("folder")
            if folder_val:
                folder_candidates = [sf.parent / folder_val]
                if config_dir:
                    folder_candidates.append(Path(config_dir) / folder_val)
                if repo_root:
                    folder_candidates.append(Path(repo_root) / folder_val)
                for fc in folder_candidates:
                    if fc.is_dir():
                        core_ext = fc / "core.extension.yml"
                        if core_ext.is_file():
                            try:
                                ce_data = yaml_read(core_ext) or {}
                                for m in ce_data.get("module", {}).keys():
                                    if m not in modules:
                                        modules.append(m)
                                for t in ce_data.get("theme", {}).keys():
                                    if t not in themes:
                                        themes.append(t)
                            except Exception:
                                pass
                        break

            splits[split_id] = {
                "id": split_id,
                "label": data.get("label") or split_id,
                "status": data.get("status", False),
                "path": str(sf),
                "modules": modules,
                "themes": themes,
                "folder": folder_val,
            }

            for m in modules:
                extensions.setdefault(m, []).append(split_id)
            for t in themes:
                extensions.setdefault(t, []).append(split_id)
        except Exception:
            pass

    return {
        "splits": splits,
        "extensions": extensions,
    }


def static_discovery(cfg):
    roots, comp = find_roots(cfg)
    selected = wrapper(cfg, roots)
    lp = Path(roots["composer"], "composer.lock")
    lock = read(lp) if lp.is_file() else {}
    packages = lock.get("packages", []) + lock.get("packages-dev", [])
    result = {
        "schemaVersion": "1.0",
        "roots": roots,
        "wrapper": selected,
        "environment": cfg["environment"],
        "site": cfg.get("site"),
        "composer": {
            "requirements": comp.get("require", {}),
            "developmentRequirements": comp.get("require-dev", {}),
            "platform": comp.get("config", {}).get("platform", {}),
            "allowPlugins": comp.get("config", {}).get("allow-plugins", {}),
            "repositories": comp.get("repositories", []),
            "patches": comp.get("extra", {}).get("patches", {}),
            "patchFiles": comp.get("extra", {}).get("patches-file"),
            "scripts": comp.get("scripts", {}),
            "packages": packages,
            "lockStatus": "present" if lock else "unknown",
        },
        "deployment": deployment(Path(roots["repository"])),
        "runtime": {"status": "unknown", "commands": {}},
        "extensions": [],
    }
    direct = set(comp.get("require", {})) | set(comp.get("require-dev", {}))
    result["dependencyGraph"] = [
        {
            "name": p.get("name"),
            "version": p.get("version"),
            "direct": p.get("name") in direct,
            "requires": p.get("require", {}),
            "type": p.get("type"),
        }
        for p in packages
    ]
    result["composer"]["plugins"] = [
        p.get("name") for p in packages if p.get("type") == "composer-plugin"
    ]
    result["composer"]["developmentTools"] = [
        p.get("name")
        for p in packages
        if any(
            term in p.get("name", "")
            for term in ("phpstan", "rector", "phpunit", "upgrade_status", "drush")
        )
    ]
    result["buildAndTestEntrypoints"] = []
    for base, dirs, files in os.walk(roots["repository"]):
        dirs[:] = [
            d for d in dirs if d not in {".git", "vendor", "node_modules", "files", ".venv", "core"}
        ]
        for name in sorted(
            set(files)
            & {
                "package.json",
                "phpunit.xml",
                "phpunit.xml.dist",
                "phpstan.neon",
                "phpstan.neon.dist",
                "rector.php",
                "behat.yml",
                "playwright.config.ts",
                "playwright.config.js",
            }
        ):
            p = Path(base, name)
            entry = {"path": str(p.relative_to(roots["repository"]))}
            if name == "package.json":
                entry["scripts"] = read(p).get("scripts", {})
            result["buildAndTestEntrypoints"].append(entry)
    status_data = result["runtime"]["commands"].get("status", {}).get("data", {})
    if status_data.get("config-sync") and not cfg.get("roots", {}).get("config"):
        runtime_sync = relative(roots["drupal"], status_data["config-sync"])
        if runtime_sync.is_dir():
            roots["config"] = str(runtime_sync)
            roots["configEvidence"] = "runtime selected site"
    exported = {}
    if roots["config"] and Path(roots["config"], "core.extension.yml").is_file():
        exported = yaml_read(Path(roots["config"], "core.extension.yml"))
    config_splits = discover_config_splits(roots.get("config"), roots.get("repository"))
    split_exts = config_splits.get("extensions", {})
    active = result["runtime"]["commands"].get("activeExtensions", {}).get("data", {})
    result["configuration"] = {
        "exportedExtensions": exported or None,
        "activeExtensions": active or None,
        "configSplits": config_splits.get("splits", {}),
    }
    installed_path = Path(roots["vendor"], "composer/installed.json")
    mappings = []
    if installed_path.is_file():
        installed = read(installed_path)
        for p in installed.get("packages", []) if isinstance(installed, dict) else installed:
            if p.get("install-path"):
                mappings.append((relative(installed_path.parent, p["install-path"]), p["name"]))
    dr = Path(roots["drupal"])
    locations = [
        dr / "modules",
        dr / "themes",
        dr / "profiles",
        dr / "core/modules",
        dr / "core/themes",
    ] + [Path(p) for p in roots["custom"]]
    seen = set()
    for base in locations:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.info.yml")):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            info = yaml_read(path)
            name = path.name[:-9]
            pkg = next(
                (p for loc, p in mappings if loc == path.parent or loc in path.parents), None
            )
            kind = (
                info.get("type") if info.get("type") in ("module", "theme", "profile") else "module"
            )
            active_set = active.get(kind, {}) if isinstance(active, dict) else {}
            installed_state = name in active_set if active else None
            in_splits = split_exts.get(name, [])
            is_base_exported = (
                name == exported.get("profile")
                if kind == "profile"
                else name in exported.get(kind, {})
            ) if exported else False
            is_split_exported = bool(in_splits)
            is_exported = (is_base_exported or is_split_exported) if (exported or is_split_exported) else None
            result["extensions"].append(
                {
                    "name": name,
                    "type": kind,
                    "path": str(path),
                    "package": pkg,
                    "packageEvidence": "installed.json" if pkg else "unknown",
                    "installed": installed_state,
                    "exported": is_exported,
                    "configSplits": in_splits,
                    "inConfigSplit": bool(in_splits),
                    "coreConstraint": info.get("core_version_requirement"),
                    "dependencies": info.get("dependencies", []),
                    "compatibility": "unknown",
                }
            )
    ignored_active = {"standard", "minimal", "demo_umami", "testing", "nightwatch_testing"}
    if active and isinstance(active, dict) and active.get("profile"):
        ignored_active.add(active["profile"])
    present = {e["name"] for e in result["extensions"]} | ignored_active
    result["missingActiveCode"] = (
        sorted((set(active.get("module", {})) | set(active.get("theme", {}))) - present)
        if active
        else None
    )
    result["removedCoreDependencies"] = [
        {
            "name": n,
            "active": n in active.get("module", {}) if active else None,
            "exported": (n in exported.get("module", {}) or n in split_exts) if (exported or n in split_exts) else None,
            "configSplits": split_exts.get(n, []),
            "inConfigSplit": n in split_exts,
        }
        for n in ["action", "book", "forum", "statistics", "tour", "tracker"]
    ]
    return result


def runtime_discovery(cfg, roots, selected):
    require_nonprod(cfg)
    if not cfg.get("site", {}).get("uri"):
        raise Problem("Runtime inspection requires explicit site.uri")
    site = ["--uri=" + cfg["site"]["uri"]]
    records = {}

    def probe(tool, args):
        return command(runtime_argv(cfg, roots, selected, tool, args), roots["composer"])

    # Only independent, read-only PHP and Composer probes overlap.
    with ThreadPoolExecutor(max_workers=2) as pool:
        php = pool.submit(
            probe,
            "php",
            [
                "-r",
                'echo json_encode(["version"=>PHP_VERSION,"extensions"=>get_loaded_extensions()]);',
            ],
        )
        composer = pool.submit(probe, "composer", ["--version"])
        records["php"], records["composer"] = php.result(), composer.result()
    if cfg.get("identity"):
        records["identity"] = command(cfg["identity"]["argv"], roots["composer"])
        if records["identity"].get("stdout", "").strip() != cfg["identity"]["expected"]:
            records["identity"].update(status="tool_failure", failureCategory="identity_mismatch")
    records["drush"] = probe("drush", ["--version"])
    records["capabilities"] = probe("drush", ["list", "--format=json"] + site)
    try:
        capabilities = json.loads(records["capabilities"].get("stdout", ""))
        names = {c["name"] for c in capabilities["commands"]}
        if records["capabilities"]["exitCode"] != 0:
            names = set()
    except (ValueError, KeyError, TypeError):
        names = set()
    records["capabilities"]["status"] = "passed" if names else "tool_failure"
    probes = {
        "status": ["core:status", "--format=json"],
        "extensions": ["pm:list", "--format=json"],
        "activeExtensions": ["config:get", "core.extension", "--format=json"],
        "pendingUpdates": ["updatedb:status", "--format=json"],
        "configurationStatus": ["config:status", "--format=json"],
    }
    for name, args in probes.items():
        # Use the selected installation's command catalog and default fields.
        # No db-version field is assumed; absent database version stays unknown.
        if args[0] not in names:
            records[name] = {
                "status": "unknown",
                "exitCode": None,
                "failureCategory": "missing_capability",
                "message": "Command not verified in selected Drush catalog: " + args[0],
            }
            continue
        records[name] = probe("drush", args + site)
    for name in ["php"] + list(probes):
        rec = records[name]
        if rec["exitCode"] != 0:
            continue
        try:
            output = rec.get("stdout", "")
            diagnostic = re.sub(
                r"\x1b\[[0-9;]*m", "", output + "\n" + rec.get("stderr", "")
            ).strip()
            if name == "pendingUpdates" and re.fullmatch(
                r"\[success\]\s+No database updates required\.", diagnostic
            ):
                rec["data"] = []
                rec["emptySuccessEvidence"] = (
                    "Verified updatedb:status success diagnostic with exit 0"
                )
            else:
                data = json.loads(output)
                expected = (
                    (dict, list)
                    if name in ("pendingUpdates", "configurationStatus", "extensions")
                    else (dict,)
                )
                if not isinstance(data, expected) or (name in ("php", "status") and not data):
                    raise ValueError()
                rec["data"] = data
            if name == "status":
                rec["availableFields"] = sorted(rec["data"])
                allowed = {
                    "drupal-version",
                    "php-version",
                    "drush-version",
                    "root",
                    "uri",
                    "config",
                    "config-sync",
                    "bootstrap",
                    "db-driver",
                    "db-status",
                }
                rec["data"] = {k: v for k, v in rec["data"].items() if k in allowed}
                rec["stdout"] = json.dumps(rec["data"])
        except ValueError:
            rec.update(
                status="tool_failure",
                parseError="Expected structured JSON",
                failureCategory="malformed_output",
            )
    return {
        "status": "collected"
        if all(r["status"] == "passed" for r in records.values())
        else "incomplete",
        "commands": records,
    }


TOOLKIT_HASH_ROOTS = (
    ("src", "config/schemas") if (ROOT / "src").is_dir() else ("scripts", "config/schemas")
)
TOOLKIT_VERSION = "2.0.0"


def toolkit_state_hashes(bases=None):
    if bases is None:
        bases = TOOLKIT_HASH_ROOTS
    hashes = {}
    for base in bases:
        base_path = ROOT / base
        if not base_path.is_dir():
            continue
        for p in sorted(base_path.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                hashes[str(p.relative_to(ROOT))] = file_hash(p)
    return hashes


def discovery_key(cfg, roots, toolkit_bases=None):
    launcher = ROOT / "bin/d11"
    dependencies = ROOT / "requirements.txt"
    inputs = {
        "source": state_hashes(cfg["_root"]),
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
        "toolkit": toolkit_state_hashes(toolkit_bases),
        "toolkitVersion": TOOLKIT_VERSION,
        "external": {},
        "launcher": file_hash(launcher) if launcher.is_file() else None,
        "dependencies": file_hash(dependencies) if dependencies.is_file() else None,
    }
    # Vendor metadata and roots outside repository are still part of static evidence.
    for base in set(
        [roots["drupal"], roots["vendor"]]
        + roots["custom"]
        + ([roots["config"]] if roots["config"] else [])
    ):
        directory = Path(base)
        for pattern in ("*.info.yml", "installed.json"):
            for p in directory.rglob(pattern):
                inputs["external"][str(p)] = file_hash(p)
        if directory != Path(cfg["_root"]) and Path(cfg["_root"]) not in directory.parents:
            inputs["external"][str(directory)] = state_hashes(directory)
    return digest(inputs)


def discover(cfg, runtime=False, refresh=False, cache_dir=None):
    started = time.monotonic()
    roots, _ = find_roots(cfg)
    key = discovery_key(cfg, roots) if cache_dir else None
    cache_file = Path(cache_dir) / ("static-" + key + ".json") if cache_dir else None
    reused = False
    if cache_file and cache_file.is_file() and not refresh:
        try:
            saved = read(cache_file)
            if saved["key"] == key and saved["digest"] == digest(saved["data"]):
                result = copy.deepcopy(saved["data"])
                reused = True
        except (Problem, KeyError, TypeError):
            pass
    if not reused:
        result = static_discovery(cfg)
        if cache_file:
            data = redact_tree(result)
            write(cache_file, {"key": key, "digest": digest(data), "data": data})
            result = copy.deepcopy(data)
    roots = result["roots"]
    if runtime:
        result["runtime"] = runtime_discovery(cfg, roots, result["wrapper"]["selected"])
        status = result["runtime"]["commands"].get("status", {}).get("data", {})
        sync = status.get("config") or status.get("config-sync")
        if sync and not cfg.get("roots", {}).get("config"):
            candidate = relative(roots["drupal"], sync)
            if candidate.is_dir():
                roots["config"] = str(candidate)
                roots["configEvidence"] = "runtime selected site"
    # Mutable state is never loaded from the static cache.
    active = result["runtime"]["commands"].get("activeExtensions", {}).get("data", {})
    exported_file = Path(roots["config"], "core.extension.yml") if roots["config"] else None
    exported = yaml_read(exported_file) if exported_file and exported_file.is_file() else {}
    config_splits = discover_config_splits(roots.get("config"), roots.get("repository"))
    split_exts = config_splits.get("extensions", {})
    result["configuration"] = {
        "activeExtensions": active or None,
        "exportedExtensions": exported or None,
        "configSplits": config_splits.get("splits", {}),
    }
    for e in result["extensions"]:
        e["installed"] = (
            (
                e["name"] == active.get("profile")
                if e["type"] == "profile"
                else e["name"] in active.get(e["type"], {})
            )
            if active
            else None
        )
        e["presentOnDisk"] = True
        e["stateEvidence"] = "active_configuration" if active else "unknown"
        in_splits = split_exts.get(e["name"], [])
        e["configSplits"] = in_splits
        e["inConfigSplit"] = bool(in_splits)
        is_base_exported = (
            e["name"] == exported.get("profile")
            if e["type"] == "profile"
            else e["name"] in exported.get(e["type"], {})
        ) if exported else False
        is_split_exported = bool(in_splits)
        e["exported"] = (is_base_exported or is_split_exported) if (exported or is_split_exported) else None
    # Drupal's selected discovery path wins when several files share a machine name.
    discovered = result["runtime"]["commands"].get("extensions", {}).get("data", {})
    unique = {}
    for extension in result["extensions"]:
        key = (extension["type"], extension["name"])
        selected = discovered.get(extension["name"], {}) if isinstance(discovered, dict) else {}
        if not isinstance(selected, dict):
            selected = {}
        expected = selected.get("path")
        if (
            expected
            and Path(extension["path"]).parent.resolve()
            != relative(roots["drupal"], expected).resolve()
        ):
            continue
        unique.setdefault(key, extension)
    result["extensions"] = list(unique.values())
    ignored_active = {"standard", "minimal", "demo_umami", "testing", "nightwatch_testing"}
    if active and isinstance(active, dict) and active.get("profile"):
        ignored_active.add(active["profile"])
    present = {e["name"] for e in result["extensions"]} | ignored_active
    result["missingActiveCode"] = (
        sorted((set(active.get("module", {})) | set(active.get("theme", {}))) - present)
        if active
        else None
    )
    for e in result["removedCoreDependencies"]:
        e["active"] = e["name"] in active.get("module", {}) if active else None
        e["exported"] = (e["name"] in exported.get("module", {}) or e["name"] in split_exts) if (exported or e["name"] in split_exts) else None
        e["configSplits"] = split_exts.get(e["name"], [])
        e["inConfigSplit"] = e["name"] in split_exts
    result["git"] = {
        "head": command(["git", "rev-parse", "HEAD"], roots["repository"]),
        "dirty": command(["git", "status", "--porcelain=v1"], roots["repository"]),
    }
    records = list(result["runtime"]["commands"].values()) + list(result["git"].values())
    result["metrics"] = {
        "elapsedSeconds": round(time.monotonic() - started, 6),
        "staticReused": reused,
        "cacheKey": key,
        "runtimeRefreshed": runtime,
        "subprocessCount": sum("argv" in r for r in records),
        "failureCategories": {
            r.get("failureCategory") or r["status"]: sum(
                (x.get("failureCategory") or x["status"])
                == (r.get("failureCategory") or r["status"])
                for x in records
            )
            for r in records
            if r["status"] != "passed"
        },
    }
    return result
