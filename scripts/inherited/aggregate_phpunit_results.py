#!/usr/bin/env python3
"""Normalize one or more existing PHPUnit/JUnit XML reports to contract 3.0."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA_VERSION = "3.0"


def number(node: ET.Element, key: str) -> int:
    try:
        return int(float(node.attrib.get(key, "0")))
    except ValueError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="+", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    suites = []
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    try:
        for path in sorted(args.xml):
            root = ET.parse(path).getroot()
            nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
            for node in nodes:
                item = {
                    "name": node.attrib.get("name", path.stem),
                    **{key: number(node, key) for key in totals},
                }
                suites.append(item)
                for key in totals:
                    totals[key] += item[key]
    except Exception as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    status = "passed" if totals["failures"] == 0 and totals["errors"] == 0 else "failed"
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "releaseCandidateId": args.release_id,
        "summary": totals,
        "suites": sorted(suites, key=lambda item: item["name"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
