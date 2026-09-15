"""Normalize Drupal 11 extension evidence into one decision-oriented model."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .common import digest, file_hash, get_d11_home, now, write
from .knowledge import get_removed_core, get_target_constraint
from .patches import is_d11_compatible

DECISIONS = {
    "keep",
    "compatible_release",
    "available_patch",
    "ai_manual_patch",
    "remove",
    "manual_remediation",
    "defer",
}
PRE_RELEASE = re.compile(r"(?:dev|alpha|beta|rc)", re.I)
removed_core = get_removed_core()
REMOVED_CORE_TO_CONTRIB = {
    "action": "drupal/action",
    "book": "drupal/book",
    "forum": "drupal/forum",
    "statistics": "drupal/statistics",
    "tour": "drupal/tour",
    "tracker": "drupal/tracker",
}
OBSOLETE_PERFORMANCE_MODULES = {
    "advagg",
    "advagg_bundler",
    "advagg_css_minify",
    "advagg_js_minify",
    "advagg_mod",
    "advagg_validator",
    "fastclick",
}


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def fixture_extension(extension):
    parts = Path(extension.get("path") or "").parts
    return any(part.lower() in ("tests", "test", "fixtures", "examples") for part in parts)


def safe_upgrade(row, checks):
    reasons = []
    current = row.get("currentVersion")
    target = row.get("targetVersion")

    def version(value):
        match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
        return tuple(int(part or 0) for part in match.groups()) if match else None

    before, after = version(current), version(target)
    if row.get("status") != "update_available":
        reasons.append("No release upgrade recommendation")
    if row.get("releaseKind") != "stable":
        reasons.append("Selected release is not stable")
    if before is None or after is None:
        reasons.append("Version comparison is unknown")
    elif after <= before:
        reasons.append("Target is not newer than the installed version")
    if any(
        checks.get(name, {}).get("status") not in ("passed", "findings")
        for name in ("upgrade_status", "drupal_rector")
    ):
        reasons.append("Required compatibility scanner evidence is unavailable")
    if row.get("upgradeStatus", {}).get("issueCount") or row.get("rector", {}).get("fixableCount"):
        reasons.append("Code findings require individual review")
    # A missing decision alone is resolved by selecting the verified release.
    if any(
        message != "Select and validate one compatibility decision"
        for message in row.get("blockers", [])
    ):
        reasons.append("Extension has unresolved blockers")
    return {
        "safeUpgradeEligible": not reasons,
        "safeUpgradeVersion": target if not reasons else None,
        "safeUpgradeExclusionReasons": reasons,
    }


def _source(extension):
    path = str(extension.get("path", "")).replace("\\", "/")
    if re.search(r"(?:^|/)core/(?:modules|themes|profiles)/", path):
        return "core"
    if "/contrib/" in path or extension.get("package"):
        return "contrib"
    return "custom"


def semver_match(v1: str, v2: str) -> bool:
    """Compare two version strings taking SemVer tuples and dev aliases into account."""
    if not v1 or not v2:
        return False
    if v1 == v2:
        return True
    t1, t2 = version_tuple(v1), version_tuple(v2)
    if t1 is not None and t2 is not None and t1 == t2:
        return True
    c1 = str(v1).replace("dev-", "").replace(".x-dev", "").replace(".x", "").strip()
    c2 = str(v2).replace("dev-", "").replace(".x-dev", "").replace(".x", "").strip()
    return bool(c1 and c1 == c2)


def find_extension_for_file(file_path: str, extensions: list[dict] | None = None) -> str | None:
    """Find extension machine name for a file path using longest-prefix match or profile/module/theme regex."""
    norm_file = "/" + str(file_path).replace("\\", "/").strip("/")

    best_name = None
    best_len = -1
    if extensions:
        for ext in extensions:
            ext_path = ext.get("path")
            if not ext_path:
                continue
            norm_ext = "/" + str(ext_path).replace("\\", "/").strip("/")
            if norm_file == norm_ext or norm_file.startswith(norm_ext + "/"):
                if len(norm_ext) > best_len:
                    best_len = len(norm_ext)
                    best_name = ext.get("name")
    if best_name:
        return best_name

    m = re.search(r"/(?:modules|themes|profiles)/(?:contrib|custom)/([^/]+)", norm_file)
    return m.group(1) if m else None


def _scan_map(check, extensions=None):
    result = {}
    for issue in check.get("issues", []) if isinstance(check, dict) else []:
        path = str(issue.get("location", {}).get("path", "")).replace("\\", "/")
        key = find_extension_for_file(path, extensions)
        if key:
            result.setdefault(key, []).append(issue)
    return result


def _rector_map(check, extensions=None):
    result = {}
    for item in check.get("changes", []) if isinstance(check, dict) else []:
        path = str(item.get("file") or item.get("path") or "").replace("\\", "/")
        key = find_extension_for_file(path, extensions)
        if key:
            result.setdefault(key, []).append(item)
    return result


def _config_references(context, name, texts=None):
    root = context.get("roots", {}).get("config")
    if not root or not Path(root).is_dir():
        return []
    needle = re.compile(r"(^|[\s:\-])" + re.escape(name) + r"([\s,]|$)")
    if texts is not None:
        return [path for path, text in texts if needle.search(text)][:100]
    matches = []
    for path in Path(root).rglob("*.yml"):
        if path.name == "core.extension.yml":
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if needle.search(text):
            matches.append(str(path.relative_to(root)))
    return matches[:100]


def _safe_default(status, release_candidates, target, kind, fixes):
    """Return only decisions that require no risk acceptance or human inference."""
    if status == "ready":
        return {
            "action": "keep",
            "candidateId": None,
            "candidateVersion": target,
            "acceptRisk": False,
            "note": "Automatically selected from verified compatibility evidence",
            "autoSelected": True,
        }
    stable = next(
        (
            item
            for item in release_candidates
            if item.get("version") == target and item.get("stability") == "stable"
        ),
        None,
    )
    if status == "update_available" and stable:
        return {
            "action": "compatible_release",
            "candidateId": None,
            "candidateVersion": stable["version"],
            "acceptRisk": False,
            "note": "Automatically selected verified stable Drupal 11-compatible release",
            "autoSelected": True,
        }
    validated = [
        item
        for item in fixes
        if isinstance(item, dict) and item.get("validated") is True and item.get("proposalDigest")
    ]
    if kind == "custom" and fixes and len(validated) == len(fixes):
        values = {item["proposalDigest"] for item in validated}
        if len(values) == 1:
            return {
                "action": "manual_remediation",
                "candidateId": None,
                "candidateVersion": None,
                "acceptRisk": False,
                "note": "Automatically selected validated deterministic Rector change",
                "proposalDigest": values.pop(),
                "autoSelected": True,
            }
    return None


def build(context, tool_checks, solver, patches, out, decisions=None, provider=None):
    checks = {x.get("id"): x for x in tool_checks}
    upgrade = checks.get("upgrade_status", {})
    rector = checks.get("drupal_rector", {})
    extensions = context.get("extensions", [])
    scan = _scan_map(upgrade, extensions=extensions)
    rector_changes = _rector_map(rector, extensions=extensions)
    decisions = decisions or {}
    packages = {
        p.get("name"): p for p in context.get("composer", {}).get("packages", []) if p.get("name")
    }
    target_versions = solver.get("versions", {})
    patch_map = {p.get("package"): p for p in patches.get("packages", [])}
    ext_installed = {e.get("name"): bool(e.get("installed")) for e in extensions if e.get("name")}
    ext_map = {e.get("name"): e for e in extensions if e.get("name")}
    reverse = {e.get("name"): [] for e in extensions}
    config_texts = []
    config_root = context.get("roots", {}).get("config")
    if config_root and Path(config_root).is_dir():
        for path in Path(config_root).rglob("*.yml"):
            if path.name == "core.extension.yml":
                continue
            try:
                config_texts.append(
                    (str(path.relative_to(config_root)), path.read_text(errors="replace"))
                )
            except OSError:
                pass
    for owner in extensions:
        for dependency in owner.get("dependencies") or []:
            raw = str(dependency).split("(", 1)[0].strip()
            machine = (raw.split(":", 1)[1] if ":" in raw else raw).split("/")[-1]
            if machine in reverse:
                reverse[machine].append(owner.get("name"))
    rows = []
    excluded = []
    seen = set()
    us_argv = upgrade.get("command", {}).get("argv", []) if isinstance(upgrade, dict) else []
    ignore_contrib = "--ignore-contrib" in us_argv or context.get("scanMode") == "fast"

    for extension in extensions:
        if _source(extension) == "core" and extension.get("name") not in removed_core:
            continue
        identity = (extension.get("type"), extension.get("name"))
        if identity in seen:
            continue
        seen.add(identity)
        if fixture_extension(extension) and extension.get("installed") is not True:
            excluded.append(
                {"name": extension.get("name"), "reason": "Inactive test or development fixture"}
            )
            continue
        name = extension.get("name")
        package = extension.get("package")
        kind = _source(extension)
        scanned = not (kind == "contrib" and ignore_contrib)
        current = (
            packages.get(package, {}).get("version")
            or extension.get("currentVersion")
            or extension.get("version")
        )
        target = target_versions.get(package)
        issues = scan.get(name, [])
        fixes = rector_changes.get(name, [])
        patch_group = patch_map.get(package, {}) if package else {}
        candidates = patch_group.get("candidates", [])
        compatible_declared = is_d11_compatible(extension.get("coreConstraint"))
        release_candidates = []
        if target:
            release_candidates.append(
                {
                    "version": target,
                    "stability": "prerelease" if PRE_RELEASE.search(target) else "stable",
                    "source": "disposable_composer_solver",
                }
            )
        release_candidates.extend(patch_group.get("releaseCandidates", []))
        release_candidates = list(
            {
                item["version"]: item
                for item in reversed(release_candidates)
                if item.get("version")
            }.values()
        )
        release_candidates.sort(
            key=lambda item: (
                item.get("stability") == "stable",
                version_tuple(item.get("version")) or (-1, -1, -1),
            ),
            reverse=True,
        )
        if not target and release_candidates:
            target = release_candidates[0]["version"]
        release_kind = next(
            (item.get("stability") for item in release_candidates if item["version"] == target),
            None,
        )
        release_lookup = patch_group.get("releaseLookup", "ok")
        if release_lookup == "failed" and not target and not release_candidates and not issues and not fixes:
            status = "unknown"
            recommended = "defer"
            evidence = "Upstream Drupal 11 release lookup failed; deferred to prevent unsafe removal"
        elif kind == "core" and name in removed_core:
            is_active = bool(extension.get("installed") or extension.get("exported"))
            if is_active and name in REMOVED_CORE_TO_CONTRIB:
                package = REMOVED_CORE_TO_CONTRIB[name]
                status = "update_available"
                recommended = "compatible_release"
                evidence = f"Drupal 11 removed core extension provided by official contrib package {package}"
                if not release_candidates:
                    release_candidates.append(
                        {
                            "version": "^1.0",
                            "stability": "stable",
                            "source": "drupal_core_to_contrib_bridge",
                        }
                    )
                if not target:
                    target = "^1.0"
            else:
                status = "ready" if not is_active else "blocked"
                recommended = "remove" if status == "blocked" else "compatible_release"
                evidence = "Drupal 11 removed-core extension inventory"
        elif name in OBSOLETE_PERFORMANCE_MODULES and not (package and target and not PRE_RELEASE.search(target)):
            status = (
                "update_available" if extension.get("installed") or extension.get("exported") else "ready"
            )
            recommended = "remove"
            evidence = "Obsolete performance module superseded by native Drupal 11 core asset aggregation"
        elif kind == "core":
            status = "ready"
            recommended = "compatible_release"
            evidence = "Drupal core target resolution"
        elif (
            kind == "contrib"
            and extension.get("installed") is False
            and extension.get("exported") is False
        ):
            status = "ready"
            recommended = "remove"
            evidence = "Unused contrib extension; uninstalled and defaulted to remove as part of cleanup"
        elif (
            kind == "contrib"
            and extension.get("type") == "theme"
            and not (target and not PRE_RELEASE.search(target))
            and not any(rc.get("stability") == "stable" for rc in release_candidates)
        ):
            status = "ready" if not extension.get("installed") else "update_available"
            recommended = "remove"
            evidence = "Contrib theme has no stable Drupal 11 release; defaulted to remove/uninstall instead of patch"
        elif solver.get("status") == "passed" and package and target:
            status = "ready" if current == target and compatible_declared else "update_available"
            recommended = "compatible_release"
            evidence = "Disposable Composer Drupal 11 resolution"
        elif package and compatible_declared and not issues and not fixes:
            if scanned:
                status = "ready"
                recommended = "keep"
                evidence = "Installed extension is compatible with Drupal 11"
            else:
                status = "update_available" if target and target != current else "ready"
                recommended = "compatible_release" if target and target != current else "keep"
                evidence = "Contrib extension unscanned by fast audit; resolved by Composer"
        elif package and release_candidates:
            status = "update_available"
            recommended = "compatible_release"
            evidence = "Drupal.org release history with Drupal 11 core compatibility"
        elif issues or fixes:
            status = "patch_available" if candidates else "manual_remediation"
            recommended = "available_patch" if candidates else "ai_manual_patch"
            evidence = "Upgrade Status and Drupal Rector findings"
        elif candidates:
            status = "patch_available"
            recommended = "available_patch"
            evidence = "Drupal.org merge request candidate"
        elif (
            kind == "custom"
            and compatible_declared
            and upgrade.get("status") in ("passed", "findings")
            and not issues
            and not fixes
        ):
            status = "ready"
            recommended = "compatible_release"
            evidence = "Declared Drupal 11 support plus completed Upgrade Status scan"
        elif upgrade.get("status") not in ("passed", "findings"):
            status = "unknown"
            recommended = "defer"
            evidence = "Required compatibility scanner evidence is unavailable"
        else:
            status = "blocked"
            recommended = "ai_manual_patch"
            evidence = "No compatible release or verified patch was established"
        before, after = version_tuple(current), version_tuple(target)
        version_relation = (
            "unknown"
            if before is None or after is None
            else "newer"
            if after > before
            else "equal"
            if after == before
            else "older"
        )
        if package and target and not (kind == "core" and name in removed_core):
            if version_relation == "older":
                status = "blocked"
                recommended = "manual_remediation"
            elif version_relation == "equal":
                status = "ready" if not issues and not fixes else "unknown"
                recommended = "keep" if status == "ready" else "defer"
            elif version_relation == "unknown":
                status = "unknown"
                recommended = "defer"
        config_refs = (
            _config_references(context, name, config_texts)
            if extension.get("installed") is not False
            else []
        )
        module_impact = (
            checks.get("module_impact", {}).get("modules", {}).get(name, {})
            if checks.get("module_impact")
            else {}
        )
        if name in OBSOLETE_PERFORMANCE_MODULES:
            populated = False
        elif checks.get("module_impact", {}).get("status") in ("passed", "findings"):
            if name in checks.get("module_impact", {}).get("modules", {}):
                populated = module_impact.get("populatedContent")
            else:
                populated = False
        else:
            populated = module_impact.get("populatedContent")
        impact = {
            "enabled": extension.get("installed"),
            "exported": extension.get("exported"),
            "reverseDependencies": sorted(set(reverse.get(name, []))),
            "configurationReferences": config_refs,
            "fieldProviders": module_impact.get("fieldProviders", []),
            "populatedCounts": module_impact.get("populatedCounts", {}),
            "populatedContent": populated,
        }
        supplied = name in decisions
        is_automatic = (decisions.get(name, {}).get("origin") == "automatic") if supplied else True
        decision = dict(decisions.get(name, {}) or {})
        if not decision.get("action"):
            decision = _safe_default(status, release_candidates, target, kind, fixes) or {}
            is_automatic = True
        selected = decision.get("action")
        blockers = []
        if extension.get("installed") is None:
            blockers.append("Selected-site installation state is unknown")
        if version_relation == "older":
            blockers.append(
                "Resolved candidate is older than installed; downgrade requires a separate reviewed remediation plan"
            )
        if fixture_extension(extension):
            blockers.append("Active test or development extension requires review")
        if decision.get("requiresRevalidation"):
            blockers.append(
                "Retained operator choice: apply choices to rebuild and verify against this scan"
            )
        if not selected:
            blockers.append("Select and validate one compatibility decision")
        if (
            selected == "keep"
            and status != "ready"
            and not (compatible_declared and not issues and not fixes)
        ):
            blockers.append(
                "Keep is allowed only when completed evidence establishes Drupal 11 compatibility"
            )
        if selected == "compatible_release":
            chosen_version = decision.get("candidateVersion") or target
            chosen_release = next(
                (item for item in release_candidates if item["version"] == chosen_version), None
            )
            if not chosen_release:
                blockers.append("No Drupal 11-compatible release candidate is selected")
            if (
                chosen_release
                and chosen_release.get("stability") == "prerelease"
                and decision.get("acceptRisk") is not True
            ):
                blockers.append("Prerelease selection requires explicit risk acceptance")
        if selected == "available_patch":
            chosen = next(
                (c for c in candidates if c.get("id") == decision.get("candidateId")), None
            )
            if not chosen or not chosen.get("approvalEligible"):
                blockers.append(
                    "No selected applicability-tested, hash-pinned patch is approval eligible"
                )
        if selected == "ai_manual_patch" and not decision.get("proposalDigest"):
            blockers.append("AI patch has not been generated and validated")
        if selected == "manual_remediation" and not decision.get("proposalDigest"):
            blockers.append("Manual remediation requires an exact validated diff")
        if selected == "remove":
            if extension.get("type") == "theme" and impact["enabled"]:
                blockers.append(
                    "Enabled theme removal requires an exact replacement/default-theme plan"
                )
            blocking_dependents = [
                dep for dep in impact["reverseDependencies"]
                if ext_installed.get(dep) is True
                and (
                    decisions.get(dep, {}).get("action")
                    or (ext_map.get(dep, {}).get("recommendedAction") if not decisions.get(dep) else None)
                ) != "remove"
            ]
            if blocking_dependents:
                blockers.append("Other extensions depend on this extension")
            if (
                impact["configurationReferences"]
                and name not in OBSOLETE_PERFORMANCE_MODULES
                and name not in removed_core
            ):
                blockers.append(
                    "Configuration references require an exact reviewed deletion or migration plan"
                )
            if (
                impact["enabled"]
                and impact["populatedContent"] is not False
                and name not in OBSOLETE_PERFORMANCE_MODULES
            ):
                blockers.append("Populated-content impact is unknown or nonempty")
        if selected == "defer":
            blockers.append("Compatibility decision deferred")
        if selected != "remove" and upgrade.get("status") not in ("passed", "findings"):
            blockers.append("Required Upgrade Status evidence is unavailable")
        if selected == "remove" and checks.get("module_impact", {}).get("status") not in (
            "passed",
            "findings",
        ):
            blockers.append("Removal impact evidence is unavailable")
        chosen_patch = next(
            (c for c in candidates if c.get("id") == decision.get("candidateId")), None
        )
        patch_critical = chosen_patch and chosen_patch.get("riskClassification") == "critical"
        risk = (
            "critical"
            if blockers
            and (extension.get("installed") or selected in ("available_patch", "remove"))
            or patch_critical
            else "high"
            if blockers
            or selected in ("available_patch", "ai_manual_patch", "manual_remediation", "remove")
            or release_kind == "prerelease"
            else "medium"
            if status == "update_available"
            else "low"
        )
        rows.append(
            {
                "id": extension.get("type", "module") + ":" + name,
                "name": name,
                "label": name.replace("_", " ").title(),
                "type": extension.get("type"),
                "source": kind,
                "removedFromCore": kind == "core" and name in removed_core,
                "packageInstalled": bool(package and package in packages),
                "installed": True,
                "enabled": extension.get("installed"),
                "exported": extension.get("exported"),
                "package": package,
                "currentVersion": current,
                "coreConstraint": extension.get("coreConstraint"),
                "targetVersion": target,
                "releaseKind": release_kind,
                "releaseCandidates": release_candidates,
                "status": status,
                "evidenceSource": evidence,
                "scanned": scanned,
                "scanStatus": "scanned" if scanned else "not_scanned",
                "releaseLookup": release_lookup,
                "upgradeStatus": {
                    "status": upgrade.get("status", "unknown"),
                    "issueCount": len(issues),
                    "affectedFiles": sorted(
                        {
                            i.get("location", {}).get("path")
                            for i in issues
                            if i.get("location", {}).get("path")
                        }
                    ),
                    "issues": issues,
                },
                "rector": {
                    "status": rector.get("status", "unknown"),
                    "fixableCount": len(fixes),
                    "changes": fixes,
                },
                "manualFindings": [
                    issue
                    for issue in issues
                    if issue.get("location", {}).get("path")
                    not in {str(fix.get("file") or fix.get("path") or "") for fix in fixes}
                ],
                "patches": candidates,
                "impact": impact,
                "recommendedAction": recommended,
                "selectedAction": selected,
                "decision": decision or None,
                "autoSelected": bool(decision.get("action")) and is_automatic,
                "risk": risk,
                "blockers": blockers,
                "verificationChecks": ["composer_validate", "drupal_bootstrap", "gate_2_routes"],
            }
        )
    for row in rows:
        row["presentOnDisk"] = True
        row["installed"] = row["enabled"]
        row.update(safe_upgrade(row, checks))
        if (
            solver.get("status") != "passed"
            or row.get("package")
            and target_versions.get(row["package"]) != row.get("targetVersion")
        ):
            row.update(safeUpgradeEligible=False, safeUpgradeVersion=None)
            row["safeUpgradeExclusionReasons"].append(
                "Exact target has not passed joint Composer resolution"
            )
        verified = all(
            checks.get(key, {}).get("status") in ("passed", "findings")
            for key in ("upgrade_status", "drupal_rector")
        )
        code_clear = not row["upgradeStatus"]["issueCount"] and not row["rector"]["fixableCount"]
        resolved = (
            row.get("source") == "custom"
            or row.get("selectedAction") in ("keep", "remove", "available_patch", "manual_remediation", "ai_manual_patch")
            or target_versions.get(row.get("package")) == row.get("targetVersion")
            or (row.get("selectedAction") == "compatible_release" and row.get("targetVersion"))
        )
        automatic_valid = (
            verified
            and (code_clear if row.get("selectedAction") == "keep" else True)
            and resolved
            and not row["blockers"]
            and (
                (row["selectedAction"] == "keep" and row.get("status") == "ready")
                or (row["selectedAction"] == "compatible_release" and (row.get("targetVersion") or row.get("releaseCandidates") or row["safeUpgradeEligible"]))
                or row["selectedAction"] in ("remove", "available_patch", "manual_remediation", "ai_manual_patch")
            )
        )
        if not (row["name"] in decisions) and row.get("autoSelected") and not automatic_valid:
            row.update(autoSelected=False, selectedAction=None, decision=None)
            if "Select and validate one compatibility decision" not in row["blockers"]:
                row["blockers"].append("Select and validate one compatibility decision")
        elif (row["name"] in decisions) and row.get("autoSelected") and not automatic_valid:
            row["autoSelected"] = False
        if row.get("decision"):
            row["decision"]["origin"] = decisions.get(row["name"], {}).get("origin") or (
                "automatic" if row["autoSelected"] else "operator"
            )
            if row["selectedAction"] == "keep" and not code_clear:
                row["blockers"].append("Code findings require remediation before Keep")
        row["sharedBlockers"] = [
            message
            for message in row["blockers"]
            if message == "Required Upgrade Status evidence is unavailable"
        ]
        row["attentionBlockers"] = [
            message for message in row["blockers"] if message not in row["sharedBlockers"]
        ]
        row["reviewDisposition"] = (
            "automatically_planned"
            if row["autoSelected"]
            else "verification_blocked"
            if not verified
            and not row.get("selectedAction")
            and not row["upgradeStatus"]["issueCount"]
            and not row["rector"]["fixableCount"]
            else "needs_decision"
        )
        row["workCategory"] = (
            "Unknown or blocked"
            if row["blockers"] or not verified
            else "Already compatible"
            if row["selectedAction"] == "keep"
            else "Routine updates"
            if row["selectedAction"] == "compatible_release" and row["safeUpgradeEligible"]
            else "Remediation required"
        )
    shared = []
    for key, label in [("upgrade_status", "Upgrade Status"), ("drupal_rector", "Drupal Rector")]:
        if checks.get(key, {}).get("status") not in ("passed", "findings"):
            shared.append(
                {
                    "id": key,
                    "message": label + " evidence is incomplete",
                    "affectedCount": len(rows),
                }
            )
    if solver.get("status") != "passed":
        shared.append(
            {
                "id": "composer",
                "message": "Joint Composer resolution is incomplete",
                "affectedCount": len(rows),
            }
        )
    if any(row["installed"] is None for row in rows):
        shared.append(
            {
                "id": "installation_state",
                "message": "Active Drupal extension inventory is unavailable",
                "affectedCount": sum(row["installed"] is None for row in rows),
            }
        )
    present_only = [row for row in rows if row["installed"] is False]
    for row in present_only:
        row.update(
            selectedAction=None,
            decision=None,
            autoSelected=False,
            reviewDisposition="present_not_installed",
            safeUpgradeEligible=False,
            safeUpgradeVersion=None,
        )
        row["blockers"] = []
        row["workCategory"] = "Present but not installed"
    rows = [row for row in rows if row["installed"] is not False]
    counts = {
        key: sum(1 for row in rows if row["status"] == key)
        for key in (
            "ready",
            "update_available",
            "patch_available",
            "manual_remediation",
            "blocked",
            "unknown",
        )
    }
    effective = {
        row["name"]: {k: v for k, v in row["decision"].items() if k != "autoSelected"}
        for row in rows
        if row.get("decision")
    }
    action_counts = {
        name: sum(1 for row in rows if row.get("selectedAction") == name) for name in DECISIONS
    }
    result = {
        "schemaVersion": "1.1",
        "generatedAt": now(),
        "target": get_target_constraint(context.get("target") if context else None),
        "scanner": {
            k: {
                key: v.get(key)
                for key in ("status", "version", "findingCount", "failureCategory", "message")
            }
            for k, v in checks.items()
        },
        "provider": provider,
        "summary": {
            **counts,
            "total": len(rows),
            "unresolved": sum(1 for r in rows if r["blockers"]),
            "actions": action_counts,
        },
        "extensions": rows,
        "decisions": effective,
        "decisionDigest": digest(effective),
        "evidence": {
            "composerResolution": solver.get("status"),
            "composerResolutionHash": file_hash(Path(out) / "composer-resolution.json")
            if (Path(out) / "composer-resolution.json").is_file()
            else None,
            "patchDiscovery": patches.get("status"),
        },
    }
    result.update(schemaVersion="1.2", sharedBlockers=shared)
    result.update(inventoryVersion="2", presentNotInstalled=present_only, excludedFixtures=excluded)
    for blocker in shared:
        if blocker["id"] != "installation_state":
            blocker["affectedCount"] = len(rows)
    groups = {}
    for row in rows:
        if row["status"] == "update_available":
            groups.setdefault(row.get("package") or row["name"], []).append(row["name"])
    result["packageUpdates"] = [
        {"package": package, "installedExtensions": names}
        for package, names in sorted(groups.items())
    ]
    result["summary"].update(
        installed=sum(row["installed"] is True for row in rows),
        unknownInstallation=sum(row["installed"] is None for row in rows),
        presentNotInstalled=len(present_only),
        excludedFixtures=len(excluded),
        updatePackages=len(groups),
    )
    result["summary"]["workCategories"] = {
        category: sum(row["workCategory"] == category for row in rows)
        for category in (
            "Already compatible",
            "Routine updates",
            "Remediation required",
            "Unknown or blocked",
        )
    }
    result["summary"]["attentionRequired"] = sum(
        row["reviewDisposition"] == "needs_decision" for row in rows
    )
    result["digest"] = digest({k: v for k, v in result.items() if k != "digest"})
    write_compatibility_report(out, result)
    return result


def write_compatibility_report(out_dir: Path | str, report: dict, home: Path | None = None) -> Path:
    """Write compatibility-report.json and content-address canonical copy in workbench storage."""
    out_dir = Path(out_dir)
    target = out_dir / "compatibility-report.json"
    d = report.get("digest") or digest({k: v for k, v in report.items() if k != "digest"})
    report["digest"] = d
    home_dir = Path(home) if home else get_d11_home()
    store = home_dir / "store" / "compatibility"
    try:
        store.mkdir(parents=True, exist_ok=True)
        canonical = store / f"{d}.json"
        if not canonical.is_file():
            write(canonical, report)
        if target.exists():
            target.unlink()
        try:
            os.link(canonical, target)
        except OSError:
            write(target, report)
    except Exception:
        write(target, report)
    return target


def validate_decisions(report, decisions):
    if not isinstance(decisions, list):
        raise ValueError("Compatibility decisions must be a list")
    all_extensions = report.get("extensions", []) + report.get("presentNotInstalled", [])
    known = {
        row["name"]: row
        for row in all_extensions
        if row.get("source") != "core" or row.get("name") in removed_core
    }
    normalized = {}
    for item in decisions:
        if (
            not isinstance(item, dict)
            or item.get("name") not in known
            or item.get("action") not in DECISIONS
        ):
            raise ValueError("Unknown extension or compatibility action")
        if item["name"] in normalized:
            raise ValueError("Duplicate compatibility decision")
        if item.get("bulkSafe"):
            row = known[item["name"]]
            if (
                not row.get("safeUpgradeEligible")
                or item["action"] != "compatible_release"
                or item.get("candidateVersion") != row.get("safeUpgradeVersion")
            ):
                raise ValueError("Safe upgrade eligibility changed for " + item["name"])
        if item["action"] == "compatible_release" and item.get("candidateVersion"):
            available = {
                candidate.get("version")
                for candidate in known[item["name"]].get("releaseCandidates", [])
            }
            if available and item["candidateVersion"] not in available:
                cand_major = item["candidateVersion"].split(".")[0] if item["candidateVersion"] else ""
                norm_match = next(
                    (
                        v
                        for v in available
                        if semver_match(v, item["candidateVersion"])
                    ),
                    None,
                )
                if not norm_match and cand_major:
                    same_major = [v for v in available if v.split(".")[0] == cand_major]
                    if same_major:
                        norm_match = same_major[0]
                if norm_match:
                    item["candidateVersion"] = norm_match
                else:
                    raise ValueError(
                        f"Selected compatible release '{item['candidateVersion']}' was not present in the audited candidates"
                    )
        normalized[item["name"]] = {
            "action": item["action"],
            "candidateId": item.get("candidateId"),
            "candidateVersion": item.get("candidateVersion"),
            "acceptRisk": item.get("acceptRisk") is True,
            "note": str(item.get("note", "")).strip(),
            "origin": item.get("origin"),
        }
    for name, row in known.items():
        if name not in normalized and row.get("decision"):
            normalized[name] = dict(row["decision"])
    for name, item in normalized.items():
        previous = known[name].get("decision") or {}
        unchanged = all(
            item.get(key) == previous.get(key)
            for key in ("action", "candidateId", "candidateVersion", "acceptRisk", "note")
        )
        if not item.get("origin"):
            item["origin"] = previous.get("origin", "operator") if unchanged else "operator"
        if unchanged and previous.get("proposalDigest"):
            item["proposalDigest"] = previous["proposalDigest"]
    packages = {}
    for name, item in normalized.items():
        package = known[name].get("package")
        if package and item["action"] == "compatible_release":
            version = item.get("candidateVersion") or known[name].get("targetVersion")
            if package in packages and packages[package][1] != version:
                raise ValueError(
                    "Conflicting versions for "
                    + package
                    + ": "
                    + packages[package][0]
                    + " and "
                    + name
                )
            packages[package] = (name, version)
    return normalized
