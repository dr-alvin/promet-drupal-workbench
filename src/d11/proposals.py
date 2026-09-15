"""AI returns data; the deterministic executor applies reviewed file changes."""

import difflib
from pathlib import Path

from .common import Problem, digest, file_hash
from .execution import validate_steps
from .intake import safe_path

PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "changes", "steps", "limitations"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "path",
                    "beforeSha256",
                    "after",
                    "finding",
                    "rationale",
                    "verificationCheckIds",
                ],
                "properties": {
                    "path": {"type": "string"},
                    "beforeSha256": {"type": ["string", "null"]},
                    "after": {"type": "string"},
                    "finding": {"type": "string"},
                    "rationale": {"type": "string"},
                    "verificationCheckIds": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        # Steps are chosen by registered IDs, not arbitrary AI shell commands.
        "steps": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_proposal(proposal, root, registered_steps, checks):
    from jsonschema import ValidationError, validate

    try:
        validate(proposal, PROPOSAL_SCHEMA)
    except ValidationError as e:
        raise Problem("Malformed AI proposal: " + e.message, 2)
    if len(proposal["changes"]) > 50:
        raise Problem("Proposal exceeds 50 file changes; split the batch")
    names = set()
    diffs = []
    for change in proposal["changes"]:
        p = safe_path(root, change["path"])
        parts = Path(change["path"]).parts
        if (
            not parts
            or any(
                x in parts
                for x in (
                    ".git",
                    ".codex",
                    ".agents",
                    "vendor",
                    "node_modules",
                    "files",
                    "private",
                    "core",
                )
            )
            or p.name in ("settings.php", ".env", "auth.json")
        ):
            raise Problem(
                "Protected file is outside automated remediation scope: " + change["path"]
            )
        if change["path"] in names:
            raise Problem("Duplicate proposed path")
        names.add(change["path"])
        before = p.read_text() if p.is_file() else ""
        expected = file_hash(p) if p.is_file() else None
        if expected != change["beforeSha256"]:
            raise Problem("Proposal source is stale: " + change["path"])
        if len(change["after"].encode()) > 25_000_000:
            raise Problem("Proposed file exceeds size limit")
        if not change["verificationCheckIds"] or not set(change["verificationCheckIds"]).issubset(
            checks
        ):
            raise Problem("Each change requires registered verification check IDs")
        diffs.append(
            "".join(
                difflib.unified_diff(
                    before.splitlines(True),
                    change["after"].splitlines(True),
                    fromfile="before/" + change["path"],
                    tofile="after/" + change["path"],
                )
            )
        )
    ids = {s["id"]: s for s in registered_steps}
    if len(set(proposal["steps"])) != len(proposal["steps"]) or not set(proposal["steps"]).issubset(
        ids
    ):
        raise Problem("AI selected an unregistered or duplicate command step")
    selected = [ids[i] for i in proposal["steps"]]
    validate_steps(selected)
    if not proposal["changes"] and not selected:
        raise Problem("No actionable remediation proposed; inspect limitations")
    return {
        "proposal": proposal,
        "diff": "\n".join(diffs),
        "steps": selected,
        "digest": digest(proposal),
    }


def apply_changes(root, proposal, verify_only=False):
    # Check every input before writing any file; a failed partial write is never retried.
    for c in proposal["changes"]:
        p = safe_path(root, c["path"])
        actual = file_hash(p) if p.is_file() else None
        if actual != c["beforeSha256"]:
            raise Problem("Patch input changed: " + c["path"])
    if not verify_only:
        for c in proposal["changes"]:
            p = safe_path(root, c["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c["after"])
