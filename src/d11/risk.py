"""Deterministic advisory risk scoring for the two-gate workbench."""

from .common import digest

POINTS = {"critical": 15, "high": 10, "medium": 5, "low": 2}
CAPS = {
    "platform_recovery": 25,
    "dependencies_patches": 30,
    "custom_code": 20,
    "configuration_deployment": 15,
    "qa_coverage": 10,
}
HARD_CHECKS = {
    "privacy_review",
    "runtime_identity",
    "source_version",
    "target_platform",
    "recovery_checkpoint",
    "baseline_capture",
    "compatibility",
    "composer_resolution",
    "critical_custom_code",
}


def finding(identifier, category, severity, message, status="findings", hard=False, evidence=None):
    if category not in CAPS or severity not in POINTS:
        raise ValueError("Unknown risk category or severity")
    return {
        "id": identifier,
        "category": category,
        "severity": severity,
        "status": status,
        "message": message,
        "hardBlocker": bool(hard),
        "evidence": evidence or [],
    }


def evaluate(findings):
    """Return score and recommendation. Evidence gates override the score."""
    totals = {name: 0 for name in CAPS}
    hard = []
    active = []
    for item in findings:
        if item.get("status") == "passed":
            continue
        category = item.get("category", "platform_recovery")
        severity = item.get("severity", "medium")
        totals[category] = min(CAPS[category], totals[category] + POINTS[severity])
        active.append(item)
        if (
            item.get("hardBlocker")
            or item.get("id") in HARD_CHECKS
            and item.get("status") in ("unknown", "failed", "blocked")
        ):
            hard.append(item["id"])
    score = min(100, sum(totals.values()))
    recommendation = "No-Go" if hard or score >= 70 else "Conditional Go" if score >= 25 else "Go"
    result = {
        "score": score,
        "recommendation": recommendation,
        "categories": totals,
        "caps": CAPS,
        "hardBlockers": sorted(set(hard)),
        "findings": active,
    }
    result["digest"] = digest(result)
    return result
