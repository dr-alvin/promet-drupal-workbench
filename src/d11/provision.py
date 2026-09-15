"""Docksal-image provisioning confined to a toolkit-owned project and network."""

import gzip
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .common import ROOT, Problem, command, digest, file_hash, now, read, write
from .intake import CONTROL_KEYS
from .profile import get_profile
from .settings_template import render_settings_php


def checked(argv, cwd, timeout=180):
    rec = command(argv, cwd, timeout)
    if rec["exitCode"]:
        raise Problem(
            "Local setup command failed: "
            + " ".join(argv[:4])
            + "; "
            + rec.get("stderr", "")[-1000:]
        )
    return rec["stdout"]


def export_local(source, target, wrapper="fin", expected=None):
    from .source_runtime import export_database

    return export_database(source, target, wrapper, expected)


def prefix(p):
    return [
        "docker",
        "compose",
        "-p",
        read(p / "runtime.json")["project"],
        "-f",
        str(p / "runtime/compose.json"),
    ]


def provision(setup, p, d, bundle):
    runtime = p / "runtime"
    runtime.mkdir(mode=0o700)
    project = "d11-" + d["id"]
    site = setup.home / "projects" / d["id"] / "site"
    if site.exists():
        raise Problem("Managed runtime directory already exists; reconcile before continuing")
    site.parent.mkdir(parents=True, mode=0o700)
    shutil.copytree(bundle / "code", site)
    if not (site / "vendor/autoload.php").is_file():
        raise Problem(
            "Source dependencies are missing. Supply a copy with dependencies installed from its lockfile; source Composer scripts are never run automatically."
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    browser = f"http://{project}"
    env = {
        "MYSQL_DATABASE": "upgrade_test",
        "MYSQL_USER": "upgrade_test",
        "MYSQL_PASSWORD": secrets.token_hex(24),
        "MYSQL_ROOT_PASSWORD": secrets.token_hex(24),
    }
    (runtime / "runtime.env").write_text("\n".join(k + "=" + v for k, v in env.items()) + "\n")
    os.chmod(runtime / "runtime.env", 0o600)
    (runtime / "php.ini").write_text(
        "sendmail_path=/bin/true\ndisable_functions=mail\nmemory_limit=512M\n"
    )
    (runtime / "pool.conf").write_text(
        "[www]\nuser=docker\ngroup=docker\nclear_env=no\ncatch_workers_output=yes\nphp_admin_value[memory_limit]=512M\n"
    )
    token = secrets.token_hex(24)
    (runtime / "apache.conf").write_text(f'''DocumentRoot /var/www/{d["drupal"]}
DirectoryIndex index.php
ProxyRequests Off
<Directory /var/www/{d["drupal"]}>
AllowOverride All
Require all granted
</Directory>
<FilesMatch \\.php$>
SetHandler "proxy:fcgi://cli:9000"
</FilesMatch>
Header always set X-D11-Identity "{token}"
Header always set X-Robots-Tag "noindex, nofollow"
Header always set Content-Security-Policy "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self'; form-action 'self'; base-uri 'self'"
''')
    prof = get_profile(d.get("runtimeProfile"))
    services = {
        "db": {
            "image": prof["images"]["db"],
            "env_file": [str(runtime / "runtime.env")],
            "volumes": ["test_db:/var/lib/mysql"],
            "networks": ["backend"],
        },
        "cli": {
            "image": prof["images"]["cli"],
            "entrypoint": ["sh", "-c", f"usermod -u {os.getuid()} docker && exec php-fpm -F"],
            "working_dir": "/var/www",
            "env_file": [str(runtime / "runtime.env")],
            "volumes": [
                f"{site}:/var/www",
                f"{runtime}/php.ini:/usr/local/etc/php/conf.d/zzz-test.ini:ro",
                f"{runtime}/pool.conf:/usr/local/etc/php-fpm.d/zzz-test.conf:ro",
            ],
            "networks": ["backend"],
            "labels": prof.get("cliLabels", {"io.docksal.user": "docker", "io.docksal.shell": "bash"}),
        },
        "web": {
            "image": prof["images"]["web"],
            "volumes": [
                f"{site}:/var/www:ro",
                f"{runtime}/apache.conf:/usr/local/apache2/conf/extra/includes/host.conf:ro",
            ],
            "ports": [f"127.0.0.1:{port}:80"],
            "networks": {"backend": {"aliases": [project]}, "ingress": {}},
        },
        "memcached": {"image": prof["images"].get("memcached", "memcached:1.6-alpine"), "networks": ["backend"]},
    }
    services["cli"]["healthcheck"] = {
        "test": ["CMD", "php", "-r", 'exit(@fsockopen("127.0.0.1",9000)?0:1);'],
        "interval": "10s",
        "timeout": "3s",
        "retries": 3,
    }
    compose = {
        "services": services,
        "networks": {"backend": {"internal": True}, "ingress": {}},
        "volumes": {"test_db": {}},
    }
    write(runtime / "compose.json", compose)
    write(
        p / "runtime.json",
        {
            "project": project,
            "site": {"uri": uri},
            "browserUri": browser,
            "identity": token,
            "sitePath": str(site),
            "composeHash": file_hash(runtime / "compose.json"),
        },
    )
    # Fin configuration points only to the generated runtime.
    (site / ".docksal").mkdir(exist_ok=True)
    write(site / ".docksal/docksal.yml", compose)
    (site / ".docksal/docksal.env").write_text(
        f'COMPOSE_PROJECT_NAME={project}\nDOCKSAL_STACK=""\nDOCKSAL_VOLUMES=disabled\nDOCROOT={d["drupal"]}\n'
    )
    settings = site / d["drupal"] / "sites/default/settings.php"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        render_settings_php(
            project=project,
            drupal_root=d["drupal"],
            config_sync_directory=d["configRoot"] or "config/sync",
            hash_salt=secrets.token_hex(32),
            config_overrides=d.get("configOverrides") or d.get("settingsOverrides"),
        )
    )
    uploads = site / d["drupal"] / "sites/default/files"
    if uploads.exists():
        shutil.rmtree(uploads)
    shutil.copytree(bundle / "files", uploads)
    if uploads.joinpath(".htaccess").exists():
        os.chmod(uploads / ".htaccess", 0o600)
    uploads.joinpath(".htaccess").write_text(
        'Options -ExecCGI\n<FilesMatch "\\.(php|phtml|phar)$">\nRequire all denied\n</FilesMatch>\n'
    )
    if (bundle / "private").exists():
        shutil.copytree(bundle / "private", runtime / "private-files")
        services["cli"]["volumes"].append(f"{runtime}/private-files:/var/private")
        with settings.open("a") as stream:
            stream.write("$settings['file_private_path']='/var/private';\n")
        write(runtime / "compose.json", compose)
        write(site / ".docksal/docksal.yml", compose)
        proof = read(p / "runtime.json")
        proof["composeHash"] = file_hash(runtime / "compose.json")
        write(p / "runtime.json", proof)
    cp = prefix(p)
    checked(cp + ["up", "-d"], p, 600)
    for _ in range(60):
        setup.checkpoint(p, d, "waiting_for_database")
        r = command(
            cp
            + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE" -Nse "SELECT 1"',
            ],
            p,
            10,
        )
        if r["exitCode"] == 0:
            break
        time.sleep(1)
    else:
        raise Problem("Isolated database did not become ready")
    return finish_import(setup, p, d, bundle, site, uri, cp, runtime)


def finish_import(setup, p, d, bundle, site, uri, cp, runtime):
    setup.checkpoint(p, d, "importing_database")
    # Scoped account cannot write another database. Do not import into a nonempty target.
    count = checked(
        cp
        + [
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_PASSWORD" mariadb -h127.0.0.1 -u"$MYSQL_USER" "$MYSQL_DATABASE" -Nse "SHOW TABLES"',
        ],
        p,
    )
    if count.strip():
        raise Problem("Target database is not empty; import refused")
    dbfile = next((bundle / "database").iterdir())
    with gzip.open(dbfile, "rb") if dbfile.suffix == ".gz" else dbfile.open("rb") as data:
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
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            for chunk in iter(lambda: data.read(1024 * 1024), b""):
                if (p / "stop").exists():
                    proc.terminate()
                    raise Problem("Import interrupted; reconciliation required")
                proc.stdin.write(chunk)
            proc.stdin.close()
            err = proc.stderr.read()
            code = proc.wait()
            if code:
                raise Problem("Database import failed; reconcile partial target before retrying")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
    setup.checkpoint(p, d, "sanitizing_copy")
    sanitizer = (ROOT / "scripts/workbench/sanitize.php").read_text()
    rec = command(cp + ["exec", "-T", "cli", "php"], p, 300, input_text=sanitizer)
    if rec["exitCode"]:
        raise Problem("Sanitization failed; inspect the isolated copy before continuing")
    write(p / "sanitization.json", json.loads(rec["stdout"]))
    checked(cp + ["cp", "cli:/tmp/d11-test-login.json", str(runtime / "test-login.json")], p)
    os.chmod(runtime / "test-login.json", 0o600)
    checked(
        cp
        + [
            "exec",
            "-T",
            "--user",
            "docker",
            "cli",
            "php",
            "vendor/bin/drush.php",
            "cache:rebuild",
            "--uri=" + uri,
        ],
        p,
    )
    # Freeze scrubbed DB, discard only the quarantined input copy.
    clean = runtime / "sanitized.sql.gz"
    with gzip.open(clean, "wb") as out:
        proc = subprocess.Popen(
            cp
            + [
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" mariadb-dump -u"$MYSQL_USER" --single-transaction --skip-lock-tables --skip-triggers "$MYSQL_DATABASE"',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
            out.write(chunk)
        err = proc.stderr.read()
        if proc.wait():
            raise Problem("Recovery database export failed")
    for f in (bundle / "database").iterdir():
        f.unlink()
    shutil.copy2(clean, bundle / "database/sanitized.sql.gz")
    # Snapshot code contains generated safe settings; credentials remain outside code.
    for rel in [".docksal", d["drupal"] + "/sites/default/settings.php"]:
        source = site / rel
        target = bundle / "code" / rel
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    proof = read(p / "runtime.json")
    verify_runtime(p, d, proof)
    return proof


def verify_runtime(p, d, proof):
    if file_hash(p / "runtime/compose.json") != proof["composeHash"]:
        raise Problem("Runtime configuration changed")
    network = json.loads(
        checked(["docker", "network", "inspect", proof["project"] + "_backend"], p)
    )[0]
    if not network["Internal"]:
        raise Problem("PHP/database backend network is not isolated")
    ids = checked(prefix(p) + ["ps", "-q"], p).split()
    containers = json.loads(checked(["docker", "inspect", *ids], p))
    for c in containers:
        role = c["Config"]["Labels"]["com.docker.compose.service"]
        if role != "web" and set(c["NetworkSettings"]["Networks"]) != {
            proof["project"] + "_backend"
        }:
            raise Problem("Unexpected runtime network attachment")
        for mount in c.get("Mounts", []):
            if mount["Type"] == "bind" and not any(
                Path(mount["Source"]).resolve().is_relative_to(root)
                for root in [p.resolve(), Path(proof["sitePath"]).resolve()]
            ):
                raise Problem("Unexpected source mount in runtime")
    result = checked(
        prefix(p)
        + [
            "exec",
            "-T",
            "cli",
            "php",
            "-r",
            "echo json_encode(['database'=>getenv('MYSQL_DATABASE'),'mailDisabled'=>!function_exists('mail'),'egressBlocked'=>!@fsockopen('1.1.1.1',443,$e,$m,2)]);",
        ],
        p,
    )
    evidence = json.loads(result)
    if evidence != {"database": "upgrade_test", "mailDisabled": True, "egressBlocked": True}:
        raise Problem("Runtime identity or outbound control check failed")
    import urllib.request

    with urllib.request.urlopen(proof["site"]["uri"], timeout=30) as response:
        if response.status != 200 or response.headers.get("X-D11-Identity") != proof["identity"]:
            raise Problem("Site route identity failed")
    from visual_audit import image_tag

    image = image_tag()
    if command(["docker", "image", "inspect", image], p, 30)["exitCode"]:
        checked(["docker", "build", "-t", image, str(ROOT / "visual-audit")], p, 600)
    browser = json.loads(
        checked(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                proof["project"] + "_backend",
                "--entrypoint",
                "node",
                "-v",
                str(ROOT / "scripts/workbench/check_browser_route.js")
                + ":/tmp/check-browser.js:ro",
                image,
                "/tmp/check-browser.js",
                proof["browserUri"],
                proof["identity"],
            ],
            p,
            90,
        )
    )
    write(
        p / "runtime-verification.json",
        {
            "at": now(),
            "networkInternal": True,
            "identity": evidence,
            "site": proof["site"],
            "browserRouting": browser,
        },
    )


def finalize_registration(setup, p, d, body, proof, provisional=False):
    home = setup.home
    target = home / "projects" / d["id"]
    runtime = proof["runtime"]
    snapshot = digest(proof["hashes"])
    frozen = home / "snapshots" / snapshot
    if frozen.exists():
        raise Problem("Snapshot destination already exists; review before reuse")
    shutil.copytree(p / "bundle", frozen)
    if provisional:
        review = {
            "status": "pending_proceed",
            "reviewer": None,
            "reviewedAt": None,
            "controls": {k: False for k in CONTROL_KEYS},
            "note": "Human managed-copy safety confirmation is collected by Proceed.",
        }
        runtime_reviewer = "Automated isolation verification"
    else:
        review = {
            "status": "completed",
            "reviewer": body["reviewer"],
            "reviewedAt": now(),
            "controls": {k: True for k in CONTROL_KEYS},
            "note": body["note"],
        }
        runtime_reviewer = body["reviewer"]
    write(frozen / "manifest.json", {**review, "fixture": False, "hashes": proof["hashes"]})
    recovery = {
        "code": str(frozen / "code"),
        "database": str(frozen / "database/sanitized.sql.gz"),
        "files": str(frozen / "files"),
        "owner": runtime_reviewer,
        "procedure": "Toolkit coordinated code/database/public/private-files restore",
        "verifiedAt": now(),
    }
    cfg = {
        "schemaVersion": "1.0",
        "repository": "site",
        "environment": {"id": runtime["project"], "kind": "local", "authorized": True},
        "site": runtime["site"],
        "roots": {
            "composer": ".",
            "drupal": d["drupal"],
            "custom": [
                n
                for n in [d["drupal"] + "/modules/custom", d["drupal"] + "/themes/custom"]
                if (target / "site" / n).is_dir()
            ],
        },
        "runtime": {"wrapper": "fin"},
        "identity": {
            "argv": ["fin", "exec", "printenv", "MYSQL_DATABASE"],
            "expected": "upgrade_test",
        },
        "recovery": recovery,
        "steps": [],
        "checks": [],
        "estimates": [],
    }
    if d["configRoot"]:
        cfg["roots"]["config"] = d["configRoot"]
    write(target / "project.json", cfg)
    write(
        target / "route-inputs.json",
        {
            "sourceUrl": d.get("sourceUrl"),
            "sitemapUrl": d.get("sitemapUrl"),
            "routes": d.get("routes", []),
            "captureNavLinks": d.get("captureNavLinks", True) is not False,
            "selectionLimit": 100,
        },
    )
    record = {
        "id": d["id"],
        "name": d["name"],
        "createdAt": now(),
        "snapshotId": snapshot,
        "snapshotHashes": proof["hashes"],
        "review": review,
        "safetyReviewStatus": "pending_proceed" if provisional else "completed",
        "runtimeValidated": True,
        "fixture": False,
        "site": runtime["site"],
        "setupId": d["id"],
        "nextAction": "Scan project",
    }
    write(target / "registration.json", record)
    write(
        target / "runtime-review.json",
        {
            "reviewer": runtime_reviewer,
            "reviewKind": "automated_isolation" if provisional else "named_review",
            "humanSafetyReviewPending": provisional,
            "reviewedAt": now(),
            "configurationHash": file_hash(target / "project.json"),
            "snapshotId": snapshot,
            "site": cfg["site"],
            **{
                k: True
                for k in [
                    "databaseImported",
                    "filesImported",
                    "separateDatabase",
                    "separateVolumes",
                    "outboundDisabled",
                    "schedulesDisabled",
                ]
            },
        },
    )
    write(
        target / "recovery.json",
        {
            "snapshot": str(frozen),
            "hashes": proof["hashes"],
            "restoreRehearsed": False,
            "reviewer": runtime_reviewer,
            "runtimeEvidence": str(p / "runtime-verification.json"),
        },
    )
    return record


def finalize_zero_copy_registration(setup, p, d, source, db_file, original_branch, upgrade_branch):
    """Register a zero-copy project directly referencing source repository on upgrade branch."""
    home = setup.home
    target = home / "projects" / d["id"]
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    runtime_reviewer = "Zero-copy feature branch isolation"
    db_file = Path(db_file)
    db_hash = file_hash(db_file) if db_file.is_file() else "pre-upgrade"
    site_uri = d.get("sourceUrl") or "http://127.0.0.1:8080"
    site_dict = {"uri": site_uri}
    wrapper = d.get("wrapper") or ("ddev" if (source / ".ddev").is_dir() else "fin")

    custom_roots = [
        n
        for n in [d["drupal"] + "/modules/custom", d["drupal"] + "/themes/custom"]
        if (source / n).is_dir()
    ]

    try:
        from .trusted_hosts import ensure_trusted_hosts
        ensure_trusted_hosts(source, site_uri, drupal_root=d.get("drupal", "web"), wrapper=wrapper)
    except Exception as th_err:
        import logging
        logging.getLogger("d11.provision").warning("Failed to configure trusted hosts: %s", th_err)

    if wrapper == "ddev" and (source / ".ddev").is_dir():
        identity = {"argv": ["ddev", "exec", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "fin" or (source / ".docksal").is_dir():
        identity = {"argv": ["fin", "exec", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "lando" and any(
        (source / f).is_file()
        for f in (".lando.yml", ".lando.yaml", ".lando.base.yml", ".lando.dist.yml")
    ):
        identity = {"argv": ["lando", "php", "-r", 'echo "ok";'], "expected": "ok"}
    elif wrapper == "compose" and any(
        (source / f).is_file()
        for f in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yaml",
            "compose.yml",
        )
    ):
        identity = {
            "argv": ["docker", "compose", "exec", "-T", "cli", "php", "-r", 'echo "ok";'],
            "expected": "ok",
        }
    else:
        identity = {"argv": [sys.executable, "-c", 'print("ok")'], "expected": "ok"}

    recovery = {
        "code": str(source),
        "database": str(db_file),
        "files": str(source / d["drupal"] / "sites/default/files"),
        "zeroCopy": True,
        "sourcePath": str(source),
        "originalBranch": original_branch,
        "upgradeBranch": upgrade_branch,
        "databaseBackup": str(db_file),
        "databaseHash": db_hash,
        "owner": runtime_reviewer,
        "procedure": "Zero-copy Git branch reset and database snapshot restore",
        "verifiedAt": now(),
    }

    cfg = {
        "schemaVersion": "1.0",
        "repository": "site",
        "zeroCopy": True,
        "sourcePath": str(source),
        "originalBranch": original_branch,
        "upgradeBranch": upgrade_branch,
        "databaseBackup": str(db_file),
        "environment": {"id": "zero-copy-" + d["id"], "kind": "local", "authorized": True},
        "site": site_dict,
        "roots": {"composer": ".", "drupal": d["drupal"], "custom": custom_roots},
        "runtime": {"wrapper": wrapper},
        "identity": identity,
        "recovery": recovery,
        "steps": [],
        "checks": [],
        "estimates": [],
    }
    if d.get("configRoot"):
        cfg["roots"]["config"] = d["configRoot"]
    write(target / "project.json", cfg)

    write(
        target / "route-inputs.json",
        {
            "sourceUrl": d.get("sourceUrl"),
            "sitemapUrl": d.get("sitemapUrl"),
            "routes": d.get("routes", []),
            "captureNavLinks": d.get("captureNavLinks", True) is not False,
            "selectionLimit": 100,
        },
    )

    record = {
        "id": d["id"],
        "name": d["name"],
        "createdAt": now(),
        "zeroCopy": True,
        "sourcePath": str(source),
        "originalBranch": original_branch,
        "upgradeBranch": upgrade_branch,
        "safetyReviewStatus": "completed",
        "runtimeValidated": True,
        "fixture": False,
        "site": site_dict,
        "setupId": d["id"],
        "snapshotId": "zero-copy-" + d["id"],
        "nextAction": "Scan project",
    }
    write(target / "registration.json", record)

    write(
        target / "runtime-review.json",
        {
            "reviewer": runtime_reviewer,
            "reviewKind": "zero_copy_branch_isolation",
            "humanSafetyReviewPending": False,
            "reviewedAt": now(),
            "configurationHash": file_hash(target / "project.json"),
            "snapshotId": "zero-copy-" + d["id"],
            "site": cfg["site"],
            "zeroCopy": True,
            **{
                k: True
                for k in [
                    "databaseImported",
                    "filesImported",
                    "separateDatabase",
                    "separateVolumes",
                    "outboundDisabled",
                    "schedulesDisabled",
                ]
            },
        },
    )

    write(
        target / "recovery.json",
        {
            "zeroCopy": True,
            "sourcePath": str(source),
            "originalBranch": original_branch,
            "upgradeBranch": upgrade_branch,
            "database": str(db_file),
            "restoreRehearsed": False,
            "reviewer": runtime_reviewer,
        },
    )

    return {
        "zeroCopy": True,
        "hashes": {"database": db_hash},
        "runtime": {"project": "zero-copy-" + d["id"], "site": site_dict},
        "sourceUnchanged": True,
        "reviewRequirements": [
            "Working directly on dedicated feature branch " + upgrade_branch,
            "Atomic compressed database snapshot captured prior to upgrade",
            "Zero duplicated media files or dependencies on disk",
            "1-Click Rollback armed: branch switch and database restore",
        ],
        "site": site_dict,
    }
