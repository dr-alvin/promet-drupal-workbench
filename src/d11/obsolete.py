"""Pre-flight detector for uninstalled and obsolete packages in composer.json."""

from __future__ import annotations

from pathlib import Path

from .common import read

IGNORE_PACKAGES = {
    "drupal/core",
    "drupal/core-recommended",
    "drupal/core-composer-scaffold",
    "drupal/core-project-message",
    "drupal/core-dev",
    "drush/drush",
}


def inspect_obsolete_packages(
    site_root: Path,
    active_extensions: list[str] | set[str] | None = None,
    context: dict | None = None,
) -> dict:
    """Find packages in composer.json that are uninstalled in the Drupal database."""
    site_root = Path(site_root).resolve()
    composer_file = site_root / "composer.json"
    if not composer_file.is_file():
        return {"status": "error", "message": "composer.json not found"}

    manifest = read(composer_file)
    active_set = set(active_extensions or [])

    pkg_to_extensions: dict[str, set[str]] = {}
    if context and "extensions" in context:
        for ext in context["extensions"]:
            p = ext.get("package")
            if p and ext.get("name"):
                pkg_to_extensions.setdefault(p, set()).add(ext["name"])

    installed_json = site_root / "vendor" / "composer" / "installed.json"
    if not pkg_to_extensions and installed_json.is_file():
        try:
            data = read(installed_json)
            pkgs = data.get("packages", []) if isinstance(data, dict) else data
            for p in pkgs:
                pkg_name = p.get("name")
                if not pkg_name or not pkg_name.startswith("drupal/"):
                    continue
                inst_path = p.get("install-path")
                if inst_path:
                    abs_path = (installed_json.parent / inst_path).resolve()
                    if abs_path.is_dir():
                        for info_yml in abs_path.rglob("*.info.yml"):
                            pkg_to_extensions.setdefault(pkg_name, set()).add(info_yml.name[:-9])
        except Exception:
            pass

    uninstalled = []
    required_packages = []

    for section in ("require", "require-dev"):
        for pkg, constraint in manifest.get(section, {}).items():
            if not pkg.startswith("drupal/") or pkg in IGNORE_PACKAGES:
                continue
            required_packages.append((pkg, section, constraint))

    for pkg, section, constraint in required_packages:
        extensions = pkg_to_extensions.get(pkg)
        if extensions:
            is_active = any(ext in active_set for ext in extensions)
        else:
            project_name = pkg.split("/", 1)[1].replace("-", "_")
            is_active = project_name in active_set or any(
                ext == project_name or ext.startswith(project_name + "_") for ext in active_set
            )
        if not is_active:
            uninstalled.append(
                {
                    "package": pkg,
                    "section": section,
                    "constraint": constraint,
                    "reason": f"No extensions from package '{pkg}' are enabled in the database",
                }
            )

    remove_args = [item["package"] for item in uninstalled]
    command = f"composer remove {' '.join(remove_args)}" if remove_args else None

    return {
        "status": "passed",
        "totalRequiredDrupalPackages": len(required_packages),
        "uninstalledCount": len(uninstalled),
        "uninstalledPackages": uninstalled,
        "recommendedCommand": command,
    }
