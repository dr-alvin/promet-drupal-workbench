import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/inherited" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


G = module("generate_lifecycle_packet")
V = module("validate_upgrade_artifacts")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "tests/fixtures/lifecycle-reviewed.json").read_text())

    def test_missing_evidence_cannot_be_green(self):
        for key in [
            "infrastructure",
            "phpstan",
            "rector",
            "contentDiff",
            "smokeTests",
            "uatResults",
        ]:
            d = copy.deepcopy(self.data)
            d.pop(key)
            with self.assertRaises(ValueError):
                G.validate_input(d)

    def test_reports_and_packet_share_gate(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "packet"
            G.create_packet(self.data, out)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/inherited/generate_audience_reports.py"),
                    str(out),
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual(V.validate_packet(out)["status"], "success")
            text = (out / "client-summary.md").read_text()
            self.assertIn("GREEN", text)
            self.assertNotIn("does not support starting", text)

    def test_uat_release_mismatch_blocked(self):
        self.data["uatApproval"]["testedReleaseCandidateId"] = "wrong-release"
        # Actual contract uses testedReleaseId; exercise that field as well.
        self.data["uatApproval"]["testedReleaseId"] = "wrong-release"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "packet"
            G.create_packet(self.data, out)
            self.assertEqual(V.validate_packet(out)["status"], "block")

    def test_unknown_evidence_stays_unknown(self):
        self.data.pop("releaseGate")
        self.data["findings"][0]["status"] = "unknown"
        for p in self.data["phases"]:
            p["status"] = "unknown"
        self.data.pop("databaseHealth")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "packet"
            G.create_packet(self.data, out)
            self.assertEqual(
                json.loads((out / "00-baseline/database-health.json").read_text())["status"],
                "unknown",
            )

    def test_baseline_packet_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "packet"
            G.create_packet(self.data, out)
            with self.assertRaises(ValueError):
                G.create_packet(self.data, out)


if __name__ == "__main__":
    unittest.main()
