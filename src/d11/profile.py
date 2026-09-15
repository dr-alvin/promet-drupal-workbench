"""Runtime profile loader and validation."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .common import Problem, schema

ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = ROOT / "config" / "profiles"

_PROFILES: dict[str, dict] = {}
DEFAULT_PROFILE = "docksal-php83"


def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def load_profile(name: str = DEFAULT_PROFILE) -> dict:
    profile_id = name.removesuffix(".json")
    if profile_id in _PROFILES:
        return _PROFILES[profile_id]

    path = PROFILES_DIR / f"{profile_id}.json"
    if not path.is_file():
        available = ", ".join(list_profiles()) or "none"
        raise Problem(f"Unknown runtime profile '{name}'. Available profiles: {available}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise Problem(f"Failed to read runtime profile '{name}': {e}") from e

    schema(data, "profile")
    _PROFILES[profile_id] = data
    return data


def get_profile(name: str | dict | None = None) -> dict:
    if isinstance(name, dict):
        return name
    if not name:
        name = DEFAULT_PROFILE
    return load_profile(str(name))


def validate_profile_php(profile: dict, php_version: str) -> None:
    php = str(php_version)
    pattern = profile.get("php", {}).get("supportedPattern")
    dest_ver = profile.get("php", {}).get("version", "8.3")
    if pattern and not re.match(pattern, php):
        raise Problem(
            f"Source PHP version is unsupported by the managed PHP {dest_ver} destination: {php}"
        )


def detect_database_engine(version: str) -> str:
    ver_strip = str(version).strip()
    ver_lower = ver_strip.lower()
    if "mariadb" in ver_lower:
        return "MariaDB"
    if "mysql" in ver_lower or "percona" in ver_lower or re.match(r"^[58]\.", ver_strip):
        return "MySQL"
    if re.match(r"^(?:10\.|11\.)", ver_strip):
        return "MariaDB"
    return "Unknown"


def validate_profile_database(profile: dict, database_version: str) -> None:
    version = str(database_version).strip()
    db_info = profile.get("database", {})
    expected_type = db_info.get("type", "MariaDB")
    supported_types = db_info.get(
        "supportedTypes",
        ["MariaDB", "MySQL"] if expected_type in ("MariaDB", "MySQL") else [expected_type],
    )
    pattern = db_info.get("supportedPattern", r"^(?:10\.(?:[3-9]|10|11)|11\.|8\.[0-9]+)")

    engine = detect_database_engine(version)
    if engine not in supported_types:
        types_str = ", ".join(supported_types)
        raise Problem(
            f"Automatic destination currently supports {types_str} sources; detected {version}"
        )

    if not re.match(pattern, version):
        raise Problem(
            f"Source database version is unsupported by the managed destination: {version}"
        )

