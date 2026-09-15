#!/usr/bin/env python3
"""Compare Drupal 11 content/state baseline JSON snapshots.

Emit strict JSON aligned to the artifact contract. The input may be a raw audit
object or an object with an ``audit`` key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "3.0"
VOLATILE_KEYS = {
    "timestamp",
    "generatedAt",
    "runId",
    "artifactPath",
    "sha256",
    "executionId",
}
CLASSIFICATIONS = (
    "approved_configuration_change",
    "expected_upgrade_change",
    "introduced_regression",
    "pre_existing_issue",
    "unknown",
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    return value.get("audit", value) if isinstance(value, dict) else value


def normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize_value(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        normalized = [normalize_value(item) for item in value]
        if all(isinstance(item, (str, int, float, bool, type(None))) for item in normalized):
            return sorted(normalized, key=lambda item: (str(type(item)), str(item)))
        if all(isinstance(item, dict) for item in normalized):

            def get_sort_key(item: dict[str, Any]) -> str:
                for k in ("id", "machineName", "machine_name", "name", "key", "path"):
                    if k in item and isinstance(item[k], (str, int)):
                        return f"{k}:{item[k]}"
                return json.dumps(item, sort_keys=True)

            return sorted(normalized, key=get_sort_key)
        return normalized
    return value


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(iter_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(iter_dicts(item))
    return found


def extract_config_uuid(value: Any) -> str | None:
    for item in iter_dicts(value):
        for key in ("configUuid", "config_uuid"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    for item in iter_dicts(value):
        candidate = item.get("uuid")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def collect_revision_markers(value: Any) -> set[str]:
    markers: set[str] = set()
    for item in iter_dicts(value):
        entity_type = item.get("entityType") or item.get("entity_type") or item.get("entity")
        revision_id = item.get("revisionId") or item.get("revision_id")
        entity_id = item.get("entityId") or item.get("entity_id") or item.get("id")
        if entity_type is not None and revision_id is not None:
            markers.add(f"{entity_type}:{entity_id}:{revision_id}")
    return markers


def collect_field_storage_markers(value: Any) -> set[str]:
    markers: set[str] = set()
    for item in iter_dicts(value):
        entity_type = item.get("entityType") or item.get("entity_type")
        field_name = item.get("fieldName") or item.get("field_name")
        storage_type = item.get("storageType") or item.get("storage_type") or item.get("type")
        cardinality = item.get("cardinality")
        if entity_type and field_name:
            markers.add(f"{entity_type}:{field_name}:{storage_type}:{cardinality}")
    return markers


def classify_change(path: str, before: Any, after: Any) -> str:
    lower = path.lower()
    if isinstance(after, dict) and after.get("approvedConfigurationChange") is True:
        return "approved_configuration_change"
    if isinstance(after, dict) and after.get("baselineState") == "existing":
        return "pre_existing_issue"
    if any(token in lower for token in ("schema", "storage", "fieldstorage", "field_storage")):
        return "expected_upgrade_change"
    if any(token in lower for token in ("regression", "error", "broken", "failed")):
        return "introduced_regression"
    return "unknown"


def diff_values(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else key
            if key not in before:
                changes.append(
                    {
                        "path": child,
                        "before": None,
                        "after": after[key],
                        "classification": "unknown",
                    }
                )
            elif key not in after:
                changes.append(
                    {
                        "path": child,
                        "before": before[key],
                        "after": None,
                        "classification": "unknown",
                    }
                )
            else:
                changes.extend(diff_values(before[key], after[key], child))
        return changes
    return [
        {
            "path": path,
            "before": before,
            "after": after,
            "classification": classify_change(path, before, after),
        }
    ]


def build_result(before: Any, after: Any) -> dict[str, Any]:
    normalized_before = normalize_value(before)
    normalized_after = normalize_value(after)
    changes = diff_values(normalized_before, normalized_after)
    before_uuid = extract_config_uuid(normalized_before)
    after_uuid = extract_config_uuid(normalized_after)
    before_revisions = collect_revision_markers(normalized_before)
    after_revisions = collect_revision_markers(normalized_after)
    before_storage = collect_field_storage_markers(normalized_before)
    after_storage = collect_field_storage_markers(normalized_after)

    summary = {label: 0 for label in CLASSIFICATIONS}
    for change in changes:
        summary[change["classification"]] += 1

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ok",
        "changed": bool(changes),
        "changeCount": len(changes),
        "checks": {
            "configurationUuidPreserved": before_uuid == after_uuid
            if before_uuid and after_uuid
            else False,
            "entityRevisionDriftDetected": before_revisions != after_revisions,
            "fieldStorageDriftDetected": before_storage != after_storage,
        },
        "classificationSummary": summary,
        "changes": changes,
    }


def build_error_payload(message: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "error",
        "error": message,
    }


def emit_payload(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        before = load_json(args.before)
        after = load_json(args.after)
        payload = build_result(before, after)
        emit_payload(payload, args.output)
        return 0
    except Exception as exc:  # pragma: no cover - CLI safety path
        payload = build_error_payload(str(exc))
        emit_payload(payload, args.output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
