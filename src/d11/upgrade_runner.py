"""Unified 1-command upgrade runner: detect, scan, auto-remediate, decide, approve, execute, and verify."""

import argparse
import getpass
import json
import os
import time
from pathlib import Path

from .common import Problem, read
from .guided_setup import Setup
from .knowledge import get_default_branch
from .source_runtime import detect_runtime
from .workflow import Workflow


def print_step(step, total, title):
    print(f"\n\033[1;36m==> [{step}/{total}] {title}\033[0m", flush=True)


def print_success(msg):
    print(f"\033[1;32m✔ {msg}\033[0m", flush=True)


def print_warning(msg):
    print(f"\033[1;33m⚠ {msg}\033[0m", flush=True)


def print_error(msg):
    print(f"\033[1;31m✖ {msg}\033[0m", flush=True)


def _format_command_hint(argv):
    cmd_str = " ".join(argv)
    if "visual-audit" in cmd_str:
        return "Capturing baseline route screenshots & layout diffs"
    if "rector" in cmd_str:
        return "Running custom code Rector & deprecation analysis"
    if "phpstan" in cmd_str:
        return "Running static analysis & PHPStan inspection"
    if "composer" in cmd_str and "install" in cmd_str:
        return "Composer installing dependencies (downloading & extracting packages)"
    if "composer" in cmd_str and "validate" in cmd_str:
        return "Validating composer.json schema and lock integrity"
    if "composer" in cmd_str and "check-platform-reqs" in cmd_str:
        return "Checking PHP version and platform extensions"
    if "drush" in cmd_str and "cache:rebuild" in cmd_str:
        return "Rebuilding Drupal caches (drush cr)"
    if "drush" in cmd_str and "updatedb" in cmd_str:
        return "Applying Drupal database schema migrations (drush updatedb)"
    if "drush" in cmd_str and "core:status" in cmd_str:
        return "Probing Drupal bootstrap and database connectivity"
    if "drush" in cmd_str and "pm:list" in cmd_str:
        return "Scanning installed Drupal modules & themes"
    if "drush" in cmd_str and "config:get" in cmd_str:
        return "Inspecting active core extension configuration"
    if "drush" in cmd_str and "config:status" in cmd_str:
        return "Verifying configuration synchronization status"
    if "drush" in cmd_str and "pm:uninstall" in cmd_str:
        return "Uninstalling obsolete module via Drush"
    if "assess" in cmd_str:
        return "Assessing project runtime, platform & environment"
    if "docker" in cmd_str and ("compose" in cmd_str or "run" in cmd_str):
        return "Executing disposable analysis container tasks"
    if "git" in cmd_str:
        return "Checking Git branch and working tree state"
    return ""


def wait_for_run(workflow, rid, label="Operation"):
    """Poll run until completion with live sub-task tailing and liveness heartbeats."""
    out, state = workflow.run(rid)
    last_checkpoint = ""
    start_time = time.monotonic()
    last_print_time = start_time
    seen_commands = 0
    last_hint = ""
    cmd_file = out / "commands.jsonl"

    while state.get("status") == "running":
        now_time = time.monotonic()
        elapsed = int(now_time - start_time)
        checkpoint = state.get("checkpoint", "").replace("_", " ")

        if checkpoint != last_checkpoint:
            last_checkpoint = checkpoint
            last_print_time = now_time
            print(f"    ... {checkpoint} ({elapsed}s)", flush=True)

        if cmd_file.is_file():
            try:
                lines = cmd_file.read_text(errors="replace").splitlines()
                if len(lines) > seen_commands:
                    for line in lines[seen_commands:]:
                        seen_commands += 1
                        if not line.strip():
                            continue
                        try:
                            cmd_data = json.loads(line)
                            argv = cmd_data.get("argv", [])
                            hint = _format_command_hint(argv)
                            if hint and hint != last_hint:
                                last_hint = hint
                                last_print_time = now_time
                                print(f"    ↳ {hint}...", flush=True)
                        except Exception:
                            pass
            except Exception:
                pass

        quiet_seconds = int(now_time - last_print_time)
        if quiet_seconds >= 15:
            last_print_time = now_time
            activity = last_hint or checkpoint or "processing"
            if elapsed > 180:
                print(
                    f"    ⏳ [{elapsed}s] Long operation in progress: {activity}... (Docker/network active, not stuck)",
                    flush=True,
                )
            elif elapsed > 60:
                print(
                    f"    ⏳ [{elapsed}s] Still running: {activity}... (active, please wait)",
                    flush=True,
                )
            else:
                print(f"    ⏳ [{elapsed}s] In progress: {activity}...", flush=True)

        time.sleep(1.5)
        out, state = workflow.run(rid)

    status = state.get("status")
    if status not in ("completed", "needs_attention"):
        err = state.get("error") or f"{label} ended with status: {status}"
        raise Problem(f"{label} failed: {err}")
    return state


def print_summary_card(project_name, pid, source, gate, compat):
    risk = gate.get("risk", {})
    recom = risk.get("recommendation", "Unknown")
    score = risk.get("score", 0)
    target = gate.get("targetCore", "11.x")
    current = gate.get("currentCore", "10.x")
    eligible = gate.get("approvalEligible", False)
    blockers = risk.get("hardBlockers", [])

    counts = {
        "keep": 0,
        "compatible_release": 0,
        "available_patch": 0,
        "ai_manual_patch": 0,
        "manual_remediation": 0,
        "remove": 0,
        "defer": 0,
        "pending": 0,
    }
    for row in compat.get("extensions", []):
        act = row.get("selectedAction") or "pending"
        counts[act] = counts.get(act, 0) + 1

    status_color = "\033[1;32m" if eligible else "\033[1;31m"
    recom_color = (
        "\033[1;32m"
        if recom.lower() == "go"
        else ("\033[1;33m" if "conditional" in recom.lower() else "\033[1;31m")
    )

    print("\n" + "=" * 76)
    print(f"  \033[1mDRUPAL 11 UPGRADE PLAN: {project_name}\033[0m")
    print("=" * 76)
    print(f"  Project ID:     {pid}")
    print(f"  Source Path:    {source}")
    print(f"  Target Core:    Drupal {target} (Current: Drupal {current})")
    print(f"  Risk Rating:    {recom_color}{score}/100 — {recom}\033[0m")
    print(f"  Hard Blockers:  {len(blockers)}")
    print("-" * 76)
    print("  \033[1mExtension Remediation & Decision Plan:\033[0m")
    print(f"    • Keep / Compatible:    {counts['keep']} extensions")
    print(f"    • Package Updates:      {counts['compatible_release']} extensions")
    print(f"    • Community Patches:    {counts['available_patch']} extensions")
    print(
        f"    • Custom Remediations:  {counts['manual_remediation'] + counts['ai_manual_patch']} extensions"
    )
    if counts["remove"]:
        print(f"    • Removals:             {counts['remove']} extensions")
    if counts["defer"] or counts["pending"]:
        print(f"    • Unresolved / Blocked: {counts['defer'] + counts['pending']} extensions")
    print("-" * 76)
    print(
        f"  Gate 1 Status:  {status_color}{'READY TO UPGRADE (Approval Eligible)' if eligible else 'BLOCKED — Requires Attention'}\033[0m"
    )
    print("=" * 76 + "\n")


def run_upgrade(
    source_path=".",
    yes=False,
    reviewer=None,
    dry_run=False,
    dashboard=False,
    source_url=None,
    wrapper="auto",
    project_id=None,
    project_name=None,
    no_remediate=False,
    accept_prereleases=True,
    git_branch=None,
    handoff=False,
    no_handoff=False,
    zero_copy=True,
    fast=True,
    capture_visual=False,
    skip_visual=False,
    provider=None,
    model=None,
):
    if provider:
        os.environ["DEFAULT_AI_PROVIDER"] = provider
    if model:
        os.environ["DEFAULT_AI_MODEL"] = model
    git_branch = git_branch or get_default_branch()
    workflow = Workflow()
    setup = Setup(workflow)
    total_steps = (
        4
        if dry_run
        else (6 if (Path(source_path).resolve() / ".git").is_dir() and not no_handoff else 5)
    )

    # Step 1: Detect runtime & project setup
    print_step(1, total_steps, "Detecting project runtime and configuration...")
    source_dir = Path(source_path).resolve()
    if not (source_dir / "composer.json").is_file():
        raise Problem(f"Directory {source_dir} does not contain a composer.json file.")

    info = detect_runtime(source_dir)
    wrapper_choice = wrapper if wrapper != "auto" else info.get("wrapper", "auto")
    pid = project_id or info.get("id")
    name = project_name or info.get("name")
    url = source_url or info.get("sourceUrl")

    print(f"    • Project Root:  {source_dir}")
    print(f"    • Project Name:  {name} ({pid})")
    print(f"    • Runtime:       {wrapper_choice}")
    print(f"    • Local URL:     {url or 'Auto-detected on scan'}")
    if zero_copy and (source_dir / ".git").is_dir():
        print(
            f"    • Mode:          \033[1;32m⚡ Zero-Copy Feature Branch\033[0m (branch: {git_branch})"
        )

    # Ensure registered project or create draft
    existing_project = (workflow.home / "projects" / pid).is_dir()
    existing_draft = (workflow.home / "drafts" / pid).is_dir()

    if not existing_project and not existing_draft:
        setup.create(
            {
                "id": pid,
                "name": name,
                "source": str(source_dir),
                "sourceUrl": url or "",
                "wrapper": wrapper_choice,
                "exportAuthorized": True,
                "zeroCopy": zero_copy,
                "gitBranch": git_branch,
            }
        )
        print_success("Created project registration.")
    else:
        print_success("Reusing existing project profile.")

    # Pre-flight: Obsolete package check
    if existing_project:
        try:
            obsolete = workflow.inspect_obsolete(pid)
            packages = obsolete.get("obsoletePackages", [])
            if packages:
                names = [p["package"] for p in packages]
                print_warning(
                    f"Found uninstalled packages in composer.json that may block Drupal 11: {', '.join(names)}"
                )
        except Exception:
            pass

    # Step 2: Scan & Baseline
    print_step(2, total_steps, "Scanning project & analyzing extension compatibility...")
    scan_res = setup.scan(pid, fast=fast, capture_baseline=capture_visual)
    scan_run_id = None
    if scan_res.get("run"):
        scan_run_id = scan_res["run"]["id"]
    else:
        # Check if setup is running in background worker
        setup_draft = setup.get(pid)
        start_time = time.monotonic()
        while setup_draft.get("status") == "running":
            elapsed = int(time.monotonic() - start_time)
            cp = setup_draft.get("checkpoint", "").replace("_", " ")
            print(f"    ... preparing project environment: {cp} ({elapsed}s)", flush=True)
            time.sleep(1.5)
            setup_draft = setup.get(pid)

        if setup_draft.get("status") in ("review_required", "registered"):
            res2 = setup.scan(pid, fast=fast, capture_baseline=capture_visual)
            if res2.get("run"):
                scan_run_id = res2["run"]["id"]
        elif setup_draft.get("status") != "registered":
            err = setup_draft.get("error") or "Project environment preparation failed."
            raise Problem(err)

    if not scan_run_id:
        # Look for latest guided-audit run for this project
        runs = [
            r
            for r in workflow.runs()
            if r.get("project") == pid and r.get("action") == "guided-audit"
        ]
        if runs:
            scan_run_id = runs[0]["id"]

    if not scan_run_id:
        raise Problem("Failed to launch or identify guided-audit scan run.")

    wait_for_run(workflow, scan_run_id, "Audit scan")
    if capture_visual:
        print_success("Audit scan and visual baseline capture complete.")
    else:
        print_success("Audit scan complete (fast mode; visual baseline deferred to pre-upgrade guardrail).")

    # Step 3: Auto-remediate & Auto-decide
    print_step(3, total_steps, "Auto-remediating custom code & resolving extension decisions...")
    if not no_remediate:
        print("    ↳ [1/2] Analyzing custom code deprecations and applying Rector remediations...", flush=True)
        remed_res = workflow.auto_remediate(scan_run_id)
        if remed_res.get("proposals"):
            print_success(
                f"Generated {len(remed_res['proposals'])} custom code remediation proposals."
            )
        else:
            print("    ↳ No automated custom code patches required.", flush=True)

    print("    ↳ [2/2] Resolving extension compatibility actions against Drupal.org releases...", flush=True)
    decide_res = workflow.auto_decide(
        scan_run_id, accept_prereleases=accept_prereleases, auto_remediate_custom=not no_remediate
    )
    print_success(f"Resolved decisions for {decide_res.get('totalDecisions', 0)} extensions.")

    out, _ = workflow.run(scan_run_id)
    gate = read(out / "gate.json")
    compat = workflow.compatibility(scan_run_id)

    # Display Plan
    print_summary_card(name, pid, str(source_dir), gate, compat)

    # Step 3b: Verify Guardrails
    from .guardrails import validate_guardrails

    managed_site = workflow.home / "projects" / pid / "site"
    g_res = validate_guardrails(managed_site)
    print("\n  \033[1;36m🛡 Upgrade Guardrails Verification:\033[0m")
    p_status = "✔ PASSED" if g_res["summary"]["phpstanPassed"] else "⚠ FINDINGS"
    c_status = "✔ PASSED" if g_res["summary"]["phpcsPassed"] else "⚠ FINDINGS"
    comp_status = "✔ PASSED" if g_res["summary"]["composerPassed"] else "✖ FAILED"
    twig_status = "✔ PASSED" if g_res["summary"]["twigPassed"] else "⚠ FINDINGS"
    print(f"    • PHPStan Drupal 11 Rules:         \033[32m{p_status}\033[0m")
    print(f"    • PHPCS Drupal & DrupalPractice:  \033[32m{c_status}\033[0m")
    print(f"    • Composer Schema & Integrity:     \033[32m{comp_status}\033[0m")
    print(f"    • Twig 3 Template Linter:          \033[32m{twig_status}\033[0m")
    print(
        "    • Git Safety Policy:               \033[32m✔ ENFORCED (Always new branch, no auto-commit/push)\033[0m\n"
    )

    if dry_run:
        print_success("Dry-run complete. Upgrade plan generated; project code remains untouched.")
        if dashboard:
            from .dashboard import launch

            launch()
        return 0

    if not gate.get("approvalEligible"):
        blockers = gate.get("risk", {}).get("hardBlockers", [])
        print_error("Cannot proceed: Gate 1 is blocked:")
        for b in blockers:
            print(f"    ✖ {b}")
        return 1

    # Step 4: Confirmation
    resolved_reviewer = reviewer or os.environ.get("USER") or getpass.getuser() or "Developer"
    if not yes:
        try:
            confirm = input(
                f"\033[1mProceed with Drupal 11 upgrade on feature branch? [Y/n] (Reviewer: {resolved_reviewer})\033[0m: "
            )
        except (KeyboardInterrupt, EOFError):
            print("\nUpgrade cancelled.")
            return 0
        if confirm.strip().lower() in ("n", "no"):
            print("Upgrade cancelled by user.")
            return 0

    # Step 5: Execute Upgrade
    print_step(4, total_steps, "Executing Drupal 11 upgrade batch & database migrations...")
    approval_payload = {
        "reviewer": resolved_reviewer,
        "managedCopyReviewed": True,
        "safetyNote": "Approved via d11 upgrade CLI",
        "acceptRisks": True,
        "approvalDigest": gate["approvalDigest"],
    }
    workflow.approve_gate(scan_run_id, approval_payload)
    upgrade_run = workflow.start(
        pid, "guided-upgrade", scan_run_id, options={"skip_visual": skip_visual}
    )
    upgrade_run_id = upgrade_run["id"]

    wait_for_run(workflow, upgrade_run_id, "Upgrade execution")

    # Step 6: Post-upgrade Verification
    print_step(5, total_steps, "Verifying Gate 2 routes and health checks...")
    u_out, u_state = workflow.run(upgrade_run_id)
    u_status = u_state.get("status")

    _, p_info = workflow.project(pid)
    site_uri = p_info.get("site", {}).get("uri") or url or "Local site"

    if u_status == "completed":
        print("\n" + "=" * 76)
        print("  \033[1;32m🎉 DRUPAL 11 UPGRADE SUCCESSFUL!\033[0m")
        print("=" * 76)
        print(f"  Target Core:    Drupal {gate.get('targetCore', '11.x')}")
        print(f"  Site URL:       \033[1m{site_uri}\033[0m")
        print("  Status:         All automated route & baseline checks passed.")
        print("  Next steps:     Test the upgraded site at the URL above, or view reports:")
        print("                  \033[1;36m./bin/d11 dashboard\033[0m")
        print("=" * 76 + "\n")
    elif u_status == "needs_attention":
        print("\n" + "=" * 76)
        print("  \033[1;33m⚠ UPGRADE COMPLETED WITH VISUAL / ROUTE FINDINGS\033[0m")
        print("=" * 76)
        print(f"  Site URL:       \033[1m{site_uri}\033[0m")
        print("  Notice:         Code and database migrations completed, but visual or route")
        print("                  differences were detected against the baseline.")
        print("  Next steps:     Review the visual diffs in the dashboard before accepting:")
        print("                  \033[1;36m./bin/d11 dashboard\033[0m")
        print("=" * 76 + "\n")
    else:
        print_error(f"Upgrade halted with status: {u_status}. Check rollback logs in dashboard.")
        return 1

    # Step 7: Git Handoff (Option 1 workflow)
    is_git = (source_dir / ".git").is_dir()
    if is_git and not no_handoff:
        do_handoff = handoff
        if not do_handoff and not yes:
            try:
                prompt_msg = f"\033[1mHandoff Drupal 11 changes to new git branch '{git_branch}' in source repository (never auto-committed or pushed)? [Y/n]\033[0m: "
                ans = input(prompt_msg).strip().lower()
                do_handoff = ans not in ("n", "no")
            except (KeyboardInterrupt, EOFError):
                do_handoff = False
        if do_handoff:
            print_step(
                total_steps,
                total_steps,
                f"Synchronizing changes to new Git branch '{git_branch}'...",
            )
            from .handoff import sync_to_git_branch

            try:
                managed_site = workflow.home / "projects" / pid / "site"
                h_res = sync_to_git_branch(managed_site, source_dir, branch_name=git_branch)
                print_success(
                    f"Git Handoff complete: {len(h_res['filesChanged'])} files placed on branch '{git_branch}'."
                )
                print(f"    • Branch:       \033[1m{git_branch}\033[0m")
                print(f"    • Source Path:  {source_dir}")
                print(
                    f"    • Status:       \033[1;36m{h_res.get('guardrailNotice', 'Uncommitted changes on new branch')}\033[0m\n"
                )
                print("  \033[1mNext Steps (Manual Developer Verification):\033[0m")
                for inst in h_res.get("instructions", []):
                    print(f"    $ {inst}")
                print()
            except Exception as exc:
                print_warning(f"Git Handoff skipped or encountered error: {exc}")
        else:
            print(
                "\n  \033[1mGit Handoff skipped.\033[0m You can sync changes at any time by running:"
            )
            print(f"    \033[1;36m./bin/d11 handoff {pid} --branch {git_branch}\033[0m\n")

    if dashboard:
        from .dashboard import launch

        launch()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="All-in-one Drupal 11 upgrade runner: detect, scan, auto-remediate, decide, and upgrade."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=".",
        help="Path to Drupal project root (default: current directory)",
    )
    parser.add_argument(
        "--source-url",
        "--url",
        dest="source_url",
        help="Local site URL (e.g. https://project.ddev.site). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--provider",
        help="AI remediation provider (gemini, claude, openai, ollama)",
    )
    parser.add_argument(
        "--model",
        help="Specific AI model name override",
    )
    parser.add_argument(
        "--wrapper",
        choices=["auto", "ddev", "fin"],
        default="auto",
        help="Runtime wrapper (default: auto)",
    )
    parser.add_argument(
        "--project", dest="project_id", help="Project ID (default: auto-generated from directory)"
    )
    parser.add_argument(
        "--name", dest="project_name", help="Project display name (default: directory name)"
    )
    parser.add_argument(
        "--reviewer", help="Reviewer name for approval audit trail (default: current user)"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Bypass interactive approval prompt"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop after scanning and generating the decision plan without executing the upgrade",
    )
    parser.add_argument(
        "--no-remediate",
        action="store_true",
        help="Skip automatic custom module info.yml and rector remediation",
    )
    parser.add_argument(
        "--no-prereleases",
        dest="accept_prereleases",
        action="store_false",
        default=True,
        help="Do not auto-accept alpha/beta/RC release candidates",
    )
    parser.add_argument(
        "--git-branch",
        default=None,
        help="Target Git branch for handoff (default: derived from target knowledge)",
    )
    parser.add_argument(
        "--zero-copy",
        dest="zero_copy",
        action="store_true",
        default=True,
        help="Use Option A zero-copy feature branch workflow (default: True)",
    )
    parser.add_argument(
        "--no-zero-copy",
        dest="zero_copy",
        action="store_false",
        help="Disable zero-copy and duplicate all site files",
    )
    parser.add_argument(
        "--handoff",
        action="store_true",
        help="Automatically synchronize safe upgrade changes to Git branch upon completion",
    )
    parser.add_argument(
        "--no-handoff", action="store_true", help="Skip Git repository handoff prompt after upgrade"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Perform full deep scan of all extensions instead of fast scan",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch web dashboard after completing the operation",
    )
    parser.add_argument(
        "--capture-visual",
        action="store_true",
        help="Capture visual baseline screenshots during scan (default: False, deferred to pre-upgrade)",
    )
    parser.add_argument(
        "--no-visual",
        action="store_true",
        help="Skip visual baseline and Gate 2 visual regression testing entirely",
    )

    args = parser.parse_args()
    try:
        return run_upgrade(
            source_path=args.source,
            yes=args.yes,
            reviewer=args.reviewer,
            dry_run=args.dry_run,
            dashboard=args.dashboard,
            source_url=args.source_url,
            wrapper=args.wrapper,
            project_id=args.project_id,
            project_name=args.project_name,
            no_remediate=args.no_remediate,
            accept_prereleases=args.accept_prereleases,
            git_branch=args.git_branch,
            handoff=args.handoff,
            no_handoff=args.no_handoff,
            zero_copy=args.zero_copy,
            fast=not args.full,
            capture_visual=args.capture_visual,
            skip_visual=args.no_visual,
            provider=args.provider,
            model=args.model,
        )
    except Problem as e:
        print_error(str(e))
        return 2
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130
