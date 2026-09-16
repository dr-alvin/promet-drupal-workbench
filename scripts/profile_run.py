#!/usr/bin/env python3
"""Attribute a run's wall time to phases and commands from its own evidence.

Usage:
    profile_run.py <run_dir> [--json] [--top N]

Reads ``state.json`` (wall time), ``events.jsonl`` (``span`` events and checkpoints)
and ``commands.jsonl`` (subprocess records). Commands nested inside another command's
window (for example drush probes run by the ``d11 assess`` child process) are counted
once, under their parent, so the totals are not double-counted. The remainder that no
top-level command explains is reported as *unattributed*; spans show where it went.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path


def _iso(value):
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _jsonl(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"type": "malformed_line", "length": len(line)})
    return rows


def profile(run_dir):
    run_dir = Path(run_dir)
    state = json.loads((run_dir / "state.json").read_text()) if (run_dir / "state.json").is_file() else {}
    events = _jsonl(run_dir / "events.jsonl")
    commands = _jsonl(run_dir / "commands.jsonl")

    finished = [c for c in commands if c.get("type") == "command_finished" and _iso(c.get("startedAt"))]
    windows = [(_iso(c["startedAt"]), _iso(c["finishedAt"]) or _iso(c["startedAt"]), c) for c in finished]
    top_level = []
    for start, end, rec in windows:
        nested = any(
            other is not rec and o_start <= start and end <= o_end and (o_start, o_end) != (start, end)
            for o_start, o_end, other in windows
        )
        if not nested:
            top_level.append(rec)

    by_command = defaultdict(lambda: {"count": 0, "seconds": 0.0})
    for rec in top_level:
        argv = rec.get("argv") or []
        key = " ".join(a for a in argv[:4] if not a.startswith("/")) or "(unknown)"
        by_command[key]["count"] += 1
        by_command[key]["seconds"] += float(rec.get("elapsedSeconds") or 0)

    spans = [e for e in events if e.get("type") == "span"]
    by_span = defaultdict(lambda: {"count": 0, "seconds": 0.0, "failed": 0})
    for e in spans:
        item = by_span[e.get("step") or "(unnamed)"]
        item["count"] += 1
        item["seconds"] += float(e.get("elapsedSeconds") or 0)
        item["failed"] += e.get("status") == "failed"

    checkpoints = [(e.get("at"), e.get("checkpoint")) for e in events if e.get("type") == "checkpoint"]
    checkpoint_spans = []
    for (at, name), (next_at, _) in zip(checkpoints, checkpoints[1:]):  # noqa: B905 (py3.9: no strict=)
        a, b = _iso(at), _iso(next_at)
        if a and b:
            checkpoint_spans.append({"checkpoint": name, "seconds": round((b - a).total_seconds(), 3)})

    wall = state.get("elapsedSeconds")
    if wall is None and _iso(state.get("startedAt")) and _iso(state.get("finishedAt")):
        wall = (_iso(state["finishedAt"]) - _iso(state["startedAt"])).total_seconds()
    in_commands = sum(float(r.get("elapsedSeconds") or 0) for r in top_level)
    malformed = sum(1 for c in commands if c.get("type") == "malformed_line")
    return {
        "run": run_dir.name,
        "status": state.get("status"),
        "scanMode": state.get("scanMode"),
        "wallSeconds": round(wall, 3) if wall is not None else None,
        "commandSeconds": round(in_commands, 3),
        "unattributedSeconds": round(wall - in_commands, 3) if wall is not None else None,
        "commandCount": len(finished),
        "topLevelCommandCount": len(top_level),
        "malformedCommandLines": malformed,
        "commands": dict(sorted(by_command.items(), key=lambda kv: -kv[1]["seconds"])),
        "spans": dict(sorted(by_span.items(), key=lambda kv: -kv[1]["seconds"])),
        "checkpointSpans": checkpoint_spans,
    }


def _table(rows, top):
    for key, item in list(rows.items())[:top]:
        extra = f"  x{item['count']}" if item.get("count", 1) != 1 else ""
        failed = f"  ({item['failed']} failed)" if item.get("failed") else ""
        print(f"  {item['seconds']:9.1f}s  {key[:90]}{extra}{failed}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir")
    parser.add_argument("--json", action="store_true", help="emit the profile as JSON")
    parser.add_argument("--top", type=int, default=12, help="rows per table")
    args = parser.parse_args(argv)
    result = profile(args.run_dir)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0
    wall = result["wallSeconds"]
    print(f"run {result['run']}  status={result['status']}  scanMode={result['scanMode']}")
    if wall is not None:
        share = 100 * result["unattributedSeconds"] / wall if wall else 0
        print(f"wall {wall:.1f}s  |  top-level commands {result['commandSeconds']:.1f}s  |  unattributed {result['unattributedSeconds']:.1f}s ({share:.0f}%)")
    print(f"commands: {result['commandCount']} recorded, {result['topLevelCommandCount']} top-level, {result['malformedCommandLines']} malformed lines")
    print("\nspans (phases):")
    _table(result["spans"], args.top) if result["spans"] else print("  (no span events; run predates span instrumentation)")
    print("\ntop-level commands:")
    _table(result["commands"], args.top)
    if result["checkpointSpans"]:
        print("\ncheckpoint spans:")
        for item in result["checkpointSpans"]:
            print(f"  {item['seconds']:9.1f}s  {item['checkpoint']}")
    return 1 if result["malformedCommandLines"] else 0


if __name__ == "__main__":
    sys.exit(main())
