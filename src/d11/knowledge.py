"""Version knowledge loader and validation for version-agnostic upgrade workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .common import ROOT, Problem, schema

_CACHE: dict[str, dict[str, Any]] = {}

# Default fallbacks if config files are inaccessible
DEFAULT_TARGET = "11"
DEFAULT_BRANCH = "upgrade/drupal-11"
DEFAULT_TARGET_CONSTRAINT = "^11"
DEFAULT_TARGET_INFO_YML = "^10 || ^11"
DEFAULT_SOURCE_MAJOR = 10
DEFAULT_SOURCE_MINIMUM = "10.3.0"
REMOVED_CORE = {"action", "book", "forum", "statistics", "tour", "tracker"}


def _normalize_target(target: Any) -> str:
    if not target:
        return DEFAULT_TARGET
    if isinstance(target, dict):
        target = target.get("targetVersion") or target.get("target") or DEFAULT_TARGET
    if not isinstance(target, (str, int)):
        return DEFAULT_TARGET
    t = str(target).strip()
    if t.endswith(".json"):
        t = t[:-5]
    if t.startswith("drupal-"):
        t = t[7:]
    m = re.search(r"^(\d+)", t) or re.search(r"(\d+)", t)
    if m:
        return m.group(1)
    return t or DEFAULT_TARGET


def load_knowledge(target: str | int | None = None) -> dict[str, Any]:
    """Load and validate knowledge configuration for the given Drupal target version."""
    norm = _normalize_target(target)
    if norm in _CACHE:
        return _CACHE[norm]

    candidates = [
        ROOT / f"config/knowledge/drupal-{norm}.json",
        ROOT / f"config/knowledge/{norm}.json",
        ROOT / f"config/knowledge/drupal-{norm}",
        ROOT / f"config/knowledge/{norm}",
    ]

    found = None
    for cand in candidates:
        if cand.is_file():
            found = cand
            break

    if not found:
        # Fallback to default if asking for 11 and file is missing
        if norm == DEFAULT_TARGET:
            data = {
                "targetVersion": DEFAULT_TARGET,
                "source": {"major": DEFAULT_SOURCE_MAJOR, "minimum": DEFAULT_SOURCE_MINIMUM},
                "target": {
                    "constraint": DEFAULT_TARGET_CONSTRAINT,
                    "infoYml": DEFAULT_TARGET_INFO_YML,
                },
                "removedCoreExtensions": sorted(REMOVED_CORE),
                "patchSearchTerm": "search=Drupal%2011",
                "defaultBranch": DEFAULT_BRANCH,
                "deprecationRules": [],
            }
            _CACHE[norm] = data
            return data
        raise Problem(f"No knowledge configuration found for Drupal target: {target}")

    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except Exception as e:
        raise Problem(f"Failed to read knowledge configuration from {found}: {e}")

    # Validate schema
    schema_path = ROOT / "config/schemas/knowledge.json"
    if schema_path.is_file():
        schema(data, "knowledge")

    _CACHE[norm] = data
    return data


def get_knowledge(target: str | int | None = None) -> dict[str, Any]:
    """Retrieve cached knowledge configuration for target."""
    return load_knowledge(target)


def get_target_constraint(target: str | int | None = None) -> str:
    return str(load_knowledge(target)["target"]["constraint"])


def get_target_info_yml(target: str | int | None = None) -> str:
    return str(load_knowledge(target)["target"]["infoYml"])


def get_source_floor(target: str | int | None = None) -> tuple[int, tuple[int, ...], str]:
    k = load_knowledge(target)
    major = int(k["source"]["major"])
    min_str = str(k["source"]["minimum"])
    parts = tuple(int(x) for x in min_str.split("."))
    return major, parts, min_str


def get_removed_core(target: str | int | None = None) -> set[str]:
    return set(load_knowledge(target).get("removedCoreExtensions", []))


def get_default_branch(target: str | int | None = None) -> str:
    return str(load_knowledge(target).get("defaultBranch", DEFAULT_BRANCH))


def get_patch_search_term(target: str | int | None = None) -> str:
    return str(load_knowledge(target).get("patchSearchTerm", "search=Drupal%2011"))


def get_deprecation_rules(target: str | int | None = None) -> list[dict[str, Any]]:
    return list(load_knowledge(target).get("deprecationRules", []))


def get_requirements(target: str | int | None = None) -> dict[str, Any]:
    return dict(load_knowledge(target).get("requirements", {}))


def get_obsolete_modules(target: str | int | None = None) -> list[str]:
    return list(load_knowledge(target).get("obsoleteModules", []))


def get_removed_packages(target: str | int | None = None) -> list[str]:
    return list(load_knowledge(target).get("removedCorePackages", []))


def get_replacements(target: str | int | None = None) -> dict[str, str]:
    return dict(load_knowledge(target).get("replacements", {}))


def resolve_target(cfg_or_target: Any) -> str:
    if isinstance(cfg_or_target, dict):
        return str(cfg_or_target.get("target") or DEFAULT_TARGET)
    if cfg_or_target:
        return str(cfg_or_target)
    return DEFAULT_TARGET
