from __future__ import annotations

"""Loopback-only Drupal upgrade dashboard."""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from .common import ROOT, Problem, digest, read
from .knowledge import get_default_branch
from .workflow import Workflow


def create_app(home=None, port=8765):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    service = Workflow(home) if home else Workflow()
    app.state.workflow = service
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return JSONResponse({"error": "Invalid local host"}, 403)
        if (
            request.url.path.startswith("/api/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("origin") not in origins
        ):
            return JSONResponse({"error": "Same-origin action required"}, 403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    async def malformed(request, exc):
        return JSONResponse({"error": "Malformed request or missing required field"}, 400)

    @app.exception_handler(Problem)
    async def problem(request, exc):
        return JSONResponse({"error": str(exc)}, 409)

    @app.exception_handler(Exception)
    async def handle_unhandled(request, exc):
        import logging
        logging.getLogger("d11.dashboard").exception("Unhandled error in dashboard: %s", exc)
        return JSONResponse({"error": f"Server error: {str(exc)}"}, 500)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/dashboard/index.html")

    @app.get("/app.js")
    def js():
        return FileResponse(ROOT / "web/dashboard/app.js", media_type="application/javascript")

    @app.get("/compatibility-view.js")
    def compatibility_js():
        return FileResponse(
            ROOT / "web/dashboard/compatibility-view.js", media_type="application/javascript"
        )

    @app.get("/theme-init.js")
    def theme_init_js():
        return FileResponse(
            ROOT / "web/dashboard/theme-init.js", media_type="application/javascript"
        )

    @app.get("/style.css")
    def css():
        return FileResponse(ROOT / "web/dashboard/style.css", media_type="text/css")

    @app.get("/favicon.svg")
    def favicon_svg():
        return FileResponse(ROOT / "web/dashboard/favicon.svg", media_type="image/svg+xml")

    @app.get("/favicon.png")
    def favicon_png():
        return FileResponse(ROOT / "web/dashboard/favicon.png", media_type="image/png")

    @app.get("/favicon.ico")
    def favicon_ico():
        ico_file = ROOT / "web/dashboard/favicon.ico"
        if ico_file.is_file():
            return FileResponse(ico_file, media_type="image/x-icon")
        return FileResponse(ROOT / "web/dashboard/favicon.svg", media_type="image/svg+xml")

    from .guided_setup import Setup

    setup = Setup(service)

    @app.post("/api/setup")
    async def add_draft(request: Request):
        return setup.create(await request.json())

    @app.post("/api/setup/quick-add")
    async def quick_add(request: Request):
        body = await request.json()
        pid = (body.get("id") or "").strip()
        if pid:
            if (service.home / "drafts" / pid).exists() and not (
                service.home / "projects" / pid
            ).exists():
                import shutil

                shutil.rmtree(service.home / "drafts" / pid, ignore_errors=True)
        draft = setup.create(body)
        pid = draft["id"]
        scan_res = setup.scan(pid)
        return {
            "draft": draft,
            "project": draft,
            "scan": scan_res,
            "run": scan_res,
            "pid": pid,
            "id": pid,
        }

    @app.post("/api/setup/auto-detect")
    async def auto_detect_setup(request: Request):
        body = await request.json()
        source = body.get("source", "").strip()
        if not source:
            raise Problem("Source path required")
        from .source_runtime import detect_runtime

        return detect_runtime(source)

    @app.post("/api/setup/{pid}/inspect")
    def inspect_source(pid):
        return setup.inspect(pid)

    @app.get("/api/setup/{pid}")
    def setup_detail(pid):
        from .common import read

        p = setup.path(pid)
        d = setup.get(pid)
        proof = read(p / "preparation.json") if (p / "preparation.json").exists() else None
        if proof:
            proof["fileCounts"] = {k: len(v) for k, v in proof.pop("hashes", {}).items()}
        return {
            "draft": d,
            "credentialsPath": str(p / "runtime/test-login.json")
            if (p / "runtime/test-login.json").exists()
            else None,
            "preparation": proof,
            "sanitization": read(p / "sanitization.json")
            if (p / "sanitization.json").exists()
            else None,
        }

    @app.get("/api/setup/{pid}/report")
    def setup_report(pid):
        return setup.report(pid)

    @app.post("/api/setup/{pid}/inputs")
    async def setup_inputs(pid, request: Request):
        return setup.update_inputs(pid, await request.json())

    @app.post("/api/setup/{pid}/start")
    async def setup_start(pid, request: Request):
        body = await request.json()
        return setup.start(pid, body.get("exportAuthorized") is True)

    @app.post("/api/setup/{pid}/stop")
    def setup_stop(pid):
        return setup.stop(pid)

    @app.post("/api/setup/{pid}/review")
    async def setup_review(pid, request: Request):
        return setup.review(pid, await request.json())

    @app.post("/api/setup/{pid}/reconcile")
    async def setup_reconcile(pid, request: Request):
        b = await request.json()
        return setup.reconcile(pid, b["reviewer"], b["note"])

    @app.post("/api/setup/{pid}/resume-verification")
    def setup_resume_verification(pid):
        return setup.resume_verification(pid)

    @app.get("/api/projects/{pid}/scenarios")
    def get_project_scenarios(pid):
        try:
            p, cfg = service.project(pid)
            v = cfg.get("visual", {})
            cfg_file = v.get("config")
            if cfg_file and (p / cfg_file).is_file():
                return read(p / cfg_file).get("scenarios", [])
        except Exception:
            pass
        return []

    @app.post("/api/projects/{pid}/scenarios")
    async def scenarios(pid, request: Request):
        from .scenario_setup import configure

        with service.lock():
            return configure(service, pid, await request.json())

    @app.post("/api/projects/{pid}/audit")
    async def guided_audit(pid, request: Request):
        fast = True
        try:
            body = await request.json()
            if isinstance(body, dict) and "fast" in body:
                fast = bool(body["fast"])
        except Exception:
            pass
        return service.start(pid, "guided-audit", options={"fast": fast})

    @app.post("/api/projects/{pid}/scan")
    async def scan_project(pid, request: Request):
        fast = True
        try:
            body = await request.json()
            if isinstance(body, dict) and "fast" in body:
                fast = bool(body["fast"])
        except Exception:
            pass
        if (service.home / "projects" / pid / "registration.json").is_file():
            return {"run": service.start(pid, "guided-audit", options={"fast": fast})}
        if not fast:
            return setup.scan(pid, fast=False)
        return setup.scan(pid)

    @app.get("/api/projects/{pid}/activity")
    def project_activity(pid: str):
        events = []
        draft_events = service.home / "drafts" / pid / "events.jsonl"
        if draft_events.is_file():
            try:
                for line in draft_events.read_text().splitlines()[-50:]:
                    if line.strip():
                        events.append(json.loads(line))
            except Exception:
                pass
        runs_dir = service.home / "runs"
        if runs_dir.is_dir():
            matching_runs = []
            for r in runs_dir.iterdir():
                s_file = r / "state.json"
                if s_file.is_file():
                    try:
                        s = read(s_file)
                        if s.get("project") == pid:
                            matching_runs.append((s.get("startedAt", ""), r))
                    except Exception:
                        pass
            matching_runs.sort(reverse=True)
            for _, r in matching_runs[:2]:
                ev_file = r / "events.jsonl"
                if ev_file.is_file():
                    try:
                        for line in ev_file.read_text().splitlines()[-30:]:
                            if line.strip():
                                events.append(json.loads(line))
                    except Exception:
                        pass
        return {"events": events}

    @app.get("/api/projects/{pid}")
    def get_project_details(pid: str):
        try:
            p, cfg = service.project(pid)
            reg_file = p / "registration.json"
            reg = read(reg_file) if reg_file.is_file() else {}
            route_inputs_file = p / "route-inputs.json"
            route_inputs = read(route_inputs_file) if route_inputs_file.is_file() else {}
            return {
                "id": pid,
                "name": reg.get("name") or (cfg.get("environment") or {}).get("id") or pid,
                "source": reg.get("source") or cfg.get("sourcePath") or (str((p / "site").resolve()) if (p / "site").exists() else ""),
                "sourceUrl": (cfg.get("site") or {}).get("uri") or reg.get("site_url") or "",
                "wrapper": (cfg.get("runtime") or {}).get("wrapper") or (cfg.get("environment") or {}).get("wrapper") or "auto",
                "routes": route_inputs.get("routes", []),
                "sitemapUrl": route_inputs.get("sitemapUrl", ""),
                "captureNavLinks": route_inputs.get("captureNavLinks", True),
                "database": reg.get("database", ""),
                "files": reg.get("files", ""),
                "status": reg.get("status", "registered"),
            }
        except Exception:
            try:
                return setup.get(pid)
            except Exception:
                raise Problem(f"Project not found: {pid}", 404)

    @app.put("/api/projects/{pid}")
    async def update_project_api(pid: str, request: Request):
        body = await request.json()
        if (service.home / "projects" / pid).is_dir():
            return service.update_project(pid, body)
        elif (service.home / "drafts" / pid).is_dir():
            return setup.update_inputs(pid, body)
        raise Problem(f"Project '{pid}' not found", 404)

    @app.get("/api/projects/{pid}/runs")
    def get_project_runs_api(pid: str):
        all_runs = service.runs()
        project_runs = [r for r in all_runs if r.get("project") == pid]
        return {"runs": project_runs, "count": len(project_runs)}

    @app.delete("/api/projects/{pid}/runs")
    def delete_project_runs_api(pid: str, keep: int = 0, action: Optional[str] = None):
        return service.delete_project_runs(pid, keep_latest=(keep > 0), action_filter=action)

    @app.post("/api/workflow/prune")
    async def workflow_prune_api(request: Request):
        keep = 5
        try:
            body = await request.json()
            if isinstance(body, dict) and "keep" in body:
                keep = int(body["keep"])
        except Exception:
            pass
        return service.prune(keep=keep)

    @app.delete("/api/projects/{pid}")
    def delete_project_delete(pid: str):
        return service.delete_project(pid)

    @app.post("/api/projects/{pid}/delete")
    def delete_project_post(pid: str):
        return service.delete_project(pid)

    @app.post("/api/projects/{pid}/handoff")
    async def project_handoff(pid: str, request: Request):
        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        p, cfg = service.project(pid)
        branch = body.get("branch", get_default_branch(cfg.get("target")))
        commit = body.get("commit", False)
        managed_site = p / "site"
        source_root = body.get("source")
        if not source_root:
            draft_file = service.home / "drafts" / pid / "draft.json"
            if draft_file.is_file():
                source_root = read(draft_file).get("source")
        if not source_root:
            for r in (service.home / "runs").glob("*/result/context.json"):
                c = read(r)
                if c.get("roots", {}).get("repository") and c.get("roots", {}).get(
                    "repository"
                ) != str(managed_site.resolve()):
                    source_root = c["roots"]["repository"]
                    break
        if not source_root:
            source_root = managed_site
        from .handoff import sync_to_git_branch

        return sync_to_git_branch(
            managed_site, Path(source_root).resolve(), branch_name=branch, commit=commit
        )

    @app.get("/api/projects/{pid}/handoff/preview")
    def project_handoff_preview(pid: str):
        p, cfg = service.project(pid)
        managed_site = p / "site"
        draft_file = service.home / "drafts" / pid / "draft.json"
        source_root = None
        if draft_file.is_file():
            source_root = read(draft_file).get("source")
        if not source_root:
            for r in (service.home / "runs").glob("*/result/context.json"):
                c = read(r)
                if c.get("roots", {}).get("repository") and c.get("roots", {}).get(
                    "repository"
                ) != str(managed_site.resolve()):
                    source_root = c["roots"]["repository"]
                    break
        if not source_root:
            source_root = managed_site
        from .handoff import inspect_sync_candidates

        candidates = inspect_sync_candidates(managed_site, Path(source_root).resolve())
        return {"sourcePath": str(Path(source_root).resolve()), "candidates": candidates}

    @app.get("/api/projects/{pid}/guardrails")
    def project_guardrails(pid: str):
        p, cfg = service.project(pid)
        managed_site = p / "site"
        from .guardrails import validate_guardrails

        return validate_guardrails(managed_site)

    @app.post("/api/projects/{pid}/guardrails/fix")
    def project_guardrails_fix(pid: str):
        p, cfg = service.project(pid)
        managed_site = p / "site"
        from .guardrails import fix_drupal_standards, validate_guardrails

        fixed_files = []
        for c in ("web/modules/custom", "modules/custom", "web/themes/custom", "themes/custom"):
            target_dir = managed_site / c
            if target_dir.is_dir():
                for f in target_dir.rglob("*"):
                    if f.is_file() and f.suffix in (
                        ".php",
                        ".module",
                        ".theme",
                        ".inc",
                        ".install",
                        ".profile",
                    ):
                        content = f.read_text(errors="replace")
                        fixed = fix_drupal_standards(content, str(f.relative_to(managed_site)))
                        if fixed != content:
                            f.write_text(fixed)
                            fixed_files.append(str(f.relative_to(managed_site)))
        report = validate_guardrails(managed_site)
        return {"status": "fixed", "fixedFiles": fixed_files, "report": report}

    @app.get("/api/settings")
    def settings_get():
        from .ai_providers import get_ai_settings

        return get_ai_settings()

    @app.post("/api/settings")
    async def settings_post(request: Request):
        from .ai_providers import save_ai_settings

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        updated = save_ai_settings(body)
        return {"status": "saved", "settings": updated}

    @app.post("/api/settings/test")
    async def settings_test(request: Request):
        from .ai_providers import test_api_connection

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        provider = body.get("provider", "gemini")
        api_key = body.get("apiKey")
        model = body.get("model")
        host = body.get("host")
        return test_api_connection(provider, api_key=api_key, model=model, host=host)

    @app.get("/api/providers")
    def providers():
        from .ai_providers import inventory

        return inventory()

    @app.get("/api/knowledge")
    def get_knowledge_base():
        from .knowledge import load_knowledge

        return load_knowledge("11")

    @app.get("/api/knowledge/{target}")
    def get_knowledge_for_target(target: str):
        from .knowledge import load_knowledge

        return load_knowledge(target)

    @app.post("/api/terminal/run")
    async def terminal_run(request: Request):
        import sys

        from .web_runner import create_job

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        subcommand = body.get("subcommand", "upgrade").strip()
        allowed_subcommands = {
            "upgrade",
            "handoff",
            "audit",
            "doctor",
            "plan",
            "report",
            "remediate",
            "apply",
            "qa",
            "status",
            "providers",
            "version",
            "help",
        }
        if subcommand not in allowed_subcommands:
            raise Problem(f"Unsupported subcommand '{subcommand}'")

        argv = [sys.executable, "-m", "d11.cli", subcommand]

        if "rawArgs" in body and isinstance(body["rawArgs"], list):
            for arg in body["rawArgs"]:
                if isinstance(arg, str):
                    argv.append(arg)
        else:
            project_path = body.get("projectPath", "").strip()
            if project_path and subcommand in (
                "upgrade",
                "audit",
                "plan",
                "report",
                "remediate",
                "apply",
                "qa",
                "status",
                "guardrails",
            ):
                argv.append(project_path)

            if subcommand in ("upgrade", "audit"):
                if body.get("url"):
                    argv.extend(["--url", body["url"].strip()])
                if body.get("provider"):
                    argv.extend(["--provider", body["provider"].strip()])
                if body.get("model"):
                    argv.extend(["--model", body["model"].strip()])
                if body.get("branch"):
                    argv.extend(["--git-branch", body["branch"].strip()])
                if body.get("handoff") and subcommand != "audit":
                    argv.append("--handoff")
                if body.get("dryRun") or subcommand == "audit":
                    argv.append("--dry-run")
                if body.get("yes"):
                    argv.append("-y")
            elif subcommand == "handoff":
                if body.get("branch"):
                    argv.extend(["--branch", body["branch"].strip()])
                if body.get("commit"):
                    argv.append("--commit")

        desc = f"d11 {' '.join(argv[2:])}"
        job = create_job(argv, cwd=ROOT, description=desc)
        job.start()
        return job.to_dict()

    @app.get("/api/terminal/active")
    def terminal_active():
        from .web_runner import get_active_job

        job = get_active_job()
        return {
            "active": job is not None,
            "job": job.to_dict() if job else None,
        }

    @app.get("/api/terminal/{job_id}")
    def terminal_get(job_id: str):
        from .web_runner import get_job

        return get_job(job_id).to_dict()

    @app.get("/api/terminal/{job_id}/stream")
    def terminal_stream(job_id: str):
        from .web_runner import get_job, stream_job_events

        job = get_job(job_id)
        return StreamingResponse(stream_job_events(job), media_type="text/event-stream")

    @app.post("/api/terminal/{job_id}/input")
    async def terminal_input(job_id: str, request: Request):
        from .web_runner import get_job

        job = get_job(job_id)
        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        val = body.get("input", "")
        job.send_input(val)
        return {"status": "ok"}

    @app.post("/api/terminal/{job_id}/stop")
    def terminal_stop(job_id: str):
        from .web_runner import get_job

        job = get_job(job_id)
        job.terminate()
        return {"status": "terminated"}

    @app.get("/api/runs/{rid}/compatibility")
    def compatibility(rid):
        return service.compatibility(rid)

    @app.post("/api/runs/{rid}/compatibility-decisions")
    async def compatibility_decisions(rid, request: Request):
        return service.compatibility_decisions(rid, await request.json())

    @app.post("/api/runs/{rid}/auto-resolve")
    async def auto_resolve(rid, request: Request):
        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        auto_remediate = body.get("autoRemediate", True)
        accept_prereleases = body.get("acceptPrereleases", True)
        if auto_remediate:
            try:
                service.auto_remediate(rid)
            except Exception:
                pass
        decisions = service.auto_decide(
            rid, accept_prereleases=accept_prereleases, auto_remediate_custom=auto_remediate
        )
        out, _ = service.run(rid)
        gate = read(out / "gate.json") if (out / "gate.json").is_file() else {}
        return {"status": "resolved", "decisions": decisions, "gate": gate}

    @app.get("/api/runs/{rid}/quick-summary")
    def quick_summary(rid):
        out, _ = service.run(rid)
        qs_file = out / "quick-summary.json"
        if qs_file.is_file():
            try:
                return read(qs_file)
            except Exception:
                pass
        gate = read(out / "gate.json") if (out / "gate.json").is_file() else {}
        try:
            compat = service.compatibility(rid)
        except Exception:
            compat = {}
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
            act = row.get("selectedAction")
            if not act:
                status = row.get("status")
                rec = row.get("recommendedAction")
                if status == "ready":
                    act = "keep"
                elif status == "update_available" or rec == "compatible_release":
                    act = "compatible_release"
                elif status == "patch_available" or rec == "available_patch":
                    act = "available_patch"
                elif status == "manual_remediation" or rec == "ai_manual_patch":
                    act = "ai_manual_patch"
                elif rec:
                    act = rec
                else:
                    act = "pending"
            counts[act] = counts.get(act, 0) + 1
        target_core = (
            gate.get("targetCore") or gate.get("exactCoreVersion") or gate.get("target") or "11.x"
        )
        current_core = gate.get("currentCore")
        if not current_core and (out / "result/context.json").is_file():
            try:
                ctx = read(out / "result/context.json")
                for p in ctx.get("composer", {}).get("packages", []):
                    if p.get("name") == "drupal/core":
                        current_core = p.get("version")
                        break
            except Exception:
                pass
        try:
            pid = gate.get("project")
            if pid:
                proj_reg = service.home / "projects" / pid / "registration.json"
                if proj_reg.is_file():
                    reg_data = read(proj_reg)
                    sp = Path(reg_data.get("sourcePath", ""))
                    if sp.is_dir():
                        from .init_project import probe_core_version, probe_drupal_root
                        dr = reg_data.get("drupal_root") or probe_drupal_root(sp)
                        probed = probe_core_version(sp, dr)
                        if probed:
                            current_core = probed
        except Exception:
            pass
        has_baseline = False
        for candidate in (out / "visual-work", out / "visual"):
            cs_file = candidate / "capture-settings.json"
            if (candidate / "bitmaps_reference").is_dir() or cs_file.is_file():
                has_baseline = True
                break
        if not has_baseline and (out / "visual").is_dir():
            has_baseline = any((out / "visual").rglob("*.png"))
        if not has_baseline and (out / "visual-work").is_dir():
            has_baseline = any((out / "visual-work").rglob("*.png"))
        if not has_baseline:
            state_data = read(out / "state.json") if (out / "state.json").is_file() else {}
            if state_data.get("checkpoint") == "baseline_captured":
                has_baseline = True

        screenshot_count = 0
        if (out / "visual").is_dir():
            screenshot_count = len(list((out / "visual").rglob("*.png")))
        elif (out / "visual-work").is_dir():
            screenshot_count = len(list((out / "visual-work").rglob("*.png")))

        gate_risk = gate.get("risk") or {}
        gate_baseline = gate.get("baseline") or {}
        gate_hard_blockers = gate_risk.get("hardBlockers") or []

        res = {
            "targetCore": target_core,
            "currentCore": current_core or "10.x",
            "alreadyD11": bool(current_core and current_core.startswith("11")),
            "approvalEligible": gate.get("approvalEligible", False),
            "riskScore": gate_risk.get("score", 0),
            "recommendation": gate_risk.get("recommendation", "Unknown"),
            "hardBlockers": gate_hard_blockers,
            "counts": counts,
            "timeline": {
                "automatedDuration": "5–10 minutes",
                "manualReviewHours": 0
                if not gate_hard_blockers
                else len(gate_hard_blockers) * 2,
                "rollbackSeconds": "30 seconds",
            },
            "baselineRoutes": gate_baseline.get("selected", 0),
            "headerCount": gate_baseline.get("headerCount", 0),
            "footerCount": gate_baseline.get("footerCount", 0),
            "baselineCaptured": has_baseline,
            "baselineScreenshotCount": screenshot_count,
        }
        try:
            write(qs_file, res)
        except Exception:
            pass
        return res

    @app.post("/api/runs/{rid}/capture-baseline")
    async def capture_baseline_endpoint(rid: str):
        from .two_gate import capture_run_baseline
        return capture_run_baseline(service, rid)

    @app.post("/api/runs/{rid}/ai-patch")
    async def ai_patch(rid, request: Request):
        from .two_gate import generate_ai_patch

        body = await request.json()
        module = body.get("module")
        provider = body.get("provider")
        audit_out, state = service.run(rid)
        patch_out = audit_out / "ai-patches" / module
        patch_out.mkdir(parents=True, exist_ok=True)
        res = generate_ai_patch(service, rid, module, provider, patch_out)
        return {"status": "completed", "module": module, **res}

    @app.post("/api/workflow/reset-lock")
    async def reset_workflow_lock():
        from .common import now, read, write

        cleared = 0
        with service.lock():
            for s_path in (service.home / "runs").glob("*/state.json"):
                try:
                    s_data = read(s_path)
                    if s_data.get("status") in ("running", "reconciliation_required"):
                        s_data["status"] = "interrupted"
                        s_data["error"] = "Workflow manually reset by user"
                        s_data["reconciliation"] = {
                            "reviewer": "Operator Reset",
                            "note": "Lock reset and run reconciled via dashboard",
                            "at": now(),
                        }
                        write(s_path, s_data)
                        cleared += 1
                except Exception:
                    pass
        lock_file = service.home / "workflow.lock"
        if lock_file.is_file():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        return {"ok": True, "clearedRuns": cleared}

    @app.post("/api/runs/{rid}/batch-ai-patch")
    async def batch_ai_patch(rid: str, request: Request):
        from .two_gate import batch_generate_ai_patches

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        modules = body.get("modules")
        provider = body.get("provider")
        return batch_generate_ai_patches(service, rid, modules=modules, provider=provider)

    @app.post("/api/runs/{rid}/self-heal")
    async def self_heal(rid: str, request: Request):
        from .two_gate import self_heal_upgrade_failure

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        provider = body.get("provider")
        return self_heal_upgrade_failure(service, rid, provider=provider)

    @app.post("/api/runs/{rid}/ai-summary")
    async def ai_summary(rid: str, request: Request):
        from .two_gate import generate_handoff_report

        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        provider = body.get("provider")
        return generate_handoff_report(service, rid, provider=provider)

    @app.post("/api/runs/{rid}/one-click-upgrade")
    async def one_click_upgrade(rid: str, request: Request):
        body = (
            await request.json()
            if (
                request.headers.get("content-length")
                and request.headers.get("content-length") != "0"
            )
            else {}
        )
        auto_remediate = body.get("autoRemediate", True)
        accept_prereleases = body.get("acceptPrereleases", True)
        reviewer = body.get("reviewer") or "Automated Upgrade Engine"
        note = body.get("note") or "1-Click automated Drupal 11 upgrade"
        user_decisions = body.get("decisions")
        report_digest = body.get("reportDigest")
        skip_visual = body.get("skipVisual", False)

        if user_decisions:
            try:
                current_report = service.compatibility(rid)
                current_digest = current_report.get("digest")
                ext_map = {e.get("name"): e for e in current_report.get("extensions", [])}
                sanitized_decisions = []
                for d in user_decisions:
                    d_copy = dict(d)
                    name = d_copy.get("name")
                    ext = ext_map.get(name) or {}
                    is_clean = (
                        ext.get("status") == "ready"
                        and not (ext.get("upgradeStatus") or {}).get("issueCount")
                        and not (ext.get("rector") or {}).get("fixableCount")
                    )
                    if d_copy.get("action") == "keep" and not is_clean:
                        target_ver = ext.get("targetVersion") or ext.get("currentVersion")
                        d_copy["action"] = "compatible_release"
                        d_copy["candidateVersion"] = target_ver
                        d_copy["acceptRisk"] = True
                        d_copy["note"] = "Auto-aligned to compatible_release because code findings require remediation before Keep"
                    sanitized_decisions.append(d_copy)
                user_decisions = sanitized_decisions
                decisions_payload = {"decisions": user_decisions}
                if current_digest:
                    decisions_payload["reportDigest"] = current_digest
                service.compatibility_decisions(rid, decisions_payload)
            except Exception as exc:
                logging.getLogger("d11.dashboard").warning("Failed to save decisions in one-click-upgrade: %s", exc)
                raise Problem(f"Failed to apply compatibility decisions: {exc}", 400)

        if auto_remediate:
            try:
                service.auto_remediate(rid)
            except Exception as exc:
                logging.getLogger("d11.dashboard").warning("auto_remediate failed during one-click-upgrade: %s", exc)

        out, state = service.run(rid)
        gate = read(out / "gate.json") if (out / "gate.json").is_file() else {}
        compat_summary = (gate.get("compatibility") or {}).get("summary") or {}
        unresolved_count = compat_summary.get("unresolved", 0)

        if unresolved_count > 0 or not user_decisions or not gate.get("approvalEligible"):
            try:
                decisions = service.auto_decide(
                    rid, accept_prereleases=accept_prereleases, auto_remediate_custom=auto_remediate
                )
            except Exception as exc:
                logging.getLogger("d11.dashboard").warning("auto_decide failed during one-click-upgrade: %s", exc)
                decisions = {}
            out, state = service.run(rid)
            gate = read(out / "gate.json") if (out / "gate.json").is_file() else {}
        else:
            decisions = {d["name"]: d for d in user_decisions}
        gate_risk = gate.get("risk") or {}
        if not gate.get("approvalEligible") or gate_risk.get("recommendation") == "No-Go":
            finding_msgs = [
                f.get("message", f.get("id"))
                for f in (gate_risk.get("findings") or [])
                if f.get("hardBlocker")
            ]
            msg = "; ".join(finding_msgs) if finding_msgs else "Resolve listed hard blockers before approving"
            raise Problem(f"No-Go audit cannot be approved: {msg}", 409)
        actual_gate = (
            digest({k: v for k, v in gate.items() if k != "approvalDigest"}) if gate else None
        )
        if not skip_visual:
            has_baseline = False
            for candidate in (out / "visual-work", out / "visual"):
                if (candidate / "bitmaps_reference").is_dir() or (candidate / "capture-settings.json").is_file():
                    has_baseline = True
                    break
            if not has_baseline and (out / "visual").is_dir():
                has_baseline = any((out / "visual").rglob("*.png"))
            if not has_baseline and (out / "visual-work").is_dir():
                has_baseline = any((out / "visual-work").rglob("*.png"))
            if not has_baseline:
                state_data = read(out / "state.json") if (out / "state.json").is_file() else {}
                if state_data.get("checkpoint") == "baseline_captured":
                    has_baseline = True
            if not has_baseline:
                from .two_gate import capture_run_baseline
                try:
                    capture_run_baseline(service, rid)
                except Exception as exc:
                    logging.getLogger("d11.dashboard").warning("Baseline auto-capture before upgrade failed: %s", exc)

        approval_digest = body.get("approvalDigest") or gate.get("approvalDigest") or actual_gate
        approval = service.approve_gate(
            rid,
            {
                "reviewer": reviewer,
                "note": note,
                "managedCopyReviewed": True,
                "privacyReviewed": True,
                "acceptRisks": True,
                "approvalDigest": approval_digest,
            },
        )
        _, state = service.run(rid)
        run = service.start(
            state["project"], "guided-upgrade", rid, options={"skip_visual": skip_visual}
        )
        return {"status": "started", "run": run, "approval": approval, "decisions": decisions}

    @app.post("/api/runs/{rid}/approve-and-upgrade")
    async def approve_and_upgrade(rid, request: Request):
        body = await request.json()
        approval = service.approve_gate(rid, body)
        _, state = service.run(rid)
        run = service.start(
            state["project"],
            "guided-upgrade",
            rid,
            options={"skip_visual": body.get("skipVisual", False)},
        )
        return {"approval": approval, "run": run}

    @app.post("/api/runs/{rid}/rollback")
    def rollback(rid):
        from .common import write

        _, state = service.run(rid)
        if state.get("status") == "running" and state.get("action") == "guided-upgrade":
            proc_id = state.get("pid")
            is_alive = True
            if proc_id:
                try:
                    os.kill(proc_id, 0)
                except OSError:
                    is_alive = False
            if is_alive:
                raise Problem(
                    "Cannot roll back while upgrade rehearsal is actively running. The upgrade workflow must finish before rollback can be executed.",
                    409,
                )
            state["status"] = "interrupted"
            state["error"] = "Worker process exited unexpectedly"
            write(service.home / "runs" / rid / "state.json", state)

        for r in service.runs():
            if (
                r.get("project") == state.get("project")
                and r.get("status") == "running"
                and r.get("action") == "guided-upgrade"
            ):
                proc_id = r.get("pid")
                is_alive = True
                if proc_id:
                    try:
                        os.kill(proc_id, 0)
                    except OSError:
                        is_alive = False
                if is_alive:
                    raise Problem(
                        "Cannot roll back while an upgrade rehearsal is actively running for this project. The upgrade workflow must finish before rollback can be executed.",
                        409,
                    )
        return service.start(state["project"], "guided-rollback", rid)

    @app.post("/api/runs/{rid}/disposition")
    async def disposition(rid, request: Request):
        return service.disposition(rid, await request.json())

    @app.get("/api/runs/{rid}/report")
    def browser_report(rid):
        from .browser_reports import view

        return view(service, rid)

    @app.post("/api/runs/{rid}/freshness")
    def freshness(rid):
        out, s = service.run(rid)
        p, cfg = service.project(s["project"])
        return {"sourceChanged": s.get("fingerprint") != service.fingerprint(p, cfg)}

    @app.post("/api/runs/{rid}/docx")
    def export_docx(rid):
        from .browser_reports import docx

        try:
            return docx(service, rid)
        except ImportError:
            raise Problem(
                "DOCX export needs python-docx in the toolkit environment; install the updated requirements"
            )

    @app.get("/api/runs/{rid}/report.html")
    def export_html(rid):
        from .browser_reports import standalone_html

        content = standalone_html(service, rid)
        return HTMLResponse(
            content=content,
            headers={
                "Content-Disposition": f'attachment; filename="drupal-upgrade-report-{rid[:8]}.html"'
            },
        )

    @app.get("/api/runs/{rid}/modules")
    def module_transition_report(rid):
        from .browser_reports import generate_module_transition_report

        return generate_module_transition_report(service, rid)

    @app.get("/api/runs/{rid}/modules.csv")
    def module_transition_csv(rid):
        from fastapi.responses import Response
        from .browser_reports import generate_module_transition_csv

        csv_text = generate_module_transition_csv(service, rid)
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="drupal-upgrade-modules-{rid[:8]}.csv"'
            },
        )

    @app.get("/api/capabilities")
    def capabilities():
        import importlib.util
        import shutil

        return {
            "docx": importlib.util.find_spec("docx") is not None,
            "docker": shutil.which("docker") is not None,
            "fin": shutil.which("fin") is not None,
            "workbench_path": str(ROOT),
            "home_path": str(Path.home()),
        }

    @app.get("/api/projects")
    def projects():
        return service.projects()

    @app.post("/api/projects")
    async def register(request: Request):
        body = await request.json()
        return service.register(body["bundle"], body["id"], body["config"])

    @app.get("/api/runs")
    def runs():
        return service.runs()

    @app.post("/api/runs")
    async def start(request: Request):
        body = await request.json()
        return service.start(body["project"], body["action"], body.get("batch"))

    @app.post("/api/runs/{rid}/stop")
    def stop(rid):
        return service.stop(rid)

    @app.delete("/api/runs/{rid}")
    def delete_run(rid):
        return service.delete_run(rid)

    @app.post("/api/runs/{rid}/approve")
    async def approve(rid, request: Request):
        body = await request.json()
        return service.approve(rid, body["planId"], body["reviewer"])

    @app.post("/api/runs/{rid}/reconcile")
    async def reconcile(rid, request: Request):
        body = await request.json()
        return service.reconcile(rid, body["reviewer"], body["note"])

    @app.post("/api/runs/{rid}/effort")
    async def effort(rid, request: Request):
        return service.effort(rid, await request.json())

    @app.get("/api/runs/{rid}/artifacts")
    def artifacts(rid):
        return service.artifacts(rid)

    @app.get("/api/runs/{rid}/artifact")
    def artifact(rid, name: str):
        return FileResponse(
            service.artifact(rid, name),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if name.endswith(".docx")
            else "application/json"
            if name.endswith(".json")
            else "image/png"
            if name.endswith(".png")
            else "image/jpeg"
            if name.endswith(".jpg")
            else "text/plain",
            filename=Path(name).name,
        )

    @app.get("/api/runs/{rid}/events")
    def events(rid):
        out, _ = service.run(rid)

        async def stream():
            import asyncio

            position = 0
            while True:
                path = out / "events.jsonl"
                lines = path.read_text().splitlines() if path.exists() else []
                for line in lines[position:]:
                    yield "data: " + line + "\n\n"
                position = len(lines)
                if service.run(rid)[1]["status"] != "running":
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def launch():
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Local Drupal upgrade dashboard")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--no-open", action="store_true", help="Print the local URL without opening a browser"
    )
    args = parser.parse_args()
    import socket

    # Reserve the listener before issuing the URL; retain it to avoid a race.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", args.port if args.port is not None else 8765))
            except OSError:
                if args.port is not None:
                    raise
                listener.bind(("127.0.0.1", 0))
            listener.listen(128)
        except OSError as exc:
            parser.exit(
                2,
                f"Dashboard could not bind port {args.port}: {exc}. "
                "Stop the existing dashboard or choose --port 8766.\n",
            )
        port = listener.getsockname()[1]
        app = create_app(port=port)
        dashboard_url = f"http://127.0.0.1:{port}/"
        print(f"Open {dashboard_url}", flush=True)
        if not args.no_open:
            import threading
            import webbrowser

            threading.Timer(0.7, lambda: webbrowser.open(dashboard_url)).start()
        uvicorn.run(app, host="127.0.0.1", port=port, fd=listener.fileno(), access_log=False)
