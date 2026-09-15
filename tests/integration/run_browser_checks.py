#!/usr/bin/env python3
"""Real container tests against an isolated local HTTP fixture. Requires Docker."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "PORT": str(port),
        "AUDIT_SECRET_USER": "fixture",
        "AUDIT_SECRET_PASSWORD": "fixture",
    }
    server = subprocess.Popen([sys.executable, str(ROOT / "tests/integration/site.py")], env=env)
    summary = []
    out = ROOT / "artifacts/integration"
    out.mkdir(parents=True, exist_ok=True)
    try:
        time.sleep(0.3)
        base = f"http://host.docker.internal:{port}"
        with tempfile.TemporaryDirectory(prefix="d11-browser-fixtures-") as td:
            temp = Path(td)
            common = {
                "environment": {"id": "fixture", "kind": "local", "authorized": True},
                "scenarios": [],
            }
            for name, target, expected in [
                ("same", "/", 0),
                ("mismatch", "/changed", 1),
                ("redirect", "/redirect", 1),
                ("http_error", "/error", 1),
                ("missing_image", "/missing-image", 1),
                ("hidden_image", "/hidden-image", 0),
            ]:
                cfg = {
                    **common,
                    "scenarios": [
                        {
                            "id": "page",
                            "label": name,
                            "referenceUrl": base + "/",
                            "testUrl": base + target + ("?same=1" if name == "same" else ""),
                            "expectedUrl": {
                                "reference": "/",
                                "test": target + ("?same=1" if name == "same" else ""),
                            },
                            "viewport": {"width": 640, "height": 480},
                            "requiredElements": ["h1"],
                            "requiredText": ["Fixture home"],
                            "ready": {"selector": "h1", "timeoutMs": 1000},
                        }
                    ],
                }
                file = temp / (name + ".json")
                file.write_text(json.dumps(cfg))
                dest = temp / name
                for mode, wanted in [("reference", 0), ("test", expected)]:
                    p = subprocess.run(
                        [
                            str(ROOT / "bin/visual-audit"),
                            mode,
                            "--config",
                            str(file),
                            "--output",
                            str(dest),
                        ],
                        env=env,
                        cwd="/tmp",
                        capture_output=True,
                        text=True,
                    )
                    (out / (name + "-" + mode + ".log")).write_text(p.stdout + p.stderr)
                    if not (dest / "result.json").is_file():
                        raise AssertionError(
                            f"{name} {mode}: no result; see artifacts/integration/{name}-{mode}.log"
                        )
                    launcher = json.loads((dest / "launcher-metrics.json").read_text())
                    if mode == "test":
                        assert launcher["imageReused"], "Matching image should be reused"
                    result = json.loads((dest / "result.json").read_text())
                    summary.append(
                        {
                            "name": name + "-" + mode,
                            "expected": wanted,
                            "actual": p.returncode,
                            "result": result,
                            "passed": p.returncode == wanted,
                        }
                    )
                    if p.returncode != wanted:
                        raise AssertionError(
                            f"{name} {mode}: expected {wanted}, got {p.returncode}; see artifacts/integration"
                        )
                if expected:
                    assert (dest / "report.tar.gz").is_file(), (
                        "Failed verification must be packaged"
                    )
                # Replacing baseline is forbidden, and report packaging must still work.
                p = subprocess.run(
                    [
                        str(ROOT / "bin/visual-audit"),
                        "reference",
                        "--config",
                        str(file),
                        "--output",
                        str(dest),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert p.returncode == 3
            auth = json.loads((ROOT / "tests/integration/browser.json").read_text())
            auth["referenceUrl"] = "http://invalid.fixture.test"
            auth["testUrl"] = "http://invalid.fixture.test"
            f = temp / "auth.json"
            f.write_text(json.dumps(auth))
            for mode in ["reference", "test"]:
                p = subprocess.run(
                    [
                        str(ROOT / "bin/visual-audit"),
                        mode,
                        "--config",
                        str(f),
                        "--output",
                        str(temp / "auth"),
                        "--reference-url",
                        base,
                        "--test-url",
                        base,
                    ],
                    env=env,
                    cwd="/tmp",
                    capture_output=True,
                    text=True,
                )
                (out / ("auth-" + mode + ".log")).write_text(p.stdout + p.stderr)
                summary.append(
                    {
                        "name": "authenticated-" + mode,
                        "actual": p.returncode,
                        "passed": p.returncode == 0,
                    }
                )
                if p.returncode:
                    raise AssertionError("Authenticated scenario failed")
            # Early critical feedback must still be followed by the complete catalog.
            catalog = json.loads((temp / "same.json").read_text())
            catalog["scenarios"][0]["critical"] = True
            extra = {**catalog["scenarios"][0], "id": "secondary", "critical": False}
            catalog["scenarios"].append(extra)
            catalog_file = temp / "critical.json"
            catalog_file.write_text(json.dumps(catalog))
            critical_out = temp / "critical"
            for mode in ["reference", "test"]:
                p = subprocess.run(
                    [
                        str(ROOT / "bin/visual-audit"),
                        mode,
                        "--config",
                        str(catalog_file),
                        "--output",
                        str(critical_out),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                (out / ("critical-" + mode + ".log")).write_text(p.stdout + p.stderr)
                assert p.returncode == 0, "Critical catalog failed: " + p.stderr[-500:]
            critical = json.loads((critical_out / "result.json").read_text())
            assert (
                critical["coverage"] == "complete"
                and critical["criticalResult"]["status"] == "passed"
                and len(critical["validCaptures"]) == 2
            )
            summary.append({"name": "critical-then-complete", "passed": True})
            badenv = {**env, "AUDIT_SECRET_PASSWORD": "wrong"}
            p = subprocess.run(
                [
                    str(ROOT / "bin/visual-audit"),
                    "reference",
                    "--config",
                    str(f),
                    "--output",
                    str(temp / "failed-auth"),
                    "--reference-url",
                    base,
                    "--test-url",
                    base,
                ],
                env=badenv,
                capture_output=True,
                text=True,
            )
            summary.append(
                {
                    "name": "failed-authentication",
                    "actual": p.returncode,
                    "passed": p.returncode != 0,
                }
            )
            assert p.returncode != 0
            assert len((temp / "auth/cleanup.jsonl").read_text().splitlines()) == 1
            image = next((temp / "auth/bitmaps_reference").glob("*.png"))
            image.write_bytes(b"corrupt")
            p = subprocess.run(
                [
                    str(ROOT / "bin/visual-audit"),
                    "test",
                    "--config",
                    str(f),
                    "--output",
                    str(temp / "auth"),
                    "--reference-url",
                    base,
                    "--test-url",
                    base,
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            summary.append(
                {
                    "name": "changed-baseline-rejected",
                    "actual": p.returncode,
                    "passed": p.returncode == 3,
                }
            )
            assert p.returncode == 3
            # Wrong required content produces incomplete/failed evidence, never a valid baseline.
            auth["scenarios"][0]["requiredElements"] = ["#absent"]
            f.write_text(json.dumps(auth))
            p = subprocess.run(
                [
                    str(ROOT / "bin/visual-audit"),
                    "reference",
                    "--config",
                    str(f),
                    "--output",
                    str(temp / "incomplete"),
                    "--reference-url",
                    base,
                    "--test-url",
                    base,
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            summary.append(
                {"name": "incomplete-capture", "actual": p.returncode, "passed": p.returncode != 0}
            )
            assert p.returncode != 0
    finally:
        server.terminate()
        server.wait(timeout=5)
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "tests": len(summary),
                "passed": all(x["passed"] for x in summary),
                "evidence": str(out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
