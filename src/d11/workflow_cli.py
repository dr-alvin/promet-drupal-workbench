"""CLI uses the same service as the browser dashboard."""

import argparse
import json

from .common import Problem, read
from .workflow import Workflow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "projects",
            "runs",
            "register",
            "start",
            "stop",
            "approve",
            "reconcile",
            "report",
            "modules",
            "scan",
            "audit",
            "providers",
            "compatibility",
            "compatibility-decide",
            "auto-decide",
            "auto-remediate",
            "inspect-obsolete",
            "ai-patch",
            "approve-upgrade",
            "rollback",
            "disposition",
            "setup-create",
            "setup-inspect",
            "setup-start",
            "setup-status",
            "setup-stop",
            "setup-review",
            "setup-reconcile",
        ],
    )
    parser.add_argument("--csv", action="store_true", help="Output module transition report as CSV")
    parser.add_argument("--project")
    parser.add_argument("--bundle")
    parser.add_argument("--config")
    parser.add_argument("--run")
    parser.add_argument("--operation")
    parser.add_argument("--plan-id")
    parser.add_argument("--reviewer")
    parser.add_argument("--note")
    parser.add_argument("--batch")
    parser.add_argument("--source")
    parser.add_argument("--source-url")
    parser.add_argument("--sitemap-url")
    parser.add_argument("--route", action="append", default=[])
    parser.add_argument(
        "--wrapper",
        choices=["auto", "fin", "ddev", "lando", "compose", "local"],
        default="auto",
    )
    parser.add_argument("--database")
    parser.add_argument("--files")
    parser.add_argument("--name")
    parser.add_argument("--export-local", action="store_true")
    parser.add_argument(
        "--privacy-reviewed",
        "--managed-copy-reviewed",
        dest="privacy_reviewed",
        action="store_true",
    )
    parser.add_argument("--review-hash")
    parser.add_argument("--approval-digest")
    parser.add_argument("--accept-risks", action="store_true")
    parser.add_argument("--decision", choices=["accept", "keep-for-diagnosis"])
    parser.add_argument("--decisions-file")
    parser.add_argument("--module")
    parser.add_argument("--provider", choices=["codex", "claude", "gemini"])
    parser.add_argument("--no-remediate", dest="auto_remediate", action="store_false", default=True)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Perform full deep scan of all extensions instead of fast scan",
    )
    parser.add_argument("--home", help="Override D11_HOME directory")
    a = parser.parse_args()
    w = Workflow(a.home if a.home else None)
    try:
        from .guided_setup import Setup

        setup = Setup(w)
        if a.action == "setup-create":
            r = setup.create(
                {
                    "id": a.project or "",
                    "sourceUrl": a.source_url or "",
                    "sitemapUrl": a.sitemap_url or "",
                    "routes": a.route,
                    "wrapper": a.wrapper,
                    "source": a.source,
                    "database": a.database or "",
                    "files": a.files or "",
                    "name": a.name,
                    "exportAuthorized": a.export_local,
                }
            )
        elif a.action == "setup-inspect":
            r = setup.inspect(a.project)
        elif a.action == "setup-start":
            r = setup.start(a.project, a.export_local)
        elif a.action == "setup-status":
            r = setup.get(a.project)
        elif a.action == "setup-stop":
            r = setup.stop(a.project)
        elif a.action == "setup-review":
            r = setup.review(
                a.project,
                {
                    "reviewer": a.reviewer,
                    "note": a.note,
                    "privacyReviewed": a.privacy_reviewed,
                    "reviewHash": a.review_hash,
                },
            )
        elif a.action == "setup-reconcile":
            r = setup.reconcile(a.project, a.reviewer, a.note)
        elif a.action == "projects":
            r = w.projects()
        elif a.action == "runs":
            r = w.runs()
        elif a.action == "register":
            r = w.register(a.bundle, a.project, read(a.config))
        elif a.action == "scan":
            if not a.project:
                raise Problem("--project is required")
            options = {"fast": not a.full}
            r = (
                {"run": w.start(a.project, "guided-audit", options=options)}
                if (w.home / "projects" / a.project / "registration.json").is_file()
                else setup.scan(a.project, fast=not a.full)
            )
        elif a.action == "audit":
            r = w.start(a.project, "guided-audit", options={"fast": not a.full})
        elif a.action == "providers":
            from .ai_providers import inventory

            r = inventory()
        elif a.action == "modules":
            if not a.run:
                if a.project:
                    project_runs = [r for r in w.runs() if r.get("project") == a.project]
                    if not project_runs:
                        raise Problem(f"No runs found for project {a.project}")
                    a.run = project_runs[0]["id"]
                else:
                    raise Problem("--run or --project is required")
            if a.csv:
                from .browser_reports import generate_module_transition_csv
                print(generate_module_transition_csv(w, a.run))
                return 0
            else:
                from .browser_reports import generate_module_transition_report
                r = generate_module_transition_report(w, a.run)
        elif a.action == "compatibility":
            r = w.compatibility(a.run)
        elif a.action == "compatibility-decide":
            r = w.compatibility_decisions(a.run, {"decisions": read(a.decisions_file)})
        elif a.action == "auto-remediate":
            if not a.run:
                raise Problem("--run is required")
            r = w.auto_remediate(a.run)
        elif a.action == "auto-decide":
            if not a.run:
                raise Problem("--run is required")
            r = w.auto_decide(
                a.run, accept_prereleases=True, auto_remediate_custom=a.auto_remediate
            )
        elif a.action == "inspect-obsolete":
            if not a.project:
                raise Problem("--project is required")
            r = w.inspect_obsolete(a.project)
        elif a.action == "ai-patch":
            _, state = w.run(a.run)
            r = w.start(
                state["project"],
                "guided-ai-patch",
                a.run,
                {"module": a.module, "provider": a.provider},
            )
        elif a.action == "approve-upgrade":
            approval = w.approve_gate(
                a.run,
                {
                    "reviewer": a.reviewer,
                    "managedCopyReviewed": a.privacy_reviewed,
                    "safetyNote": a.note or "",
                    "acceptRisks": a.accept_risks,
                    "approvalDigest": a.approval_digest,
                },
            )
            _, state = w.run(a.run)
            r = {"approval": approval, "run": w.start(state["project"], "guided-upgrade", a.run)}
        elif a.action == "rollback":
            _, state = w.run(a.run)
            r = w.start(state["project"], "guided-rollback", a.run)
        elif a.action == "disposition":
            r = w.disposition(
                a.run, {"reviewer": a.reviewer, "decision": a.decision, "note": a.note or ""}
            )
        elif a.action == "start":
            r = w.start(a.project, a.operation, a.batch)
        elif a.action == "stop":
            r = w.stop(a.run)
        elif a.action == "approve":
            r = w.approve(a.run, a.plan_id, a.reviewer)
        elif a.action == "reconcile":
            r = w.reconcile(a.run, a.reviewer, a.note)
        else:
            r = w.report(a.run)
        print(json.dumps(r, indent=2))
        return 0
    except (Problem, TypeError) as e:
        print(str(e))
        return 3
