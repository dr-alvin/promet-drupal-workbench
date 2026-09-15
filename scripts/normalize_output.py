#!/usr/bin/env python3
"""Normalizes run outputs by stripping non-deterministic and dynamic values."""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("D11_HOME", Path.home() / ".d11"))
WORKBENCH = ROOT / "artifacts" / "workbench"

RE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
RE_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")
RE_TMP = re.compile(r'/(?:private/)?var/folders/[^\s"\'<>]+|/tmp/[^\s"\'<>]+')
RE_COMPOSE_PROJ = re.compile(r"\bd11-analysis-[0-9a-f]{8,32}\b")

DYNAMIC_KEYS = {
    "runId": "<RUN_ID>",
    "planId": "<PLAN_ID>",
    "pid": 0,
    "timestamp": "<TIMESTAMP>",
    "generatedAt": "<TIMESTAMP>",
    "verifiedAt": "<TIMESTAMP>",
    "inspectedAt": "<TIMESTAMP>",
    "startedAt": "<TIMESTAMP>",
    "finishedAt": "<TIMESTAMP>",
    "createdAt": "<TIMESTAMP>",
    "at": "<TIMESTAMP>",
    "recordedAt": "<TIMESTAMP>",
    "elapsedSeconds": 0,
    "configuredChecksElapsedSeconds": 0,
    "subprocessCount": 0,
    "identity": "<IDENTITY_TOKEN>",
    "toolkitVersion": "<TOOLKIT_VERSION>",
    "launcher": "<LAUNCHER_HASH>",
    "dependencies": "<DEPS_HASH>",
    "MYSQL_PASSWORD": "<PASSWORD>",
    "MYSQL_ROOT_PASSWORD": "<PASSWORD>",
}

HASH_DIGEST_KEYS = {
    "batchHash",
    "approvalDigest",
    "inputsHash",
    "recoveryDigest",
    "decisionDigest",
    "composeHash",
    "identityHash",
}

CHECK_FILES = [
    "gate.json",
    "plan.json",
    "compatibility-report.json",
    "audit-tools.json",
    "result/result.json",
    "result/context.json",
]


def normalize_string(text: str, root_str: str = str(ROOT), home_str: str = str(HOME)) -> str:
    text = text.replace(str(WORKBENCH), "<HOME>")
    text = text.replace(home_str, "<HOME>")
    text = text.replace(root_str, "<ROOT>")
    text = RE_TMP.sub("<TMPDIR>", text)
    text = RE_COMPOSE_PROJ.sub("d11-analysis-<RUN_ID>", text)
    text = RE_UUID.sub("<UUID>", text)
    text = RE_ISO_TS.sub("<TIMESTAMP>", text)
    return text


def normalize_data(val, root_str: str = str(ROOT), home_str: str = str(HOME)):
    if isinstance(val, dict):
        # Strip or normalize the toolkit self-hash block
        if "toolkit" in val and isinstance(val["toolkit"], dict):
            val = dict(val)
            val["toolkit"] = "<TOOLKIT_HASHES>"

        res = {}
        for k, v in val.items():
            if k in DYNAMIC_KEYS:
                res[k] = DYNAMIC_KEYS[k]
            elif k in HASH_DIGEST_KEYS and isinstance(v, str) and len(v) == 64:
                res[k] = "<DIGEST>"
            else:
                res[k] = normalize_data(v, root_str, home_str)
        return res
    elif isinstance(val, list):
        return [normalize_data(item, root_str, home_str) for item in val]
    elif isinstance(val, str):
        return normalize_string(val, root_str, home_str)
    else:
        return val


def normalize_json_file(path: Path) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return normalize_data(data)


def diff_directories(dir1: Path, dir2: Path) -> int:
    deltas = 0
    for rel_path in CHECK_FILES:
        p1 = dir1 / rel_path
        p2 = dir2 / rel_path

        if not p1.is_file() and not p2.is_file():
            continue
        if not p1.is_file():
            print(f"❌ Missing file in baseline ({dir1}): {rel_path}", file=sys.stderr)
            deltas += 1
            continue
        if not p2.is_file():
            print(f"❌ Missing file in target run ({dir2}): {rel_path}", file=sys.stderr)
            deltas += 1
            continue

        try:
            norm1 = normalize_json_file(p1)
            norm2 = normalize_json_file(p2)
        except Exception as e:
            print(f"❌ Error parsing {rel_path}: {e}", file=sys.stderr)
            deltas += 1
            continue

        str1 = json.dumps(norm1, indent=2, sort_keys=True).splitlines(keepends=True)
        str2 = json.dumps(norm2, indent=2, sort_keys=True).splitlines(keepends=True)

        if str1 != str2:
            deltas += 1
            print(f"\n❌ Semantic delta detected in: {rel_path}")
            diff = difflib.unified_diff(
                str1, str2, fromfile=f"baseline/{rel_path}", tofile=f"target/{rel_path}", n=3
            )
            # Print first 50 lines of diff
            diff_lines = list(diff)
            for line in diff_lines[:50]:
                sys.stdout.write(line)
            if len(diff_lines) > 50:
                print(f"... ({len(diff_lines) - 50} more lines truncated)")

    if deltas == 0:
        print("✔ Zero semantic differences detected against baseline.")
        return 0
    else:
        print(f"\n✖ Found {deltas} file(s) with semantic differences.", file=sys.stderr)
        return 1


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--diff":
        d1 = Path(sys.argv[2]).resolve()
        d2 = Path(sys.argv[3]).resolve()
        sys.exit(diff_directories(d1, d2))

    if len(sys.argv) < 2:
        print("Usage: normalize_output.py <file.json> [output.json]", file=sys.stderr)
        print("       normalize_output.py --diff <dir1> <dir2>", file=sys.stderr)
        sys.exit(1)

    src = Path(sys.argv[1])
    norm = normalize_json_file(src)
    formatted = json.dumps(norm, indent=2, sort_keys=True)
    if len(sys.argv) > 2:
        out_path = Path(sys.argv[2])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(formatted + "\n")
    else:
        print(formatted)


if __name__ == "__main__":
    main()
