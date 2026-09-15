#!/usr/bin/env python3
"""Validate Drupal 11 lifecycle artifact contract 3.0."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "3.0"
STATUSES = {"green", "amber", "red", "unknown"}
PLACEHOLDERS = {"", "none", "n/a", "na", "tbd", "todo", "unknown", "placeholder"}
PHASE_NAMES = (
    "Audit, Infrastructure, and Drupal 10 Stabilization",
    "Composer, Contributed Extensions, and Patches",
    "Custom Code Remediation and API Adjustments",
    "Core Upgrade and Configuration Alignment",
    "Quality Assurance and Mandatory UAT",
    "Deployment Rehearsal and Production Handoff",
)
PHASE_ARRAYS = (
    "entryCriteria",
    "tasks",
    "evidence",
    "dependencies",
    "acceptanceCriteria",
    "testingInstructions",
    "exitCriteria",
    "stopConditions",
)
REQUIRED_FILES = (
    "assessment.json",
    "phase-status.json",
    "phase-report.md",
    "00-baseline/infrastructure-report.json",
    "00-baseline/log-review.json",
    "00-baseline/database-health.json",
    "01-upgrade-control/approval-decision.json",
    "01-upgrade-control/approved-step-manifest.json",
    "01-upgrade-control/compatibility-matrix.json",
    "01-upgrade-control/affected-custom-extensions.csv",
    "01-upgrade-control/patch-register.json",
    "01-upgrade-control/backup-and-rollback-plan.md",
    "01-upgrade-control/risk-acceptances.json",
    "01-upgrade-control/executive-summary.md",
    "01-upgrade-control/release-recommendation.md",
    "02-static-analysis/phpstan-drupal-report.json",
    "02-static-analysis/drupal-rector-report.json",
    "03-content-audit/post-upgrade-content-diff.json",
    "04-qa-uat/qa-test-catalog.json",
    "04-qa-uat/qa-test-results.json",
    "04-qa-uat/smoke-test-report.json",
    "04-qa-uat/phpunit-report.json",
    "04-qa-uat/uat-plan.md",
    "04-qa-uat/uat-scenarios.csv",
    "04-qa-uat/uat-results.json",
    "04-qa-uat/uat-approval.json",
    "05-release/deployment-rehearsal.json",
    "05-release/rollback-rehearsal.json",
    "05-release/monitoring-plan.md",
    "05-release/final-signoff.json",
)
ALLOWED_ENTRYPOINTS = {
    "acli",
    "composer",
    "ddev",
    "drush",
    "fin",
    "lando",
    "php",
    "terminus",
    "bin/drush",
    "./bin/drush",
    "vendor/bin/drush",
    "./vendor/bin/drush",
}
NON_PROD = {"local", "dev", "multidev", "test"}
SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*[^\s]+"),
    re.compile(r"(?i)authorization:\s*bearer\s+[^\s]+"),
)


def read_json(path: Path, blocks: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocks.append(f"{path.name}: {exc}")
        return {}
    if not isinstance(value, dict):
        blocks.append(f"{path.name} must contain an object")
        return {}
    if value.get("schemaVersion") != SCHEMA_VERSION:
        blocks.append(f"{path.name} schemaVersion must be 3.0")
    return value


def good_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in PLACEHOLDERS


def valid_iso(value: Any) -> bool:
    if not good_text(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def evidence_path(root: Path, value: str) -> bool:
    pure = PurePosixPath(value)
    return not pure.is_absolute() and ".." not in pure.parts and (root / pure).is_file()


def validate_phase_status(root: Path, blocks: list[str]) -> list[dict[str, Any]]:
    data = read_json(root / "phase-status.json", blocks)
    phases = data.get("phases")
    if not isinstance(phases, list) or len(phases) != 6:
        blocks.append("phase-status.json requires exactly six phases")
        return []
    blocked_earlier = False
    for index, phase in enumerate(phases, start=1):
        if phase.get("id") != f"P{index}" or phase.get("name") != PHASE_NAMES[index - 1]:
            blocks.append(f"phase {index} identity/order is invalid")
        for key in ("goal", "owner", "risk", "estimateBand", "approvalState"):
            if not good_text(phase.get(key)):
                blocks.append(f"phase {index}.{key} requires non-placeholder text")
        if phase.get("status") not in STATUSES:
            blocks.append(f"phase {index}.status is invalid")
        for key in PHASE_ARRAYS:
            values = phase.get(key)
            if (
                not isinstance(values, list)
                or not values
                or any(not good_text(item) for item in values)
            ):
                blocks.append(f"phase {index}.{key} requires non-placeholder entries")
        if phase.get("status") == "green" and blocked_earlier:
            blocks.append(f"phase {index} cannot be green after an earlier red/unknown phase")
        if phase.get("status") in {"red", "unknown"}:
            blocked_earlier = True
    return phases


def validate_manifest(
    root: Path, approval: dict[str, Any], release_id: str, blocks: list[str]
) -> None:
    manifest = read_json(root / "01-upgrade-control/approved-step-manifest.json", blocks)
    steps = manifest.get("steps")
    if manifest.get("environment") not in NON_PROD:
        blocks.append("step manifest environment must be non-production")
    if manifest.get("releaseCandidateId") != release_id:
        blocks.append("step manifest release ID mismatch")
    for key in ("approvedBy", "backupEvidence", "rollbackPlan"):
        if not good_text(manifest.get(key)):
            blocks.append(f"step manifest requires {key}")
    if not isinstance(steps, list):
        blocks.append("step manifest steps must be a list")
        return
    for index, step in enumerate(steps):
        command = step.get("command") if isinstance(step, dict) else None
        if (
            not isinstance(command, list)
            or not command
            or any(not good_text(item) for item in command)
        ):
            blocks.append(f"step {index} command must be a non-empty argv array")
            continue
        joined = " ".join(command).lower()
        if command[0] not in ALLOWED_ENTRYPOINTS:
            blocks.append(f"step {index} entrypoint is not allowlisted")
        if any(token in joined for token in (";", "&&", "||", "|", ">", "<", "$(", "`")):
            blocks.append(f"step {index} contains shell metacharacters")
        if (
            "production" in joined
            or " prod" in f" {joined}"
            or step.get("environment") == "production"
        ):
            blocks.append(f"step {index} attempts Production execution")
    approved = set(approval.get("approvedScopes", []))
    for step in steps:
        if step.get("scope") not in approved:
            blocks.append(f"step scope is not approved: {step.get('scope')}")


def validate_uat(
    root: Path, release_id: str, production_green: bool, blocks: list[str], warnings: list[str]
) -> None:
    uat = read_json(root / "04-qa-uat/uat-approval.json", blocks)
    status = uat.get("status")
    if status not in {"not_started", "in_progress", "approved", "rejected"}:
        blocks.append("uat-approval.json status is invalid")
    if status == "rejected":
        blocks.append("UAT is rejected")
    if status != "approved":
        if production_green:
            blocks.append("Green Production handoff requires approved UAT")
        else:
            warnings.append("UAT is not approved")
        return
    required = (
        "approvedBy",
        "approvedAt",
        "environment",
        "testedReleaseId",
        "releaseCandidateId",
        "defectDisposition",
    )
    for key in required:
        if not good_text(uat.get(key)):
            blocks.append(f"approved UAT requires {key}")
    if not valid_iso(uat.get("approvedAt")):
        blocks.append("approved UAT requires ISO-8601 approvedAt")
    if uat.get("testedReleaseId") != release_id or uat.get("releaseCandidateId") != release_id:
        blocks.append("UAT release ID does not match assessment")
    if uat.get("buildMatchesReleaseCandidate") is not True:
        blocks.append("approved UAT must confirm build matches release candidate")
    if not isinstance(uat.get("acceptedScenarioIds"), list) or not uat["acceptedScenarioIds"]:
        blocks.append("approved UAT requires acceptedScenarioIds")
    defects = uat.get("unresolvedDefects")
    if not isinstance(defects, list):
        blocks.append("approved UAT requires unresolvedDefects list")
    else:
        for index, defect in enumerate(defects):
            if defect.get("releaseBlocking") is True:
                blocks.append(f"UAT defect {index} is release blocking")
            for key in ("severity", "owner", "rationale", "targetDate", "acceptedBy"):
                if not good_text(defect.get(key)):
                    blocks.append(f"UAT defect {index} requires {key}")


def validate_rehearsals(
    root: Path, release_id: str, production_green: bool, blocks: list[str]
) -> None:
    for filename in ("deployment-rehearsal.json", "rollback-rehearsal.json"):
        data = read_json(root / "05-release" / filename, blocks)
        if data.get("releaseCandidateId") != release_id:
            blocks.append(f"{filename} release ID mismatch")
        if data.get("status") not in {"passed", "failed", "not_run"}:
            blocks.append(f"{filename} status is invalid")
        if production_green and data.get("status") != "passed":
            blocks.append(f"Green Production handoff requires passed {filename}")
        for key in ("environment", "owner", "evidence"):
            if not good_text(data.get(key)):
                blocks.append(f"{filename} requires {key}")


def validate_quality(root: Path, release_id: str, phase_green: bool, blocks: list[str]) -> None:
    catalog = read_json(root / "04-qa-uat/qa-test-catalog.json", blocks)
    results = read_json(root / "04-qa-uat/qa-test-results.json", blocks)
    smoke = read_json(root / "04-qa-uat/smoke-test-report.json", blocks)
    phpunit = read_json(root / "04-qa-uat/phpunit-report.json", blocks)
    content = read_json(root / "03-content-audit/post-upgrade-content-diff.json", blocks)
    phpstan = read_json(root / "02-static-analysis/phpstan-drupal-report.json", blocks)
    rector = read_json(root / "02-static-analysis/drupal-rector-report.json", blocks)
    for name, data in (
        ("qa catalog", catalog),
        ("qa results", results),
        ("smoke report", smoke),
        ("PHPUnit report", phpunit),
    ):
        if data.get("releaseCandidateId") != release_id:
            blocks.append(f"{name} release ID mismatch")
    scenarios = catalog.get("scenarios") if isinstance(catalog.get("scenarios"), list) else []
    qa_results = results.get("results") if isinstance(results.get("results"), list) else []
    if phase_green and (not scenarios or not qa_results):
        blocks.append("Green QA/UAT phase requires non-empty QA catalog and results")
    critical = {item.get("id") for item in scenarios if item.get("criticality") == "critical"}
    result_by_id = {item.get("id"): item for item in qa_results}
    for scenario_id in critical:
        result = result_by_id.get(scenario_id)
        if not result or result.get("status") in {"blocked", "skipped"}:
            blocks.append(f"critical QA scenario is unknown: {scenario_id}")
        elif result.get("status") != "passed":
            blocks.append(f"critical QA scenario failed: {scenario_id}")
    if phase_green and (
        smoke.get("status") != "passed" or (smoke.get("summary") or {}).get("total", 0) < 1
    ):
        blocks.append("Green QA/UAT phase requires passing non-empty HTTP smoke results")
    if phase_green and (
        phpunit.get("status") != "passed" or (phpunit.get("summary") or {}).get("tests", 0) < 1
    ):
        blocks.append("Green QA/UAT phase requires passing non-empty PHPUnit results")
    checks = content.get("checks") or {}
    if phase_green and (
        content.get("status") != "ok"
        or checks.get("configurationUuidPreserved") is not True
        or checks.get("entityRevisionDriftDetected") is True
        or checks.get("fieldStorageDriftDetected") is True
    ):
        blocks.append("Green QA/UAT phase requires reconciled content/configuration state")
    if phase_green and (phpstan.get("summary") or {}).get("errorCount", 0) > 0:
        blocks.append("Green QA/UAT phase cannot contain PHPStan errors")
    if phase_green and (rector.get("summary") or {}).get("manualReviewComplete") is not True:
        blocks.append("Green QA/UAT phase requires completed Rector manual review")


def validate_signoff(
    root: Path, release_id: str, production_green: bool, blocks: list[str]
) -> None:
    signoff = read_json(root / "05-release/final-signoff.json", blocks)
    if signoff.get("releaseCandidateId") != release_id:
        blocks.append("final sign-off release ID mismatch")
    if production_green:
        if signoff.get("status") != "approved":
            blocks.append("Green Production handoff requires approved final sign-off")
        signers = signoff.get("signers")
        if (
            not isinstance(signers, list)
            or len(signers) < 5
            or any(not good_text(item) for item in signers)
        ):
            blocks.append(
                "Green Production handoff requires engineering, QA, business, platform, and release signers"
            )


def validate_amber(root: Path, findings: list[dict[str, Any]], blocks: list[str]) -> None:
    data = read_json(root / "01-upgrade-control/risk-acceptances.json", blocks)
    acceptances = data.get("acceptances") if isinstance(data.get("acceptances"), list) else []
    by_id = {item.get("findingId"): item for item in acceptances}
    for finding in findings:
        if finding.get("status") != "amber":
            continue
        acceptance = by_id.get(finding.get("id"))
        if not acceptance:
            blocks.append(f"Amber finding {finding.get('id')} lacks risk acceptance")
            continue
        for key in (
            "approvedBy",
            "rationale",
            "acceptedAt",
            "reviewOrExpiryAt",
            "owner",
            "evidence",
        ):
            if not good_text(acceptance.get(key)):
                blocks.append(f"Amber finding {finding.get('id')} acceptance requires {key}")


def validate_reports(
    root: Path, assessment: dict[str, Any], blocks: list[str], warnings: list[str]
) -> None:
    index_path = root / "report-index.json"
    if not index_path.is_file():
        warnings.append("audience reports not generated")
        return
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocks.append(f"report-index.json is invalid: {exc}")
        return
    if index.get("schemaVersion") != SCHEMA_VERSION or not isinstance(index.get("reports"), list):
        blocks.append("report-index.json requires schemaVersion 3.0 and reports list")
        return
    source_hash = hashlib.sha256((root / "assessment.json").read_bytes()).hexdigest()
    for report in index["reports"]:
        relative = report.get("path", "")
        path = root / relative
        if not evidence_path(root, relative):
            blocks.append(f"report path is invalid: {relative}")
            continue
        if report.get("releaseCandidateId") != assessment.get("releaseCandidateId"):
            blocks.append(f"report release ID mismatch: {relative}")
        if report.get("sourceAssessmentSha256") != source_hash:
            blocks.append(f"report source assessment hash mismatch: {relative}")
        if report.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            blocks.append(f"report digest mismatch: {relative}")
        if report.get("gate") != assessment.get("releaseGate"):
            blocks.append(f"report gate mismatch: {relative}")
    for name in ("client-summary.md", "developer-report.md", "combined-report.md"):
        if not (root / name).is_file():
            blocks.append(f"missing audience report: {name}")
        else:
            text = (root / name).read_text(encoding="utf-8", errors="replace")
            if assessment.get("releaseCandidateId") not in text:
                blocks.append(f"audience report missing release candidate ID: {name}")
            if f"{assessment.get('counts', {}).get('green')}" not in text:
                warnings.append(f"audience report should expose finding counts: {name}")


def validate_packet(root: Path) -> dict[str, Any]:
    blocks: list[str] = []
    warnings: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            blocks.append(f"missing artifact: {relative}")
    if blocks:
        return payload(root, blocks, warnings)
    assessment = read_json(root / "assessment.json", blocks)
    phases = validate_phase_status(root, blocks)
    release_id = assessment.get("releaseCandidateId")
    if not good_text(release_id):
        blocks.append("assessment requires releaseCandidateId")
        release_id = ""
    gate = assessment.get("releaseGate")
    findings = assessment.get("findings") if isinstance(assessment.get("findings"), list) else []
    calculated = {
        status: sum(1 for item in findings if item.get("status") == status) for status in STATUSES
    }
    if assessment.get("counts") != calculated:
        blocks.append("assessment finding counts are inconsistent")
    expected_gate = (
        "red"
        if calculated["red"]
        else "unknown"
        if calculated["unknown"]
        else "amber"
        if calculated["amber"]
        else "green"
    )
    if gate != expected_gate:
        blocks.append("assessment releaseGate does not match finding precedence")
    if calculated["red"]:
        blocks.append("assessment contains unresolved Red findings")
    if calculated["unknown"]:
        blocks.append("assessment contains unresolved Unknown findings")
    if calculated["amber"]:
        warnings.append("assessment contains accepted or pending Amber findings")
    phase_gate = (
        "red"
        if any(p.get("status") == "red" for p in phases)
        else "unknown"
        if any(p.get("status") == "unknown" for p in phases)
        else "amber"
        if any(p.get("status") == "amber" for p in phases)
        else "green"
    )
    if phases and gate != phase_gate:
        blocks.append("assessment releaseGate does not match phase statuses")

    approval = read_json(root / "01-upgrade-control/approval-decision.json", blocks)
    validate_manifest(root, approval, release_id, blocks)
    production_green = bool(phases and phases[5].get("status") == "green")
    validate_uat(root, release_id, production_green, blocks, warnings)
    validate_rehearsals(root, release_id, production_green, blocks)
    validate_quality(root, release_id, bool(phases and phases[4].get("status") == "green"), blocks)
    validate_signoff(root, release_id, production_green, blocks)
    validate_amber(root, findings, blocks)
    validate_reports(root, assessment, blocks, warnings)

    artifacts = assessment.get("artifacts") if isinstance(assessment.get("artifacts"), list) else []
    for item in artifacts:
        relative = item.get("path", "")
        if not evidence_path(root, relative):
            blocks.append(f"assessment artifact path is invalid: {relative}")
            continue
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if item.get("sha256") != digest:
            blocks.append(f"artifact digest mismatch: {relative}")

    summary_marker = f"Release gate: **{gate}**"
    count_marker = f"| {calculated['green']} | {calculated['amber']} | {calculated['red']} | {calculated['unknown']} |"
    for relative in (
        "01-upgrade-control/executive-summary.md",
        "01-upgrade-control/release-recommendation.md",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        if summary_marker not in text or count_marker not in text:
            blocks.append(f"{relative} is inconsistent with assessment")

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".md", ".csv", ".txt"}:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if any(
            pattern.search(content) and "[REDACTED]" not in pattern.search(content).group(0)
            for pattern in SECRET_PATTERNS
        ):
            warnings.append(f"possible unredacted secret: {path.relative_to(root)}")
    return payload(root, blocks, warnings)


def payload(root: Path, blocks: list[str], warnings: list[str]) -> dict[str, Any]:
    code = 1 if blocks else 2 if warnings else 0
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "block" if blocks else "warning" if warnings else "success",
        "exitCode": code,
        "artifactRoot": str(root),
        "summary": {"blockCount": len(blocks), "warningCount": len(warnings)},
        "blocks": blocks,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    result = validate_packet(args.artifact_root)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return result["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
