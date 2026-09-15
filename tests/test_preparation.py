import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_toolkit as toolkit
from d11lib.common import Problem, digest, file_hash, now, write
from d11lib.discovery import discover
from d11lib.execution import execute, plan, prerequisites


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = toolkit.ToolkitTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def batch(self):
        f = self.fixture
        f.approved()
        f.cfg.pop("requirementsEvidence")
        f.cfg.pop("baselineEvidence")
        f.cfg["steps"] = f.cfg["steps"][:1]
        f.cfg["steps"][0]["preparation"] = True
        write(f.base / "failure.json", {"scanner": "collision"})
        write(
            f.base / "prep.json",
            {
                "environment": f.cfg["environment"],
                "site": f.cfg["site"],
                "reviewer": "Fixture operator",
                "reviewedAt": now(),
                "isolationVerified": True,
                "baselineFailures": ["scanner collision"],
                "evidence": [
                    {"path": "failure.json", "sha256": file_hash(f.base / "failure.json")}
                ],
            },
        )
        f.cfg["preparationEvidence"] = "prep.json"
        ctx = discover(f.cfg)
        ctx["runtime"]["status"] = "collected"
        ctx["runtime"]["commands"]["status"] = {"data": {"drupal-version": "10.3.0"}}
        p = plan(f.cfg, ctx, f.out, preparation=True)
        a = {
            "schemaVersion": "1.0",
            "planId": p["planId"],
            "environment": p["environment"],
            "site": p["site"],
            "approver": "Fixture tester",
            "approvedAt": now(),
            "steps": ["first"],
            "recoveryDigest": digest(p["backup"]),
        }
        return p, a

    def test_preparation_without_clean_target_scans(self):
        p, a = self.batch()
        prerequisites(self.fixture.cfg, p, a)
        run, code = execute(self.fixture.cfg, p, a, self.fixture.out, preparation=True)
        self.assertEqual(code, 0)
        self.assertEqual(run["status"], "prepared")

    def test_preparation_cannot_authorize_upgrade(self):
        p, a = self.batch()
        with self.assertRaises(Problem):
            execute(self.fixture.cfg, p, a, self.fixture.out, preparation=False)

    def test_changed_failure_evidence(self):
        p, a = self.batch()
        write(self.fixture.base / "failure.json", {"different": True})
        with self.assertRaises(Problem):
            prerequisites(self.fixture.cfg, p, a)


if __name__ == "__main__":
    unittest.main()
