#!/usr/bin/env python3
import argparse
import json
import sys
import uuid
from pathlib import Path

from .common import (
    ROOT,
    Problem,
    command,
    digest,
    get_d11_home,
    load_config,
    now,
    read,
    redact,
    redact_tree,
    relative,
    require_nonprod,
    set_d11_home,
    write,
)


def main():
    # Extract global --home flag if present
    for i, arg in enumerate(list(sys.argv[1:]), start=1):
        if arg == "--home" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1]
            set_d11_home(val)
            sys.argv.pop(i + 1)
            sys.argv.pop(i)
            break
        elif arg.startswith("--home="):
            val = arg.split("=", 1)[1]
            set_d11_home(val)
            sys.argv.pop(i)
            break

    if len(sys.argv) <= 1 or sys.argv[1] in ("-h", "--help", "help"):
        print("""\033[1;36mDrupal 11 Upgrade Toolkit\033[0m (v2.0.0)
Promet Drupal 10-to-11 automated assessment, remediation, and safe execution.

\033[1;33mGlobal Options:\033[0m
  --home <PATH>       Set D11 home directory (default: ~/.d11, env: D11_HOME)

\033[1;33mPrimary Commands:\033[0m
  \033[1mupgrade\033[0m     Autonomous end-to-end Drupal 11 upgrade rehearsal
              Usage: \033[32mbin/d11 upgrade [PATH] [-y] [--dry-run] [--handoff]\033[0m
  \033[1mbaseline\033[0m    Capture or refresh visual baseline routes on-demand
              Usage: \033[32mbin/d11 baseline [PATH_OR_PROJECT_ID]\033[0m
  \033[1mdashboard\033[0m   Launch the local web dashboard and visual evidence viewer
              Usage: \033[32mbin/d11 dashboard [--port PORT]\033[0m
  \033[1mhandoff\033[0m     Synchronize verified Drupal 11 changes to a Git branch
              Usage: \033[32mbin/d11 handoff <SOURCE_PATH> <RUN_DIR> [--branch BRANCH]\033[0m
  \033[1mguardrails\033[0m    Validate PHPStan, PHPCS, Drupal standards, & Git safety
              Usage: \033[32mbin/d11 guardrails [PATH]\033[0m
  \033[1mproviders\033[0m   Inspect configured AI models and connectivity status
              Usage: \033[32mbin/d11 providers\033[0m
  \033[1mdoctor\033[0m      Verify local environment, tools, and prerequisites
              Usage: \033[32mbin/d11 doctor [--json]\033[0m
  \033[1mprune\033[0m       Prune older runs and deduplicate storage in D11_HOME
              Usage: \033[32mbin/d11 prune [--keep N]\033[0m
  \033[1minit\033[0m        Probe Drupal project directory and generate project.json
              Usage: \033[32mbin/d11 init [PATH] [-y] [--output FILE]\033[0m
  \033[1mworkflow\033[0m    Run low-level guided workflow pipeline
              Usage: \033[32mbin/d11 workflow <action>\033[0m

\033[1;33mLow-level Pipeline Commands:\033[0m
  discover, assess, plan, prepare, execute, verify, report
  Run with \033[32m--config <file> --output <dir>\033[0m.
""")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from .init_project import init_cli

        sys.argv.pop(1)
        return init_cli()
    if len(sys.argv) > 1 and sys.argv[1] == "prune":
        from .workflow import Workflow

        prune_parser = argparse.ArgumentParser(
            prog="d11 prune", description="Prune older runs and deduplicate storage"
        )
        prune_parser.add_argument(
            "--keep", type=int, default=5, help="Number of recent runs to keep (default: 5)"
        )
        prune_args = prune_parser.parse_args(sys.argv[2:])
        try:
            w = Workflow()
            res = w.prune(keep=prune_args.keep)
            print(
                f"\033[1;32m✔ Pruned {res['prunedCount']} old run(s); retained {res['retainedCount']}.\033[0m"
            )
            if res.get("deduplication", {}).get("deduplicated", 0) > 0:
                print(
                    f"  • Deduplicated {res['deduplication']['deduplicated']} compatibility report(s) in workbench storage."
                )
            return 0
        except Exception as e:
            print(f"\033[1;31m✖ Prune failed: {e}\033[0m", file=sys.stderr)
            return 1
    if len(sys.argv) > 1 and sys.argv[1] == "workflow":
        from .workflow_cli import main as workflow_main

        sys.argv.pop(1)
        return workflow_main()
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        from .dashboard import launch

        sys.argv.pop(1)
        launch()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "upgrade":
        from .upgrade_runner import main as upgrade_main

        sys.argv.pop(1)
        return upgrade_main()
    if len(sys.argv) > 1 and sys.argv[1] in ("baseline", "capture-baseline"):
        from .guided_setup import Setup
        from .workflow import Workflow

        target = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "."
        target_path = Path(target).resolve()
        w = Workflow()
        setup = Setup(w)
        pid = None
        for p in w.projects():
            if p.get("id") == target or str(Path(p.get("source", "")).resolve()) == str(target_path):
                pid = p["id"]
                break
        if not pid:
            from .source_runtime import detect_runtime

            info = detect_runtime(target_path)
            pid = info.get("id")
        if not pid:
            print("Usage: bin/d11 baseline <PROJECT_ID_OR_PATH>")
            return 1
        try:
            print(f"📸 Capturing visual baseline routes for project '{pid}'...")
            res = setup.baseline(pid)
            print(
                f"\033[1;32m✔ Visual baseline captured successfully ({res.get('routeCount', 0)} route screenshots).\033[0m"
            )
            return 0
        except Exception as e:
            print(f"\033[1;31m✖ Error capturing baseline: {e}\033[0m", file=sys.stderr)
            return 1
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        from .upgrade_runner import main as upgrade_main

        sys.argv.pop(1)
        if "--dry-run" not in sys.argv:
            sys.argv.append("--dry-run")
        return upgrade_main()
    if len(sys.argv) > 1 and sys.argv[1] in ("auto-decide", "decide"):
        from .auto_decide import auto_decide
        from .workflow import Workflow

        target = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "."
        target_path = Path(target).resolve()
        w = Workflow()
        pid = None
        for p in w.projects():
            if p.get("id") == target or str(Path(p.get("source", "")).resolve()) == str(target_path):
                pid = p["id"]
                break
        if not pid:
            from .source_runtime import detect_runtime

            info = detect_runtime(target_path)
            pid = info.get("id")
        if not pid:
            print("Usage: bin/d11 auto-decide <PROJECT_ID_OR_PATH>")
            return 1
        runs = [
            r for r in w.runs()
            if r.get("project") == pid and r.get("action") == "guided-audit" and r.get("status") == "completed"
        ]
        if not runs:
            print(f"\033[1;31m✖ No completed audit found for project '{pid}'. Run 'bin/d11 audit' first.\033[0m", file=sys.stderr)
            return 1
        latest_run = runs[0]["id"]
        try:
            print(f"Resolving automated compatibility decisions for project '{pid}' (run: {latest_run})...")
            res = auto_decide(w, latest_run)
            print(f"\033[1;32m✔ Compatibility decisions updated successfully.\033[0m")
            print(f"  Total Decisions: {res.get('totalDecisions', 0)}")
            print(f"  Breakdown:       {res.get('breakdown', {})}")
            return 0
        except Exception as e:
            print(f"\033[1;31m✖ Error running auto-decide: {e}\033[0m", file=sys.stderr)
            return 1
    if len(sys.argv) > 1 and sys.argv[1] == "handoff":
        from .handoff import handoff_cli

        sys.argv.pop(1)
        return handoff_cli()
    if len(sys.argv) > 1 and sys.argv[1] in ("delete", "remove"):
        from .workflow import Workflow

        if len(sys.argv) < 3:
            print("Usage: bin/d11 delete <PROJECT_ID>")
            return 1
        pid = sys.argv[2]
        try:
            res = Workflow().delete_project(pid)
            print(f"\n\033[1;32m✔ {res['message']}\033[0m\n")
            return 0
        except Exception as e:
            print(f"\n\033[1;31m✖ Error: {e}\033[0m\n", file=sys.stderr)
            return 1
    if len(sys.argv) > 1 and sys.argv[1] in ("guardrails", "standards"):
        from .guardrails import validate_guardrails

        target = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "."
        target_path = Path(target).resolve()
        res = validate_guardrails(target_path)
        pass_str = (
            "\033[1;32mPASSED\033[0m" if res["passed"] else "\033[1;33mFINDINGS / ATTENTION\033[0m"
        )
        phpstan_str = (
            "\033[32m✔ Passed\033[0m"
            if res["summary"]["phpstanPassed"]
            else "\033[33m⚠ Findings\033[0m"
        )
        phpcs_str = (
            "\033[32m✔ Passed\033[0m"
            if res["summary"]["phpcsPassed"]
            else "\033[33m⚠ Findings\033[0m"
        )
        comp_str = (
            "\033[32m✔ Passed\033[0m"
            if res["summary"]["composerPassed"]
            else "\033[31m✖ Failed\033[0m"
        )
        twig_str = (
            "\033[32m✔ Passed\033[0m"
            if res["summary"]["twigPassed"]
            else "\033[33m⚠ Findings\033[0m"
        )
        git_str = (
            "\033[32m✔ Enforced\033[0m"
            if res["summary"]["gitPolicyPassed"]
            else "\033[31m✖ Blocked\033[0m"
        )
        print(f"\n\033[1;36m=== Drupal 11 Upgrade Guardrails Report: {target_path.name} ===\033[0m")
        print(f"  Overall Status:  {pass_str}")
        print(f"  Violations:      {res['summary']['totalViolations']}")
        print(f"  • PHPStan Drupal Rules:         {phpstan_str}")
        print(f"  • PHPCS Drupal & Practice:     {phpcs_str}")
        print(f"  • Composer Schema & Security:   {comp_str}")
        print(f"  • Twig 3 Template Linter:       {twig_str}")
        print(f"  • Git Policy (No Auto-Commit):  {git_str}\n")
        return 0 if res["passed"] else 1
    if len(sys.argv) > 1 and sys.argv[1] == "providers":
        from .ai_providers import inventory

        print("\033[1;36m=== Configured AI Providers & Models ===\033[0m")
        for p in inventory():
            icon = (
                "\033[1;32m✔\033[0m"
                if p["available"] and p["safeInterface"]
                else "\033[1;31m✖\033[0m"
            )
            print(f"  {icon} {p['name']} ({p['id']}) - {p['mode'].upper()} - {p['message']}")
            m = p.get("model") or p.get("version") or "N/A"
            print(f"      Model / Info: {m}")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("doctor", "check"):
        from .doctor import doctor_cli

        sys.argv.pop(1)
        return doctor_cli()
    if len(sys.argv) > 1 and sys.argv[1] in ("version", "--version", "-v"):
        print("Drupal 11 Upgrade Toolkit v2.0.0")
        return 0
    parser = argparse.ArgumentParser(
        description="Promet Drupal 11 evidence and controlled upgrade toolkit"
    )
    parser.add_argument(
        "command", choices=["discover", "assess", "plan", "prepare", "execute", "verify", "report"]
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass static discovery cache; runtime evidence is always fresh",
    )
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument(
        "--preparation-plan",
        action="store_true",
        help="Plan bounded preparation with reviewed isolation and baseline-failure evidence",
    )
    parser.add_argument(
        "--package", action="store_true", help="Package sanitized audience reports and evidence"
    )
    parser.add_argument("--plan-file")
    parser.add_argument("--approval-file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Run only steps already authorized by the matching approval file",
    )
    parser.add_argument("--home", help="Override D11 home directory (default: ~/.d11)")
    try:
        args = parser.parse_args()
    except SystemExit as e:
        return 64 if e.code else 0
    from .discovery import config_diff, discover
    from .execution import execute, plan
    from .reporting import checks, exit_code, findings, package_reports, reports, resolve_compatibility
    out = Path(args.output).resolve()
    cfg = None
    try:
        cfg = load_config(args.config)
        if out == Path(cfg["_root"]) or out in Path(cfg["_root"]).parents:
            raise Problem("Output cannot be project root or an ancestor", 64)
        if args.command == "report":
            reports(out, read(out / "result.json"), cfg)
            if args.package:
                package_reports(out)
            return exit_code(read(out / "result.json").get("checks", []))
        if args.command in ("prepare", "execute"):
            if not args.plan_file or not args.approval_file:
                raise Problem("Execution requires --plan-file and --approval-file")
            run, code = execute(
                cfg,
                read(args.plan_file),
                read(args.approval_file),
                out,
                args.resume,
                args.command == "prepare",
            )
            result = {
                **run,
                "checks": [{"id": s["id"], "status": s["status"]} for s in run["steps"]],
            }
            reports(out, result, cfg)
            if code or args.package:
                try:
                    package_reports(out)
                except OSError as e:
                    print("Report packaging failed: " + str(e), file=sys.stderr)
            return code
        context = discover(
            cfg, args.runtime, args.refresh, get_d11_home() / "cache" / digest(cfg["_root"])
        )
        write(out / "context.json", redact_tree(context))
        items = findings(context)
        if args.command == "verify":
            commands = context["runtime"]["commands"]
            status = commands.get("status", {})
            bootstrap = status.get("data", {}).get("bootstrap")
            items = [
                {
                    "id": "bootstrap",
                    "status": "passed"
                    if str(bootstrap).lower() == "successful"
                    else "unknown"
                    if not bootstrap
                    else "findings",
                    "message": "Drupal bootstrap in the selected environment",
                }
            ]
            updates = commands.get("pendingUpdates", {})
            items.append(
                {
                    "id": "pending_updates",
                    "status": "unknown"
                    if updates.get("status") != "passed"
                    else "findings"
                    if updates.get("data")
                    else "passed",
                    "message": "Pending database updates",
                }
            )
            if not cfg.get("checks") and not cfg.get("visual"):
                items.append(
                    {
                        "id": "project_verification",
                        "status": "unknown",
                        "message": "No project-specific functional/build/log checks or visual suite configured",
                    }
                )
        if cfg.get("configComparison"):
            paths = cfg["configComparison"]
            changes = config_diff(
                relative(Path(cfg["_config"]).parent, paths["before"]),
                relative(Path(cfg["_config"]).parent, paths["after"]),
            )
            write(out / "config-diff.json", changes)
            items.append(
                {
                    "id": "configuration_changes",
                    "status": "findings" if changes else "passed",
                    "message": f"{len(changes)} configuration changes require review",
                }
            )
        if args.command == "plan":
            p = plan(cfg, context, out, preparation=args.preparation_plan)
            write(out / "plan.json", p)
            lines = [
                "# Proposed upgrade plan",
                "",
                f"Plan ID: `{p['planId']}`",
                "",
                f"Source: {p['source']}; target: {p['target']}; revision: {p['sourceRevision'] or 'unavailable'}.",
                f"Environment: {p['environment']['id']} ({p['environment']['kind']}); site: {json.dumps(p['site'])}.",
                "",
                "## Blockers",
            ]
            lines += ["- " + x for x in p["blockers"]] or [
                "No structural blockers recorded; inspect the evidence and exact scope before approval."
            ]
            lines += ["", "## Proposed changes"] + ["- " + x for x in p["changes"]]
            lines += ["", "## Commands"]
            for step in p["steps"]:
                lines += [
                    "",
                    f"### {step['id']} — {step['stage']}",
                    "",
                    step["description"],
                    f"Working directory: {str(relative(p['roots']['repository'], step['cwd']))}",
                    "```json",
                    json.dumps(step, indent=2),
                    "```",
                ]
            lines += [
                "",
                "## Recovery references",
                "```json",
                json.dumps(p["backup"], indent=2),
                "```",
                "",
                "## Verification and uncertainties",
            ] + ["- " + x for x in p["uncertainties"]]
            lines += [
                "",
                "Requirements and baseline evidence hashes, dirty-file state and all input hashes are in plan.json. This document does not authorize execution or Production changes.",
            ]
            (out / "plan.md").write_text("\n".join(lines) + "\n")
            write(
                out / "approval.template.json",
                {
                    "schemaVersion": "1.0",
                    "planId": p["planId"],
                    "environment": p["environment"],
                    "site": p["site"],
                    "approver": "",
                    "approvedAt": "",
                    "steps": [s["id"] for s in p["steps"]],
                    "recoveryDigest": digest(p["backup"]),
                },
            )
        if args.command in ("assess", "verify"):
            if args.runtime:
                items.extend(checks(cfg, context))
            else:
                items.append(
                    {
                        "id": "configured_runtime_checks",
                        "status": "unknown",
                        "message": "Use --runtime in the authorized environment to run configured checks",
                    }
                )
        if args.command == "verify" and cfg.get("visual"):
            if not args.runtime:
                items.append(
                    {
                        "id": "visual",
                        "status": "unknown",
                        "message": "Visual verification requires --runtime authorization",
                    }
                )
            else:
                require_nonprod(cfg)
                visual = cfg["visual"]
                base = Path(cfg["_config"]).parent
                rec = command(
                    [
                        str(ROOT / "bin/visual-audit"),
                        "test",
                        "--config",
                        str(relative(base, visual["config"])),
                        "--output",
                        str(relative(base, visual["output"])),
                    ],
                    ROOT,
                    1800,
                )
                items.append(
                    {
                        "id": "visual",
                        "status": {0: "passed", 1: "findings", 2: "tool_failure", 3: "blocked"}.get(
                            rec["exitCode"], "tool_failure"
                        ),
                        "command": rec,
                    }
                )
        items = resolve_compatibility(items, cfg)
        code = exit_code(items)
        metrics = context["metrics"].copy()
        configured = [i["command"] for i in items if "command" in i]
        metrics["subprocessCount"] += len(configured)
        metrics["configuredChecksElapsedSeconds"] = sum(
            r.get("elapsedSeconds", 0) for r in configured
        )
        for item in items:
            if item["status"] != "passed":
                category = item.get("command", {}).get("failureCategory") or item["status"]
                metrics["failureCategories"][category] = (
                    metrics["failureCategories"].get(category, 0) + 1
                )
        result = {
            "schemaVersion": "1.0",
            "runId": str(uuid.uuid4()),
            "timestamp": now(),
            "mode": args.command,
            "status": "passed"
            if code == 0
            else "findings"
            if code == 1
            else "tool_failure"
            if code == 2
            else "blocked",
            "checks": items,
            "metrics": metrics,
        }
        reports(out, result, cfg)
        if args.package or (args.command == "verify" and code):
            try:
                package_reports(out)
            except OSError as e:
                print("Report packaging failed: " + str(e), file=sys.stderr)
        print(json.dumps({"output": str(out), "status": result["status"], "exitCode": code}))
        return code
    except Problem as e:
        reports(
            out,
            {
                "schemaVersion": "1.0",
                "status": "tool_failure" if e.code == 2 else "blocked",
                "error": redact(str(e)),
                "checks": [
                    {
                        "id": "command",
                        "status": "tool_failure" if e.code == 2 else "blocked",
                        "message": redact(str(e)),
                    }
                ],
            },
            cfg,
        )
        if args.command in ("verify", "execute", "prepare") or args.package:
            try:
                package_reports(out)
            except OSError:
                pass
        print(str(e), file=sys.stderr)
        return e.code
    except Exception as e:
        reports(
            out,
            {
                "schemaVersion": "1.0",
                "status": "tool_failure",
                "error": redact(str(e)),
                "checks": [{"id": "unhandled", "status": "tool_failure"}],
            },
            cfg,
        )
        print(redact(str(e)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
