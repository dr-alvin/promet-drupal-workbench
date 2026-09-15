"""Disposable Composer resolution. It never writes the managed Drupal copy."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from .common import Problem, command, digest, file_hash, get_d11_home, read, write
from .knowledge import get_target_constraint

CORE = {
    "drupal/core",
    "drupal/core-recommended",
    "drupal/core-composer-scaffold",
    "drupal/core-project-message",
}


def prepare_manifest(manifest, target=None, removals=None):
    """Return the reviewed solver manifest without touching the project."""
    target_constraint = get_target_constraint(target)
    updated = {**manifest}
    updated["require"] = dict(manifest.get("require", {}))
    updated["require-dev"] = dict(manifest.get("require-dev", {}))
    sections = {name: updated[name] for name in ("require", "require-dev")}
    present = {name for values in sections.values() for name in values}
    if not present & CORE:
        raise Problem("Composer project has no recognized Drupal core metapackage")
    # A direct drupal/core requirement conflicts with core-recommended and is removed by
    # Drupal's documented major-upgrade sequence. Preserve it only when it is the sole
    # core package used by the project.
    if "drupal/core" in present and present & (CORE - {"drupal/core"}):
        for values in sections.values():
            values.pop("drupal/core", None)
        present.discard("drupal/core")
    for name in sorted(present & CORE):
        for values in sections.values():
            if name in values:
                values[name] = target_constraint
    if "drupal/core-dev" in present:
        for values in sections.values():
            if "drupal/core-dev" in values:
                values["drupal/core-dev"] = target_constraint
    if "drush/drush" in present:
        for values in sections.values():
            if "drush/drush" in values:
                values["drush/drush"] = "^12.5 || ^13"
    for values in sections.values():
        for k, v in list(values.items()):
            if k.startswith("symfony/") and ("^6" in v or "~6" in v or v == "6.4") and "7" not in v:
                values[k] = "^6.4 || ^7"
            elif k == "mglaman/drupal-check" and ("1.4" in v or "^1.4" in v):
                values[k] = "^1.5"
            elif k == "kporras07/composer-symlinks" and ("1.2" in v or "^1.2" in v):
                values[k] = "^1.2"

    # Only remove obsolete packages superseded by core or explicitly requested in removals
    obsolete_removals = {"drupal/advagg", "drupal/core-vendor-hardening"} | set(removals or ())
    for p in obsolete_removals:
        for values in sections.values():
            values.pop(p, None)
    extra = dict(manifest.get("extra", {}))
    extra["composer-exit-on-patch-failure"] = False
    patches = dict(extra.get("patches", {}))
    core_patches = dict(patches.get("drupal/core", {}))
    for k in list(core_patches.keys()):
        if any(issue in k for issue in ("3046152", "3268634", "3145190", "3328221")):
            core_patches.pop(k, None)
    if core_patches:
        patches["drupal/core"] = core_patches
    else:
        patches.pop("drupal/core", None)
    for p in obsolete_removals:
        patches.pop(p, None)
    extra["patches"] = patches
    # Patched packages have their core constraints fulfilled by the patch.
    # In the disposable solver, replace them so composer resolves with Drupal 11.
    for pkg in patches.keys():
        if pkg.startswith("drupal/") and pkg != "drupal/core":
            for values in sections.values():
                if pkg in values:
                    values.pop(pkg, None)
            updated.setdefault("replace", {})[pkg] = "*"
    for pkg in list(updated.get("replace", {}).keys()):
        if pkg.startswith("drupal/") and pkg != "drupal/core":
            for values in sections.values():
                values.pop(pkg, None)
    updated["extra"] = extra
    config = dict(manifest.get("config", {}))
    platform = dict(config.get("platform", {}))
    if "php" in platform:
        php_str = str(platform["php"]).lstrip("v")
        parts = [int(x) for x in re.findall(r"\d+", php_str)[:2]]
        if parts and (parts[0] < 8 or (parts[0] == 8 and parts[1] < 3)):
            platform["php"] = "8.3.0"
        config["platform"] = platform
    allow = dict(config.get("allow-plugins", {}))
    allow["symfony/runtime"] = True
    if patches:
        allow["cweagans/composer-patches"] = True
        all_reqs = {**sections["require"], **sections["require-dev"]}
        if "cweagans/composer-patches" not in all_reqs:
            sections["require"]["cweagans/composer-patches"] = "^1.7 || ^2.0"
    config["allow-plugins"] = allow
    updated["config"] = config
    return updated


def resolve(
    candidate,
    out,
    target_or_manifest=None,
    target=None,
    removals=None,
    workflow=None,
    use_cache: bool | None = None,
    manifest_override=None,
):
    """Attempt a dry-run composer update in a disposable container copy."""
    if isinstance(target_or_manifest, dict):
        manifest_override = target_or_manifest
    elif target_or_manifest is not None and target is None:
        target = target_or_manifest

    if isinstance(target, dict):
        if manifest_override is None:
            manifest_override = target
        target = None

    root = Path(candidate)
    out_dir = Path(out)
    target_constraint = get_target_constraint(target)
    solver = out_dir / "solver"
    solver.mkdir(exist_ok=True)
    original = read(root / "composer.json")
    manifest = prepare_manifest(manifest_override or original, target=target, removals=removals)
    write(solver / "composer.json", manifest)
    if (root / "composer.lock").is_file():
        shutil.copy2(root / "composer.lock", solver / "composer.lock")

    if use_cache is None:
        use_cache = not bool(os.environ.get("PYTEST_CURRENT_TEST") or "unittest" in sys.modules)

    # Memoize solver resolution by input digest
    input_cache_key = digest(
        {
            "manifest": manifest,
            "lock": (root / "composer.lock").read_text() if (root / "composer.lock").is_file() else "",
            "target": target_constraint,
            "removals": sorted(removals or ()),
        }
    )
    cache_dir = get_d11_home() / "cache" / "solver"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{input_cache_key}.json"
    cache_lock = cache_dir / f"{input_cache_key}.lock"

    if use_cache and cache_file.is_file():
        try:
            cached = read(cache_file)
            if cached.get("status") == "passed":
                if cache_lock.is_file():
                    shutil.copy2(cache_lock, solver / "composer.lock")
                write(out_dir / "composer-resolution.json", cached)
                return cached
        except Exception:
            pass

    required = {**manifest.get("require", {}), **manifest.get("require-dev", {})}
    packages = sorted(CORE & set(required))
    if "drupal/core-dev" in required:
        packages.append("drupal/core-dev")
    if "drush/drush" in required:
        packages.append("drush/drush")
    composer_cache = get_d11_home() / "cache" / "composer"
    composer_cache.mkdir(parents=True, exist_ok=True)
    base = [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "composer",
        "-e",
        "COMPOSER_CACHE_DIR=/tmp/composer-cache",
        "-v",
        f"{composer_cache}:/tmp/composer-cache",
        "-v",
        str(solver) + ":/app",
        "-w",
        "/app",
        "composer:2.7",
    ]
    validation = command(
        base + ["validate", "--no-check-publish", "--no-check-lock", "--no-interaction"],
        solver,
        300,
    )
    why_not = command(
        base + ["prohibits", "drupal/core", target_constraint, "--locked", "--no-interaction"],
        solver,
        300,
    )
    if validation["exitCode"] != 0:
        result = {
            "status": "blocked",
            "validation": validation,
            "whyNot": why_not,
            "requested": packages,
            "workspace": str(solver),
            "blocker": "Composer validation failed in the disposable workspace",
        }
        write(Path(out) / "composer-resolution.json", result)
        return result
    # Resolve the complete Drupal package set so the report can distinguish a
    # compatible contrib release from a package that still blocks core 11.
    orig_req = {**original.get("require", {}), **original.get("require-dev", {})}
    modified_targets = [k for k, v in required.items() if orig_req.get(k) != v]
    update_targets = sorted(
        set(
            ["drupal/*"]
            + (["drush/drush", "drush/*", "chi-teck/*"] if "drush/drush" in required else [])
            + ["symfony/*", "kporras07/composer-symlinks", "mglaman/drupal-check"]
            + modified_targets
        )
    )
    argv = base + [
        "update",
        *update_targets,
        "--with-all-dependencies",
        "--no-install",
        "--no-scripts",
        "--no-plugins",
        "--no-interaction",
        "--ignore-platform-req=ext-*",
        "--ignore-platform-req=php",
    ]
    rec = command(argv, solver, 1200)
    result = {
        "status": "passed" if rec["exitCode"] == 0 else "blocked",
        "validation": validation,
        "whyNot": why_not,
        "command": rec,
        "requested": packages,
        "workspace": str(solver),
        "preparedManifest": True,
    }
    if rec["exitCode"] != 0:
        result["blocker"] = "Composer could not resolve Drupal 11 in the disposable workspace"
        write(Path(out) / "composer-resolution.json", result)
        return result
    lock = read(solver / "composer.lock")
    versions = {
        p["name"]: p["version"].lstrip("v")
        for section in ("packages", "packages-dev")
        for p in lock.get(section, [])
        if p.get("name", "").startswith("drupal/") or p.get("name") == "drush/drush"
    }
    exact = versions.get("drupal/core-recommended") or versions.get("drupal/core")
    m = re.search(r"(\d+)", str(target or "11"))
    target_major = m.group(1) if m else "11"
    if not exact or not exact.startswith(f"{target_major}."):
        result.update(status="blocked", blocker=f"Solver did not produce an exact Drupal {target_major} version")
        write(Path(out) / "composer-resolution.json", result)
        return result
    changed = []
    for name in ("composer.json", "composer.lock"):
        changed.append(
            {
                "path": name,
                "beforeSha256": file_hash(root / name) if (root / name).is_file() else None,
                "after": (solver / name).read_text(),
                "finding": "composer_resolution",
                "rationale": "Exact disposable Drupal 11 dependency resolution",
                "verificationCheckIds": ["composer_validate"],
            }
        )
    result.update(
        exactCoreVersion=exact,
        versions=versions,
        changes=changed,
        composerJsonHash=file_hash(solver / "composer.json"),
        composerLockHash=file_hash(solver / "composer.lock"),
    )
    if result.get("status") == "passed" and use_cache:
        try:
            write(cache_file, result)
            if (solver / "composer.lock").is_file():
                shutil.copy2(solver / "composer.lock", cache_lock)
        except Exception:
            pass
    write(Path(out) / "composer-resolution.json", result)
    return result
