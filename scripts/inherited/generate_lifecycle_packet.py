#!/usr/bin/env python3
"""Generate a deterministic Drupal 11 lifecycle packet from reviewed input."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "3.0"
PLACEHOLDERS = {"", "none", "n/a", "na", "tbd", "todo", "unknown", "placeholder"}
PHASE_NAMES = (
    "Audit, Infrastructure, and Drupal 10 Stabilization",
    "Composer, Contributed Extensions, and Patches",
    "Custom Code Remediation and API Adjustments",
    "Core Upgrade and Configuration Alignment",
    "Quality Assurance and Mandatory UAT",
    "Deployment Rehearsal and Production Handoff",
)
PHASE_KEYS = (
    "id",
    "name",
    "goal",
    "entryCriteria",
    "tasks",
    "evidence",
    "owner",
    "risk",
    "estimateBand",
    "dependencies",
    "acceptanceCriteria",
    "testingInstructions",
    "approvalState",
    "exitCriteria",
    "stopConditions",
    "status",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def valid_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in PLACEHOLDERS


def require_text(value: Any, label: str) -> str:
    if not valid_text(value):
        raise ValueError(f"{label} requires non-placeholder text")
    return value.strip()


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} requires a non-empty list")
    for index, item in enumerate(value):
        if isinstance(item, str) and not valid_text(item):
            raise ValueError(f"{label}[{index}] is empty or a placeholder")
    return value


def validate_input(data: dict[str, Any]) -> None:
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("input schemaVersion must be 3.0")
    require_text(data.get("releaseCandidateId"), "releaseCandidateId")
    phases = require_list(data.get("phases"), "phases")
    if len(phases) != 6:
        raise ValueError("phases must contain exactly six records")
    for index, phase in enumerate(phases, start=1):
        if not isinstance(phase, dict):
            raise ValueError(f"phase {index} must be an object")
        for key in PHASE_KEYS:
            if key not in phase:
                raise ValueError(f"phase {index} missing {key}")
        if phase["id"] != f"P{index}" or phase["name"] != PHASE_NAMES[index - 1]:
            raise ValueError(f"phase {index} identity/order is invalid")
        if phase["status"] not in ("green", "amber", "red", "unknown"):
            raise ValueError("Invalid phase status")
        for key in ("goal", "owner", "risk", "estimateBand", "approvalState"):
            require_text(phase[key], f"phase {index}.{key}")
        for key in (
            "entryCriteria",
            "tasks",
            "evidence",
            "dependencies",
            "acceptanceCriteria",
            "testingInstructions",
            "exitCriteria",
            "stopConditions",
        ):
            require_list(phase[key], f"phase {index}.{key}")
    required_evidence = {
        0: ("infrastructure", "logReview", "databaseHealth"),
        1: ("compatibility", "patches"),
        2: ("phpstan", "rector"),
        3: ("contentDiff",),
        4: ("smokeTests", "phpunit", "qaCatalog", "qaResults", "uatResults"),
    }
    for index, keys in required_evidence.items():
        if phases[index]["status"] == "green":
            for key in keys:
                if key not in data or not data[key]:
                    raise ValueError(
                        f"Green phase {index + 1} requires explicit {key} evidence; it cannot be inferred from phase status"
                    )
    findings = require_list(data.get("findings"), "findings")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} must be an object")
        if finding.get("status") not in ("green", "amber", "red", "unknown"):
            raise ValueError("Invalid finding status")
        for key in ("id", "summary", "owner"):
            require_text(finding.get(key), f"finding {index}.{key}")
        require_list(finding.get("evidence"), f"finding {index}.evidence")
    for key in (
        "run",
        "approval",
        "stepManifest",
        "uatApproval",
        "deploymentRehearsal",
        "rollbackRehearsal",
        "finalSignoff",
    ):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"{key} requires an object")
    for key in ("uatPlan", "backupRollbackPlan", "monitoringPlan"):
        require_text(data.get(key), key)
    for key in (
        "environment",
        "releaseCandidateId",
        "approvedBy",
        "backupEvidence",
        "rollbackPlan",
    ):
        require_text(data["stepManifest"].get(key), f"stepManifest.{key}")
    for record_name in ("deploymentRehearsal", "rollbackRehearsal"):
        for key in ("releaseCandidateId", "status", "environment", "owner", "evidence"):
            require_text(data[record_name].get(key), f"{record_name}.{key}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def phase_report(phases: list[dict[str, Any]]) -> str:
    lines = ["# Drupal 11 Lifecycle Phase Report", ""]
    for phase in phases:
        lines += [
            f"## {phase['id']}. {phase['name']}",
            "",
            f"**Status:** {phase['status']}",
            f"**Owner:** {phase['owner']}",
            f"**Risk:** {phase['risk']}",
            f"**Estimate:** {phase['estimateBand']}",
            "",
            phase["goal"],
            "",
        ]
        for title, key in (
            ("Entry criteria", "entryCriteria"),
            ("Tasks", "tasks"),
            ("Evidence", "evidence"),
            ("Dependencies", "dependencies"),
            ("Acceptance criteria", "acceptanceCriteria"),
            ("Testing instructions", "testingInstructions"),
            ("Exit criteria", "exitCriteria"),
            ("Stop conditions", "stopConditions"),
        ):
            lines += [f"### {title}", ""] + [f"- {item}" for item in phase[key]] + [""]
    return "\n".join(lines)


def summary_markdown(title: str, gate: str, counts: dict[str, int], release_id: str) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            f"Release candidate: `{release_id}`",
            f"Release gate: **{gate}**",
            "",
            "| Green | Amber | Red | Unknown |",
            "| ---: | ---: | ---: | ---: |",
            f"| {counts['green']} | {counts['amber']} | {counts['red']} | {counts['unknown']} |",
            "",
            "> Production mutation is not authorized by this packet.",
        ]
    )


def create_packet(data: dict[str, Any], root: Path) -> None:
    validate_input(data)
    if root.exists() and any(root.iterdir()):
        raise ValueError("output directory must be absent or empty")
    root.mkdir(parents=True, exist_ok=True)
    phases = data["phases"]
    findings = sorted(data["findings"], key=lambda item: item["id"])
    counts = {
        status: sum(1 for item in findings if item["status"] == status)
        for status in ("green", "amber", "red", "unknown")
    }
    statuses = {p["status"] for p in phases} | {f["status"] for f in findings}
    gate = next(status for status in ("red", "unknown", "amber", "green") if status in statuses)
    if data.get("releaseGate") and data["releaseGate"] != gate:
        raise ValueError("Requested release gate disagrees with phase/finding evidence")
    release_id = data["releaseCandidateId"]

    write_json(root / "phase-status.json", {"schemaVersion": SCHEMA_VERSION, "phases": phases})
    write_text(root / "phase-report.md", phase_report(phases))

    evidence_defaults = {
        "00-baseline/infrastructure-report.json": data.get(
            "infrastructure",
            {"status": "unknown", "components": [], "evidence": phases[0]["evidence"]},
        ),
        "00-baseline/log-review.json": data.get(
            "logReview", {"status": "unknown", "findings": [], "evidence": phases[0]["evidence"]}
        ),
        "00-baseline/database-health.json": data.get(
            "databaseHealth",
            {
                "status": "unknown",
                "engine": None,
                "findings": [],
                "evidence": phases[0]["evidence"],
            },
        ),
        "01-upgrade-control/compatibility-matrix.json": data.get(
            "compatibility", {"status": "unknown", "items": []}
        ),
        "01-upgrade-control/patch-register.json": data.get(
            "patches", {"status": "unknown", "items": []}
        ),
        "01-upgrade-control/risk-acceptances.json": {
            "acceptances": data.get("riskAcceptances", [])
        },
        "02-static-analysis/phpstan-drupal-report.json": data.get(
            "phpstan",
            {
                "tool": "phpstan-drupal",
                "status": "unknown",
                "summary": {"errorCount": None},
                "findings": [],
            },
        ),
        "02-static-analysis/drupal-rector-report.json": data.get(
            "rector",
            {
                "tool": "drupal-rector",
                "status": "unknown",
                "summary": {"manualReviewRequired": True, "manualReviewComplete": False},
                "files": [],
            },
        ),
        "03-content-audit/post-upgrade-content-diff.json": data.get(
            "contentDiff", {"status": "unknown", "checks": {}, "changes": []}
        ),
        "04-qa-uat/qa-test-catalog.json": {
            "releaseCandidateId": release_id,
            "scenarios": data.get("qaCatalog", []),
        },
        "04-qa-uat/qa-test-results.json": {
            "releaseCandidateId": release_id,
            "results": data.get("qaResults", []),
        },
        "04-qa-uat/smoke-test-report.json": data.get(
            "smokeTests",
            {
                "status": "not_run",
                "releaseCandidateId": release_id,
                "summary": {"total": 0, "passed": 0, "failed": 0},
                "results": [],
            },
        ),
        "04-qa-uat/phpunit-report.json": data.get(
            "phpunit",
            {
                "status": "not_run",
                "releaseCandidateId": release_id,
                "summary": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
                "suites": [],
            },
        ),
        "04-qa-uat/uat-results.json": data.get(
            "uatResults", {"releaseCandidateId": release_id, "environment": None, "results": []}
        ),
        "04-qa-uat/uat-approval.json": data["uatApproval"],
        "05-release/deployment-rehearsal.json": data["deploymentRehearsal"],
        "05-release/rollback-rehearsal.json": data["rollbackRehearsal"],
        "05-release/final-signoff.json": data["finalSignoff"],
        "01-upgrade-control/approval-decision.json": data["approval"],
        "01-upgrade-control/approved-step-manifest.json": data["stepManifest"],
        "01-upgrade-control/context.json": data.get(
            "context",
            {"status": "unknown", "reason": "Project discovery evidence was not supplied."},
        ),
        "01-upgrade-control/composer-resolution.json": data.get(
            "composerResolution", {"status": "unknown", "blockers": [], "evidence": []}
        ),
        "01-upgrade-control/deployment-sequence.json": data.get(
            "deploymentSequence",
            {
                "status": "unknown",
                "files": [],
                "operationOrder": [],
                "moduleRemovalSafety": {"configurationBeforePackageRemoval": False},
            },
        ),
    }
    for relative, payload in evidence_defaults.items():
        if isinstance(payload, dict):
            payload = {**payload, "schemaVersion": SCHEMA_VERSION}
        write_json(root / relative, payload)

    custom = data.get("customExtensions", [])
    csv_path = root / "01-upgrade-control/affected-custom-extensions.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "type",
                "machine_name",
                "display_name",
                "path",
                "declared_core_support",
                "d11_status",
                "follow_up",
            ),
        )
        writer.writeheader()
        writer.writerows(sorted(custom, key=lambda item: (item["type"], item["machine_name"])))

    uat_scenarios = sorted(data.get("uatScenarios", []), key=lambda item: item["id"])
    uat_csv = root / "04-qa-uat/uat-scenarios.csv"
    uat_csv.parent.mkdir(parents=True, exist_ok=True)
    with uat_csv.open("w", encoding="utf-8", newline="") as handle:
        fields = (
            "id",
            "title",
            "business_owner",
            "criticality",
            "preconditions",
            "steps",
            "expected_result",
            "evidence",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(uat_scenarios)

    module_csv = root / "01-upgrade-control/module-readiness.csv"
    module_csv.parent.mkdir(parents=True, exist_ok=True)
    module_rows = data.get("moduleReadiness", [])
    module_fields = (
        "module",
        "package",
        "version",
        "composerConstraint",
        "coreRequirement",
        "enabled",
        "configurationReferences",
        "classification",
        "recommendedAction",
        "evidence",
    )
    with module_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=module_fields)
        writer.writeheader()
        writer.writerows(
            sorted(module_rows, key=lambda item: (item.get("package", ""), item.get("module", "")))
        )

    write_text(root / "04-qa-uat/uat-plan.md", data["uatPlan"])
    write_text(root / "01-upgrade-control/backup-and-rollback-plan.md", data["backupRollbackPlan"])
    write_text(root / "05-release/monitoring-plan.md", data["monitoringPlan"])
    write_text(
        root / "01-upgrade-control/executive-summary.md",
        summary_markdown("Drupal 11 Executive Summary", gate, counts, release_id),
    )
    write_text(
        root / "01-upgrade-control/release-recommendation.md",
        summary_markdown("Drupal 11 Release Recommendation", gate, counts, release_id),
    )

    artifacts = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        artifacts.append(
            {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
    assessment = {
        "schemaVersion": SCHEMA_VERSION,
        "run": data["run"],
        "releaseGate": gate,
        "counts": counts,
        "approval": data["approval"],
        "releaseCandidateId": release_id,
        "artifacts": artifacts,
        "findings": findings,
    }
    write_json(root / "assessment.json", assessment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        create_packet(load(args.input), args.output)
        return 0
    except Exception as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
