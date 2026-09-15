"""Deterministic Git Handoff Engine for safe synchronization of Drupal 11 upgrades.

Syncs verified Drupal 11 upgrade assets (composer.json, composer.lock, custom code,
patches, and exported configuration) from the isolated managed copy to a dedicated
Git branch in the source repository.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .common import Problem, command, file_hash, read
from .ignores import (
    HANDOFF_EXCLUDE_DIRS as EXCLUDE_DIRS,
)
from .ignores import (
    HANDOFF_EXCLUDE_EXTENSIONS as EXCLUDE_EXTENSIONS,
)
from .ignores import (
    HANDOFF_EXCLUDE_FILES as EXCLUDE_FILES,
)
from .knowledge import get_default_branch
from .workflow import Workflow


def _is_excluded(relative_path: Path) -> bool:
    """Check if relative path matches any excluded directories or files."""
    parts = relative_path.parts
    # Exclude directories
    for part in parts[:-1]:
        if part in EXCLUDE_DIRS:
            return True
        if part == "modules" or part == "themes":
            continue
    # Check filename
    filename = relative_path.name
    if filename in EXCLUDE_FILES:
        return True
    if any(filename.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
        return True
    # Exclude files inside files/ directories anywhere
    if "files" in parts:
        return True
    return False


def _find_syncable_patterns(managed_dir: Path) -> list[Path]:
    """Identify directories and files that should be synchronized."""
    sync_paths = []

    # Root composer files
    for root_file in ("composer.json", "composer.lock"):
        p = managed_dir / root_file
        if p.is_file():
            sync_paths.append(p)

    # Patches directory
    patches_dir = managed_dir / "patches"
    if patches_dir.is_dir():
        sync_paths.append(patches_dir)

    # Custom modules, themes, profiles
    candidates = [
        "web/modules/custom",
        "modules/custom",
        "web/themes/custom",
        "themes/custom",
        "web/profiles/custom",
        "profiles/custom",
        "config/sync",
        "config/default",
        "web/sites/default/config/sync",
    ]
    for rel in candidates:
        d = managed_dir / rel
        if d.is_dir():
            sync_paths.append(d)

    return sync_paths


def inspect_sync_candidates(managed_dir: Path, source_dir: Path) -> list[dict]:
    """Inspect and preview files that differ between managed copy and source repository."""
    managed_dir = Path(managed_dir).resolve()
    source_dir = Path(source_dir).resolve()
    changed_files = []

    sync_targets = _find_syncable_patterns(managed_dir)
    for target in sync_targets:
        rel_target = target.relative_to(managed_dir)
        if target.is_file():
            source_file = source_dir / rel_target
            if not source_file.is_file() or file_hash(target) != file_hash(source_file):
                action = "added" if not source_file.is_file() else "modified"
                changed_files.append(
                    {
                        "path": str(rel_target),
                        "action": action,
                        "managedHash": file_hash(target),
                        "sourceHash": file_hash(source_file) if source_file.is_file() else None,
                    }
                )
        elif target.is_dir():
            for item in target.rglob("*"):
                if item.is_file():
                    rel_item = item.relative_to(managed_dir)
                    if _is_excluded(rel_item):
                        continue
                    source_item = source_dir / rel_item
                    if not source_item.is_file() or file_hash(item) != file_hash(source_item):
                        action = "added" if not source_item.is_file() else "modified"
                        changed_files.append(
                            {
                                "path": str(rel_item),
                                "action": action,
                                "managedHash": file_hash(item),
                                "sourceHash": file_hash(source_item)
                                if source_item.is_file()
                                else None,
                            }
                        )

    return changed_files


def sync_to_git_branch(
    managed_dir: Path | str,
    source_dir: Path | str,
    branch_name: str | None = None,
    create_branch: bool = True,
    commit: bool = False,
    commit_msg: str | None = None,
    run_command=None,
) -> dict:
    """Synchronize safe upgrade changes to a dedicated Git branch in the source repository."""
    branch_name = branch_name or get_default_branch()
    if run_command is None:
        run_command = command
    managed_dir = Path(managed_dir).resolve()
    source_dir = Path(source_dir).resolve()

    if not managed_dir.is_dir():
        raise Problem(f"Managed site directory does not exist: {managed_dir}")
    if not source_dir.is_dir():
        raise Problem(f"Source repository directory does not exist: {source_dir}")

    # 1. Verify source is a git repository
    git_check = run_command(["git", "rev-parse", "--is-inside-work-tree"], source_dir, 10)
    if git_check.get("exitCode") != 0:
        raise Problem(f"Source directory is not a Git repository: {source_dir}")

    # 2. Guard against target protected branch
    protected_branches = {"main", "master", "production", "release", "live"}
    if branch_name.lower().strip() in protected_branches:
        raise Problem(
            f"Security Guardrail: Direct upgrade to protected branch '{branch_name}' is forbidden. Use a dedicated branch (e.g. {get_default_branch()})."
        )

    # 3. Get current branch and check for dirty working tree
    cur_branch_rec = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], source_dir, 10)
    original_branch = cur_branch_rec.get("stdout", "").strip() or "HEAD"

    pre_status = run_command(["git", "status", "--porcelain"], source_dir, 10)
    pre_dirty = [line.strip() for line in pre_status.get("stdout", "").splitlines() if line.strip()]
    if pre_dirty:
        raise Problem(
            f"Guardrail: Source repository has {len(pre_dirty)} uncommitted change(s). Please commit or stash before running upgrade handoff."
        )

    # 4. Checkout or create target branch (always create a new dedicated branch)
    checkout_rec = run_command(["git", "checkout", "-B", branch_name], source_dir, 30)
    if checkout_rec.get("exitCode") != 0:
        raise Problem(
            f"Failed to create/checkout Git branch '{branch_name}': {checkout_rec.get('stderr')}"
        )

    # 5. Inspect and scan syncable files for secrets
    candidates = inspect_sync_candidates(managed_dir, source_dir)
    from .guardrails import check_secrets_and_sensitive_data

    secret_audit = check_secrets_and_sensitive_data(
        [
            {
                "path": item["path"],
                "content": (managed_dir / item["path"]).read_text(errors="replace")
                if (managed_dir / item["path"]).is_file()
                and (managed_dir / item["path"]).stat().st_size < 1_000_000
                else None,
            }
            for item in candidates
        ]
    )
    if secret_audit.get("status") == "blocked":
        first_violation = secret_audit["violations"][0]["message"]
        raise Problem(f"Security Guardrail: {first_violation}")

    files_synced = []
    for item in candidates:
        rel_path = Path(item["path"])
        managed_file = managed_dir / rel_path
        source_file = source_dir / rel_path

        source_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(managed_file, source_file)
        files_synced.append({"path": str(rel_path), "action": item["action"]})

    # 6. Check git status on target branch
    status_rec = run_command(["git", "status", "--porcelain"], source_dir, 20)
    status_lines = [
        line.strip() for line in status_rec.get("stdout", "").splitlines() if line.strip()
    ]

    # 7. Strict Guardrail Policy: NEVER auto-commit and NEVER auto-push
    committed = False
    auto_pushed = False
    guardrail_notice = "Guardrail Active: Automatic git commit and push are strictly disabled. Changes are synchronized to the new branch for developer review."

    return {
        "success": True,
        "branch": branch_name,
        "originalBranch": original_branch,
        "sourcePath": str(source_dir),
        "managedPath": str(managed_dir),
        "filesChanged": files_synced,
        "gitStatus": status_lines,
        "committed": committed,
        "autoPushed": auto_pushed,
        "guardrailNotice": guardrail_notice,
        "summary": f"Synchronized {len(files_synced)} upgrade files to new branch '{branch_name}' (uncommitted).",
        "instructions": [
            f"cd {source_dir}",
            "git status",
            "git diff",
            "composer install",
            "ddev drush updb -y && ddev drush cr",
            'git add -A && git commit -m "Drupal 11 upgrade: updated dependencies and custom modules"',
            f"git push -u origin {branch_name}",
        ],
    }


def handoff_cli():
    """CLI runner for `bin/d11 handoff <PROJECT_ID>`."""
    parser = argparse.ArgumentParser(
        description="Synchronize Drupal 11 upgrade files to a Git branch in the source repository."
    )
    parser.add_argument("project", help="Project ID or project directory name")
    parser.add_argument(
        "--branch",
        default=None,
        help="Target Git branch name (default: derived from target knowledge)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Automatically commit the changes on the target branch",
    )
    parser.add_argument("-m", "--message", help="Commit message (if --commit is enabled)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview files that will be synchronized without modifying the repository",
    )

    args = parser.parse_args()
    branch_name = args.branch or get_default_branch()
    workflow = Workflow()
    pid = args.project

    p_path = workflow.home / "projects" / pid
    if not p_path.is_dir():
        # Try matching by directory name or list
        projects = workflow.projects()
        match = next((p for p in projects if p.get("id") == pid or p.get("name") == pid), None)
        if match:
            pid = match["id"]
            p_path = workflow.home / "projects" / pid
        else:
            print(f"Error: Unknown project ID '{pid}'.", file=sys.stderr)
            return 1

    p, cfg = workflow.project(pid)
    managed_site = p / "site"
    source_root = getattr(args, "source", None)
    if not source_root:
        draft_file = workflow.home / "drafts" / pid / "draft.json"
        if draft_file.is_file():
            source_root = read(draft_file).get("source")
    if not source_root:
        for r in (workflow.home / "runs").glob("*/result/context.json"):
            c = read(r)
            if c.get("roots", {}).get("repository") and c.get("roots", {}).get("repository") != str(
                managed_site.resolve()
            ):
                source_root = c["roots"]["repository"]
                break
    if not source_root:
        source_root = managed_site
    source_root = Path(source_root).resolve()

    if not source_root.is_dir():
        print(f"Error: Source repository path not found: {source_root}", file=sys.stderr)
        return 1

    if args.dry_run:
        candidates = inspect_sync_candidates(managed_site, source_root)
        print(
            f"\nDry-run: {len(candidates)} file(s) would be synchronized to branch '{args.branch}':"
        )
        for c in candidates:
            print(f"  [{c['action']}] {c['path']}")
        return 0

    try:
        res = sync_to_git_branch(
            managed_dir=managed_site,
            source_dir=source_root,
            branch_name=branch_name,
            commit=args.commit,
            commit_msg=args.message,
        )
        print("\n" + "=" * 76)
        print("  \033[1;32m✔ GIT HANDOFF SUCCESSFUL!\033[0m")
        print("=" * 76)
        print(f"  Target Branch:  \033[1m{res['branch']}\033[0m")
        print(f"  Source Path:    {res['sourcePath']}")
        print(f"  Files Updated:  {len(res['filesChanged'])}")
        print(f"  \033[1;36m🔒 {res['guardrailNotice']}\033[0m")
        for f in res["filesChanged"]:
            print(f"    • [{f['action']}] {f['path']}")
        print("-" * 76)
        print("  \033[1mNext Steps:\033[0m")
        for step in res["instructions"]:
            print(f"    $ {step}")
        print("=" * 76 + "\n")
        return 0
    except Problem as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
