"""Maintainer helper: build the checked-in schema contracts."""

import json
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "config/schemas"


def obj(props, required=(), extra=False):
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": extra,
    }


def arr(items):
    return {"type": "array", "items": items}


s = {"type": "string", "minLength": 1}
b = {"type": "boolean"}
argv = {**arr(s), "minItems": 1}
env = obj(
    {
        "id": s,
        "kind": {"enum": ["local", "dev", "test", "multidev", "production"]},
        "authorized": b,
    },
    ["id", "kind", "authorized"],
)
site = obj({"uri": s}, ["uri"])
check = obj(
    {
        "argv": argv,
        "timeout": {"type": "integer", "minimum": 1},
        "stdoutEquals": {"type": "string"},
    },
    ["argv"],
)
step = obj(
    {
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
        "stage": {
            "enum": [
                "A-baseline",
                "B-readiness",
                "C-remediation",
                "D-composer",
                "E-deployment",
                "F-verification",
            ]
        },
        "argv": argv,
        "cwd": s,
        "mutates": b,
        "preparation": b,
        "destructive": b,
        "reviewedRemovalDigest": s,
        "newWorkaround": b,
        "importsConfig": b,
        "preconditions": arr(check),
        "postconditions": arr(check),
        "dependsOn": arr(s),
        "timeout": {"type": "integer", "minimum": 1},
        "description": s,
    },
    ["id", "stage", "argv", "cwd", "mutates", "description"],
)
recovery = obj(
    {k: s for k in ["code", "database", "files", "owner", "procedure", "verifiedAt"]},
    ["code", "database", "files", "owner", "procedure", "verifiedAt"],
)
project = obj(
    {
        "schemaVersion": {"const": "1.0"},
        "repository": s,
        "environment": env,
        "site": site,
        "roots": obj({"composer": s, "drupal": s, "vendor": s, "config": s, "custom": arr(s)}),
        "runtime": obj(
            {
                "wrapper": {"enum": ["ddev", "fin", "lando", "local"]},
                "commands": obj({"php": argv, "composer": argv, "drush": argv}),
            }
        ),
        "identity": obj({"argv": argv, "expected": s}, ["argv", "expected"]),
        "target": s,
        "recovery": recovery,
        "requirementsEvidence": s,
        "baselineEvidence": s,
        "proposedChanges": arr(s),
        "steps": arr(step),
        "checks": arr(
            obj(
                {
                    "id": s,
                    "argv": argv,
                    "cwd": s,
                    "kind": {"enum": ["command", "upgrade_status"]},
                    "required": b,
                    "timeout": {"type": "integer", "minimum": 1},
                },
                ["id", "argv", "cwd", "kind", "required"],
            )
        ),
        "configComparison": obj({"before": s, "after": s}, ["before", "after"]),
        "estimates": arr(
            obj(
                {
                    "phase": s,
                    "owner": s,
                    "lowHours": {"type": "number", "minimum": 0},
                    "highHours": {"type": "number", "minimum": 0},
                    "basis": s,
                },
                ["phase", "owner", "lowHours", "highHours", "basis"],
            )
        ),
        "visual": obj({"config": s, "output": s}, ["config", "output"]),
    },
    ["schemaVersion", "repository", "environment"],
)
number = {"type": "number", "minimum": 0}
project["properties"]["deliveryBudget"] = obj(
    {
        "targetHours": {"type": "number", "exclusiveMinimum": 0},
        "checkpointHours": {"type": "number", "exclusiveMinimum": 0},
        "projectDecision": s,
        "reportedHumanHours": number,
        "unattendedRuntimeHours": number,
        "externalWaitingHours": number,
        "businessUatHours": number,
        "completedWork": arr(s),
        "unresolvedBlockers": arr(s),
        "forecast": obj(
            {
                "scope": {"enum": ["total", "remaining"]},
                "lowHours": number,
                "highHours": number,
                "basis": s,
            },
            ["lowHours", "highHours", "basis"],
        ),
        "checkpointReview": obj(
            {"reviewer": s, "reviewedAt": s, "evidence": s}, ["reviewer", "reviewedAt", "evidence"]
        ),
    }
)
project["properties"]["compatibilityCoverage"] = obj(
    {"requiredCheckIds": {**arr(s), "minItems": 1}, "target": s, "scopeEvidence": s},
    ["requiredCheckIds", "target", "scopeEvidence"],
)

approval = obj(
    {
        "schemaVersion": {"const": "1.0"},
        "planId": s,
        "environment": env,
        "site": site,
        "approver": s,
        "approvedAt": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
        "steps": arr(s),
        "recoveryDigest": s,
        "approvedDestructive": b,
    },
    [
        "schemaVersion",
        "planId",
        "environment",
        "site",
        "approver",
        "approvedAt",
        "steps",
        "recoveryDigest",
    ],
)
for name, value in [("project", project), ("step", step), ("approval", approval)]:
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    (D / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
# Public evidence and browser contracts; executable validation also checks cross-field invariants.
baseline = obj(
    {
        "environment": env,
        "site": site,
        "codeRevision": s,
        "installedExtensions": s,
        "activeConfiguration": s,
        "publicScenarios": {**arr(s), "minItems": 1},
        "authenticatedScenarios": arr(s),
        "captureSettings": s,
        "captureSettingsHash": s,
    },
    [
        "environment",
        "site",
        "codeRevision",
        "installedExtensions",
        "activeConfiguration",
        "publicScenarios",
        "authenticatedScenarios",
        "captureSettings",
        "captureSettingsHash",
    ],
    True,
)
requirements = obj(
    {
        "target": s,
        "sources": {**arr(s), "minItems": 1},
        "verifiedAt": s,
        "readinessPassed": b,
        "earlierDatabaseUpdatesComplete": b,
        "removedCoreExtensionsReviewed": b,
    },
    [
        "target",
        "sources",
        "verifiedAt",
        "readinessPassed",
        "earlierDatabaseUpdatesComplete",
        "removedCoreExtensionsReviewed",
    ],
    True,
)
plan_schema = obj(
    {
        "schemaVersion": {"const": "1.0"},
        "planId": s,
        "environment": env,
        "site": site,
        "source": s,
        "target": {"type": ["string", "null"]},
        "steps": arr(step),
        "blockers": arr(s),
        "inputs": {"type": "object"},
    },
    ["schemaVersion", "planId", "environment", "source", "steps", "blockers", "inputs"],
    True,
)
result_schema = obj(
    {
        "schemaVersion": {"const": "1.0"},
        "status": {
            "enum": [
                "passed",
                "failed",
                "prepared",
                "running",
                "findings",
                "tool_failure",
                "blocked",
                "unknown",
            ]
        },
        "checks": arr(
            obj(
                {
                    "id": s,
                    "status": {
                        "enum": [
                            "passed",
                            "failed",
                            "findings",
                            "visual_mismatch",
                            "functional_failure",
                            "tool_failure",
                            "blocked",
                            "skipped",
                            "unknown",
                        ]
                    },
                },
                ["id", "status"],
                True,
            )
        ),
    },
    ["schemaVersion", "status"],
    True,
)
interaction = obj(
    {
        "action": {
            "enum": [
                "click",
                "fill",
                "select",
                "press",
                "check",
                "wait",
                "assertVisible",
                "assertText",
                "upload",
            ]
        },
        "selector": s,
        "value": s,
        "file": s,
    },
    ["action", "selector"],
)
scenario = obj(
    {
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
        "label": s,
        "critical": b,
        "path": s,
        "referenceUrl": s,
        "testUrl": s,
        "expectedUrl": {"oneOf": [s, obj({"reference": s, "test": s}, ["reference", "test"])]},
        "viewport": obj(
            {
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
            },
            ["width", "height"],
        ),
        "role": s,
        "requiredElements": {**arr(s), "minItems": 1},
        "requiredText": arr(s),
        "ready": obj(
            {
                "requiredImages": arr(s),
                "selector": s,
                "timeoutMs": {"type": "integer", "minimum": 1},
                "scroll": obj(
                    {
                        "iterations": {"type": "integer", "minimum": 1},
                        "maxDistance": {"type": "integer", "minimum": 1},
                        "timeoutMs": {"type": "integer", "minimum": 1},
                    }
                ),
            },
            ["selector"],
        ),
        "threshold": {"type": "number", "minimum": 0, "maximum": 100},
        "setup": arr(interaction),
        "interactions": arr(interaction),
        "assertions": arr(interaction),
        "cleanup": arr(interaction),
        "masks": arr(obj({"selector": s, "reason": s}, ["selector", "reason"])),
        "mutates": b,
    },
    ["id", "label", "viewport", "expectedUrl", "requiredElements", "ready"],
)
scenarios_schema = obj(
    {
        "scenarios": {**arr(scenario), "minItems": 1},
        "environment": obj(
            {
                "id": s,
                "kind": {"enum": ["local", "dev", "test", "multidev", "production"]},
                "authorized": b,
                "disposable": b,
                "allowFixtureMutation": b,
            },
            ["id", "kind", "authorized"],
        ),
    },
    ["scenarios", "environment"],
    True,
)
for name, value in [
    ("baseline", baseline),
    ("requirements", requirements),
    ("plan", plan_schema),
    ("result", result_schema),
    ("scenarios", scenarios_schema),
]:
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    (D / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
