"""Reviewed snapshot intake. Never import from or mutate a client checkout."""

import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from .common import Problem, digest, file_hash, now, read, schema, write

CONTROL_KEYS = (
    "sanitized",
    "credentialsRemoved",
    "outboundWritesDisabled",
    "schedulesDisabled",
    "runtimeConfigurationReviewed",
)


def safe_path(root, value):
    root = Path(root).resolve()
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise Problem("Expected a relative path within the managed workspace", 64)
    path = root / raw
    if any(
        p.is_symlink()
        for p in [path]
        + [parent for parent in path.parents if parent != root and parent.is_relative_to(root)]
    ):
        raise Problem("Symlinks are not permitted in managed artifacts", 64)
    if not path.resolve().is_relative_to(root):
        raise Problem("Path escapes managed workspace", 64)
    return path


def inventory(root):
    root = Path(root)
    rows = {}
    if root.is_symlink():
        raise Problem("Snapshot component cannot be a symlink", 64)
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise Problem("Snapshot contains symlink: " + str(p.relative_to(root)), 64)
        if p.is_file():
            rel = p.relative_to(root)
            if "vendor" in rel.parts:
                continue
            if (
                any(x in rel.parts for x in (".git", ".codex", ".agents", "node_modules"))
                or p.name in (".env", "auth.json")
                or p.suffix in (".pem", ".key")
            ):
                raise Problem(
                    "Remove credentials, agent configuration and repository metadata before intake: "
                    + str(rel)
                )
            rows[str(rel)] = file_hash(p)
        elif not p.is_dir():
            raise Problem("Only regular files and directories are accepted")
    return rows


def import_snapshot(home, bundle, project_id, config):
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,47}", project_id):
        raise Problem("Project ID must be 3–48 lowercase letters, digits or hyphens", 64)
    started = time.monotonic()
    home = Path(home).resolve()
    bundle = safe_path(home / "inbox", bundle)
    manifest = read(bundle / "manifest.json")
    if (
        not manifest.get("reviewer")
        or not manifest.get("reviewedAt")
        or not all(manifest.get("controls", {}).get(k) is True for k in CONTROL_KEYS)
    ):
        raise Problem(
            "A named sanitization and isolation review is required; local alone is not sanitized"
        )
    hashes = {name: inventory(bundle / name) for name in ("code", "database", "files")}
    if not hashes["code"] or not hashes["database"] or not hashes["files"]:
        raise Problem("Matching code, database and files components are required")
    if hashes != manifest.get("hashes"):
        raise Problem("Snapshot hashes differ from reviewed manifest")
    schema(config, "project")
    for key in ("composer", "drupal", "config"):
        if config.get("roots", {}).get(key):
            safe_path(bundle / "code", config["roots"][key])
    for path in config.get("roots", {}).get("custom", []):
        safe_path(bundle / "code", path)
    if config["environment"]["kind"] != "local" or config["environment"]["authorized"] is not True:
        raise Problem("Dashboard requires authorized local copies")
    host = urlparse(config.get("site", {}).get("uri", "")).hostname or ""
    if not host.startswith("d11-" + project_id + "."):
        raise Problem(
            "Site hostname must start d11-" + project_id + ". and identify the isolated copy"
        )
    target = home / "projects" / project_id
    if target.exists():
        raise Problem("Project already registered; use a new project ID for a repeat run")
    # Validate before the first copy. Hashes are checked again after copying.
    frozen = home / "snapshots" / digest(hashes)
    if not frozen.exists():
        frozen.mkdir(parents=True)
        for name in hashes:
            shutil.copytree(bundle / name, frozen / name)
        write(frozen / "manifest.json", manifest)
    if {name: inventory(frozen / name) for name in hashes} != hashes:
        raise Problem("Preserved snapshot changed")
    target.mkdir(parents=True)
    shutil.copytree(frozen / "code", target / "site")
    from .budget import scaffold_delivery_budget

    scaffold_delivery_budget(config)
    config = {**config, "repository": "site"}
    write(target / "project.json", config)
    record = {
        "intakeElapsedSeconds": round(time.monotonic() - started, 3),
        "id": project_id,
        "createdAt": now(),
        "snapshotId": frozen.name,
        "snapshotHashes": hashes,
        "review": {k: manifest[k] for k in ("reviewer", "reviewedAt", "controls")},
        "runtimeValidated": False,
        "fixture": manifest.get("fixture") is True,
        "note": "Code imported. Database/files runtime import and isolated resource identity must be verified before runtime actions.",
    }
    write(target / "registration.json", record)
    return record
