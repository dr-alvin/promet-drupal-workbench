"""Audience reports share facts, gate and release identity."""

import json
import tarfile
from pathlib import Path

from .budget import budget_markdown, budget_model
from .common import *

PHASES = [
    "Audit and infrastructure",
    "Dependencies and patches",
    "Custom code remediation",
    "Core and configuration alignment",
    "QA and mandatory UAT",
    "Rehearsal and production handoff",
]


def findings(context):
    items = []

    def add(id, status, message):
        items.append({"id": id, "status": status, "message": message})

    add(
        "runtime",
        "passed" if context["runtime"]["status"] == "collected" else "unknown",
        "Runtime evidence " + context["runtime"]["status"],
    )
    commands = context["runtime"]["commands"]
    if context["runtime"]["status"] == "collected":
        bootstrap = commands.get("status", {}).get("data", {}).get("bootstrap")
        add(
            "bootstrap",
            "passed"
            if str(bootstrap).lower() == "successful"
            else "findings"
            if bootstrap
            else "unknown",
            "Drupal bootstrap in selected environment",
        )
        for name in ("pendingUpdates", "configurationStatus"):
            rec = commands.get(name, {})
            add(
                name,
                "unknown"
                if rec.get("status") != "passed"
                else "findings"
                if rec.get("data")
                else "passed",
                "Mutable Drupal state: " + name,
            )
    add(
        "lock",
        "passed" if context["composer"]["lockStatus"] == "present" else "unknown",
        "Dependency lock " + context["composer"]["lockStatus"],
    )
    add(
        "wrapper",
        "passed" if context["wrapper"]["selected"] else "unknown",
        "Runtime wrapper selection",
    )
    add(
        "config",
        "passed" if context["roots"]["config"] else "unknown",
        "Configuration directory discovery; active parity requires verification",
    )
    if context["missingActiveCode"]:
        add(
            "missing_code",
            "findings",
            "Installed extensions lack discoverable code: "
            + ", ".join(context["missingActiveCode"]),
        )
    add(
        "compatibility",
        "unknown",
        "Compatibility needs version-specific scanners and Composer resolution diagnostics",
    )
    return items


def checks(cfg, context):
    require_nonprod(cfg)
    results = []
    for check in cfg.get("checks", []):
        argv = check["argv"]
        if check["kind"] == "upgrade_status" and (
            "--format=codeclimate" not in argv
            or not ("--all" in argv or any(a.startswith("--projects=") for a in argv))
        ):
            raise Problem(
                "Upgrade Status requires --format=codeclimate and explicit --all or --projects=...",
                64,
            )
        record = command(argv, relative(cfg["_root"], check["cwd"]), check.get("timeout", 600))
        status = record["status"]
        if check["kind"] == "upgrade_status":
            try:
                data = json.loads(record.get("stdout", ""))
                valid = isinstance(data, list) and all(
                    isinstance(x, dict)
                    and x.get("type") == "issue"
                    and x.get("description")
                    and isinstance(x.get("location"), dict)
                    for x in data
                )
                if not valid:
                    raise ValueError("Expected Code Climate issues array")
                if record["exitCode"] not in (0, 1):
                    status = "tool_failure"
                elif data:
                    status = "findings"
                elif record["exitCode"] == 0:
                    status = "passed"
                else:
                    status = "tool_failure"
                record["findingCount"] = len(data)
            except ValueError:
                status = "tool_failure"
        record["failureCategory"] = (
            "compatibility_findings"
            if status == "findings"
            else "malformed_output"
            if status == "tool_failure" and record["executionStatus"] == "passed"
            else record.get("failureCategory")
        )
        results.append(
            {"id": check["id"], "status": status, "required": check["required"], "command": record}
        )
    return results


def resolve_compatibility(items, cfg):
    coverage = cfg.get("compatibilityCoverage", {})
    ids = coverage.get("requiredCheckIds", [])
    rows = {x["id"]: x for x in items if x.get("required") is True and "command" in x}
    # A reviewed complete coverage declaration and valid diagnostics are both necessary.
    sufficient = (
        ids
        and coverage.get("target") == cfg.get("target")
        and coverage.get("scopeEvidence")
        and all(i in rows and rows[i]["status"] in ("passed", "findings") for i in ids)
    )
    if sufficient:
        items = [x for x in items if x["id"] != "compatibility"]
        items.append(
            {
                "id": "compatibility",
                "status": "findings"
                if any(rows[i]["status"] == "findings" for i in ids)
                else "passed",
                "message": "Configured compatibility coverage completed for "
                + coverage["target"]
                + "; scope: "
                + coverage["scopeEvidence"],
            }
        )
    return items


def exit_code(results):
    statuses = {x["status"] for x in results if x.get("required", True)}
    if statuses & {"tool_failure", "failed"}:
        return 2
    if statuses & {"findings", "visual_mismatch", "functional_failure"}:
        return 1
    if not statuses or statuses & {"unknown", "blocked", "skipped"}:
        return 3
    return 0


def reports(output, result, cfg=None):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    if result.get("plannedSteps"):
        completed = {s["id"]: s["status"] for s in result.get("steps", [])}
        result["deferredSteps"] = [
            {
                "id": s["id"],
                "stage": s["stage"],
                "status": "blocked" if result.get("status") == "failed" else "skipped",
            }
            for s in result["plannedSteps"]
            if s["id"] not in completed
        ]
    result["deliveryBudget"] = (
        budget_model(cfg or {})
        if cfg is not None or "deliveryBudget" not in result
        else result["deliveryBudget"]
    )
    schema(result, "result")
    write(out / "result.json", result)
    status = result.get("status", "unknown")
    items = result.get("checks", [])
    developer = [
        "# Developer report",
        "",
        f"Run: {result.get('runId', 'assessment')} · Status: {status}",
        "",
        "This report describes configured evidence only. Production readiness also requires release-specific UAT and recovery rehearsal.",
        "",
    ]
    for item in items:
        developer.append(
            f"- **{item['status']}** — {item['id']}: {item.get('message', 'See result.json for command diagnostics.')}"
        )
    if result.get("error"):
        developer.extend(["", str(result["error"])])
    developer.extend(
        [
            "",
            "## Recovery",
            "On a failed mutation, stop and reconcile current state. Recover matching code, database, and files using the approved references; Git reset alone is insufficient.",
        ]
    )
    developer.extend(["", budget_markdown(result["deliveryBudget"])])
    (out / "developer-report.md").write_text("\n".join(developer) + "\n")
    client = [
        "# Client audit and approval",
        "",
        f"Audit status: **{status}**. This is not an upgrade completion certificate.",
        "",
        "## Decisions and missing evidence",
    ]
    client += [
        f"- {x.get('message', x['id'])} ({x['status']})" for x in items if x["status"] != "passed"
    ]
    client += [
        "",
        "## Estimates",
        "Estimate ranges are supplied by the reviewing engineer from the observed scope, blockers, dependencies, and testing effort. The target budget is separate from the evidence-based forecast; phase estimates below are forecasts, not capped allocations.",
    ]
    estimates = (cfg or {}).get("estimates", [])
    for e in estimates:
        client.append(
            f"- {e['phase']}: {e['lowHours']}–{e['highHours']} hours; owner: {e['owner']}; basis: {e['basis']}"
        )
    if not estimates:
        client.append(
            "No separate phase forecasts supplied. The normalized delivery forecast and target are below."
        )
    client += [
        "",
        "## Approval",
        "Review the proposed scope, estimate, risks, environment and recovery ownership. Execution approval is a separate file bound to the exact plan. UAT approval must identify the tested release and accepted scenarios.",
    ]
    client.extend(["", budget_markdown(result["deliveryBudget"])])
    (out / "client-report.md").write_text("\n".join(client) + "\n")
    (out / "qa-report.md").write_text(
        "# QA browser instructions\n\nUse the selected non-production site and assigned test role. Follow the project scenario catalog; confirm the expected page, content, permissions and dialog behavior at each viewport. Record defects and evidence against scenario IDs.\n\nCompare public navigation, search and forms, then content editing/preview, editor and media dialogs, Views, display modes, and role-specific access where configured. Do not save changes unless the scenario explicitly authorizes disposable fixtures.\n\nAutomated results are in result.json and the visual report. Missing or skipped coverage requires a recorded disposition. Business UAT must identify the exact release, approver, date, accepted scenarios and unresolved defect disposition.\n"
    )
    write(
        out / "report-index.json",
        {
            "schemaVersion": "1.0",
            "status": status,
            "sourceHash": file_hash(out / "result.json"),
            "reports": [
                {"path": p.name, "sha256": file_hash(p)} for p in sorted(out.glob("*-report.md"))
            ],
        },
    )


def package_reports(output):
    out = Path(output)
    with tarfile.open(out / "report.tar.gz", "w:gz") as archive:
        for name in (
            "result.json",
            "report-index.json",
            "client-report.md",
            "developer-report.md",
            "qa-report.md",
            "context.json",
            "config-diff.json",
        ):
            path = out / name
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=name, recursive=False)
