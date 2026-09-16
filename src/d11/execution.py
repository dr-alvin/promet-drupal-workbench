"""Plan-bound execution with crash-aware checkpoints, locks and explicit approvals."""

import contextlib
import fcntl
import os
import re
import uuid
from pathlib import Path

from .budget import budget_model, enforce_budget
from .common import *
from .discovery import config_diff, find_roots, runtime_discovery, wrapper
from .drupal_health import composer_source_fallbacks
from .knowledge import get_source_floor

STAGES = [
    "A-baseline",
    "B-readiness",
    "C-remediation",
    "D-composer",
    "E-deployment",
    "F-verification",
]
FORBIDDEN = {"git", "sudo", "sh", "bash", "zsh", "rm"}


def validate_steps(steps):
    ids = set()
    last = -1
    for step in steps:
        schema(step, "step")
        if step["id"] in ids:
            raise Problem("Duplicate step ID", 64)
        if not set(step.get("dependsOn", [])).issubset(ids):
            raise Problem("Step dependency must precede its dependent", 64)
        ids.add(step["id"])
        stage = STAGES.index(step["stage"])
        if stage < last:
            raise Problem("Stages must follow lifecycle order", 64)
        last = stage
        for argv in [step["argv"]] + [
            p["argv"] for p in step.get("preconditions", []) + step.get("postconditions", [])
        ]:
            exe = Path(argv[0]).name
            joined = " ".join(argv)
            removal = bool(set(argv) & {"pmu", "pm-uninstall", "pm:uninstall"})
            if exe in FORBIDDEN or re.search(
                r"\b(git\s+(add|commit|push|reset)|--ignore-platform-reqs?|minimum-stability)\b",
                joined,
            ):
                raise Problem("Forbidden automatic operation in plan", 64)
            if removal and (not step.get("destructive") or not step.get("reviewedRemovalDigest")):
                raise Problem("Module removal requires digest-bound impact evidence", 64)
            if set(argv) & {
                "sql:drop",
                "sql-drop",
                "config:delete",
                "config-delete",
                "cdel",
                "entity:delete",
            }:
                raise Problem(
                    "Destructive Drupal commands require a separate reviewed manual procedure", 64
                )
            if any("${" in a or "\x00" in a for a in argv):
                raise Problem("Variable interpolation is not supported in commands", 64)
        if step["mutates"] and (not step.get("preconditions") or not step.get("postconditions")):
            raise Problem("Mutations need observable preconditions and postconditions", 64)


def snapshot(cfg, output):
    external = {
        str(path): file_hash(path) if Path(path).is_file() else None
        for path in cfg.get("_batchArtifacts", [])
    }
    return {
        **({"batchArtifacts": external} if external else {}),
        "configurationFile": file_hash(cfg["_config"]) if Path(cfg["_config"]).is_file() else None,
        "configuration": digest({k: v for k, v in cfg.items() if not k.startswith("_")}),
        "files": state_hashes(cfg["_root"], [output]),
    }


def plan(cfg, context, output, preparation=False):
    steps = cfg.get("steps", [])
    validate_steps(steps)
    runtime = context["runtime"]["commands"].get("status", {}).get("data", {})
    result = {
        "schemaVersion": "1.0",
        "createdAt": now(),
        "environment": cfg["environment"],
        "site": cfg.get("site"),
        "source": runtime.get("drupal-version", "unknown"),
        "sourceRevision": context["git"]["head"].get("stdout", "").strip(),
        "dirtyState": context["git"]["dirty"].get("stdout", ""),
        "target": cfg.get("target"),
        "roots": context["roots"],
        "inputs": snapshot(cfg, output),
        "steps": steps,
        "backup": cfg.get("recovery"),
        "requirementsEvidence": cfg.get("requirementsEvidence"),
        "baselineEvidence": cfg.get("baselineEvidence"),
        "changes": cfg.get("proposedChanges", []),
        "blockers": [],
        "uncertainties": [
            "Compatibility requires scanner and runtime evidence; static inventory alone is insufficient."
        ],
    }
    if preparation:
        result["purpose"] = "preparation"
        if any(
            any(
                "drupal/core" in a
                or a in ("deploy", "cim", "config:import", "updatedb", "updb", "updatedb:status")
                for a in x["argv"]
            )
            for x in steps
        ):
            result["blockers"].append("Core and deployment operations cannot run as preparation")
        if not steps or any(
            not x.get("preparation")
            or x["stage"] not in ("B-readiness", "C-remediation", "D-composer")
            or x.get("importsConfig")
            for x in steps
        ):
            result["blockers"].append(
                "Preparation requires an exclusively bounded preparation batch"
            )
        result["preparationEvidence"] = cfg.get("preparationEvidence")
        ev = relative(Path(cfg["_config"]).parent, cfg.get("preparationEvidence", ""))
        if ev.is_file():
            result["preparationHash"] = file_hash(ev)
        else:
            result["blockers"].append(
                "Missing reviewed isolation and recorded baseline failure evidence"
            )
    result["deliveryBudget"] = budget_model(cfg)
    try:
        enforce_budget(cfg)
    except Problem as e:
        result["blockers"].append(str(e))
    for key, okay in [
        ("explicit target", cfg.get("target")),
        ("reviewed steps", steps),
        ("environment identity probe", cfg.get("identity")),
        ("recovery evidence", cfg.get("recovery")),
        ("release requirements evidence", cfg.get("requirementsEvidence")),
        ("baseline evidence", cfg.get("baselineEvidence")),
        ("runtime source version", result["source"] != "unknown"),
    ]:
        if (preparation or cfg.get("baselineDeferred") or cfg.get("fast")) and key in (
            "release requirements evidence",
            "baseline evidence",
        ):
            continue
        if not okay:
            result["blockers"].append("Missing " + key)
    imports_config = any(
        s.get("importsConfig") or set(s["argv"]) & {"deploy", "cim", "config:import"} for s in steps
    )
    if imports_config:
        comparison = cfg.get("configComparison")
        if not comparison:
            result["blockers"].append(
                "Configuration import/deploy requires configComparison evidence"
            )
        else:
            base = Path(cfg["_config"]).parent
            before, after = (
                relative(base, comparison["before"]),
                relative(base, comparison["after"]),
            )
            result["configurationReview"] = {
                "changes": config_diff(before, after),
                "before": state_hashes(before),
                "after": state_hashes(after),
            }
            if any(
                any(
                    r.startswith(
                        (
                            "field_or_storage_deletion",
                            "module_removal",
                            "theme_removal",
                            "site_uuid_mismatch",
                        )
                    )
                    for r in c["risks"]
                )
                for c in result["configurationReview"]["changes"]
            ):
                result["blockers"].append(
                    "Consequential configuration removal/UUID mismatch requires separate manual review and reconciliation"
                )
    if context["runtime"]["status"] != "collected":
        result["blockers"].append("Runtime inspection is missing or incomplete")
    if context.get("missingActiveCode"):
        result["blockers"].append("Installed extensions have missing code")
    if cfg.get("requirementsEvidence"):
        ev = Path(cfg["_config"]).parent / cfg["requirementsEvidence"]
        if not ev.is_file():
            result["blockers"].append("Requirements evidence file does not exist")
        else:
            result["requirementsHash"] = file_hash(ev)
            requirements = read(ev)
            if (
                requirements.get("readinessPassed") is not True
                or requirements.get("earlierDatabaseUpdatesComplete") is not True
                or requirements.get("removedCoreExtensionsReviewed") is not True
            ):
                result["blockers"].append(
                    "Target requirements, earlier updates and removed-core-extension review are incomplete"
                )
    if cfg.get("baselineEvidence"):
        ev = Path(cfg["_config"]).parent / cfg["baselineEvidence"]
        if not ev.is_file():
            result["blockers"].append("Baseline evidence file does not exist")
        else:
            result["baselineHash"] = file_hash(ev)
    result["planId"] = digest(result)
    return result


def check_identity(cfg, roots):
    require_nonprod(cfg)
    ident = cfg.get("identity")
    if not ident:
        raise Problem("Missing environment identity probe")
    rec = command(ident["argv"], roots["composer"])
    stdout = rec.get("stdout", "").strip()
    import re
    stdout_lines = [re.sub(r"\x1b\[[0-9;]*[mK]", "", line).strip() for line in stdout.splitlines() if line.strip()]
    expected = ident["expected"].strip()
    is_match = (
        expected in stdout_lines
        or any(expected in line for line in stdout_lines)
        or stdout == expected
    )
    if rec["exitCode"] != 0 or not is_match:
        import logging
        logging.getLogger("d11.execution").warning(
            "check_identity failed: exitCode=%s, stdout=%r, stderr=%r, expected=%r",
            rec.get("exitCode"),
            stdout,
            rec.get("stderr"),
            expected,
        )
        raise Problem("Environment identity probe did not match approved identity")
    return rec


def prerequisites(cfg, p, approval):
    require_nonprod(cfg)
    if p.get("blockers"):
        raise Problem("Plan has blockers: " + "; ".join(p["blockers"]))
    if digest({k: v for k, v in p.items() if k != "planId"}) != p.get("planId"):
        raise Problem("Plan digest is invalid")
    if p.get("configurationReview"):
        comparison = cfg.get("configComparison", {})
        base = Path(cfg["_config"]).parent
        if any(
            state_hashes(relative(base, comparison[side])) != p["configurationReview"][side]
            for side in ("before", "after")
        ):
            raise Problem("Configuration review evidence changed")
    schema(approval, "approval")
    if (
        approval["planId"] != p["planId"]
        or approval["environment"] != p["environment"]
        or approval["site"] != p["site"]
    ):
        raise Problem("Approval does not match plan/environment/site")
    if approval["environment"] != cfg["environment"] or approval["site"] != cfg.get("site"):
        raise Problem("Selected environment changed")
    if set(approval["steps"]) != {s["id"] for s in p["steps"]}:
        raise Problem("Approval step list must match exact plan")
    if any(s.get("newWorkaround") for s in p["steps"]):
        raise Problem(
            "New dependency workarounds require a separate reviewed manual procedure and replanning"
        )
    destructive = [s for s in p["steps"] if s.get("destructive")]
    if destructive and approval.get("approvedDestructive") is not True:
        raise Problem("Exact module-removal scope was not approved")
    for step in destructive:
        evidence = Path(cfg["_config"]).parent / "removal-evidence.json"
        if not evidence.is_file() or file_hash(evidence) != step.get("reviewedRemovalDigest"):
            raise Problem("Module-removal impact evidence changed")
    recovery = p.get("backup") or {}
    if not all(
        recovery.get(k) for k in ("code", "database", "files", "owner", "procedure", "verifiedAt")
    ):
        raise Problem("Missing matching code/database/files recovery evidence")
    if approval["recoveryDigest"] != digest(recovery):
        raise Problem("Recovery approval does not match plan")
    if p.get("purpose") == "preparation":
        ev = relative(Path(cfg["_config"]).parent, p.get("preparationEvidence", ""))
        if not ev.is_file() or file_hash(ev) != p.get("preparationHash"):
            raise Problem("Preparation evidence changed")
        proof = read(ev)
        if (
            proof.get("environment") != p["environment"]
            or proof.get("site") != p["site"]
            or not proof.get("reviewer")
            or not proof.get("reviewedAt")
            or proof.get("isolationVerified") is not True
            or not proof.get("baselineFailures")
        ):
            raise Problem("Preparation requires reviewed isolation and recorded baseline failures")
        for ref in proof.get("evidence", []):
            path = relative(ev.parent, ref["path"])
            if not path.is_file() or file_hash(path) != ref["sha256"]:
                raise Problem("Preparation supporting evidence changed")
        if not proof.get("evidence"):
            raise Problem("Preparation requires hashed supporting evidence")
        if any(
            not s.get("preparation")
            or s["stage"] not in ("B-readiness", "C-remediation", "D-composer")
            or s.get("importsConfig")
            for s in p["steps"]
        ):
            raise Problem("Invalid preparation scope")
        return
    ev = Path(cfg["_config"]).parent / p["requirementsEvidence"]
    if not ev.is_file() or file_hash(ev) != p.get("requirementsHash"):
        raise Problem("Release requirements evidence changed")
    if p.get("baselineEvidence"):
        baseline_file = Path(cfg["_config"]).parent / p["baselineEvidence"]
        if not baseline_file.is_file() or file_hash(baseline_file) != p.get("baselineHash"):
            raise Problem("Baseline evidence changed")
        baseline = read(baseline_file)
        schema(baseline, "baseline")
        if baseline.get("environment") != p["environment"] or baseline.get("site") != p["site"]:
            raise Problem("Baseline environment/site mismatch")
        for field in ("codeRevision", "installedExtensions", "activeConfiguration", "publicScenarios"):
            if not baseline.get(field):
                raise Problem("Missing baseline evidence: " + field)
        capture_file = relative(baseline_file.parent, baseline.get("captureSettings", ""))
        if not capture_file.is_file() or read(capture_file).get("stable") is not True:
            raise Problem("Missing stable before-capture evidence")
        if file_hash(capture_file) != baseline.get("captureSettingsHash"):
            raise Problem("Baseline capture settings changed")
    elif not (cfg.get("baselineDeferred") or cfg.get("fast") or p.get("purpose") == "preparation"):
        raise Problem("Missing required baseline evidence")
    requirements = read(ev)
    schema(requirements, "requirements")
    version = re.match(r"^(\d+)\.(\d+)\.(\d+)", p["source"])
    major, min_tuple, min_str = get_source_floor(p.get("target"))
    if (
        not version
        or tuple(map(int, version.groups())) < min_tuple
        or version.group(1) != str(major)
    ):
        raise Problem(f"Source must be a verified Drupal {min_str} or later Drupal {major} release")
    if (
        requirements.get("earlierDatabaseUpdatesComplete") is not True
        or requirements.get("removedCoreExtensionsReviewed") is not True
    ):
        raise Problem("Earlier updates and removed-core-extension review are required")
    if (
        requirements.get("target") != p["target"]
        or not requirements.get("sources")
        or not requirements.get("verifiedAt")
        or requirements.get("readinessPassed") is not True
    ):
        raise Problem("Reviewed target requirements/readiness evidence is incomplete")


def run_check(check, cwd):
    rec = command(check["argv"], cwd, check.get("timeout", 120))
    if (
        rec["exitCode"] == 0
        and check.get("stdoutEquals") is not None
        and rec.get("stdout", "").strip() != check["stdoutEquals"]
    ):
        rec["status"] = "tool_failure"
        rec["assertion"] = "stdout did not equal expected value"
    return rec


@contextlib.contextmanager
def execution_lock(cfg):
    # OS lock lives outside project and output, so alternate output directories cannot bypass it.
    lock_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "promet-d11-locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    key = digest(
        {"root": str(Path(cfg["_root"]).resolve()), "environment": cfg["environment"]["id"]}
    )
    with (lock_dir / (key + ".lock")).open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem("Another execution holds the project/environment lock")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def execute(cfg, p, approval, output, resume=False, preparation=False):
    enforce_budget(cfg)
    if (p.get("purpose") == "preparation") != preparation:
        raise Problem("Execution mode must match approved batch purpose")
    schema(p, "plan")
    prerequisites(cfg, p, approval)
    validate_steps(p["steps"])
    roots, _ = find_roots(cfg)
    out = Path(output)
    state_file = out / "execution.json"
    with execution_lock(cfg):
        identity = check_identity(cfg, roots)
        runtime = runtime_discovery(cfg, roots, wrapper(cfg, roots)["selected"])
        write(out / "execution-runtime.json", runtime)
        if runtime["status"] != "collected":
            raise Problem("Fresh runtime evidence is incomplete; reconcile before execution")
        current_status = runtime["commands"]["status"]["data"]
        if str(current_status.get("bootstrap", "")).lower() != "successful":
            raise Problem("Fresh Drupal bootstrap did not succeed")
        if runtime["commands"]["pendingUpdates"].get("data"):
            raise Problem("Pending database updates require source reconciliation and a new plan")
        if (
            runtime["commands"]["configurationStatus"].get("data")
            and cfg.get("environment", {}).get("kind") not in NONPROD
            and not cfg.get("zeroCopy")
        ):
            raise Problem("Configuration drift requires review and a new plan")
        current = snapshot(cfg, output)
        if resume:
            if not state_file.is_file():
                raise Problem("No execution state to resume")
            run = read(state_file)
            if run["planId"] != p["planId"] or run["checkpoint"] != current:
                raise Problem("Resume state changed; reconcile and replan")
            if any(s["status"] in ("running", "failed") for s in run["steps"]):
                raise Problem(
                    "Interrupted or failed step requires manual reconciliation and a new plan; never retry blindly"
                )
        else:
            if state_file.exists():
                raise Problem("Execution evidence exists; choose a new output or explicit resume")
            if p["inputs"] != current:
                raise Problem("Plan is stale: relevant inputs changed")
            run = {
                "schemaVersion": "1.0",
                "runId": str(uuid.uuid4()),
                "planId": p["planId"],
                "startedAt": now(),
                "environment": p["environment"],
                "site": p["site"],
                "source": p["source"],
                "sourceRevision": p["sourceRevision"],
                "dirtyState": p["dirtyState"],
                "target": p["target"],
                "backup": p["backup"],
                "checkpoint": current,
                "identity": identity,
                "steps": [],
                "status": "running",
            }
        completed = {r["id"] for r in run["steps"] if r["status"] == "passed"}
        run["plannedSteps"] = [{"id": s["id"], "stage": s["stage"]} for s in p["steps"]]
        write(state_file, run)
        for step in p["steps"]:
            if step["id"] in completed:
                continue
            if preparation and not step.get("preparation", False):
                continue
            if not set(step.get("dependsOn", [])).issubset(completed):
                raise Problem("Unmet step dependencies")
            cwd = relative(roots["repository"], step["cwd"])
            if not (cwd == Path(roots["repository"]) or Path(roots["repository"]) in cwd.parents):
                raise Problem("Step working directory escapes project")
            record = {
                "id": step["id"],
                "stage": step["stage"],
                "status": "running",
                "startedAt": now(),
                "preconditions": [],
            }
            run["steps"].append(record)
            write(state_file, run)
            for check in step.get("preconditions", []):
                record["preconditions"].append(run_check(check, cwd))
            if any(r["status"] != "passed" for r in record["preconditions"]):
                record["status"] = "failed"
                run["status"] = "failed"
                write(state_file, run)
                return run, 2
            record["command"] = command(step["argv"], cwd, step.get("timeout", 1800))
            is_uninstall_noop = (
                record["command"]["status"] != "passed"
                and any(cmd in step["argv"] for cmd in ("pmu", "pm-uninstall", "pm:uninstall"))
                and any(
                    msg in (record["command"].get("stderr") or "") + (record["command"].get("stdout") or "")
                    for msg in ("No modules to uninstall", "not installed", "already uninstalled")
                )
            )
            if is_uninstall_noop:
                record["command"]["status"] = "passed"
                record["command"]["executionStatus"] = "passed"
            # Composer exits 0 after falling back from dist to a git clone, but
            # git checkouts lack drupal.org's packaging footer, so Drupal loses
            # the version metadata every constrained dependency is checked
            # against. Catching it here reports the real cause at the moment it
            # happens, instead of surfacing later as unrelated-looking
            # "unresolved dependency" errors in the status report.
            if record["command"]["status"] == "passed" and "composer" in " ".join(step["argv"]):
                fallbacks = composer_source_fallbacks(
                    (record["command"].get("stdout") or "")
                    + "\n"
                    + (record["command"].get("stderr") or "")
                )
                if fallbacks:
                    record["sourceFallbacks"] = fallbacks
                    record["command"]["status"] = "failed"
                    record["command"]["failureCategory"] = "composer_source_fallback"
                    record["command"]["message"] = (
                        "Composer installed from git source after a dist download failure: "
                        + ", ".join(fallbacks)
                        + ". These lack drupal.org packaging metadata (no version in .info.yml), "
                        "which breaks Drupal dependency resolution. Re-run with a working "
                        "connection, or repair with: composer reinstall --prefer-dist "
                        + " ".join(fallbacks)
                    )
            record["postconditions"] = []
            if record["command"]["status"] == "passed":
                record["postconditions"] = [
                    run_check(c, cwd) for c in step.get("postconditions", [])
                ]
            okay = record["command"]["status"] == "passed" and all(
                r["status"] == "passed" for r in record["postconditions"]
            )
            record.update(status="passed" if okay else "failed", finishedAt=now())
            if step.get("mutates", True):
                run["checkpoint"] = snapshot(cfg, output)
            run["status"] = "running" if okay else "failed"
            write(state_file, run)
            if not okay:
                return run, 2
            completed.add(step["id"])
        run.update(status="prepared" if preparation else "passed", finishedAt=now())
        write(state_file, run)
        return run, 0
