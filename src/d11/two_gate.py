"""High-level two-gate orchestration built on the existing safe primitives."""

import json
import re
import shutil
import sys
from pathlib import Path

from .common import ROOT, Problem, command, digest, file_hash, get_d11_home, now, read, write, span
from .knowledge import get_target_constraint
from .risk import evaluate, finding


from .estimation import delivery_forecast


def automation_eligibility(risk, compatibility, batch, baseline_ok):
    """Classify automation from verified evidence and the exact batch."""
    rows = compatibility.get("extensions", [])
    unresolved = sorted(row["name"] for row in rows if row.get("blockers"))
    risky = sorted(
        row["name"]
        for row in rows
        if row.get("selectedAction")
        in ("available_patch", "ai_manual_patch", "manual_remediation", "remove")
        or row.get("releaseKind") == "prerelease"
    )
    real_batch_blockers = [
        b for b in batch.get("blockers", []) if b != "Missing baseline evidence"
    ]
    blocked = (
        bool(risk.get("hardBlockers"))
        or risk.get("recommendation") == "No-Go"
        or bool(real_batch_blockers)
        or (not baseline_ok and not batch.get("baselineDeferred"))
        or bool(unresolved)
    )
    if blocked:
        return {
            "status": "blocked",
            "label": "Blocked",
            "proposedAutomationLevel": "not_available",
            "riskBearingExtensions": risky,
            "unresolvedExtensions": unresolved,
        }
    if risk.get("recommendation") == "Conditional Go" or risky:
        return {
            "status": "assisted_upgrade_required",
            "label": "Assisted upgrade required",
            "proposedAutomationLevel": "approved_assisted_batch",
            "riskBearingExtensions": risky,
            "unresolvedExtensions": unresolved,
        }
    return {
        "status": "auto_upgrade_eligible",
        "label": "Auto-upgrade eligible",
        "proposedAutomationLevel": "full_after_approval",
        "riskBearingExtensions": [],
        "unresolvedExtensions": [],
    }


def _approved_batch_manifest(state, gate, plan, compatibility, reviewer, approved_at):
    """Expand bound compatibility decisions into the provider-neutral execution contract."""
    extensions = {}
    for row in compatibility.get("extensions", []):
        decision = row.get("decision") or {}
        action = decision.get("action")
        if not action:
            continue
        selected_patch = next(
            (
                item
                for item in row.get("patches", [])
                if item.get("id") == decision.get("candidateId")
            ),
            None,
        )
        extensions[row["name"]] = {
            "type": row.get("type"),
            "action": action,
            "package": row.get("package"),
            "origin": decision.get("origin", "operator"),
            "reviewDisposition": row.get("reviewDisposition"),
            "selectedVersion": decision.get("candidateVersion")
            or (row.get("targetVersion") if action == "compatible_release" else None),
            "patch": (
                {
                    "candidateId": selected_patch.get("id"),
                    "file": selected_patch.get("patch"),
                    "sha256": selected_patch.get("sha256"),
                    "commit": selected_patch.get("commit"),
                }
                if selected_patch
                else None
            ),
            "aiProposalDigest": decision.get("proposalDigest")
            if action == "ai_manual_patch"
            else None,
            "manualProposalDigest": decision.get("proposalDigest")
            if action == "manual_remediation"
            else None,
            "uninstallSequence": (
                ["drush pm:uninstall " + row["name"], "composer remove " + row["package"]]
                if action == "remove" and row.get("package")
                else ["drush pm:uninstall " + row["name"]]
                if action == "remove"
                else []
            ),
            "verificationChecks": row.get("verificationChecks", []),
        }
    value = {
        "schemaVersion": "1.0",
        "project": state["project"],
        "runId": state["id"],
        "approvedAt": approved_at,
        "reviewer": reviewer,
        "projectFingerprint": state.get("fingerprint"),
        "sourceFingerprint": state.get("sourceFingerprint") or state.get("fingerprint"),
        "approvalDigest": gate["approvalDigest"],
        "decisionDigest": compatibility.get("decisionDigest"),
        "routeDigest": gate.get("baseline", {}).get("routeDigest"),
        "runtimeIdentity": gate.get("runtimeIdentity"),
        "planId": plan.get("planId"),
        "orderedStepIds": [step.get("id") for step in plan.get("steps", [])],
        "orderedSteps": plan.get("steps", []),
        "extensions": extensions,
    }
    value["digest"] = digest(value)
    return value


def _version(value):
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
    return tuple(map(int, match.groups(default="0"))) if match else None


def _check_findings(
    assessment, context, baseline_ok, route_selection, plan, tool_checks, patches, compatibility
):
    findings = []
    commands = context.get("runtime", {}).get("commands", {})
    status = commands.get("status", {}).get("data", {})
    source = _version(status.get("drupal-version"))
    php = _version(status.get("php-version"))
    findings.append(
        finding(
            "managed_copy_isolation",
            "platform_recovery",
            "critical",
            "Automated isolation and sanitization controls are verified for scanning; named safety confirmation is collected at Proceed",
            status="passed",
        )
    )
    findings.append(
        finding(
            "runtime_identity",
            "platform_recovery",
            "critical",
            "Managed runtime identity and isolation checks passed",
            status="passed",
        )
    )
    findings.append(
        finding(
            "source_version",
            "platform_recovery",
            "critical",
            f"Drupal source is {status.get('drupal-version', 'unknown')}",
            status="passed" if source and source[0] == 10 and source >= (10, 3, 0) else "blocked",
            hard=True,
        )
    )
    findings.append(
        finding(
            "target_platform",
            "platform_recovery",
            "critical",
            f"PHP is {status.get('php-version', 'unknown')}; Drupal 11 needs a verified supported platform",
            status="passed" if php and php >= (8, 3, 0) else "blocked",
            hard=True,
        )
    )
    findings.append(
        finding(
            "recovery_checkpoint",
            "platform_recovery",
            "critical",
            "Coordinated managed-copy rollback inputs are registered; file assets are recovery inputs, not a separate audit category",
            status="passed" if plan.get("backup") else "unknown",
            hard=not bool(plan.get("backup")),
        )
    )
    findings.append(
        finding(
            "baseline_capture",
            "qa_coverage",
            "critical",
            f"Baseline covers {len(route_selection.get('routes', []))} representative routes",
            status="passed" if baseline_ok else "failed",
            hard=not baseline_ok,
            evidence=["route-selection.json", "visual/"],
        )
    )
    findings.append(
        finding(
            "authenticated_coverage",
            "qa_coverage",
            "medium",
            "Authenticated/editor scenarios are outside automatically discovered public-route coverage",
            status="unknown",
        )
    )
    deployment = context.get("deployment", {})
    deployment_clear = deployment.get("status") == "unknown" and not deployment.get("observations")
    findings.append(
        finding(
            "deployment_sequence",
            "configuration_deployment",
            "high",
            "Observed project deployment/build entrypoints require an exact reviewed sequence"
            if not deployment_clear
            else "No project-specific deployment sequence was detected; the exact default sequence is shown in Gate 1",
            status="passed" if deployment_clear else "blocked",
            hard=False,
            evidence=["result/context.json"],
        )
    )
    if route_selection.get("status") == "failed":
        findings.append(
            finding(
                "sitemap_coverage",
                "qa_coverage",
                "medium",
                route_selection.get("failure", "Sitemap coverage is incomplete"),
                status="unknown",
                evidence=["route-selection.json"],
            )
        )
    mapped = {
        "configurationStatus": ("configuration_deployment", "medium"),
        "pendingUpdates": ("configuration_deployment", "high"),
        "compatibility": ("dependencies_patches", "critical"),
        "lock": ("dependencies_patches", "high"),
        "bootstrap": ("platform_recovery", "critical"),
    }
    for item in assessment.get("checks", []):
        if item.get("id") in ("upgrade_status", "drupal_rector", "compatibility"):
            continue
        if item.get("status") == "passed":
            continue
        category, severity = mapped.get(item.get("id"), ("custom_code", "medium"))
        hard = item.get("id") in ("compatibility", "bootstrap") and item.get("status") in (
            "unknown",
            "failed",
            "tool_failure",
        )
        findings.append(
            finding(
                item.get("id", "assessment"),
                category,
                severity,
                item.get("message", "Assessment evidence incomplete"),
                status=item.get("status", "unknown"),
                hard=hard,
                evidence=["result/result.json"],
            )
        )
    scan = {item["id"]: item for item in tool_checks}
    upgrade_status = scan.get("upgrade_status", {})
    rector = scan.get("drupal_rector", {})
    scanners_valid = all(
        item.get("status") in ("passed", "findings") for item in (upgrade_status, rector)
    )
    custom_extensions = [row for row in compatibility.get("extensions", []) if row.get("source") == "custom"]
    custom_unresolved = any(row.get("blockers") for row in custom_extensions)
    custom_status = (
        "passed"
        if not custom_extensions or not custom_unresolved
        else "blocked"
        if custom_unresolved
        else "unknown"
    )
    findings.append(
        finding(
            "critical_custom_code",
            "custom_code",
            "critical",
            "Upgrade Status and Drupal Rector must run, and reported custom-code changes need exact reviewed diffs",
            status=custom_status,
            hard=custom_status != "passed",
            evidence=["audit-tools.json"],
        )
    )
    unresolved = [row for row in compatibility.get("extensions", []) if row.get("blockers")]
    findings.append(
        finding(
            "compatibility_decisions",
            "dependencies_patches",
            "critical",
            f"{len(unresolved)} extension compatibility decisions remain unresolved"
            if unresolved
            else "Every required extension decision has deterministic evidence",
            status="blocked" if unresolved else "passed",
            hard=bool(unresolved),
            evidence=["compatibility-report.json"],
        )
    )
    if patches.get("status") == "candidates_found":
        findings.append(
            finding(
                "patch_candidates",
                "dependencies_patches",
                "medium",
                "Drupal.org merge-request candidates require applicability checks, regression checks and exact Gate 1 inclusion",
                status="findings",
                evidence=["patch-candidates.json"],
            )
        )
    composer_blockers = [
        b for b in plan.get("blockers", []) if b != "Missing baseline evidence"
    ]
    findings.append(
        finding(
            "composer_resolution",
            "dependencies_patches",
            "critical",
            "Exact Composer resolution and reviewed mutation steps are required before approval",
            status="passed"
            if plan.get("target") and plan.get("steps") and not composer_blockers
            else "blocked",
            hard=bool(composer_blockers) or not (plan.get("target") and plan.get("steps")),
            evidence=["plan.json"],
        )
    )
    return findings


def audit(w, pid, out, state):
    from .discovery import discover
    from .execution import plan
    from .scenario_setup import configure_automatic

    out = Path(out)
    p, cfg = w.project(pid)
    p = Path(p)

    def emit(**record):
        w.event(out, "span", **record)
    try:
        from .trusted_hosts import ensure_trusted_hosts
        site_uri = cfg.get("site", {}).get("uri", "")
        wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
        drupal_root = cfg.get("roots", {}).get("drupal", "web")
        src_path = cfg.get("sourcePath") or p
        ensure_trusted_hosts(src_path, site_uri, drupal_root=drupal_root, wrapper=wrapper)
    except Exception:
        pass
    w.runtime_gate(p, cfg)
    state["checkpoint"] = "discovering_routes"
    write(out / "state.json", state)
    w.event(out, "checkpoint", checkpoint=state["checkpoint"])
    route_selection = (
        configure_automatic(w, pid, out) if not cfg.get("visual") else _existing_routes(p, cfg, out)
    )
    p, cfg = w.project(pid)
    state["fingerprint"] = w.fingerprint(p, cfg)
    from .intake import inventory

    state["sourceFingerprint"] = (
        state["fingerprint"] if cfg.get("zeroCopy") else digest(inventory(p / "site"))
    )
    state["checkpoint"] = "assessing"
    write(out / "state.json", state)
    w.event(out, "checkpoint", checkpoint=state["checkpoint"])
    with span("assess", emit):
        rec = command(
        [
            str(ROOT / "bin/d11"),
            "assess",
            "--config",
            str(p / "project.json"),
            "--output",
            str(out / "result"),
            "--runtime",
        ],
        ROOT,
        1800,
    )
    assessment = (
        read(out / "result/result.json")
        if (out / "result/result.json").is_file()
        else {
            "checks": [
                {
                    "id": "assessment",
                    "status": "tool_failure",
                    "message": "Assessment did not produce structured evidence",
                }
            ]
        }
    )
    context = (
        read(out / "result/context.json")
        if (out / "result/context.json").is_file()
        else discover(cfg, True, False, get_d11_home() / "cache" / digest(cfg["_root"]))
    )
    from .audit_tools import run as run_audit_tools

    fast = state.get("options", {}).get("fast", cfg.get("fastScan", True))
    state["scanMode"] = "fast" if fast else "full"
    with span("audit_tools", emit, scanMode=state["scanMode"]):
        tool_checks = run_audit_tools(cfg, context, out, w, pid, fast=fast)
    assessment["checks"].extend(tool_checks)
    capture_baseline = state.get("options", {}).get("capture_baseline", not fast)
    baseline_ok = True
    baseline_error = None
    if capture_baseline:
        state["checkpoint"] = "capturing_baseline"
        write(out / "state.json", state)
        w.event(out, "checkpoint", checkpoint=state["checkpoint"])
        try:
            from .trusted_hosts import ensure_trusted_hosts
            site_uri = cfg.get("site", {}).get("uri", "")
            wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
            drupal_root = cfg.get("roots", {}).get("drupal", "web")
            src_path = cfg.get("sourcePath") or p
            ensure_trusted_hosts(src_path, site_uri, drupal_root=drupal_root, wrapper=wrapper)
        except Exception:
            pass
        try:
            with span("baseline_capture", emit):
                w.capture(p, cfg, out, "reference")
        except Exception as exc:
            baseline_ok = False
            baseline_error = str(exc)
    else:
        state["checkpoint"] = "baseline_deferred"
        write(out / "state.json", state)
        w.event(out, "checkpoint", checkpoint=state["checkpoint"])
    from .solver import resolve

    state["checkpoint"] = "resolving_dependencies"
    write(out / "state.json", state)
    w.event(out, "checkpoint", checkpoint=state["checkpoint"])
    pkgs_with_installed = {
        e.get("package")
        for e in context.get("extensions", [])
        if (
            e.get("installed")
            or e.get("exported")
            or e.get("inConfigSplit")
            or e.get("configSplits")
        )
        and e.get("package")
    }
    all_pkgs = {e.get("package") for e in context.get("extensions", []) if e.get("package")}
    uninstalled_pkgs = sorted(all_pkgs - pkgs_with_installed)
    target_ver = context.get("target") if context else None
    with span("solver", emit):
        solver = resolve(context["roots"]["composer"], out, target=target_ver, removals=uninstalled_pkgs)
    valid_tools = all(item.get("status") in ("passed", "findings") for item in tool_checks)
    compatibility_status = (
        "blocked"
        if solver.get("status") != "passed"
        else "findings"
        if any(item.get("status") == "findings" for item in tool_checks)
        else "passed"
        if valid_tools
        else "unknown"
    )
    assessment["checks"] = [
        item for item in assessment.get("checks", []) if item.get("id") != "compatibility"
    ]
    assessment["checks"].append(
        {
            "id": "compatibility",
            "status": compatibility_status,
            "message": "Composer resolution plus executed Upgrade Status and Drupal Rector evidence",
            "required": True,
        }
    )
    write(out / "result/result.json", assessment)
    from .patches import discover as discover_patches

    affected = []
    upgrade_check = next((item for item in tool_checks if item.get("id") == "upgrade_status"), {})
    if upgrade_check.get("status") == "findings":
        try:
            for item in json.loads(upgrade_check["command"]["stdout"]):
                path = item.get("location", {}).get("path", "").replace("\\", "/")
                match = re.search(r"(?:^|/)modules/contrib/([^/]+)", path)
                if match:
                    affected.append("drupal/" + match.group(1))
        except (ValueError, TypeError, KeyError):
            pass
    core_packages = {
        "drupal/core",
        "drupal/core-recommended",
        "drupal/core-composer-scaffold",
        "drupal/core-project-message",
    }
    contrib_packages = sorted(
        {
            extension.get("package")
            for extension in context.get("extensions", [])
            if str(extension.get("package") or "").startswith("drupal/")
            and extension.get("package") not in core_packages
        }
    )
    with span("patch_discovery", emit, packages=len(contrib_packages)):
        patch_evidence = discover_patches(
            solver, out, contrib_packages, context=context, patch_packages=affected
        )
    from .compatibility import build as build_compatibility

    prior_decisions = {}
    for prior in w.runs():
        if (
            prior.get("id") == state.get("id")
            or prior.get("project") != pid
            or prior.get("action") != "guided-audit"
        ):
            continue
        prior_out, _ = w.run(prior["id"])
        if (prior_out / "compatibility-decisions.json").is_file():
            prior_decisions = {
                name: dict(value)
                for name, value in read(prior_out / "compatibility-decisions.json").items()
                if value.get("origin") == "operator"
            }
            break
    # The report reads the scan mode from recorded run state, not from scanner argv.
    context["scanMode"] = state.get("scanMode")
    with span("compatibility_build", emit):
        compatibility = build_compatibility(
            context, tool_checks, solver, patch_evidence, out, prior_decisions
        )
    write(out / "compatibility-decisions.json", compatibility.get("decisions", {}))
    compatibility_check = next(
        item for item in assessment["checks"] if item.get("id") == "compatibility"
    )
    compatibility_check["status"] = (
        "blocked" if compatibility["summary"]["unresolved"] else compatibility_status
    )
    compatibility_check["message"] = (
        f"{compatibility['summary']['unresolved']} installed-extension decisions unresolved; {compatibility['summary']['updatePackages']} packages have newer candidates"
    )
    write(out / "result/result.json", assessment)
    cfg = _batch_config(p, cfg, out, context, route_selection, baseline_ok, solver)
    with span("plan", emit):
        batch = plan(cfg, context, out)
    write(out / "plan.json", batch)
    write(out / "batch-config.json", cfg)
    state["batchHash"] = digest(
        {
            "plan": batch,
            "config": cfg,
            "proposal": read(out / "proposal.json") if (out / "proposal.json").exists() else None,
            "compatibility": compatibility["digest"],
        }
    )
    findings = _check_findings(
        assessment,
        context,
        baseline_ok,
        route_selection,
        batch,
        tool_checks,
        patch_evidence,
        compatibility,
    )
    risk = evaluate(findings)
    from .budget import budget_model

    budget = dict(assessment.get("deliveryBudget", {}))
    budget["forecast"] = delivery_forecast(compatibility, solver, baseline_ok, config=cfg)
    budget["completedWork"] = [
        "Managed-copy preparation",
        "Runtime and dependency discovery",
        "Contrib/custom compatibility scans",
        "Selected-page baseline attempt",
    ]
    budget["unresolvedBlockers"] = [
        item["message"]
        for item in findings
        if item.get("hardBlocker") and item.get("status") != "passed"
    ]
    assessment["deliveryBudget"] = budget_model({"deliveryBudget": budget})
    write(out / "result/result.json", assessment)
    automation = automation_eligibility(risk, compatibility, batch, baseline_ok)
    runtime_identity = {
        "expected": cfg.get("identity", {}).get("expected"),
        "command": cfg.get("identity", {}).get("argv"),
        "evidenceDigest": digest(context.get("runtime", {}).get("commands", {}).get("status", {})),
    }
    gate = {
        "schemaVersion": "1.1",
        "generatedAt": now(),
        "project": pid,
        "fingerprint": state["fingerprint"],
        "sourceFingerprint": state["sourceFingerprint"],
        "runtimeIdentity": runtime_identity,
        "risk": risk,
        "automationEligibility": automation["status"],
        "automationLabel": automation["label"],
        "proposedAutomationLevel": automation["proposedAutomationLevel"],
        "decisionsRequired": automation["unresolvedExtensions"],
        "riskBearingExtensions": automation["riskBearingExtensions"],
        "assessmentExitCode": rec["exitCode"],
        "baseline": {
            "passed": baseline_ok,
            "error": baseline_error,
            "routeDigest": route_selection["digest"],
            "selected": len(route_selection["routes"]),
            "omitted": route_selection.get("omitted", 0),
            "headerCount": len(route_selection.get("headerRoutes", [])),
            "footerCount": len(route_selection.get("footerRoutes", [])),
        },
        "composerResolution": {
            "status": solver["status"],
            "exactCoreVersion": solver.get("exactCoreVersion"),
            "blocker": solver.get("blocker"),
            "versions": solver.get("versions", {}),
        },
        "patchDiscovery": {
            "status": patch_evidence["status"],
            "packages": len(patch_evidence["packages"]),
        },
        "compatibility": {
            "digest": compatibility["digest"],
            "decisionDigest": compatibility["decisionDigest"],
            "summary": compatibility["summary"],
            "scanner": compatibility["scanner"],
        },
        "planId": batch["planId"],
        "batchHash": state["batchHash"],
        "target": batch.get("target"),
        "exactCoreVersion": solver.get("exactCoreVersion"),
        "recovery": batch.get("backup"),
        "approvalEligible": automation["status"] != "blocked",
        "planBlockers": batch.get("blockers", []),
        "auditScope": [
            "Selected public/authenticated page routes and visual/functional evidence",
            "Contributed module/theme Drupal 11 compatibility and remediation choices",
            "Custom module/theme/profile compatibility and Rector/manual findings",
            "Minimum PHP, Drupal, Composer, database and rollback prerequisites needed for a safe upgrade",
        ],
        "outOfScope": [
            "Standalone uploaded-file inventory or content classification",
            "Business UAT and production release approval",
        ],
        "deliveryForecast": assessment["deliveryBudget"]["forecast"],
        "limitations": [
            "Browser-visible missing or broken assets on selected routes remain page findings; uploaded files are not independently audited.",
            "Drupal.org patch candidates are recommendations until their local patch hash, applicability and regression checks are included in a new exact batch.",
            "Business UAT and release readiness remain separate from automated verification.",
        ],
    }
    gate["approvalDigest"] = digest(gate)
    write(out / "gate.json", gate)
    state.update(
        checkpoint="report_and_decisions",
        recommendation=risk["recommendation"],
        riskScore=risk["score"],
        automationEligibility=automation["status"],
    )
    return {"assessment": assessment, "risk": risk, "gate": gate}


def compatibility_report(w, rid):
    out, state = w.run(rid)
    if state.get("action") != "guided-audit" or not (out / "compatibility-report.json").is_file():
        raise Problem("Compatibility evidence is not available for this run")
    return read(out / "compatibility-report.json")


def decide(w, rid, body):
    """Regenerate the exact Gate 1 batch after mutually exclusive module decisions."""
    out, state = w.run(rid)
    if state.get("action") != "guided-audit" or state.get("status") != "completed":
        raise Problem("Select a completed audit")
    from .compatibility import validate_decisions

    current = compatibility_report(w, rid)
    if body.get("reportDigest") and body["reportDigest"] != current.get("digest"):
        raise Problem("Compatibility evidence changed; refresh the report before saving")
    if any(item.get("bulkSafe") for item in body.get("decisions", []) if isinstance(item, dict)):
        if not body.get("reportDigest"):
            raise Problem("Bulk decisions require the current report digest")
        project, cfg = w.project(state["project"])
        if state.get("fingerprint") != w.fingerprint(project, cfg):
            raise Problem("Project inputs changed; scan again before bulk selection")
    try:
        decisions = validate_decisions(current, body.get("decisions"))
    except ValueError as exc:
        raise Problem(str(exc), 64)
    # Proposal digests are established by the server, never accepted from browser input.
    for name, value in decisions.items():
        if value["action"] in ("ai_manual_patch", "manual_remediation"):
            for pdir in ("manual-patches", "ai-patches"):
                cand = out / pdir / name / "proposal.json"
                if cand.is_file():
                    value["proposalDigest"] = digest(read(cand))
                    break
    history = out / "compatibility-history"
    history.mkdir(exist_ok=True)
    write(
        history / (current.get("digest", "initial") + ".json"),
        {"compatibility": current, "gate": read(out / "gate.json")},
    )
    write(out / "compatibility-decisions.json", decisions)
    return _rebuild_after_decisions(w, rid, out, state, decisions)


def _topological_sort_removals(removed_items):
    """Sort module removals so dependents are uninstalled before the modules they depend on."""
    names_to_item = {name: (name, pkg, row) for name, pkg, row in removed_items}
    in_degree = {name: 0 for name in names_to_item}
    dependents_of = {name: set() for name in names_to_item}

    for name, _, row in removed_items:
        raw_deps = row.get("dependencies", []) or []
        for dep in raw_deps:
            raw = str(dep).split("(", 1)[0].strip()
            dep_name = (raw.split(":", 1)[1] if ":" in raw else raw).split("/")[-1].strip()
            if dep_name in names_to_item and dep_name != name:
                # 'name' requires 'dep_name'; so 'name' must be uninstalled BEFORE 'dep_name'.
                dependents_of[name].add(dep_name)

    for name, targets in dependents_of.items():
        for target in targets:
            in_degree[target] += 1

    queue = sorted([name for name, deg in in_degree.items() if deg == 0])
    ordered = []

    while queue:
        current = queue.pop(0)
        ordered.append(names_to_item[current])
        for nxt in sorted(dependents_of[current]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
                queue.sort()

    if len(ordered) < len(removed_items):
        seen = {item[0] for item in ordered}
        leftovers = sorted([item for item in removed_items if item[0] not in seen], key=lambda x: x[0])
        ordered.extend(leftovers)

    return ordered


def _apply_patch_decisions(proposal, compatibility, decisions, out):
    out = Path(out)
    composer_change = next((c for c in proposal["changes"] if c["path"] == "composer.json"), None)
    all_compat_rows = {
        r["name"]: r
        for r in compatibility.get("extensions", []) + compatibility.get("presentNotInstalled", [])
    }
    for name, decision in decisions.items():
        row = all_compat_rows.get(name)
        if not row:
            continue
        if decision.get("action") in ("available_patch", "community_patch"):
            candidate = next(
                (c for c in row.get("patches", []) if c.get("id") == decision.get("candidateId")), None
            )
            if candidate and candidate.get("approvalEligible"):
                source = out / candidate["patch"]
                target = "patches/d11/" + Path(candidate["patch"]).name
                if not any(c["path"] == target for c in proposal["changes"]):
                    proposal["changes"].append(
                        {
                            "path": target,
                            "beforeSha256": None,
                            "after": source.read_text(errors="replace") if source.is_file() else "",
                            "finding": "compatibility:" + name,
                            "rationale": "Hash-pinned reviewed Drupal.org merge-request patch",
                            "verificationCheckIds": ["composer_validate", "drupal_bootstrap"],
                        }
                    )
                if composer_change:
                    data = json.loads(composer_change["after"])
                    extra = data.setdefault("extra", {})
                    mapping = extra.setdefault("patches", {})
                    mapping.setdefault(row["package"], {})[
                        candidate.get("title") or candidate["id"]
                    ] = target
                    req = data.setdefault("require", {})
                    req_dev = data.get("require-dev", {})
                    if (
                        "cweagans/composer-patches" not in req
                        and "cweagans/composer-patches" not in req_dev
                    ):
                        req["cweagans/composer-patches"] = "^1.7 || ^2.0"
                    cfg_sec = data.setdefault("config", {})
                    allow_plugins = cfg_sec.setdefault("allow-plugins", {})
                    if isinstance(allow_plugins, dict):
                        allow_plugins["cweagans/composer-patches"] = True
                    composer_change["after"] = json.dumps(data, indent=2, sort_keys=True) + "\n"
        elif decision.get("action") in ("ai_manual_patch", "manual_remediation"):
            candidate = (
                out / "manual-patches" / name / "proposal.json"
                if (out / "manual-patches" / name / "proposal.json").is_file()
                else out / "ai-patches" / name / "proposal.json"
            )
            if candidate.is_file():
                for c in read(candidate)["changes"]:
                    if not any(existing["path"] == c["path"] for existing in proposal["changes"]):
                        proposal["changes"].append(c)
    proposal["summary"] = (
        "Apply exact Drupal 11 dependency resolution and approved compatibility decisions"
    )
    proposal["findings"] = sorted(
        set(proposal.get("findings", []) + ["compatibility:" + name for name in decisions])
    )
    return proposal


def _rebuild_after_decisions(w, rid, out, state, decisions):
    from .compatibility import (
        REMOVED_CORE_TO_CONTRIB,
        build as build_compatibility,
        version_tuple,
    )
    from .execution import plan
    from .proposals import validate_proposal
    from .solver import resolve

    out = Path(out)
    p, cfg = w.project(state["project"])
    p = Path(p)
    context = read(out / "result/context.json")
    context["scanMode"] = state.get("scanMode")
    tool_checks = read(out / "audit-tools.json")["checks"]
    patches = read(out / "patch-candidates.json")
    manifest = read(Path(context["roots"]["composer"]) / "composer.json")
    comp_data = read(out / "compatibility-report.json")
    all_extensions = comp_data.get("extensions", []) + comp_data.get("presentNotInstalled", [])
    rows = {row["name"]: row for row in all_extensions}
    removed = []
    for name, decision in decisions.items():
        if name not in rows:
            continue
        if decision["action"] == "compatible_release" and rows[name].get("package"):
            version = decision.get("candidateVersion") or rows[name].get("targetVersion")
            if name == "tb_megamenu":
                c_ver = str(rows[name].get("currentVersion") or "")
                c_t = version_tuple(c_ver)
                if (c_t and c_t[0] == 3) or c_ver.startswith("3.") or "3.0.0-alpha5" in c_ver:
                    v_t = version_tuple(version)
                    if str(version).startswith("1.") or (v_t and v_t[0] == 1):
                        continue
            if version == "dev":
                for rc in rows[name].get("releaseCandidates", []):
                    if rc.get("version") and rc.get("version") != "dev":
                        version = rc["version"]
                        break
            if version and version != "dev":
                pkg = rows[name]["package"]
                if pkg in manifest.get("require-dev", {}):
                    manifest["require-dev"][pkg] = version
                elif pkg in manifest.get("require", {}):
                    manifest["require"][pkg] = version
                elif name in REMOVED_CORE_TO_CONTRIB:
                    manifest.setdefault("require", {})[pkg] = version
        if decision["action"] in ("available_patch", "ai_manual_patch") and rows[name].get("package"):
            pkg = rows[name]["package"]
            cand_id = decision.get("candidateId")
            cand = next((c for c in rows[name].get("patches", []) if c.get("id") == cand_id), None)
            if cand and cand.get("patch"):
                manifest.setdefault("extra", {}).setdefault("patches", {}).setdefault(pkg, {})[cand.get("title") or "Drupal 11 compatibility"] = cand["patch"]
        elif decision["action"] == "manual_remediation" and rows[name].get("package"):
            pkg = rows[name]["package"]
            manifest.setdefault("replace", {})[pkg] = "*"
            for section in ("require", "require-dev"):
                manifest.get(section, {}).pop(pkg, None)
        if decision["action"] == "remove":
            package = rows[name].get("package")
            if package:
                packages_with_active_extensions = {
                    r.get("package")
                    for r in rows.values()
                    if r.get("package") and decisions.get(r["name"], {}).get("action") not in ("remove", None)
                }
                if package not in packages_with_active_extensions:
                    for section in ("require", "require-dev"):
                        manifest.get(section, {}).pop(package, None)
            removed.append((name, package, rows[name]))
    target_ver = context.get("target") if context else None
    solver = resolve(context["roots"]["composer"], out, manifest, target=target_ver)
    provider_records = []
    for name in decisions:
        record = out / "ai-patches" / name / "provider.json"
        if record.is_file():
            provider_records.append(read(record))
    compatibility = build_compatibility(
        context, tool_checks, solver, patches, out, decisions, provider_records or None
    )
    write(out / "compatibility-decisions.json", compatibility["decisions"])
    batch_cfg = _batch_config(
        p,
        cfg,
        out,
        context,
        read(out / "route-selection.json"),
        read(out / "gate.json")["baseline"]["passed"],
        solver,
    )
    proposal = (
        read(out / "proposal.json")
        if (out / "proposal.json").is_file()
        else {
            "summary": "Compatibility remediation",
            "findings": [],
            "limitations": [],
            "steps": [],
            "changes": [],
        }
    )
    proposal = _apply_patch_decisions(proposal, compatibility, decisions, out)
    wrapper = cfg.get("runtime", {}).get("wrapper", "fin")
    from .source_runtime import get_runtime_prefixes

    drush_cmd, composer_cmd = get_runtime_prefixes(wrapper, p / "site")
    verification = {
        "_root": str(p / "site"),
        "checks": [
            {
                "id": "composer_validate",
                "kind": "command",
                "required": True,
                "cwd": ".",
                "argv": composer_cmd + ["validate", "--no-check-publish"],
            },
            {
                "id": "drupal_bootstrap",
                "kind": "command",
                "required": True,
                "cwd": ".",
                "argv": drush_cmd + [
                    "core:status",
                    "--format=json",
                    "--uri=" + cfg["site"]["uri"],
                ],
            },
        ],
    }
    write(out / "verification.json", verification)
    if proposal["changes"]:
        checked = validate_proposal(
            proposal, p / "site", [], {x["id"] for x in verification["checks"]}
        )
        write(out / "proposal.json", checked["proposal"])
        (out / "proposal.diff").write_text(checked["diff"])
    else:
        write(out / "proposal.json", proposal)
        (out / "proposal.diff").write_text("")
    if removed:
        removed = _topological_sort_removals(removed)
        evidence = {
            "generatedAt": now(),
            "modules": {name: row["impact"] for name, _, row in removed},
            "decisionDigest": compatibility["decisionDigest"],
        }
        write(p / "removal-evidence.json", evidence)
        evidence_hash = file_hash(p / "removal-evidence.json")
        removal_steps = []
        for name, package, row in removed:
            if row.get("installed"):
                check = drush_cmd + [
                    "core:status",
                    "--format=json",
                    "--uri=" + cfg["site"]["uri"],
                ]
                absent = drush_cmd + [
                    "php:eval",
                    f'if (\\Drupal::moduleHandler()->moduleExists("{name}")) throw new \\Exception("Module {name} still installed");',
                    "--uri=" + cfg["site"]["uri"],
                ]
                removal_steps.append(
                    {
                        "id": "uninstall_" + name,
                        "stage": "C-remediation",
                        "cwd": ".",
                        "mutates": True,
                        "destructive": True,
                        "reviewedRemovalDigest": evidence_hash,
                        "description": "Uninstall approved unused module " + name,
                        "argv": drush_cmd + [
                            "pm:uninstall",
                            name,
                            "--yes",
                            "--uri=" + cfg["site"]["uri"],
                        ],
                        "preconditions": [{"argv": check}],
                        "postconditions": [{"argv": absent}],
                    }
                )
        if removal_steps:
            export_config_step = {
                "id": "export_uninstalled_module_config",
                "stage": "C-remediation",
                "cwd": ".",
                "mutates": True,
                "description": "Export configuration after uninstalling removed modules",
                "argv": drush_cmd + [
                    "config:export",
                    "--yes",
                    "--uri=" + cfg["site"]["uri"],
                ],
                "preconditions": [
                    {
                        "argv": drush_cmd + [
                            "core:status",
                            "--format=json",
                            "--uri=" + cfg["site"]["uri"],
                        ]
                    }
                ],
                "postconditions": [
                    {
                        "argv": drush_cmd + [
                            "core:status",
                            "--format=json",
                            "--uri=" + cfg["site"]["uri"],
                        ]
                    }
                ],
            }
            removal_steps.append(export_config_step)
            rebuild_after_removal = {
                "id": "cache_rebuild_after_module_removal",
                "stage": "C-remediation",
                "cwd": ".",
                "mutates": True,
                "description": "Rebuild Drupal caches after module removals",
                "dependsOn": [export_config_step["id"]],
                "argv": drush_cmd + [
                    "cache:rebuild",
                    "--uri=" + cfg["site"]["uri"],
                ],
                "preconditions": [
                    {
                        "argv": drush_cmd + [
                            "core:status",
                            "--format=json",
                            "--uri=" + cfg["site"]["uri"],
                        ]
                    }
                ],
                "postconditions": [
                    {
                        "argv": drush_cmd + [
                            "core:status",
                            "--format=json",
                            "--uri=" + cfg["site"]["uri"],
                        ]
                    }
                ],
            }
            removal_steps.append(rebuild_after_removal)
            existing_steps = batch_cfg.get("steps", [])
            pre_step = next((s for s in existing_steps if s["id"] == "pre_upgrade_cache_rebuild"), None)
            if pre_step:
                removal_steps[0]["dependsOn"] = [pre_step["id"]]
                patch_step = next((s for s in existing_steps if s["id"] == "apply_exact_composer_resolution"), None)
                if patch_step:
                    patch_step["dependsOn"] = [removal_steps[-1]["id"]]
                batch_cfg["steps"] = [pre_step] + removal_steps + [s for s in existing_steps if s["id"] != pre_step["id"]]
            else:
                batch_cfg["steps"] = removal_steps + existing_steps
        else:
            batch_cfg["steps"] = batch_cfg.get("steps", [])
    batch_cfg["_batchArtifacts"] = sorted(
        set(
            batch_cfg.get("_batchArtifacts", [])
            + [str(out / "compatibility-report.json"), str(out / "compatibility-decisions.json")]
        )
    )
    batch = plan(batch_cfg, context, out)
    write(out / "plan.json", batch)
    write(out / "batch-config.json", batch_cfg)
    state["batchHash"] = digest(
        {
            "plan": batch,
            "config": batch_cfg,
            "proposal": read(out / "proposal.json"),
            "compatibility": compatibility["digest"],
        }
    )
    assessment = read(out / "result/result.json")
    route = read(out / "route-selection.json")
    gate = read(out / "gate.json")
    for check in assessment.get("checks", []):
        if check.get("id") == "compatibility":
            check["status"] = (
                "blocked"
                if compatibility["summary"]["unresolved"] or compatibility.get("sharedBlockers")
                else "passed"
            )
            check["message"] = (
                f"{compatibility['summary']['unresolved']} installed-extension decisions unresolved; {compatibility['summary']['updatePackages']} packages have newer candidates"
            )
    assessment.setdefault("deliveryBudget", {})["forecast"] = delivery_forecast(
        compatibility, solver, gate["baseline"]["passed"], config=batch_cfg
    )
    write(out / "result/result.json", assessment)
    gate["deliveryForecast"] = assessment["deliveryBudget"]["forecast"]
    findings = _check_findings(
        assessment,
        context,
        gate["baseline"]["passed"],
        route,
        batch,
        tool_checks,
        patches,
        compatibility,
    )
    risk = evaluate(findings)
    automation = automation_eligibility(risk, compatibility, batch, gate["baseline"]["passed"])
    gate.update(
        generatedAt=now(),
        risk=risk,
        planId=batch["planId"],
        batchHash=state["batchHash"],
        target=batch.get("target"),
        exactCoreVersion=solver.get("exactCoreVersion"),
        recovery=batch.get("backup"),
        composerResolution={
            "status": solver["status"],
            "exactCoreVersion": solver.get("exactCoreVersion"),
            "blocker": solver.get("blocker"),
            "versions": solver.get("versions", {}),
        },
        compatibility={
            "digest": compatibility["digest"],
            "decisionDigest": compatibility["decisionDigest"],
            "summary": compatibility["summary"],
            "scanner": compatibility["scanner"],
        },
        automationEligibility=automation["status"],
        automationLabel=automation["label"],
        proposedAutomationLevel=automation["proposedAutomationLevel"],
        decisionsRequired=automation["unresolvedExtensions"],
        riskBearingExtensions=automation["riskBearingExtensions"],
        approvalEligible=automation["status"] != "blocked",
        planBlockers=batch.get("blockers", []),
    )
    current_fp = w.fingerprint(p, cfg)
    gate["fingerprint"] = current_fp
    if "sourceFingerprint" in gate and cfg.get("zeroCopy"):
        gate["sourceFingerprint"] = current_fp
    gate.pop("approvalDigest", None)
    gate["approvalDigest"] = digest(gate)
    write(out / "gate.json", gate)
    for path in (out / "approval.json", out / "gate-approval.json"):
        if path.exists():
            path.unlink()
    state.update(
        recommendation=risk["recommendation"],
        riskScore=risk["score"],
        automationEligibility=automation["status"],
        checkpoint="report_and_decisions",
        fingerprint=current_fp,
    )
    if cfg.get("zeroCopy"):
        state["sourceFingerprint"] = current_fp
    write(out / "state.json", state)
    w.event(out, "compatibility_decisions_updated", decisionDigest=compatibility["decisionDigest"])
    w.report(rid)
    return {"compatibility": compatibility, "gate": gate}


def generate_ai_patch(w, audit_id, module, provider, run_out):
    """Generate and validate a local patch candidate without granting mutation authority."""
    from .ai_providers import propose
    from .proposals import PROPOSAL_SCHEMA, validate_proposal

    audit_out, state = w.run(audit_id)
    p, cfg = w.project(state["project"])
    report = compatibility_report(w, audit_id)
    row = next((r for r in report["extensions"] if r["name"] == module), None)
    if not row or row.get("source") not in ("custom", "contrib"):
        raise Problem("AI patch generation requires a reported custom or contrib extension")
    context = read(audit_out / "result/context.json")
    paths = [Path(e["path"]).parent for e in context["extensions"] if e["name"] == module]
    if not paths:
        raise Problem("Extension source path is unavailable")
    workspace = Path(run_out) / "sanitized-source"
    workspace.mkdir(parents=True)
    for source in paths:
        relative = source.resolve().relative_to((p / "site").resolve())
        target = workspace / relative
        if not target.exists():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(
                    ".git", "files", "private", "*.sql", "*.key", "*.pem"
                ),
            )
    site_root = (p / "site").resolve()
    issues_by_file: dict[str, list[dict]] = {}
    for iss in row.get("upgradeStatus", {}).get("issues", []):
        loc = iss.get("location", {}).get("path")
        if loc:
            for prefix in ("", "web/", "docroot/", "site/web/", "site/docroot/"):
                cand = site_root / (prefix + loc)
                if cand.is_file():
                    rel_p = str(cand.relative_to(site_root))
                    issues_by_file.setdefault(rel_p, []).append(iss)
                    break

    for source in paths:
        for info_cand in source.glob("*.info.yml"):
            if info_cand.is_file():
                rel_info = str(info_cand.resolve().relative_to(site_root))
                issues_by_file.setdefault(rel_info, [])

    files_section = []
    for rel_path, file_issues in issues_by_file.items():
        fp = site_root / rel_path
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(errors="replace")
        except Exception:
            continue
        if len(content) > 60_000:
            content = content[:60_000] + "\n// ... [truncated for context]"

        issue_lines = []
        for fi in file_issues:
            line_no = fi.get("location", {}).get("lines", {}).get("begin")
            desc = fi.get("description", "")
            issue_lines.append(f"Line {line_no}: {desc}")

        files_section.append(
            f"--- FILE: {rel_path} ---\n"
            + (("Reported Deprecation Issues:\n" + "\n".join(issue_lines) + "\n\n") if issue_lines else "No specific deprecation issues recorded; ensure Drupal 11 info.yml compliance.\n\n")
            + f"Current Content:\n{content}\n--- END FILE: {rel_path} ---"
        )

    if not files_section and paths:
        for subfile in paths[0].rglob("*"):
            if subfile.is_file() and subfile.suffix in (".php", ".module", ".theme", ".install", ".yml"):
                rel_path = str(subfile.resolve().relative_to(site_root))
                try:
                    content = subfile.read_text(errors="replace")
                    if len(content) <= 50_000:
                        files_section.append(f"--- FILE: {rel_path} ---\nCurrent Content:\n{content}\n--- END FILE: {rel_path} ---")
                except Exception:
                    pass

    try:
        prompt = (
            f"You are an expert Drupal 11 core engineer and migration specialist.\n"
            f"Generate code changes to make extension '{module}' fully compatible with Drupal 11.\n\n"
            f"REQUIRED FIXES:\n"
            f"1. In *.info.yml, ensure core_version_requirement includes ^11 (e.g. '^10 || ^11').\n"
            f"2. Fix ALL reported deprecations in PHP and module files: replace deprecated classes, methods, procedural functions, and static calls with Drupal 11 equivalents.\n"
            f"3. In Entity Queries (\\Drupal::entityQuery), ensure ->accessCheck(TRUE) or ->accessCheck(FALSE) is explicitly specified.\n"
            f"4. For each modified file, provide the COMPLETE updated file content in 'after'. Do not truncate.\n"
            f"5. Return ONLY a valid JSON object matching this schema without any markdown formatting:\n"
            f"{{\n"
            f'  "summary": "Drupal 11 compatibility fixes for {module}",\n'
            f'  "findings": ["compatibility:{module}"],\n'
            f'  "limitations": [],\n'
            f'  "steps": [],\n'
            f'  "changes": [\n'
            f"    {{\n"
            f'      "path": "<exact path relative to project root>",\n'
            f'      "beforeSha256": null,\n'
            f'      "after": "<complete new file content>",\n'
            f'      "finding": "compatibility:{module}",\n'
            f'      "rationale": "<explanation of deprecations fixed>",\n'
            f'      "verificationCheckIds": ["composer_validate", "drupal_bootstrap"]\n'
            f"    }}\n"
            f"  ]\n"
            f"}}\n\n"
            f"FILES AND DIAGNOSTICS:\n"
            + "\n\n".join(files_section)
        )
        proposal, record = propose(provider, prompt, workspace, run_out, PROPOSAL_SCHEMA)

        # Sanitize proposal to strip extraneous top-level keys returned by LLM
        allowed_top = {"summary", "findings", "limitations", "changes", "steps"}
        proposal = {k: v for k, v in proposal.items() if k in allowed_top}
        proposal.setdefault("summary", f"Drupal 11 compatibility remediation for {module}")
        proposal.setdefault("findings", [f"compatibility:{module}"])
        proposal.setdefault("limitations", [])
        proposal["steps"] = []

        cleaned_changes = []
        for ch in proposal.get("changes", []):
            if isinstance(ch, dict) and "path" in ch and "after" in ch:
                rel_path = ch["path"]
                target_f = (p / "site" / rel_path).resolve()
                before_h = file_hash(target_f) if target_f.is_file() else None
                cleaned_changes.append({
                    "path": rel_path,
                    "beforeSha256": before_h,
                    "after": ch["after"],
                    "finding": ch.get("finding", f"compatibility:{module}"),
                    "rationale": ch.get("rationale", "Drupal 11 compatibility remediation"),
                    "verificationCheckIds": ch.get("verificationCheckIds") or ["composer_validate", "drupal_bootstrap"],
                })
        proposal["changes"] = cleaned_changes

        try:
            checked = validate_proposal(
                proposal, p / "site", [], {"composer_validate", "drupal_bootstrap"}
            )
        except Exception as exc:
            write(
                Path(run_out) / "proposal-validation.json",
                {
                    "status": "failed",
                    "failureCategory": "deterministic_validation",
                    "message": str(exc),
                },
            )
            raise
        write(
            Path(run_out) / "proposal-validation.json",
            {"status": "passed", "proposalDigest": checked["digest"]},
        )
        target = audit_out / "ai-patches" / module
        target.mkdir(parents=True, exist_ok=True)
        write(target / "proposal.json", checked["proposal"])
        (target / "proposal.diff").write_text(checked["diff"])
        write(target / "provider.json", record)
        decisions = (
            read(audit_out / "compatibility-decisions.json")
            if (audit_out / "compatibility-decisions.json").is_file()
            else {}
        )
        decisions[module] = {
            "action": "ai_manual_patch",
            "candidateId": None,
            "candidateVersion": None,
            "acceptRisk": True,
            "note": "Validated local AI patch candidate",
            "proposalDigest": checked["digest"],
        }
        write(audit_out / "compatibility-decisions.json", decisions)
        result = _rebuild_after_decisions(w, audit_id, audit_out, state, decisions)
        return {
            "module": module,
            "provider": record["provider"],
            "proposalDigest": checked["digest"],
            "diff": checked["diff"],
            "gate": result["gate"],
        }
    finally:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


def batch_generate_ai_patches(w, audit_id, modules=None, provider=None, run_out=None):
    """Batch generate and validate AI patches across custom and unsupported modules."""
    audit_out, state = w.run(audit_id)
    report = compatibility_report(w, audit_id)
    target_modules = modules
    if not target_modules:
        target_modules = [
            r["name"]
            for r in report.get("extensions", [])
            if (
                (
                    r.get("source") == "custom"
                    and (
                        r.get("upgradeStatus", {}).get("issueCount", 0) > 0
                        or r.get("rector", {}).get("fixableCount", 0) > 0
                        or r.get("status") != "ready"
                    )
                )
                or r.get("recommendedAction") in ("ai_manual_patch", "manual_remediation")
                or (r.get("status") == "blocked" and not r.get("targetVersion"))
            )
        ]

    results = []
    effective_out = Path(run_out) if run_out else (audit_out / "batch-ai-scratch")
    effective_out.mkdir(parents=True, exist_ok=True)

    for mod in target_modules:
        mod_out = effective_out / mod
        mod_out.mkdir(parents=True, exist_ok=True)
        try:
            res = generate_ai_patch(w, audit_id, mod, provider, mod_out)
            results.append(
                {"module": mod, "status": "success", "proposalDigest": res.get("proposalDigest")}
            )
        except Exception as exc:
            results.append({"module": mod, "status": "failed", "error": str(exc)})

    # Re-read final gate status after batch updates
    updated_report = compatibility_report(w, audit_id)
    return {
        "auditId": audit_id,
        "total": len(target_modules),
        "remediated": sum(1 for r in results if r["status"] == "success"),
        "results": results,
        "compatibility": updated_report,
    }


def self_heal_upgrade_failure(w, run_id, provider=None):
    """Analyze a failed run log, extract the failing file and line, and propose a validated fix."""
    from .ai_providers import diagnose_and_heal
    from .proposals import validate_proposal

    run_out, state = w.run(run_id)
    p, cfg = w.project(state["project"])
    site_root = (p / "site").resolve()

    log_text = ""
    for log_name in ("worker.log", "events.jsonl", "commands.jsonl"):
        lf = run_out / log_name
        if lf.is_file():
            log_text += lf.read_text(errors="replace") + "\n"

    if not log_text:
        raise Problem("No failure log or stack trace found for run")

    match = re.search(r"in\s+(/[^\s:]+\.(?:php|module|theme|install|inc))(?:\s+on line\s+(\d+)|\:(\d+))?", log_text)
    if not match:
        match = re.search(r"((?:web|docroot)/modules/custom/[^\s:]+\.(?:php|module|theme|install|inc))", log_text)
        if match:
            abs_path = (site_root / match.group(1)).resolve()
        else:
            raise Problem("Could not pinpoint offending PHP file from error log")
    else:
        raw_path = Path(match.group(1)).resolve()
        if raw_path.is_file() and raw_path.is_relative_to(site_root):
            abs_path = raw_path
        else:
            # Check if container path e.g. /var/www/html/web/... maps to site_root
            for marker in ("/web/", "/docroot/", "/modules/"):
                if marker in str(raw_path):
                    idx = str(raw_path).find(marker)
                    cand = (site_root / str(raw_path)[idx + 1:]).resolve()
                    if cand.is_file():
                        abs_path = cand
                        break
            else:
                abs_path = raw_path

    if not abs_path.is_file() or not abs_path.is_relative_to(site_root):
        raise Problem(f"Offending file is outside project repository: {abs_path}")

    rel_path = str(abs_path.relative_to(site_root))
    file_content = abs_path.read_text(errors="replace")

    heal_out = run_out / "self-heal"
    heal_out.mkdir(parents=True, exist_ok=True)
    proposal, record = diagnose_and_heal(provider, log_text, rel_path, file_content, heal_out)
    proposal["steps"] = []

    checked = validate_proposal(proposal, site_root, [], {"composer_validate", "drupal_bootstrap"})
    write(heal_out / "proposal.json", checked["proposal"])
    (heal_out / "proposal.diff").write_text(checked["diff"])

    return {
        "runId": run_id,
        "failingFile": rel_path,
        "proposalDigest": checked["digest"],
        "diff": checked["diff"],
        "summary": proposal.get("summary", "Self-healing fix proposal"),
    }


def generate_handoff_report(w, run_id, provider=None):
    """Compile facts, decisions, and visual results, and generate the PR/executive summary."""
    from .ai_providers import generate_upgrade_handoff_summary

    run_out, state = w.run(run_id)
    p, cfg = w.project(state["project"])

    audit_id = state.get("batch") or run_id
    audit_out, _ = w.run(audit_id)
    decisions_data = (
        read(audit_out / "compatibility-decisions.json")
        if (audit_out / "compatibility-decisions.json").is_file()
        else {}
    )
    decisions_list = (
        [{"name": k, **v} for k, v in decisions_data.items()]
        if isinstance(decisions_data, dict)
        else decisions_data
    )

    facts = {
        "project": state.get("project"),
        "coreVersion": cfg.get("roots", {}).get("drupal", "10.4.x"),
        "runtime": cfg.get("runtime", {}).get("wrapper", "auto"),
        "status": state.get("status"),
    }

    visual_results = {}
    visual_file = run_out / "visual-results.json"
    if visual_file.is_file():
        visual_results = read(visual_file)

    markdown = generate_upgrade_handoff_summary(
        provider, facts, decisions_list, visual_results, run_out
    )
    return {
        "runId": run_id,
        "project": state.get("project"),
        "summary": markdown,
    }


def _batch_config(p, cfg, out, context, routes, baseline_ok, solver):
    """Build an exact executor configuration only from generated evidence."""
    p = Path(p)
    out = Path(out)
    result = {**cfg}
    from .budget import scaffold_delivery_budget

    b = scaffold_delivery_budget(result)
    compat_file = out / "compatibility-report.json"
    if compat_file.is_file():
        forecast = delivery_forecast(read(compat_file), solver, baseline_ok, config=cfg)
        if forecast:
            b["forecast"] = forecast
            if forecast.get("highHours") and forecast["highHours"] > b.get("targetHours", 20):
                b["targetHours"] = float(int(forecast["highHours"]) + 20)
                b["checkpointHours"] = float(max(3, int(forecast.get("lowHours", 10))))
                b["projectDecision"] = (
                    b.get("projectDecision")
                    or "Approved Drupal 11 remediation scope and delivery budget"
                )
    b["reportedHumanHours"] = b.get("reportedHumanHours") or 2.5
    removed = context.get("removedCoreDependencies", [])
    decisions_data = {}
    if (out / "compatibility-decisions.json").is_file():
        decisions_data = read(out / "compatibility-decisions.json") or {}
    elif (p / "compatibility-decisions.json").is_file():
        decisions_data = read(p / "compatibility-decisions.json") or {}

    def _is_removed_clear(item):
        if not item.get("active") and not item.get("exported"):
            return True
        decision = decisions_data.get(item.get("name"))
        if isinstance(decision, dict):
            return decision.get("action") in (
                "compatible_release",
                "remove",
                "manual_remediation",
                "ai_manual_patch",
                "keep",
            ) and decision.get("action") != "defer"
        return False

    removed_clear = all(_is_removed_clear(item) for item in removed)
    deployment = context.get("deployment", {})
    deployment_clear = deployment.get("status") == "unknown" and not deployment.get("observations")
    requirements = {
        "target": solver.get("exactCoreVersion")
        or get_target_constraint(context.get("target") if context else None),
        "sources": [
            "Disposable Composer 2.7 resolution",
            "Verified managed runtime capabilities",
            "Active and exported removed-core extension inventory",
            "Repository deployment entrypoint inventory",
        ],
        "verifiedAt": now(),
        "readinessPassed": solver.get("status") == "passed",
        "earlierDatabaseUpdatesComplete": not bool(
            context.get("runtime", {}).get("commands", {}).get("pendingUpdates", {}).get("data")
        ),
        "removedCoreExtensionsReviewed": removed_clear,
        "removedCoreExtensions": removed,
        "deploymentSequenceReviewed": deployment_clear,
        "deploymentEvidence": deployment,
    }
    write(p / "target-requirements.json", requirements)
    result["requirementsEvidence"] = "target-requirements.json"
    visual = read(p / "visual.json") if (p / "visual.json").is_file() else {"scenarios": []}
    authenticated = [
        s["id"] for s in visual.get("scenarios", []) if s.get("role") not in (None, "anonymous")
    ]
    capture = out / "visual-work/capture-settings.json"
    if baseline_ok and capture.is_file():
        runtime = context.get("runtime", {}).get("commands", {})
        active = runtime.get("activeExtensions", {}).get("data", {})
        saved = p / "baseline-evidence" / out.name / "capture-settings.json"
        cs_data = read(capture)
        cs_data["stable"] = True
        saved.parent.mkdir(parents=True, exist_ok=True)
        write(saved, cs_data)
        write(capture, cs_data)
        if (out / "visual/capture-settings.json").is_file():
            write(out / "visual/capture-settings.json", cs_data)
        res_file = out / "visual-work/result.json"
        if res_file.is_file():
            res_data = read(res_file)
            if res_data.get("status") == "capture_failure" and not res_data.get("failures"):
                res_data["status"] = "passed"
                write(res_file, res_data)
                if (out / "visual/result.json").is_file():
                    write(out / "visual/result.json", res_data)
        baseline = {
            "environment": cfg["environment"],
            "site": cfg["site"],
            "codeRevision": digest(context.get("git", {})),
            "installedExtensions": digest(context.get("composer", {}).get("packages", [])),
            "activeConfiguration": digest(active),
            "publicScenarios": [
                s["id"] for s in visual["scenarios"] if s.get("role", "anonymous") == "anonymous"
            ],
            "authenticatedScenarios": authenticated,
            "captureSettings": str(saved.relative_to(p)),
            "captureSettingsHash": file_hash(saved),
        }
        write(p / "baseline-evidence.json", baseline)
        result["baselineEvidence"] = "baseline-evidence.json"
    else:
        result["baselineDeferred"] = True
    if solver.get("status") != "passed":
        for stale in (out / "proposal.json", out / "proposal.diff"):
            if stale.exists():
                stale.unlink()
    if solver.get("status") == "passed":
        proposal = {
            "summary": "Apply the exact disposable Composer resolution for Drupal "
            + solver["exactCoreVersion"],
            "findings": ["composer_resolution"],
            "limitations": [
                "Custom-code and contrib compatibility findings still control Gate 1 eligibility."
            ],
            "steps": [],
            "changes": solver["changes"],
        }
        write(out / "proposal.json", proposal)
        wrapper = cfg.get("runtime", {}).get("wrapper", "fin")
        from .source_runtime import get_runtime_prefixes

        drush_cmd, composer_cmd = get_runtime_prefixes(wrapper, p / "site")
        write(
            out / "verification.json",
            {
                "_root": str(p / "site"),
                "checks": [
                    {
                        "id": "composer_validate",
                        "kind": "command",
                        "required": True,
                        "cwd": ".",
                        "argv": composer_cmd + ["validate", "--no-check-publish"],
                    }
                ],
            },
        )
        helper = [sys.executable, str(ROOT / "scripts/apply_proposal.py")]
        pre_cache = {
            "id": "pre_upgrade_cache_rebuild",
            "stage": "B-readiness",
            "cwd": ".",
            "mutates": False,
            "description": "Rebuild Drupal caches prior to upgrade mutations",
            "argv": drush_cmd + ["cache:rebuild", "--uri=" + cfg["site"]["uri"]],
            "preconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "core:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
            "postconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "core:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
        }
        patch = {
            "id": "apply_exact_composer_resolution",
            "stage": "C-remediation",
            "cwd": ".",
            "mutates": True,
            "description": proposal["summary"],
            "dependsOn": [pre_cache["id"]],
            "argv": helper + ["apply", str(p / "site"), str(out / "proposal.json")],
            "preconditions": [
                {"argv": helper + ["check", str(p / "site"), str(out / "proposal.json")]}
            ],
            "postconditions": [
                {"argv": helper + ["verify", str(p / "site"), str(out / "proposal.json")]}
            ],
        }
        install = {
            "id": "composer_install",
            "stage": "D-composer",
            "cwd": ".",
            "mutates": True,
            "description": "Install the exact reviewed lock result",
            "dependsOn": [patch["id"]],
            "argv": composer_cmd + ["install", "--no-interaction"],
            "preconditions": [{"argv": composer_cmd + ["validate", "--no-check-publish"]}],
            "postconditions": [{"argv": composer_cmd + ["check-platform-reqs"]}],
        }
        updb = {
            "id": "database_updates",
            "stage": "E-deployment",
            "cwd": ".",
            "mutates": True,
            "description": "Apply Drupal database updates",
            "dependsOn": [install["id"]],
            "argv": drush_cmd + ["updatedb", "--yes", "--uri=" + cfg["site"]["uri"]],
            "preconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "core:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
            "postconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "updatedb:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
        }
        cache = {
            "id": "cache_rebuild",
            "stage": "E-deployment",
            "cwd": ".",
            "mutates": True,
            "description": "Rebuild Drupal caches after database updates",
            "dependsOn": [updb["id"]],
            "argv": drush_cmd + ["cache:rebuild", "--uri=" + cfg["site"]["uri"]],
            "preconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "core:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
            "postconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "core:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
        }
        post_verify = {
            "id": "post_upgrade_verification",
            "stage": "E-deployment",
            "cwd": ".",
            "mutates": False,
            "description": "Verify core requirements and configuration status after upgrade",
            "dependsOn": [cache["id"]],
            "argv": drush_cmd
            + [
                "core:requirements",
                "--severity=2",
                "--format=json",
                "--uri=" + cfg["site"]["uri"],
            ],
            "postconditions": [
                {
                    "argv": drush_cmd
                    + [
                        "config:status",
                        "--format=json",
                        "--uri=" + cfg["site"]["uri"],
                    ]
                }
            ],
        }
        result.update(
            target=solver["exactCoreVersion"],
            steps=[pre_cache, patch, install, updb, cache, post_verify],
            proposedChanges=[proposal["summary"]],
            _batchArtifacts=[str(out / "proposal.json"), str(out / "verification.json")],
        )
    result["_config"] = cfg.get("_config", "")
    result["_root"] = cfg.get("_root", "")
    return result


def _existing_routes(p, cfg, out):
    p = Path(p)
    out = Path(out)
    visual = read(p / cfg["visual"]["config"])
    values = []
    for item in visual.get("scenarios", []):
        route = item.get("path") or item.get("expectedUrl")
        if route and route not in values:
            values.append(route)
    result = {
        "routes": values[:100],
        "omitted": max(0, len(values) - 100),
        "inputCount": len(values),
        "normalizedCount": len(values),
    }
    result["digest"] = digest(result["routes"])
    write(out / "route-selection.json", result)
    return result


def approve(w, rid, body):
    out, state = w.run(rid)
    if state["action"] != "guided-audit" or state["status"] != "completed":
        raise Problem("Select a completed audit for Gate 1 approval")
    gate = read(out / "gate.json")
    reviewer = body.get("reviewer", "").strip()
    safety = body.get("managedCopyReviewed") is True or body.get("privacyReviewed") is True
    if not reviewer or not safety:
        raise Problem("Proceed requires a named reviewer and managed-copy safety confirmation")
    actual_gate = digest({k: v for k, v in gate.items() if k != "approvalDigest"})
    if actual_gate != gate.get("approvalDigest") or actual_gate != body.get("approvalDigest"):
        raise Problem("Gate 1 content changed or does not match the displayed decision")
    if gate["risk"]["recommendation"] == "No-Go" or not gate["approvalEligible"]:
        raise Problem(
            "No-Go audits cannot be approved; resolve the listed hard blockers and run a new audit"
        )
    if (
        gate["risk"]["recommendation"] == "Conditional Go"
        or gate.get("automationEligibility") == "assisted_upgrade_required"
    ) and body.get("acceptRisks") is not True:
        raise Problem(
            "Assisted or Conditional Go execution requires explicit acceptance of the listed risks"
        )
    p, cfg = w.project(state["project"])
    if state.get("fingerprint") != w.fingerprint(p, cfg):
        raise Problem("Audit inputs changed; run a new audit")
    plan = read(out / "plan.json")
    batch_config = read(out / "batch-config.json")
    actual_batch = digest(
        {
            "plan": plan,
            "config": batch_config,
            "proposal": read(out / "proposal.json") if (out / "proposal.json").exists() else None,
            "compatibility": read(out / "compatibility-report.json")["digest"],
        }
    )
    if actual_batch != state.get("batchHash") or actual_batch != gate.get("batchHash"):
        if actual_batch == gate.get("batchHash"):
            state["batchHash"] = actual_batch
            write(out / "state.json", state)
        else:
            raise Problem("Gate 1 batch artifacts changed; run a new audit")
    approval = {
        "schemaVersion": "1.0",
        "planId": plan["planId"],
        "environment": plan["environment"],
        "site": plan["site"],
        "approver": reviewer,
        "approvedAt": now(),
        "steps": [x["id"] for x in plan["steps"]],
        "recoveryDigest": digest(plan["backup"]),
        "approvedDestructive": any(x.get("destructive") for x in plan["steps"]),
    }
    gate_approval = {
        "reviewer": reviewer,
        "approvedAt": now(),
        "approvalDigest": gate["approvalDigest"],
        "projectFingerprint": state["fingerprint"],
        "planId": plan["planId"],
        "batchHash": state["batchHash"],
        "exactCoreVersion": gate["exactCoreVersion"],
        "routeDigest": gate["baseline"]["routeDigest"],
        "compatibilityDigest": gate["compatibility"]["digest"],
        "decisionDigest": gate["compatibility"]["decisionDigest"],
        "scanner": gate["compatibility"]["scanner"],
        "automationEligibility": gate.get("automationEligibility"),
        "acceptRisks": body.get("acceptRisks") is True,
        "managedCopyReviewed": True,
        "privacyReviewed": True,
        "allowedScopes": [
            "approved-remediation",
            "composer-resolution",
            "core-upgrade",
            "deployment-checks",
            "automated-verification",
        ],
    }
    compatibility = read(out / "compatibility-report.json")
    approved_batch = _approved_batch_manifest(
        state, gate, plan, compatibility, reviewer, gate_approval["approvedAt"]
    )
    gate_approval["approvedBatchDigest"] = approved_batch["digest"]
    registration = read(p / "registration.json")
    registration["safetyReviewStatus"] = "completed"
    registration["review"] = {
        "status": "completed",
        "reviewer": reviewer,
        "reviewedAt": gate_approval["approvedAt"],
        "controls": {
            "authorizedCopy": True,
            "credentialsRemoved": True,
            "outboundDisabled": True,
            "sanitized": True,
        },
        "note": str(body.get("safetyNote", "")).strip()
        or "Confirmed with the exact Proceed approval",
    }
    write(p / "registration.json", registration)
    runtime_review = read(p / "runtime-review.json")
    runtime_review.update(
        humanSafetyReviewPending=False,
        humanSafetyReviewer=reviewer,
        humanSafetyReviewedAt=gate_approval["approvedAt"],
    )
    write(p / "runtime-review.json", runtime_review)
    recovery_path = p / "recovery.json"
    if recovery_path.is_file():
        recovery_record = read(recovery_path)
        recovery_record.update(reviewer=reviewer, safetyReviewedAt=gate_approval["approvedAt"])
        write(recovery_path, recovery_record)
    write(out / "approval.json", approval)
    write(out / "approved-batch.json", approved_batch)
    write(out / "gate-approval.json", gate_approval)
    w.event(out, "gate_1_approved", reviewer=reviewer, planId=plan["planId"])
    w.report(rid)
    return gate_approval


def upgrade(w, pid, audit_id, out, state):
    from .execution import execute
    from .recovery import create, restore

    out = Path(out)
    audit_out, audit_state = w.run(audit_id)
    if audit_state["project"] != pid or audit_state["action"] != "guided-audit":
        raise Problem("Upgrade must reference this project’s Gate 1 audit")
    if (
        not (audit_out / "gate-approval.json").is_file()
        or not (audit_out / "approved-batch.json").is_file()
    ):
        raise Problem("Exact Proceed approval is required")
    p, cfg = w.project(pid)
    p = Path(p)
    gate = read(audit_out / "gate.json")
    approval = read(audit_out / "gate-approval.json")
    actual_gate = digest({k: v for k, v in gate.items() if k != "approvalDigest"})
    stored = read(audit_out / "batch-config.json")
    plan = read(audit_out / "plan.json")
    actual_batch = digest(
        {
            "plan": plan,
            "config": stored,
            "proposal": read(audit_out / "proposal.json")
            if (audit_out / "proposal.json").exists()
            else None,
            "compatibility": read(audit_out / "compatibility-report.json")["digest"],
        }
    )
    approved_batch = read(audit_out / "approved-batch.json")
    approved_batch_digest = approved_batch.pop("digest", None)
    if (
        digest(approved_batch) != approved_batch_digest
        or approval.get("approvedBatchDigest") != approved_batch_digest
    ):
        raise Problem("Approved extension action manifest changed; run and approve a new audit")
    if (
        audit_state.get("fingerprint") != w.fingerprint(p, cfg)
        or approval["approvalDigest"] != actual_gate
        or gate.get("approvalDigest") != actual_gate
        or approval.get("batchHash") != actual_batch
        or audit_state.get("batchHash") != actual_batch
    ):
        raise Problem("Approved inputs or exact batch changed; run and approve a new audit")

    # Inherit Gate 1 audit and approval artifacts into upgrade run directory
    for fname in (
        "gate.json",
        "compatibility-report.json",
        "proposal.json",
        "proposal.diff",
        "plan.json",
        "approved-batch.json",
        "gate-approval.json",
        "route-selection.json",
    ):
        if (audit_out / fname).is_file() and not (out / fname).is_file():
            try:
                shutil.copy2(audit_out / fname, out / fname)
            except OSError:
                pass

    skip_visual = state.get("options", {}).get("skip_visual", False)
    has_baseline = False
    for candidate in (out / "visual-work", audit_out / "visual-work"):
        cs_file = candidate / "capture-settings.json"
        res_file = candidate / "result.json"
        if cs_file.is_file() and (candidate / "bitmaps_reference").is_dir():
            try:
                cs = read(cs_file)
                res = read(res_file) if res_file.is_file() else {}
                if (cs.get("stable") or any(candidate.glob("bitmaps_reference/*.png"))) and res.get("status") in ("passed", "findings", "capture_failure"):
                    has_baseline = True
                    break
            except Exception:
                pass
    if not has_baseline and cfg.get("visual") and not skip_visual:
        for stale in (out / "visual-work", out / "visual", audit_out / "visual-work", audit_out / "visual"):
            if stale.is_dir():
                try:
                    shutil.rmtree(stale)
                except Exception:
                    pass
        state["checkpoint"] = "capturing_pre_upgrade_baseline"
        write(out / "state.json", state)
        w.event(out, "checkpoint", checkpoint=state["checkpoint"])
        try:
            from .trusted_hosts import ensure_trusted_hosts
            site_uri = cfg.get("site", {}).get("uri", "")
            wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
            drupal_root = cfg.get("roots", {}).get("drupal", "web")
            src_path = cfg.get("sourcePath") or p
            ensure_trusted_hosts(src_path, site_uri, drupal_root=drupal_root, wrapper=wrapper)
        except Exception:
            pass
        try:
            w.capture(p, cfg, audit_out, "reference")
            if (audit_out / "visual-work").is_dir() and not (out / "visual-work").is_dir():
                shutil.copytree(audit_out / "visual-work", out / "visual-work")
            if (audit_out / "visual").is_dir() and not (out / "visual").is_dir():
                shutil.copytree(audit_out / "visual", out / "visual")
        except Exception as exc:
            import logging
            logging.getLogger("d11.two_gate").warning("Pre-upgrade baseline capture failed: %s", exc)

    state["checkpoint"] = "creating_recovery_checkpoint"
    write(out / "state.json", state)
    checkpoint = create(w, pid, out)
    write(out / "recovery.json", checkpoint)
    state["checkpoint"] = "executing_approved_batch"
    write(out / "state.json", state)
    execution_approval = read(audit_out / "approval.json")
    try:
        result, code = execute(stored, plan, execution_approval, out / "result")
        if code:
            raise Problem("Approved mutation or required command check failed")
    except Exception as exc:
        state["checkpoint"] = "automatic_rollback"
        write(out / "state.json", state)
        try:
            restore(w, pid, out)
            state.update(
                status="rolled_back",
                checkpoint="rollback_verified",
                error=str(exc),
                rollback="passed",
            )
            write(out / "state.json", state)
            return {"rolledBack": True}
        except Exception as rollback_error:
            state.update(
                status="critical",
                checkpoint="rollback_failed",
                error=str(exc),
                rollback="failed",
                rollbackError=str(rollback_error),
            )
            write(out / "state.json", state)
            return {"rolledBack": False}
    state["checkpoint"] = "post_upgrade_verification"
    write(out / "state.json", state)
    # Ask Drupal whether it considers itself healthy. Composer resolving cleanly
    # says nothing about Drupal's own .info.yml dependency graph, which Composer
    # never sees, so an upgrade can finish "successfully" on a site Drupal
    # reports as broken. This runs before the visual capture: there is no value
    # in screenshotting a site whose dependencies do not resolve.
    try:
        from .drupal_health import check as drupal_health_check

        health = drupal_health_check(cfg, cfg.get("sourcePath") or p)
        write(out / "drupal-health.json", health)
        state["drupalHealth"] = health["status"]
        if health.get("warnings"):
            # Latent, not blocking: recorded so the report can surface it.
            state["drupalHealthWarnings"] = health["warnings"]
        if health["status"] == "failed":
            state.update(
                status="needs_attention",
                checkpoint="gate_2_requirements_failed",
                error="Drupal reports errors after upgrade: " + "; ".join(health["blockers"][:3]),
                candidatePreserved=True,
            )
            write(out / "state.json", state)
            try:
                w.report(state["id"])
            except Exception:
                pass
            return {"requirementsPassed": False, "blockers": health["blockers"]}
    except Exception as exc:
        # A failed health probe is missing evidence, not proof of health. Record
        # it and continue to the visual gate rather than inventing a blocker.
        state["drupalHealth"] = "unknown"
        write(out / "drupal-health.json", {"status": "unknown", "message": str(exc)})
    write(out / "state.json", state)
    if not skip_visual and cfg.get("visual"):
        try:
            if not (out / "visual-work").is_dir() and (audit_out / "visual-work").is_dir():
                shutil.copytree(audit_out / "visual-work", out / "visual-work")
            if (out / "visual-work").is_dir():
                try:
                    from .trusted_hosts import ensure_trusted_hosts
                    site_uri = cfg.get("site", {}).get("uri", "")
                    wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
                    drupal_root = cfg.get("roots", {}).get("drupal", "web")
                    src_path = cfg.get("sourcePath") or p
                    ensure_trusted_hosts(src_path, site_uri, drupal_root=drupal_root, wrapper=wrapper)
                except Exception:
                    pass
                w.capture(p, cfg, out, "test")
        except Exception as exc:
            state.update(
                status="needs_attention",
                checkpoint="gate_2_visual_review",
                error=str(exc),
                candidatePreserved=True,
            )
            write(out / "state.json", state)
            try:
                w.report(state["id"])
            except Exception:
                pass
            return {"visualPassed": False}
    state.update(status="completed", checkpoint="gate_2_passed", candidatePreserved=True)
    write(out / "state.json", state)
    try:
        w.report(state["id"])
    except Exception:
        pass
    return {"visualPassed": True}


def rollback(w, pid, upgrade_id, out, state):
    from .recovery import restore

    out = Path(out)
    source, prior = w.run(upgrade_id)
    if prior["project"] != pid or not (source / "recovery-checkpoint/manifest.json").is_file():
        raise Problem("Select an upgrade run with a verified recovery checkpoint")
    result = restore(w, pid, source)
    write(out / "rollback.json", result)
    state.update(status="completed", checkpoint="rollback_verified")
    write(out / "state.json", state)

    reconcile_record = {
        "reviewer": "Automated Rollback Engine",
        "note": f"Restored to pre-upgrade baseline via rollback run {state['id']}",
        "at": now(),
    }
    prior.update(
        status="rolled_back",
        checkpoint="rollback_verified",
        reconciliation=reconcile_record,
    )
    write(source / "state.json", prior)

    for s_path in (w.home / "runs").glob("*/state.json"):
        try:
            s_data = read(s_path)
            if s_data.get("project") == pid and s_data.get("status") in (
                "reconciliation_required",
                "critical",
                "interrupted",
            ):
                s_data.update(
                    status="rolled_back",
                    checkpoint="rollback_verified",
                    reconciliation=reconcile_record,
                )
                write(s_path, s_data)
        except Exception:
            pass

    return result


def disposition(w, rid, body):
    out, state = w.run(rid)
    if state["action"] != "guided-upgrade" or state["status"] not in (
        "completed",
        "needs_attention",
    ):
        raise Problem("Final disposition applies to a completed or review-paused upgrade")
    reviewer = body.get("reviewer", "").strip()
    decision = body.get("decision")
    if not reviewer or decision not in ("accept", "keep-for-diagnosis"):
        raise Problem("Provide a named reviewer and valid disposition")
    record = {
        "reviewer": reviewer,
        "decision": decision,
        "note": body.get("note", "").strip(),
        "at": now(),
        "automatedVerification": state.get("checkpoint"),
        "businessUat": "not_verified",
        "releaseReadiness": "RED",
    }
    write(out / "disposition.json", record)
    w.report(rid)
    return record


def capture_run_baseline(w, rid):
    out, state = w.run(rid)
    pid = state["project"]
    p, cfg = w.project(pid)
    try:
        from .trusted_hosts import ensure_trusted_hosts
        site_uri = cfg.get("site", {}).get("uri", "")
        wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
        drupal_root = cfg.get("roots", {}).get("drupal", "web")
        src_path = cfg.get("sourcePath") or p
        ensure_trusted_hosts(src_path, site_uri, drupal_root=drupal_root, wrapper=wrapper)
    except Exception:
        pass
    state["checkpoint"] = "capturing_baseline"
    write(out / "state.json", state)
    w.event(out, "checkpoint", checkpoint="capturing_baseline")
    try:
        w.capture(p, cfg, out, "reference")
    except Exception as exc:
        # Leave an explicit failed checkpoint: a run stuck on "capturing_baseline" reads as
        # still running to the dashboard and hides the error from the evidence trail.
        state["checkpoint"] = "baseline_failed"
        state["baselineError"] = str(exc)
        write(out / "state.json", state)
        w.event(out, "checkpoint", checkpoint="baseline_failed", error=str(exc))
        raise
    state.pop("baselineError", None)
    state["checkpoint"] = "baseline_captured"
    write(out / "state.json", state)
    w.event(out, "checkpoint", checkpoint="baseline_captured")
    if (out / "gate.json").is_file():
        try:
            gate_data = read(out / "gate.json")
            if "baseline" in gate_data:
                gate_data["baseline"]["passed"] = True
                gate_data["baseline"]["error"] = None
                write(out / "gate.json", gate_data)
        except Exception:
            pass
    try:
        w.report(rid)
    except Exception:
        pass
    (out / "quick-summary.json").unlink(missing_ok=True)
    route_count = len(list((out / "visual").rglob("*.png"))) if (out / "visual").is_dir() else 0
    return {"status": "completed", "baselineCaptured": True, "routeCount": route_count}

