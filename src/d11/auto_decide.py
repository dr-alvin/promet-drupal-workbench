"""Automated compatibility decision engine for Drupal 11 upgrades."""

from __future__ import annotations

from .auto_remediate import generate_remediation_proposals
from .common import Problem, read, write
from .compatibility import OBSOLETE_PERFORMANCE_MODULES, REMOVED_CORE_TO_CONTRIB, semver_match, version_tuple
from .knowledge import get_obsolete_modules, get_replacements
from .two_gate import compatibility_report, decide


def rule_operator_override(ext: dict, ctx: dict) -> dict | None:
    existing = ctx["existing"]
    is_clean = ctx["is_clean"]
    manual_proposal = ctx["manual_proposal"]
    name = ext.get("name")
    target = ext.get("targetVersion")
    accept_prereleases = ctx["accept_prereleases"]

    has_valid_patch = (
        not existing
        or existing.get("action") not in ("manual_remediation", "ai_manual_patch")
        or (manual_proposal.is_file() and not is_clean)
    )
    if (
        existing
        and existing.get("origin") == "operator"
        and existing.get("action")
        and existing["action"] not in ("defer", "unresolved")
        and (existing["action"] != "keep" or is_clean)
        and has_valid_patch
    ):
        action = existing["action"]
        cand_ver = existing.get("candidateVersion") or target
        rc_candidates = [rc.get("version") for rc in ext.get("releaseCandidates", []) if rc.get("version")]
        if action == "compatible_release" and is_clean and (not cand_ver or cand_ver == ext.get("currentVersion")):
            action = "keep"
            cand_ver = ext.get("currentVersion")
        elif action == "compatible_release" and rc_candidates and cand_ver not in rc_candidates:
            cand_major = cand_ver.split(".")[0] if cand_ver else ""
            match = next((v for v in rc_candidates if semver_match(v, cand_ver)), None)
            if not match and cand_major:
                same_major = [v for v in rc_candidates if v.split(".")[0] == cand_major]
                if same_major:
                    match = same_major[0]
            cand_ver = match or rc_candidates[0]
        return {
            "name": name,
            "action": action,
            "candidateId": existing.get("candidateId"),
            "candidateVersion": cand_ver,
            "acceptRisk": True if accept_prereleases else existing.get("acceptRisk", False),
            "note": existing.get("note") or "Preserved reviewed operator decision",
            "origin": "operator",
        }
    return None


def rule_removed_core_bridge(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    if name in REMOVED_CORE_TO_CONTRIB and (ext.get("installed") or ext.get("exported")):
        contrib_pkg = REMOVED_CORE_TO_CONTRIB[name]
        return {
            "name": name,
            "action": "compatible_release",
            "candidateId": None,
            "candidateVersion": ext.get("targetVersion") or "^1.0",
            "acceptRisk": False,
            "note": f"Removed from Drupal 11 core; transitioned to official contrib package {contrib_pkg}",
            "origin": "automatic",
        }
    return None


def rule_obsolete_performance(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    if name in ctx["obsolete_modules"] or ext.get("recommendedAction") == "remove":
        return {
            "name": name,
            "action": "remove",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Obsolete performance, unused, or removed extension uninstalled for Drupal 11 alignment",
            "origin": "automatic",
        }
    if (
        ext.get("installed") is False
        and ext.get("exported") is False
        and not ext.get("releaseCandidates")
    ):
        return {
            "name": name,
            "action": "remove",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Uninstalled extension removed for Drupal 11 alignment",
            "origin": "automatic",
        }
    return None


def rule_clean_extension(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    is_clean = ctx["is_clean"]
    if is_clean and ext.get("installed") is not False:
        return {
            "name": name,
            "action": "keep",
            "candidateId": None,
            "candidateVersion": ext.get("currentVersion"),
            "acceptRisk": False,
            "note": "Installed version is already Drupal 11 compatible",
            "origin": "automatic",
        }
    return None


def rule_known_replacement(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    pkg = ext.get("package") or f"drupal/{name}"
    replacements = ctx.get("replacements", {})
    if pkg in replacements:
        rep = replacements[pkg]
        return {
            "name": name,
            "action": "compatible_release",
            "candidateId": None,
            "candidateVersion": ext.get("targetVersion") or "^1.0",
            "acceptRisk": False,
            "note": f"Known community replacement: transitioned to {rep}",
            "origin": "automatic",
        }
    return None


def rule_compatible_release(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    src = ext.get("source")
    target = ext.get("targetVersion")
    is_clean = ctx["is_clean"]
    accept_prereleases = ctx["accept_prereleases"]
    manual_proposal = ctx["manual_proposal"]

    if manual_proposal.is_file():
        return {
            "name": name,
            "action": "ai_manual_patch" if ext.get("recommendedAction") == "ai_manual_patch" else "manual_remediation",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": True,
            "note": "Automatically generated validated remediation proposal",
            "origin": "automatic",
        }

    if src == "contrib" and (target or ext.get("releaseCandidates")):
        candidate_version = target
        rc_candidates = [rc.get("version") for rc in ext.get("releaseCandidates", []) if rc.get("version")]
        if not candidate_version or candidate_version == "dev" or (rc_candidates and candidate_version not in rc_candidates):
            for rc in ext.get("releaseCandidates", []):
                if rc.get("version") and rc.get("version") != "dev":
                    candidate_version = rc["version"]
                    break
        if not candidate_version and rc_candidates:
            candidate_version = rc_candidates[0]

        curr_tuple = version_tuple(ext.get("currentVersion"))
        target_tuple = version_tuple(candidate_version) if candidate_version else None

        if curr_tuple and target_tuple and target_tuple <= curr_tuple and is_clean:
            return {
                "name": name,
                "action": "keep",
                "candidateId": None,
                "candidateVersion": ext.get("currentVersion"),
                "acceptRisk": False,
                "note": "Installed version is already Drupal 11 compatible",
                "origin": "automatic",
            }
        elif candidate_version and (not rc_candidates or candidate_version in rc_candidates):
            return {
                "name": name,
                "action": "compatible_release",
                "candidateId": None,
                "candidateVersion": candidate_version,
                "acceptRisk": accept_prereleases,
                "note": "Target D11 release resolved by Composer solver or release history",
                "origin": "automatic",
            }
    return None


def rule_validated_patch(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    patches = ctx["patches"]
    if patches:
        return {
            "name": name,
            "action": "available_patch",
            "candidateId": patches[0]["id"],
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Automatically selected approved patch candidate",
            "origin": "automatic",
        }
    return None


def rule_uninstalled_cleanup(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    src = ext.get("source")
    target = ext.get("targetVersion")

    if ext.get("installed") is False and ext.get("exported") is False:
        return {
            "name": name,
            "action": "remove",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Uninstalled extension removed for Drupal 11 alignment",
            "origin": "automatic",
        }

    if (
        src == "contrib"
        and ext.get("type") == "theme"
        and not target
        and not any(rc.get("stability") == "stable" for rc in ext.get("releaseCandidates", []))
    ):
        return {
            "name": name,
            "action": "remove",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Contrib theme has no stable Drupal 11 release; removed/replaced instead of patched",
            "origin": "automatic",
        }
    return None


def rule_defer_with_blocker(ext: dict, ctx: dict) -> dict | None:
    name = ext.get("name")
    src = ext.get("source")
    target = ext.get("targetVersion")
    is_clean = ctx["is_clean"]

    if ext.get("releaseLookup") == "failed" and not target and not ext.get("releaseCandidates"):
        return {
            "name": name,
            "action": "defer",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Release lookup failed due to network error; deferred with hard blocker to prevent unsafe removal",
            "origin": "automatic",
        }

    if src == "custom":
        if is_clean:
            return {
                "name": name,
                "action": "keep",
                "candidateId": None,
                "candidateVersion": ext.get("currentVersion"),
                "acceptRisk": False,
                "note": "Declared Drupal 11 compatible and clean",
                "origin": "automatic",
            }
        else:
            return {
                "name": name,
                "action": "manual_remediation",
                "candidateId": None,
                "candidateVersion": None,
                "acceptRisk": True,
                "note": "Custom extension contains code findings requiring review/remediation",
                "origin": "automatic",
            }

    return {
        "name": name,
        "action": "defer",
        "candidateId": None,
        "candidateVersion": None,
        "acceptRisk": False,
        "note": "Undetermined extension candidate; operator review required",
        "origin": "automatic",
    }


DECISION_RULES = [
    rule_operator_override,
    rule_removed_core_bridge,
    rule_obsolete_performance,
    rule_clean_extension,
    rule_known_replacement,
    rule_compatible_release,
    rule_validated_patch,
    rule_uninstalled_cleanup,
    rule_defer_with_blocker,
]


def auto_decide(w, rid: str, accept_prereleases: bool = True, auto_remediate: bool = True) -> dict:
    """Analyze scan evidence, auto-remediate custom code, and resolve all extension decisions."""
    out, state = w.run(rid)
    if state.get("action") != "guided-audit" or state.get("status") != "completed":
        raise Problem("Select a completed audit")

    report = compatibility_report(w, rid)
    p, cfg = w.project(state["project"])
    site_root = p / "site"

    context = read(out / "result/context.json") if (out / "result/context.json").is_file() else {}

    remediation_summary = None
    if auto_remediate:
        remediation_summary = generate_remediation_proposals(site_root, out, report, context)

    existing_decisions = (
        read(out / "compatibility-decisions.json")
        if (out / "compatibility-decisions.json").is_file()
        else {}
    )
    decisions = []
    actions_count = {}

    target_ver = state.get("target") or "11"
    obsolete_mods = set(get_obsolete_modules(target_ver)) | set(OBSOLETE_PERFORMANCE_MODULES)
    known_replacements = get_replacements(target_ver)

    active_extensions = list(report.get("extensions", []))
    installed_packages = {ext.get("package") for ext in active_extensions if ext.get("package")}
    uninstalled_roots = []
    seen_packages = set()
    for ext in report.get("presentNotInstalled", []):
        pkg = ext.get("package")
        if (
            pkg
            and pkg not in installed_packages
            and pkg not in seen_packages
            and ext.get("recommendedAction") == "remove"
            and ext.get("installed") is False
            and ext.get("exported") is False
        ):
            seen_packages.add(pkg)
            uninstalled_roots.append(ext)

    for ext in active_extensions + uninstalled_roots:
        name = ext.get("name")
        patches = [c for c in ext.get("patches", []) if c.get("approvalEligible")]
        manual_proposal = out / "manual-patches" / name / "proposal.json"
        existing = existing_decisions.get(name)

        is_clean = (
            ext.get("status") == "ready"
            and ext.get("scanned", True) is not False
            and not ext.get("upgradeStatus", {}).get("issueCount")
            and not ext.get("rector", {}).get("fixableCount")
        )

        ctx = {
            "existing": existing,
            "is_clean": is_clean,
            "manual_proposal": manual_proposal,
            "patches": patches,
            "accept_prereleases": accept_prereleases,
            "obsolete_modules": obsolete_mods,
            "replacements": known_replacements,
        }

        decision = None
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                break

        if decision is None:
            decision = {
                "name": name,
                "action": "defer",
                "candidateId": None,
                "candidateVersion": None,
                "acceptRisk": False,
                "note": "Undetermined extension candidate; operator review required",
                "origin": "automatic",
            }

        decisions.append(decision)
        actions_count[decision["action"]] = actions_count.get(decision["action"], 0) + 1

    # Rebuild Gate 1 with the complete decisions payload
    result = decide(w, rid, {"decisions": decisions})

    return {
        "status": "passed",
        "run": rid,
        "totalDecisions": len(decisions),
        "breakdown": actions_count,
        "remediation": remediation_summary,
        "gate": result.get("gate", {}),
    }
