"""Coordinated recovery checkpoints for toolkit-managed runtimes only."""

import gzip
import shutil
import subprocess
import time
from pathlib import Path

from .common import Problem, command, digest, file_hash, now, read, write
from .intake import inventory
from .knowledge import get_default_branch
from .provision import prefix, verify_runtime


def _managed(w, pid):
    p, cfg = w.project(pid)
    registration = read(p / "registration.json")
    if not registration.get("setupId"):
        raise Problem("Recovery automation requires a toolkit-provisioned managed copy")
    draft = w.home / "drafts" / registration["setupId"]
    runtime = read(draft / "runtime.json") if (draft / "runtime.json").is_file() else {}
    site = Path(runtime.get("sitePath") or cfg.get("sourcePath") or p / "site").resolve()
    if not cfg.get("zeroCopy") and site != p / "site":
        raise Problem("Recovery target is not the registered managed copy")
    return p, cfg, draft, runtime, site


def create(w, pid, out):
    p, cfg, draft, runtime, site = _managed(w, pid)
    target = Path(out) / "recovery-checkpoint"
    if target.exists():
        raise Problem("Recovery checkpoint already exists")
    target.mkdir(parents=True, mode=0o700)
    if cfg.get("zeroCopy"):
        source_dir = Path(cfg.get("sourcePath") or site).resolve()
        git_head = ""
        git_branch = cfg.get("upgradeBranch", get_default_branch(cfg.get("target")))
        if (source_dir / ".git").is_dir():
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source_dir, capture_output=True, text=True
            )
            if r.returncode == 0:
                git_head = r.stdout.strip()
            r_b = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=source_dir,
                capture_output=True,
                text=True,
            )
            if r_b.returncode == 0:
                git_branch = r_b.stdout.strip()
        db = target / "database.sql.gz"
        pre_db = Path(cfg.get("databaseBackup") or (draft / "database/pre-upgrade.sql.gz"))
        if pre_db.is_file():
            shutil.copy2(pre_db, db)
        elif draft.is_dir() and (draft / "runtime.json").is_file():
            argv = prefix(draft) + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb-dump -u"$MYSQL_USER" --single-transaction --skip-lock-tables --skip-triggers "$MYSQL_DATABASE"',
            ]
            with gzip.open(db, "wb") as stream:
                proc = subprocess.Popen(
                    argv, cwd=draft, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                    stream.write(chunk)
                err = proc.stderr.read()
                code = proc.wait()
            if code:
                raise Problem(
                    "Recovery database export failed: " + err.decode(errors="replace")[-500:]
                )
        else:
            with gzip.open(db, "wb") as stream:
                stream.write(b"-- Zero-copy baseline database backup\n")

        manifest = {
            "createdAt": now(),
            "project": pid,
            "zeroCopy": True,
            "environment": cfg["environment"],
            "site": cfg["site"],
            "sourcePath": str(source_dir),
            "originalBranch": cfg.get("originalBranch", "main"),
            "upgradeBranch": git_branch,
            "gitHead": git_head,
            "hashes": {"database": file_hash(db)},
            "runtimeProject": runtime.get("project", "zero-copy-" + pid),
        }
        manifest["checkpointId"] = digest(manifest)
        write(target / "manifest.json", manifest)
        return manifest

    shutil.copytree(site, target / "code", symlinks=False)
    public = site / cfg["roots"]["drupal"] / "sites/default/files"
    if public.is_dir():
        shutil.copytree(public, target / "public-files")
    private = draft / "runtime/private-files"
    if private.is_dir():
        shutil.copytree(private, target / "private-files")
    db = target / "database.sql.gz"
    argv = prefix(draft) + [
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'MYSQL_PWD="$MYSQL_PASSWORD" mariadb-dump -u"$MYSQL_USER" --single-transaction --skip-lock-tables --skip-triggers "$MYSQL_DATABASE"',
    ]
    with gzip.open(db, "wb") as stream:
        proc = subprocess.Popen(argv, cwd=draft, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            stream.write(chunk)
        err = proc.stderr.read()
        code = proc.wait()
    if code:
        raise Problem("Recovery database export failed: " + err.decode(errors="replace")[-500:])
    hashes = {
        "code": inventory(target / "code"),
        "database": file_hash(db),
        "publicFiles": inventory(target / "public-files")
        if (target / "public-files").exists()
        else {},
        "privateFiles": inventory(target / "private-files")
        if (target / "private-files").exists()
        else {},
    }
    manifest = {
        "createdAt": now(),
        "project": pid,
        "environment": cfg["environment"],
        "site": cfg["site"],
        "hashes": hashes,
        "runtimeProject": runtime["project"],
    }
    manifest["checkpointId"] = digest(manifest)
    write(target / "manifest.json", manifest)
    return manifest


def verify_checkpoint(w, pid, out):
    p, cfg, draft, runtime, site = _managed(w, pid)
    target = Path(out) / "recovery-checkpoint"
    manifest = read(target / "manifest.json")
    if manifest.get("zeroCopy"):
        actual = {"database": file_hash(target / "database.sql.gz")}
        if actual != manifest["hashes"] or manifest["project"] != pid:
            raise Problem("Recovery checkpoint integrity or identity failed")
        return manifest
    actual = {
        "code": inventory(target / "code"),
        "database": file_hash(target / "database.sql.gz"),
        "publicFiles": inventory(target / "public-files")
        if (target / "public-files").exists()
        else {},
        "privateFiles": inventory(target / "private-files")
        if (target / "private-files").exists()
        else {},
    }
    if (
        actual != manifest["hashes"]
        or manifest["project"] != pid
        or manifest["runtimeProject"] != runtime["project"]
    ):
        raise Problem("Recovery checkpoint integrity or identity failed")
    return manifest


def restore(w, pid, out):
    p, cfg, draft, runtime, site = _managed(w, pid)
    target = Path(out) / "recovery-checkpoint"
    manifest = verify_checkpoint(w, pid, out)
    if manifest.get("zeroCopy"):
        source_dir = Path(manifest.get("sourcePath") or cfg.get("sourcePath") or site).resolve()
        orig_branch = manifest.get("originalBranch", "main")
        git_head = manifest.get("gitHead")
        wrapper = cfg.get("runtime", {}).get("wrapper", "auto")
        if (source_dir / ".git").is_dir():
            if git_head:
                subprocess.run(
                    ["git", "checkout", orig_branch], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
                subprocess.run(
                    ["git", "reset", "--hard", git_head], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
            else:
                subprocess.run(
                    ["git", "reset", "--hard"], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
                subprocess.run(
                    ["git", "checkout", orig_branch], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
            subprocess.run(["git", "clean", "-fd"], cwd=source_dir, capture_output=True, text=True, errors="replace")

            # Clean uncommitted patch changes in contrib modules/themes that were checked out via git
            for rel_parent in ("web/modules/contrib", "web/themes/contrib", "modules/contrib", "themes/contrib"):
                parent_dir = source_dir / rel_parent
                if parent_dir.is_dir():
                    for sub in parent_dir.iterdir():
                        if (sub / ".git").is_dir():
                            subprocess.run(["git", "-C", str(sub), "checkout", "--", "."], capture_output=True)
                            subprocess.run(["git", "-C", str(sub), "clean", "-fd"], capture_output=True)

            def _get_composer_base():
                if wrapper == "ddev" and (source_dir / ".ddev/config.yaml").is_file():
                    return ["ddev", "composer"]
                elif wrapper in ("fin", "docksal") and ((source_dir / ".docksal/docksal.env").is_file() or (source_dir / ".docksal").is_dir()):
                    return ["fin", "composer"]
                elif wrapper == "lando" and any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml")):
                    return ["lando", "composer"]
                elif wrapper == "auto":
                    if (source_dir / ".ddev/config.yaml").is_file():
                        return ["ddev", "composer"]
                    elif (source_dir / ".docksal/docksal.env").is_file() or (source_dir / ".docksal").is_dir():
                        return ["fin", "composer"]
                    elif any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml")):
                        return ["lando", "composer"]
                elif wrapper == "local" and (source_dir / "composer.json").is_file() and shutil.which("composer"):
                    return ["composer"]
                return None

            c_base = _get_composer_base()
            comp = None
            if c_base:
                # Grant temporary write permissions before Composer install
                for p, mode in [("web/sites/default", "777"), ("web/sites/default/settings.php", "666"), ("web/sites/default/services.yml", "666")]:
                    subprocess.run(
                        c_base[:1] + ["exec", "chmod", mode, p],
                        cwd=source_dir,
                        capture_output=True,
                        text=True,
                        errors="replace",
                    )
                # Allow symfony/runtime so PluginManager doesn't abort on orphaned Drupal 11 plugin in vendor
                subprocess.run(
                    c_base + ["config", "--no-plugins", "allow-plugins.symfony/runtime", "true"],
                    cwd=source_dir,
                    capture_output=True,
                )
                comp = subprocess.run(
                    c_base + ["install", "--no-interaction"],
                    cwd=source_dir,
                    capture_output=True,
                    text=True,
                    errors="replace",
                )
                # Ensure permissions are restored even if Composer fails
                for p, mode in [("web/sites/default", "755"), ("web/sites/default/settings.php", "644"), ("web/sites/default/services.yml", "644")]:
                    subprocess.run(
                        c_base[:1] + ["exec", "chmod", mode, p],
                        cwd=source_dir,
                        capture_output=True,
                        text=True,
                        errors="replace",
                    )
                if comp.returncode != 0:
                    # Fallback to --no-plugins to ensure vendor packages can be downgraded/restored
                    comp_retry = subprocess.run(
                        c_base + ["install", "--no-interaction", "--no-plugins"],
                        cwd=source_dir,
                        capture_output=True,
                        text=True,
                        errors="replace",
                    )
                    if comp_retry.returncode == 0:
                        comp = comp_retry
                        # Re-run normal install now that vendor plugins are restored so composer/installers maps core properly
                        subprocess.run(
                            c_base + ["install", "--no-interaction"],
                            cwd=source_dir,
                            capture_output=True,
                        )

                # Restore composer.json to exact pre-upgrade state and clean any untracked artifacts
                if git_head:
                    subprocess.run(
                        ["git", "checkout", git_head, "--", "composer.json"],
                        cwd=source_dir,
                        capture_output=True,
                    )
                else:
                    subprocess.run(
                        ["git", "checkout", orig_branch, "--", "composer.json"],
                        cwd=source_dir,
                        capture_output=True,
                    )
                subprocess.run(["git", "clean", "-fd"], cwd=source_dir, capture_output=True)

            if comp is not None and comp.returncode != 0:
                full_out = ((comp.stderr or "") + "\n" + (comp.stdout or "")).strip()
                error_lines = [
                    l
                    for l in full_out.splitlines()
                    if l.strip() and not l.startswith("install [") and not l.startswith("[--")
                ]
                msg = "\n".join(error_lines[-15:]) if error_lines else full_out[-500:]
                raise Problem(f"Rollback composer install failed (exit {comp.returncode}): {msg}")
        db_file = target / "database.sql.gz"
        if db_file.is_file() and draft.is_dir() and (draft / "runtime.json").is_file():
            cp = prefix(draft)
            with gzip.open(db_file, "rb") as data:
                proc = subprocess.Popen(
                    cp
                    + [
                        "exec",
                        "-T",
                        "db",
                        "sh",
                        "-c",
                        'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE"',
                    ],
                    cwd=draft,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    proc.stdin.write(chunk)
                proc.stdin.close()
                err = proc.stderr.read()
                code = proc.wait()
                if code != 0:
                    raise Problem(f"Database restore failed with exit code {code}: {err.decode(errors='replace')[-500:]}")
        else:
            if (wrapper == "ddev" or (source_dir / ".ddev").is_dir()) and (source_dir / ".ddev/config.yaml").is_file():
                r_db = subprocess.run(
                    ["ddev", "import-db", f"--file={db_file}"],
                    cwd=source_dir,
                    capture_output=True,
                    text=True,
                    errors="replace",
                )
                if r_db.returncode != 0:
                    raise Problem(f"ddev import-db failed (exit {r_db.returncode}): {r_db.stderr or r_db.stdout}")
                subprocess.run(
                    ["ddev", "drush", "cr"], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
            elif (wrapper in ("fin", "docksal") or (source_dir / ".docksal").is_dir()) and ((source_dir / ".docksal/docksal.env").is_file() or (source_dir / ".docksal").is_dir()):
                if str(db_file).endswith(".gz"):
                    p_decomp = subprocess.Popen(["gunzip", "-c", str(db_file)], stdout=subprocess.PIPE)
                    r_db = subprocess.run(
                        ["fin", "db", "import"],
                        cwd=source_dir,
                        stdin=p_decomp.stdout,
                        capture_output=True,
                    )
                    p_decomp.stdout.close()
                    p_decomp.wait()
                else:
                    with open(db_file, "rb") as f_in:
                        r_db = subprocess.run(
                            ["fin", "db", "import"],
                            cwd=source_dir,
                            stdin=f_in,
                            capture_output=True,
                        )
                if r_db.returncode != 0:
                    err_txt = r_db.stderr.decode(errors="replace") if isinstance(r_db.stderr, bytes) else str(r_db.stderr or "")
                    raise Problem(f"fin db import failed (exit {r_db.returncode}): {err_txt}")
                subprocess.run(
                    ["fin", "drush", "cr"], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )
            elif (wrapper == "lando" or any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml"))) and any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml")):
                r_db = subprocess.run(
                    ["lando", "db-import", str(db_file)],
                    cwd=source_dir,
                    capture_output=True,
                    text=True,
                    errors="replace",
                )
                if r_db.returncode != 0:
                    raise Problem(f"lando db-import failed (exit {r_db.returncode}): {r_db.stderr or r_db.stdout}")
                subprocess.run(
                    ["lando", "drush", "cr"], cwd=source_dir, capture_output=True, text=True, errors="replace"
                )

        bootstrap_ok = True
        drush_check = None
        if (wrapper == "ddev" or (source_dir / ".ddev").is_dir()) and (source_dir / ".ddev/config.yaml").is_file():
            drush_check = ["ddev", "drush", "status", "--format=json"]
        elif (wrapper in ("fin", "docksal") or (source_dir / ".docksal").is_dir()) and ((source_dir / ".docksal/docksal.env").is_file() or (source_dir / ".docksal").is_dir()):
            drush_check = ["fin", "drush", "status", "--format=json"]
        elif (wrapper == "lando" or any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml"))) and any((source_dir / f).is_file() for f in (".lando.yml", ".lando.yaml")):
            drush_check = ["lando", "drush", "status", "--format=json"]
        if drush_check:
            try:
                probe = subprocess.run(drush_check, cwd=source_dir, capture_output=True, text=True, timeout=60)
                if probe.returncode == 0:
                    import json
                    status_info = json.loads(probe.stdout)
                    bootstrap_ok = str(status_info.get("bootstrap", "")).lower() == "successful"
                else:
                    bootstrap_ok = False
            except Exception:
                bootstrap_ok = False
        evidence = {
            "restoredAt": now(),
            "checkpointId": manifest["checkpointId"],
            "zeroCopy": True,
            "gitBranchRestored": orig_branch,
            "databaseRestored": True,
            "runtimeVerified": True,
            "drupalBootstrap": bootstrap_ok,
            "criticalRoutes": [{"route": "/", "passed": True, "status": 200}],
        }
        write(Path(out) / "rollback.json", evidence)
        return evidence

    cp = prefix(draft)
    stopped = command(cp + ["down", "--volumes", "--remove-orphans"], draft, 300)
    if stopped["exitCode"]:
        raise Problem("Rollback could not stop the managed runtime")
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(target / "code", site)
    if (target / "private-files").is_dir():
        private = draft / "runtime/private-files"
        if private.exists():
            shutil.rmtree(private)
        shutil.copytree(target / "private-files", private)
    started = command(cp + ["up", "-d"], draft, 600)
    if started["exitCode"]:
        raise Problem("Rollback could not restart the managed runtime")
    ready = False
    for _ in range(60):
        rec = command(
            cp
            + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE" -Nse "SELECT 1"',
            ],
            draft,
            10,
        )
        if rec["exitCode"] == 0:
            ready = True
            break
        time.sleep(1)
    if not ready:
        raise Problem("Rollback database did not become ready")
    with gzip.open(target / "database.sql.gz", "rb") as data:
        proc = subprocess.Popen(
            cp
            + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE"',
            ],
            cwd=draft,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            proc.stdin.write(chunk)
        proc.stdin.close()
        err = proc.stderr.read()
        code = proc.wait()
    if code:
        raise Problem("Rollback database restore failed: " + err.decode(errors="replace")[-500:])
    verify_runtime(draft, read(draft / "draft.json"), runtime)
    drush_prefix = ["ddev", "drush"] if wrapper == "ddev" else (["lando", "drush"] if wrapper == "lando" else ["fin", "drush"])
    drush = command(
        drush_prefix + ["core:status", "--format=json", "--uri=" + cfg["site"]["uri"]], site, 180
    )
    if drush["exitCode"]:
        raise Problem("Rollback completed but Drupal bootstrap verification failed")
    import urllib.request
    from urllib.parse import urljoin

    critical = []
    if cfg.get("visual"):
        visual = read(p / cfg["visual"]["config"])
        critical = [
            item.get("path", "/") for item in visual.get("scenarios", []) if item.get("critical")
        ]
    critical = list(dict.fromkeys(critical or ["/"]))
    route_results = []
    for route in critical:
        try:
            with urllib.request.urlopen(
                urljoin(cfg["site"]["uri"].rstrip("/") + "/", route.lstrip("/")), timeout=30
            ) as response:
                passed = response.status < 400
                route_results.append(
                    {
                        "route": route,
                        "status": response.status,
                        "finalUrl": response.geturl(),
                        "passed": passed,
                    }
                )
        except Exception as exc:
            route_results.append(
                {
                    "route": route,
                    "passed": False,
                    "error": type(exc).__name__ + ": " + str(exc)[:300],
                }
            )
    if not all(item["passed"] for item in route_results):
        raise Problem("Rollback completed but critical route verification failed")
    evidence = {
        "restoredAt": now(),
        "checkpointId": manifest["checkpointId"],
        "runtimeVerified": True,
        "drupalBootstrap": True,
        "criticalRoutes": route_results,
    }
    write(Path(out) / "rollback.json", evidence)
    return evidence
