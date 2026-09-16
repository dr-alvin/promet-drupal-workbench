"""Automated compatibility decision engine for Drupal 11 upgrades."""

from __future__ import annotations

from .auto_remediate import generate_remediation_proposals
from .common import Problem, read, write
from .compatibility import OBSOLETE_PERFORMANCE_MODULES, PROVUS_ECOSYSTEM_MODULES, REMOVED_CORE_TO_CONTRIB, is_clean_extension, is_provus_project, semver_match, semver_tuple, version_tuple
from .knowledge import get_obsolete_modules, get_replacements
from .patches import is_d11_compatible
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
        if ext.get("name") == "tb_megamenu":
            c_ver = str(ext.get("currentVersion") or "")
            c_t = semver_tuple(c_ver)
            if (c_t and c_t[0] == 3) or c_ver.startswith("3.") or "3.0.0-alpha5" in c_ver:
                cand_v = str(existing.get("candidateVersion") or "")
                cand_t = semver_tuple(cand_v)
                if cand_v.startswith("1.") or (cand_t and cand_t[0] == 1) or existing.get("action") == "compatible_release":
                    return {
                        "name": "tb_megamenu",
                        "action": "keep",
                        "candidateId": None,
                        "candidateVersion": ext.get("currentVersion") or "3.0.0-alpha5",
                        "acceptRisk": False,
                        "note": "tb_megamenu 3.0.0-alpha5 is already Drupal 11 compatible; corrected from 1.x to keep to prevent mega menu breakage",
                        "origin": "automatic",
                    }
        action = existing["action"]
        cand_ver = existing.get("candidateVersion") or target
        rc_candidates = [rc.get("version") for rc in ext.get("releaseCandidates", []) if rc.get("version")]
        curr_tuple = version_tuple(ext.get("currentVersion"))
        cand_tuple = version_tuple(cand_ver) if cand_ver else None
        if action == "compatible_release" and is_clean and (not cand_ver or cand_ver == ext.get("currentVersion") or (curr_tuple and cand_tuple and cand_tuple <= curr_tuple)):
            action = "keep"
            cand_ver = ext.get("currentVersion")
        elif action == "compatible_release":
            if (curr_tuple and cand_tuple and cand_tuple < curr_tuple) or (rc_candidates and cand_ver not in rc_candidates):
                valid_candidates = [
                    v for v in rc_candidates
                    if version_tuple(v) is None or curr_tuple is None or version_tuple(v) >= curr_tuple
                ]
                if valid_candidates:
                    cand_ver = valid_candidates[0]
                elif rc_candidates:
                    cand_ver = rc_candidates[0]
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


def rule_tb_megamenu(ext: dict, ctx: dict) -> dict | None:
    """tb_megamenu 3.0.0-alpha5 / 3.x must default to keep and never downgrade to 1.x.

    1.x releases (8.x-1.10) break the site's mega menu configuration when upgrading from 3.x.
    Since 3.0.0-alpha5 is already Drupal 11 compatible (^8 || ^9 || ^10 || ^11), it must be kept on 3.x.
    """
    if ext.get("name") == "tb_megamenu":
        c_ver = str(ext.get("currentVersion") or "")
        c_t = semver_tuple(c_ver)
        if (c_t and c_t[0] == 3) or c_ver.startswith("3.") or "3.0.0-alpha5" in c_ver:
            return {
                "name": "tb_megamenu",
                "action": "keep",
                "candidateId": None,
                "candidateVersion": ext.get("currentVersion") or "3.0.0-alpha5",
                "acceptRisk": False,
                "note": "tb_megamenu 3.0.0-alpha5 is already Drupal 11 compatible; retained on 3.x branch to prevent menu breakage from 1.x downgrade",
                "origin": "automatic",
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


def rule_provus_ecosystem(ext: dict, ctx: dict) -> dict | None:
    """Protect Provus ecosystem dependencies from being auto-removed.

    When the project is a Provus distribution, modules in
    ``PROVUS_ECOSYSTEM_MODULES`` must never receive ``action:"remove"``
    automatically.  They can remain uninstalled in Drupal — kept at the
    Composer/vendor level — unless the operator explicitly overrides the
    decision.
    """
    if not ctx.get("is_provus"):
        return None

    name = ext.get("name")
    if name not in PROVUS_ECOSYSTEM_MODULES:
        return None

    # If there is a clean D11-compatible release available, recommend it.
    target = ext.get("targetVersion")
    rc_candidates = [rc.get("version") for rc in ext.get("releaseCandidates", []) if rc.get("version")]
    is_clean = ctx["is_clean"]
    accept_prereleases = ctx["accept_prereleases"]
    manual_proposal = ctx.get("manual_proposal")

    if is_clean:
        return {
            "name": name,
            "action": "keep",
            "candidateId": None,
            "candidateVersion": ext.get("currentVersion"),
            "acceptRisk": False,
            "note": "Provus ecosystem dependency — already Drupal 11 compatible; retained in vendor",
            "origin": "automatic",
        }

    candidate_version = target
    if not candidate_version and rc_candidates:
        candidate_version = rc_candidates[0]

    if candidate_version:
        return {
            "name": name,
            "action": "compatible_release",
            "candidateId": None,
            "candidateVersion": candidate_version,
            "acceptRisk": accept_prereleases,
            "note": "Provus ecosystem dependency — retained in vendor; update to D11-compatible release",
            "origin": "automatic",
        }

    # If a manual/AI remediation proposal exists (e.g. custom Provus code like promet_provus_blocks), use it!
    if manual_proposal and manual_proposal.is_file():
        proposal = read(manual_proposal)
        return {
            "name": name,
            "action": proposal.get("action", "ai_manual_patch") if ext.get("recommendedAction") == "ai_manual_patch" else "manual_remediation",
            "candidateId": proposal.get("candidateId"),
            "candidateVersion": None,
            "acceptRisk": True,
            "note": "Provus ecosystem component with validated remediation proposal",
            "origin": "automatic",
        }

    # If an uninstalled contrib module has no D11 release and no manual proposal,
    # and its coreConstraint conflicts with Drupal 11, allow cleanup to remove it
    # so Composer can upgrade core.
    if (
        ext.get("installed") is False
        and ext.get("exported") is False
        and ext.get("coreConstraint")
        and not is_d11_compatible(ext.get("coreConstraint"))
        and not candidate_version
    ):
        return {
            "name": name,
            "action": "remove",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": False,
            "note": "Uninstalled Provus dependency with no D11 release removed for Drupal 11 alignment",
            "origin": "automatic",
        }

    # No release candidate yet — defer rather than remove.
    return {
        "name": name,
        "action": "defer",
        "candidateId": None,
        "candidateVersion": None,
        "acceptRisk": False,
        "note": "Provus ecosystem dependency — no D11 release found yet; retained in vendor, operator review required",
        "origin": "automatic",
    }


def rule_config_split_protection(ext: dict, ctx: dict) -> dict | None:
    """Protect modules configured in config splits (e.g. 'live', 'prod') from being removed.

    When an extension is defined in a config split, it is required in specific environments
    (such as live/production) even if uninstalled in the local database. It must never receive
    action: "remove".
    """
    splits = ext.get("configSplits") or []
    if not splits and not ext.get("inConfigSplit"):
        return None

    name = ext.get("name")
    split_str = ", ".join(splits) if splits else "split"
    target = ext.get("targetVersion")
    rc_candidates = [rc.get("version") for rc in ext.get("releaseCandidates", []) if rc.get("version")]
    is_clean = ctx["is_clean"]
    accept_prereleases = ctx["accept_prereleases"]

    if is_clean:
        return {
            "name": name,
            "action": "keep",
            "candidateId": None,
            "candidateVersion": ext.get("currentVersion"),
            "acceptRisk": False,
            "note": f"Extension configured in config split ('{split_str}') — clean and retained in vendor",
            "origin": "automatic",
        }

    candidate_version = target
    if not candidate_version and rc_candidates:
        candidate_version = rc_candidates[0]

    if candidate_version:
        return {
            "name": name,
            "action": "compatible_release",
            "candidateId": None,
            "candidateVersion": candidate_version,
            "acceptRisk": accept_prereleases,
            "note": f"Extension configured in config split ('{split_str}') — retained in vendor; update to D11-compatible release",
            "origin": "automatic",
        }

    return {
        "name": name,
        "action": "defer",
        "candidateId": None,
        "candidateVersion": None,
        "acceptRisk": False,
        "note": f"Extension configured in config split ('{split_str}') — no D11 release found yet; retained in vendor, operator review required",
        "origin": "automatic",
    }


def rule_obsolete_performance(ext: dict, ctx: dict) -> dict | None:
    if ext.get("configSplits") or ext.get("inConfigSplit"):
        return None
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
    if ext.get("configSplits") or ext.get("inConfigSplit"):
        return None

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
    rule_tb_megamenu,             # tb_megamenu 3.0.0-alpha5: keep 3.x to prevent menu breakage from 1.x
    rule_removed_core_bridge,
    rule_provus_ecosystem,      # Provus ecosystem deps: never auto-remove
    rule_config_split_protection, # Config split deps (e.g. live split): never auto-remove
    rule_obsolete_performance,
    rule_uninstalled_cleanup,
    rule_clean_extension,
    rule_known_replacement,
    rule_compatible_release,
    rule_validated_patch,
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

    # Detect Provus project once for the whole run — avoids repeated filesystem I/O.
    source_path = cfg.get("sourcePath") or str(site_root)
    _provus_ctx = {"extensions": report.get("extensions", [])}
    _provus_ctx.update(context)
    _is_provus = is_provus_project(site_root=source_path, context=_provus_ctx)

    active_extensions = list(report.get("extensions", []))
    installed_packages = {ext.get("package") for ext in active_extensions if ext.get("package")}
    uninstalled_roots = []
    split_roots = []
    seen_packages = set()
    for ext in report.get("presentNotInstalled", []):
        pkg = ext.get("package")
        if not pkg or pkg in installed_packages or pkg in seen_packages:
            continue
        if ext.get("configSplits") or ext.get("inConfigSplit"):
            seen_packages.add(pkg)
            split_roots.append(ext)
        elif ext.get("installed") is False and ext.get("exported") is False:
            seen_packages.add(pkg)
            uninstalled_roots.append(ext)

    for ext in active_extensions + uninstalled_roots + split_roots:
        name = ext.get("name")
        patches = [c for c in ext.get("patches", []) if c.get("approvalEligible")]
        manual_proposal = out / "manual-patches" / name / "proposal.json"
        existing = existing_decisions.get(name)

        # Single shared definition of "clean" (Invariant 2); see compatibility.is_clean_extension.
        is_clean = is_clean_extension(ext)

        ctx = {
            "existing": existing,
            "is_clean": is_clean,
            "is_provus": _is_provus,
            "manual_proposal": manual_proposal,
            "patches": patches,
            "accept_prereleases": accept_prereleases,
            "obsolete_modules": obsolete_mods,
            "replacements": known_replacements,
        }

        decision = None
        decided_by = "fallback_defer"
        for rule in DECISION_RULES:
            decision = rule(ext, ctx)
            if decision is not None:
                decided_by = rule.__name__
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

        # Provenance: which rule decided, and whether it overrode the
        # evidence-derived recommendation shown to the Gate 1 reviewer.
        recommended = ext.get("recommendedAction")
        decision["decidedBy"] = decided_by
        decision["recommendedAction"] = recommended
        decision["overrodeRecommendation"] = bool(recommended) and decision["action"] != recommended

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
