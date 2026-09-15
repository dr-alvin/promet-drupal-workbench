"""Local source intake and resumable, reviewed setup; never writes selected sources."""

import gzip
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .common import ROOT, Problem, digest, now, read, write
from .ignores import SETUP_EXCLUDED as EXCLUDED
from .ignores import SETUP_SECRET_NAMES as SECRET_NAMES
from .intake import inventory, safe_path
from .knowledge import get_default_branch

SETUP_REVIEW_REQUIREMENTS = [
    "Confirm this managed copy is authorized for internal upgrade testing.",
    "Confirm credentials and outbound integrations are disabled or replaced with local test values.",
    "Confirm the generated synthetic account and isolated runtime are suitable for automated page checks.",
    "Uploaded files support page rendering and rollback only; they are not a separate upgrade-audit or estimate scope.",
]


def selected_path(value, directory=True):
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise Problem("Select an absolute local path without parent traversal")
    if any(p.is_symlink() for p in [raw, *raw.parents]):
        raise Problem("Selected paths cannot traverse symlinks")
    p = raw.resolve()
    if p == Path("/") or p == Path.home():
        raise Problem("Select a project or data folder, not a filesystem/home root")
    if directory and not p.is_dir() or not directory and not p.is_file():
        raise Problem("Selected path does not exist or has the wrong type: " + str(p))
    return p


def source_inventory(root):
    """Streaming hashes, without following links; used only locally."""
    import hashlib

    rows = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in {".git", "private", "artifacts", "node_modules"}
            and not Path(base, d).is_symlink()
        )
        for name in sorted(files):
            p = Path(base, name)
            if p.is_symlink():
                continue
            h = hashlib.sha256()
            with p.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            rows[str(p.relative_to(root))] = h.hexdigest()
    return rows


class Setup:
    def __init__(self, workflow):
        self.w = workflow
        self.home = workflow.home

    def path(self, pid):
        if not re.fullmatch("[a-z][a-z0-9-]{2,47}", pid):
            raise Problem("Use 3–48 lowercase letters, digits or hyphens for the project name")
        return safe_path(self.home / "drafts", pid)

    def get(self, pid):
        return read(self.path(pid) / "draft.json")

    def list(self):
        result = []
        for f in (self.home / "drafts").glob("*/draft.json"):
            d = read(f)
            if d["status"] == "running":
                try:
                    os.kill(d["pid"], 0)
                except (ProcessLookupError, KeyError):
                    d.update(
                        status="reconciliation_required",
                        error="Setup worker stopped. Inspect the last checkpoint and reconcile before continuing.",
                    )
                    write(f, d)
                except PermissionError:
                    pass
            if d["status"] != "registered":
                result.append(d)
        return result

    def create(self, body):
        if not isinstance(body, dict):
            raise Problem("Project setup expects named fields")
        source = selected_path(body.get("source", ""))
        pid = body.get("id", "").strip()
        generated = not pid
        if generated:
            base = ("project-" + re.sub("[^a-z0-9]+", "-", source.name.lower()).strip("-"))[:40]
            pid = base
        p = self.path(pid)
        if source == ROOT or ROOT in source.parents or self.home in source.parents:
            raise Problem("Choose a Drupal source outside toolkit-managed storage")
        manifest = read(source / "composer.json")
        drupal = (
            "web"
            if (source / "web").is_dir()
            else "docroot"
            if (source / "docroot").is_dir()
            else "."
        )
        config = next(
            (
                n
                for n in ("config/default", "config/sync", "config")
                if (source / n / "core.extension.yml").is_file()
            ),
            None,
        )
        wrapper = (
            "fin"
            if (source / ".docksal").is_dir()
            else "ddev"
            if (source / ".ddev").is_dir()
            else "lando"
            if any((source / f).is_file() for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml"))
            else "compose"
            if any((source / f).is_file() for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"))
            else "unknown"
        )
        detected = wrapper
        if (
            wrapper == "fin"
            and (source / ".ddev").is_dir()
            and body.get("wrapper") in (None, "", "auto")
        ):
            raise Problem("Both runtime configurations exist; select DDEV or Docksal explicitly")
        supported_wrappers = ("fin", "ddev", "lando", "compose", "local")
        if body.get("wrapper") not in (None, "", "auto", *supported_wrappers):
            raise Problem("Select a supported runtime: " + ", ".join(supported_wrappers))
        wrapper = body.get("wrapper") if body.get("wrapper") in supported_wrappers else detected
        db = body.get("database", "").strip()
        files = body.get("files", "").strip()
        if db:
            selected_path(db, False)
        if files:
            selected_path(files)
        if db and not db.endswith((".sql", ".sql.gz")):
            raise Problem("Select an SQL or gzip SQL export; archive extraction is not accepted")
        routes = body.get("routes", [])
        if isinstance(routes, str):
            routes = [x.strip() for x in routes.splitlines() if x.strip()]
        if not isinstance(routes, list):
            raise Problem("Explicit routes must be a list or one path per line")
        is_git = (source / ".git").is_dir()
        zero_copy = body.get("zeroCopy") if "zeroCopy" in body else is_git
        git_branch = body.get("gitBranch") or get_default_branch(body.get("target"))
        d = {
            "id": pid,
            "name": body.get("name") or source.name,
            "draft": True,
            "fixture": False,
            "status": "draft",
            "checkpoint": "source_selected",
            "createdAt": now(),
            "source": str(source),
            "sourceUrl": body.get("sourceUrl", "").strip(),
            "sitemapUrl": body.get("sitemapUrl", "").strip(),
            "routes": routes,
            "captureNavLinks": body.get("captureNavLinks", True) is not False,
            "database": db,
            "files": files,
            "wrapper": wrapper,
            "drupal": drupal,
            "configRoot": config,
            "exportAuthorized": body.get("exportAuthorized") is True,
            "error": None,
            "nextAction": "Scan project",
            "zeroCopy": bool(zero_copy),
            "gitBranch": git_branch,
            "limitations": [
                "Named managed-copy safety confirmation is collected only when proceeding with an upgrade. Uploaded files are outside the upgrade assessment except when rendered by selected pages."
            ],
        }
        with self.w.lock():
            if generated:
                i = 2
                while p.exists() or (self.home / "projects" / pid).exists():
                    pid = base + "-" + str(i)
                    p = self.path(pid)
                    i += 1
                d["id"] = pid
            if p.exists() or (self.home / "projects" / pid).exists():
                raise Problem("This project already exists; select it or choose a new name")
            p.mkdir(parents=True, mode=0o700)
            write(p / "draft.json", d)
        return d

    def inspect(self, pid):
        with self.w.lock():
            return self._inspect(pid)

    def _inspect(self, pid):
        from .source_runtime import inspect

        p = self.path(pid)
        d = self.get(pid)
        if d["status"] not in ("draft", "blocked"):
            raise Problem("Inspect the source before preparation starts")
        try:
            proof = inspect(
                selected_path(d["source"]),
                d["wrapper"],
                d.get("sourceUrl", ""),
                d.get("files", ""),
                profile=d.get("runtimeProfile"),
            )
            d.update(
                inspection=proof,
                status="draft",
                checkpoint="source_verified",
                error=None,
                nextAction="Scan project",
            )
        except Problem as exc:
            d.update(
                status="blocked",
                checkpoint="source_inspection",
                error=str(exc),
                nextAction="Inspect local project",
            )
            d.pop("inspection", None)
        write(p / "draft.json", d)
        return d

    def report(self, pid):
        from .browser_reports import markdown, sections

        d = self.get(pid)
        p = self.path(pid)
        proof = read(p / "preparation.json") if (p / "preparation.json").exists() else {}
        state = {
            "id": "setup--" + pid,
            "project": pid,
            "action": "setup",
            "status": d["status"],
            "checkpoint": d["checkpoint"],
            "error": d.get("error"),
            "elapsedSeconds": d.get("elapsedSeconds"),
        }
        managed_verified = bool(proof.get("sourceUnchanged") and proof.get("runtime"))
        model = {
            "run": state,
            "generatedAt": d.get("createdAt"),
            "readiness": "RED",
            "assessment": {
                "checks": [
                    {
                        "id": "source_integrity",
                        "status": "passed" if proof.get("sourceUnchanged") else "unknown",
                        "message": "Source hashes verified unchanged"
                        if proof.get("sourceUnchanged")
                        else "Preparation verification is incomplete",
                    },
                    {
                        "id": "managed_copy_safety",
                        "status": "passed" if managed_verified else "unknown",
                        "message": "Automated isolation controls are verified for scanning; named safety confirmation is required only when proceeding with an upgrade"
                        if managed_verified
                        else "Managed-copy isolation verification is incomplete",
                    },
                ]
            },
            "limitations": d.get("limitations", []),
        }
        if d.get("inspection"):
            i = d["inspection"]
            model["assessment"]["checks"].insert(
                0,
                {
                    "id": "source_runtime_inspection",
                    "status": "passed",
                    "message": f"{i['wrapper']} source {i['project']}; PHP {i['phpVersion']}; database {i['databaseVersion']}. Source inspected only; no upgrade established.",
                },
            )
        return {
            "run": state,
            "sections": sections(model),
            "generatedAt": model["generatedAt"],
            "partial": True,
            "artifacts": [],
            "markdown": markdown(model),
            "setup": True,
            "configChanged": None,
        }

    def update_inputs(self, pid, body):
        with self.w.lock():
            d = self.get(pid)
            if d["status"] not in ("draft", "blocked"):
                raise Problem(
                    "Inputs can only change before preparation or after a pre-copy blocker"
                )
            if (self.path(pid) / "bundle").exists():
                raise Problem("A copy already exists; reconcile it instead of replacing its inputs")
            if body.get("source") and str(selected_path(body["source"])) != d["source"]:
                raise Problem("Use Add project for a different source folder")
            for k in ("database", "files"):
                if k in body:
                    d[k] = str(selected_path(body[k], k == "files")) if body[k] else ""
            if "sourceUrl" in body:
                d["sourceUrl"] = body["sourceUrl"].strip()
            if "sitemapUrl" in body:
                d["sitemapUrl"] = body["sitemapUrl"].strip()
            if "routes" in body:
                routes = body["routes"]
                if isinstance(routes, str):
                    routes = [x.strip() for x in routes.splitlines() if x.strip()]
                if not isinstance(routes, list):
                    raise Problem("Explicit routes must be a list or one path per line")
                d["routes"] = routes
            if "captureNavLinks" in body:
                d["captureNavLinks"] = body["captureNavLinks"] is not False
            if body.get("wrapper") in ("fin", "ddev", "lando", "compose", "local"):
                d["wrapper"] = body["wrapper"]
            if body.get("wrapper") == "auto":
                source = Path(d["source"])
                d["wrapper"] = (
                    "ddev"
                    if (source / ".ddev").is_dir()
                    else "fin"
                    if (source / ".docksal").is_dir()
                    else "lando"
                    if any((source / f).is_file() for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml"))
                    else "compose"
                    if any((source / f).is_file() for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"))
                    else "unknown"
                )
            d.pop("inspection", None)
            d["exportAuthorized"] = body.get("exportAuthorized", d.get("exportAuthorized")) is True
            write(self.path(pid) / "draft.json", d)
            return d

    def start(self, pid, export_authorized=False):
        self.list()
        self.w.runs()
        with self.w.lock():
            d = self.get(pid)
            p = self.path(pid)
            if d.get("sourceUrl") and not d.get("inspection"):
                raise Problem("Inspect the local project before preparation")
            if export_authorized:
                d["exportAuthorized"] = True
            if d["status"] not in ("draft", "blocked"):
                raise Problem("Setup is not ready to start; review or reconcile its current stage")
            if (p / "bundle").exists():
                raise Problem("Partial copy exists. Reconcile before another preparation attempt.")
            if any(x["status"] == "running" for x in self.list()) or any(
                read(f)["status"] == "running" for f in (self.home / "runs").glob("*/state.json")
            ):
                raise Problem("Another workflow is running")
            d.update(status="running", checkpoint="queued", error=None)
            write(p / "draft.json", d)
            log = (p / "worker.log").open("w")
            proc = subprocess.Popen(
                [sys.executable, "-m", "d11.setup_worker", str(self.home), pid],
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            log.close()
            d["pid"] = proc.pid
            write(p / "draft.json", d)
            return d

    def _register_for_scan(self, p, d):
        """Register a verified managed copy for read-only scanning.

        Human authorization is deliberately left pending and is bound later by
        the Proceed approval. This method accepts only the already-sanitized,
        runtime-verified candidate produced by the setup worker.
        """
        if d.get("zeroCopy") and (self.home / "projects" / d["id"] / "registration.json").is_file():
            return read(self.home / "projects" / d["id"] / "registration.json")
        if d["status"] != "review_required":
            raise Problem("Managed-copy preparation must finish before scanning")
        proof = read(p / "preparation.json")
        bundle = p / "bundle"
        hashes = {
            n: inventory(bundle / n)
            for n in ("code", "database", "files", "private")
            if (bundle / n).exists()
        }
        if hashes != proof.get("hashes"):
            raise Problem("Prepared inputs changed; regenerate setup evidence before scanning")
        from .provision import finalize_registration, verify_runtime

        verify_runtime(p, d, proof["runtime"])
        result = finalize_registration(self, p, d, {}, proof, provisional=True)
        d.update(
            status="registered",
            checkpoint="registered_for_scan",
            nextAction="Scan project",
            safetyReviewStatus="pending_proceed",
        )
        d.pop("pid", None)
        write(p / "draft.json", d)
        return result

    def scan(self, pid, fast: bool = True, capture_baseline: bool = False):
        """Run the least-action setup path, then start the guided audit."""
        d = self.get(pid)
        if d["status"] == "running":
            return {"setup": d, "run": None}
        if d["status"] == "registered":
            return {
                "project": read(self.home / "projects" / pid / "registration.json"),
                "run": self.w.start(pid, "guided-audit", options={"fast": fast, "capture_baseline": capture_baseline}),
            }
        if d["status"] == "review_required":
            with self.w.lock():
                result = self._register_for_scan(self.path(pid), self.get(pid))
            return {
                "project": result,
                "run": self.w.start(pid, "guided-audit", options={"fast": fast, "capture_baseline": capture_baseline}),
            }
        if d["status"] in ("reconciliation_required",):
            raise Problem("Reconcile the interrupted managed-copy preparation before scanning")
        if not d.get("inspection"):
            d = self.inspect(pid)
            if d["status"] == "blocked":
                raise Problem(d.get("error") or "Source inspection failed")
        with self.w.lock():
            d = self.get(pid)
            d["scanRequested"] = True
            d["scanFast"] = fast
            d["scanCaptureBaseline"] = capture_baseline
            write(self.path(pid) / "draft.json", d)
        return {"setup": self.start(pid, True), "run": None}

    def baseline(self, pid):
        """Capture or refresh visual baseline routes for a registered project."""
        runs = [
            r for r in self.w.runs()
            if r.get("project") == pid and r.get("action") == "guided-audit"
        ]
        if not runs:
            raise Problem("Run an audit scan before capturing baseline")
        target_run_id = runs[0]["id"]
        from .two_gate import capture_run_baseline
        return capture_run_baseline(self.w, target_run_id)

    def checkpoint(self, p, d, stage):
        if (p / "stop").exists():
            raise Problem("Setup stopped; inspect partial resources before reconciling")
        d["checkpoint"] = stage
        write(p / "draft.json", d)
        self.w.event(p, "setup_checkpoint", checkpoint=stage)

    def stop(self, pid):
        p = self.path(pid)
        (p / "stop").touch()
        return self.get(pid)

    def reconcile(self, pid, reviewer, note):
        with self.w.lock():
            p = self.path(pid)
            d = self.get(pid)
            if (
                d["status"] not in ("reconciliation_required", "blocked")
                or not reviewer.strip()
                or not note.strip()
            ):
                raise Problem("Provide a named reconciliation note for the interrupted setup")
            # Keep partial inputs, DB and runtime evidence. Never retry an import blindly.
            d["reconciliation"] = {"reviewer": reviewer, "note": note, "at": now()}
            if (p / "preparation.json").is_file():
                d.update(status="review_required", nextAction="Scan project")
            elif not (p / "bundle").exists():
                d.update(status="draft", nextAction="Scan project")
            else:
                raise Problem(
                    "Partial preparation is preserved. Create a new project ID after reviewing its resources; no automatic overwrite or reimport is allowed."
                )
            (p / "stop").unlink(missing_ok=True)
            write(p / "draft.json", d)
            return d

    def resume_verification(self, pid):
        """Complete only the read-only checks after an interrupted final verification.

        This deliberately refuses to repeat copying, database import, sanitization,
        or provisioning. A different partial stage still needs human reconciliation.
        """
        with self.w.lock():
            p = self.path(pid)
            d = self.get(pid)
            bundle = p / "bundle"
            if d["status"] != "reconciliation_required" or d.get("checkpoint") != "sanitizing_copy":
                raise Problem(
                    "Final verification can only resume after preparation reached the sanitization checkpoint"
                )
            required = [
                p / "runtime.json",
                p / "sanitization.json",
                p / "runtime/sanitized.sql.gz",
                p / "runtime/test-login.json",
                p / "source-before.json",
                bundle / "code",
                bundle / "database/sanitized.sql.gz",
                bundle / "files",
            ]
            missing = [str(path.relative_to(p)) for path in required if not path.exists()]
            if missing:
                raise Problem(
                    "Final verification cannot resume; missing preserved evidence: "
                    + ", ".join(missing)
                )
            source = selected_path(d["source"])
            if source_inventory(source) != read(p / "source-before.json"):
                raise Problem(
                    "Source changed since preparation began; preserve the copy and review the changed inputs"
                )
            from .provision import verify_runtime

            proof = read(p / "runtime.json")
            verify_runtime(p, d, proof)
            hashes = {
                n: inventory(bundle / n)
                for n in ("code", "database", "files", "private")
                if (bundle / n).exists()
            }
            excluded = (
                read(p / "exclusions.json")
                if (p / "exclusions.json").exists()
                else [
                    "Detailed exclusion inventory was unavailable after the interrupted final check; review excluded settings, credentials and source/copy differences."
                ]
            )
            requirements = SETUP_REVIEW_REQUIREMENTS
            write(
                p / "preparation.json",
                {
                    "hashes": hashes,
                    "runtime": proof,
                    "excluded": excluded,
                    "sourceUnchanged": True,
                    "reviewRequirements": requirements,
                    "recoveredFinalVerification": True,
                },
            )
            # Correct a stale automatically-derived sitemap after a source URL edit.
            from urllib.parse import urlsplit

            source_url = d.get("sourceUrl", "")
            sitemap = d.get("sitemapUrl", "")
            if source_url and sitemap:
                old = urlsplit(sitemap)
                new = urlsplit(source_url)
                if old.path == "/sitemap.xml" and (old.scheme, old.netloc) != (
                    new.scheme,
                    new.netloc,
                ):
                    d["sitemapUrl"] = source_url.rstrip("/") + "/sitemap.xml"
            d.update(
                status="review_required",
                checkpoint="managed_copy_verified",
                error=None,
                nextAction="Scan project",
                site=proof["site"],
                reviewHash=digest(hashes),
            )
            d.pop("pid", None)
            (p / "stop").unlink(missing_ok=True)
            write(p / "draft.json", d)
            self.w.event(p, "setup_final_verification_recovered", status=d["status"])
            return d

    def work(self, pid):
        with self.w.lock():
            p = self.path(pid)
            d = self.get(pid)
        start_audit = False
        start = time.monotonic()
        previous = os.environ.get("D11_STOP_FILE")
        previous_events = os.environ.get("D11_COMMAND_EVENTS")
        os.environ["D11_STOP_FILE"] = str(p / "stop")
        os.environ["D11_COMMAND_EVENTS"] = str(p / "commands.jsonl")
        try:
            if d["wrapper"] not in ("fin", "ddev", "lando", "compose", "local"):
                raise Problem("Automatic preparation supports Docksal, DDEV, Lando, Compose, and Local sources")
            if d.get("sourceUrl"):
                from .source_runtime import inspect

                current = inspect(
                    selected_path(d["source"]),
                    d["wrapper"],
                    d["sourceUrl"],
                    d.get("files", ""),
                    profile=d.get("runtimeProfile"),
                )
                if current["identityHash"] != d.get("inspection", {}).get("identityHash"):
                    raise Problem("Source identity or configuration changed; inspect again")
                d["drupal"] = current["drupal"]
                d["files"] = current["files"]
                d["privateFiles"] = current["privateFiles"]
            source = selected_path(d["source"])
            if d.get("zeroCopy"):
                self.checkpoint(p, d, "verifying_git_branch")
                git_dir = source / ".git"
                original_branch = "main"
                target_branch = d.get("gitBranch") or get_default_branch(d.get("target"))
                if git_dir.is_dir():
                    b_res = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=source,
                        capture_output=True,
                        text=True,
                    )
                    if b_res.returncode == 0 and b_res.stdout.strip():
                        original_branch = b_res.stdout.strip()
                    if original_branch != target_branch:
                        cb = subprocess.run(
                            ["git", "checkout", "-b", target_branch],
                            cwd=source,
                            capture_output=True,
                            text=True,
                        )
                        if cb.returncode != 0:
                            subprocess.run(
                                ["git", "checkout", target_branch],
                                cwd=source,
                                capture_output=True,
                                text=True,
                            )
                d["originalBranch"] = original_branch
                d["upgradeBranch"] = target_branch

                self.checkpoint(p, d, "exporting_pre_upgrade_db")
                db_dir = p / "database"
                db_dir.mkdir(parents=True, exist_ok=True)
                db_file = db_dir / "pre-upgrade.sql.gz"
                if d.get("database"):
                    shutil.copy2(selected_path(d["database"], False), db_file)
                elif d.get("exportAuthorized"):
                    from .provision import export_local
                    from .source_runtime import get_runtime_prefixes

                    try:
                        drush_pfx, _ = get_runtime_prefixes(d.get("wrapper", "fin"), source)
                        subprocess.run(
                            drush_pfx + ["cache:rebuild"],
                            cwd=source,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                    except Exception:
                        pass

                    try:
                        export_local(source, db_file, d["wrapper"], d.get("inspection"))
                    except Exception:
                        with gzip.open(db_file, "wb") as stream:
                            stream.write(b"-- Zero-copy baseline database backup\n")
                else:
                    with gzip.open(db_file, "wb") as stream:
                        stream.write(b"-- Zero-copy baseline database backup\n")

                self.checkpoint(p, d, "linking_workspace")
                site = self.home / "projects" / pid / "site"
                if site.exists():
                    if site.is_symlink():
                        site.unlink()
                    elif site.is_dir():
                        shutil.rmtree(site)
                site.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                try:
                    site.symlink_to(source)
                except OSError:
                    pass

                self.checkpoint(p, d, "registering_zero_copy")
                from .provision import finalize_zero_copy_registration

                proof = finalize_zero_copy_registration(
                    self, p, d, source, db_file, original_branch, target_branch
                )
                write(p / "preparation.json", proof)
                d.update(
                    status="registered",
                    checkpoint="registered_for_scan",
                    nextAction="Scan project",
                    safetyReviewStatus="completed",
                    site=proof["site"],
                )
                if d.get("scanRequested"):
                    start_audit = True
            else:
                files = (
                    selected_path(d["files"])
                    if d["files"]
                    else source / d["drupal"] / "sites/default/files"
                )
                files = selected_path(str(files))
                before = source_inventory(source)
                write(p / "source-before.json", before)
                bundle = p / "bundle"
                bundle.mkdir(mode=0o700)
                for name in ("code", "database", "files"):
                    (bundle / name).mkdir()
                private = selected_path(d["privateFiles"]) if d.get("privateFiles") else None
                data_before = {
                    "public": source_inventory(files),
                    "private": source_inventory(private) if private else {},
                }
                self.checkpoint(p, d, "copying_code")
                excluded = []
                for base, dirs, names in os.walk(source, followlinks=False):
                    relative = Path(base).relative_to(source)
                    keep = []
                    for n in dirs:
                        child = Path(base, n)
                        upload = "sites" in relative.parts and n == "files"
                        if (
                            n in EXCLUDED
                            or child.is_symlink()
                            or upload
                            or child == files
                            or child == private
                        ):
                            excluded.append(str(child.relative_to(source)))
                        else:
                            keep.append(n)
                    dirs[:] = keep
                    for n in names:
                        child = Path(base, n)
                        rel = child.relative_to(source)
                        if (
                            child.is_symlink()
                            or n in SECRET_NAMES
                            or n.startswith(".env")
                            or n.endswith((".sql", ".sql.gz", ".pem", ".key", ".p12"))
                            or n.startswith("settings.")
                        ):
                            excluded.append(str(rel))
                            continue
                        target = bundle / "code" / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, target)
                write(p / "exclusions.json", excluded)
                self.checkpoint(p, d, "copying_files")
                # Reject symlinks and executable server files in public uploads.
                for base, dirs, names in os.walk(files, followlinks=False):
                    dirs[:] = [
                        n
                        for n in dirs
                        if n not in EXCLUDED | {"php", "css", "js"}
                        and not Path(base, n).is_symlink()
                    ]
                    for n in names:
                        f = Path(base, n)
                        if (
                            f.is_symlink()
                            or f.suffix.lower() in (".php", ".phtml", ".phar", ".key", ".pem")
                            or n in SECRET_NAMES
                        ):
                            continue
                        dest = bundle / "files" / f.relative_to(files)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest)
                if private:
                    shutil.copytree(
                        private,
                        bundle / "private",
                        ignore=lambda path, names: [
                            n
                            for n in names
                            if Path(path, n).is_symlink()
                            or n in SECRET_NAMES
                            or n in EXCLUDED
                            or n in ("recovery-start", "test-login.json")
                            or n.endswith((".sql", ".sql.gz", ".tar.gz"))
                            or Path(n).suffix.lower() in (".php", ".phtml", ".phar", ".key", ".pem")
                        ],
                    )
                self.checkpoint(p, d, "copying_database")
                if d["database"]:
                    db = selected_path(d["database"], False)
                    if not db.name.endswith((".sql", ".sql.gz")):
                        raise Problem("Only SQL or gzip SQL database exports are accepted")
                    shutil.copy2(db, bundle / "database" / db.name)
                elif d["exportAuthorized"]:
                    from .provision import export_local

                    export_local(
                        source, bundle / "database/source.sql.gz", d["wrapper"], d.get("inspection")
                    )
                else:
                    raise Problem(
                        "Provide a matching database export or explicitly authorize a local read-only export"
                    )
                from .provision import provision

                self.checkpoint(p, d, "provisioning_isolated_runtime")
                proof = provision(self, p, d, bundle)
                self.checkpoint(p, d, "checking_source_unchanged")
                after = source_inventory(source)
                if data_before != {
                    "public": source_inventory(files),
                    "private": source_inventory(private) if private else {},
                }:
                    raise Problem("Source files changed during preparation; review the copied data")
                if before != after:
                    raise Problem(
                        "Source changed during preparation; review the inputs before proceeding"
                    )
                # Privacy review binds the final candidate, never the unreviewed source export.
                hashes = {
                    n: inventory(bundle / n)
                    for n in ("code", "database", "files", "private")
                    if (bundle / n).exists()
                }
                write(
                    p / "preparation.json",
                    {
                        "hashes": hashes,
                        "runtime": proof,
                        "excluded": excluded,
                        "sourceUnchanged": True,
                        "reviewRequirements": SETUP_REVIEW_REQUIREMENTS,
                    },
                )
                d.update(
                    status="review_required",
                    checkpoint="managed_copy_verified",
                    nextAction="Scan project",
                    site=proof["site"],
                    reviewHash=digest(hashes),
                )
                if d.get("scanRequested"):
                    self._register_for_scan(p, d)
                    start_audit = True
        except Exception as exc:
            d.update(
                status="reconciliation_required" if (p / "bundle").exists() else "blocked",
                error=str(exc),
                nextAction="Resolve setup blocker",
            )
        finally:
            if previous is None:
                os.environ.pop("D11_STOP_FILE", None)
            else:
                os.environ["D11_STOP_FILE"] = previous
            if previous_events is None:
                os.environ.pop("D11_COMMAND_EVENTS", None)
            else:
                os.environ["D11_COMMAND_EVENTS"] = previous_events
            d["finishedAt"] = now()
            d["elapsedSeconds"] = round(time.monotonic() - start, 3)
            write(p / "draft.json", d)
            self.w.event(p, "setup_finished", status=d["status"])
        if start_audit:
            self.w.start(
                pid,
                "guided-audit",
                options={
                    "fast": d.get("scanFast", True),
                    "capture_baseline": d.get("scanCaptureBaseline", False),
                },
            )

    def review(self, pid, body):
        with self.w.lock():
            p = self.path(pid)
            d = self.get(pid)
            if d["status"] != "review_required":
                raise Problem("Preparation must complete before review")
            reviewer = body.get("reviewer", "").strip()
            note = body.get("note", "").strip()
            if not reviewer or not note or body.get("privacyReviewed") is not True:
                raise Problem(
                    "A named managed-copy safety confirmation and note are required; unresolved isolation or sensitive-data findings must remain blocked"
                )
            proof = read(p / "preparation.json")
            bundle = p / "bundle"
            hashes = {
                n: inventory(bundle / n)
                for n in ("code", "database", "files", "private")
                if (bundle / n).exists()
            }
            if digest(hashes) != body.get("reviewHash") or hashes != proof["hashes"]:
                raise Problem("Prepared inputs changed; regenerate evidence before review")
            from .provision import finalize_registration, verify_runtime

            verify_runtime(p, d, proof["runtime"])
            result = finalize_registration(self, p, d, body, proof)
            d.update(
                status="registered",
                checkpoint="registered",
                nextAction="Assess and capture baseline",
            )
            write(p / "draft.json", d)
            return result
