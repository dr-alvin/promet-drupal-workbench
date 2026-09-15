"""Tests for retention pruning and content-addressed compatibility report deduplication."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from d11.common import ROOT, Problem, digest, read, set_d11_home, write
from d11.compatibility import write_compatibility_report
from d11.workflow import Workflow


class TestPruneAndDeduplication(unittest.TestCase):
    def setUp(self):
        self.orig_environ = os.environ.copy()
        self.tmp = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.tmp.name).resolve()
        set_d11_home(self.home_dir)
        self.w = Workflow(self.home_dir)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_environ)
        self.tmp.cleanup()

    def test_prune_runs_keeps_n_most_recent(self):
        runs_dir = self.home_dir / "runs"
        # Create 7 runs with timestamps 2026-09-01 through 2026-09-07
        for i in range(1, 8):
            rid = f"run-00{i}"
            r_dir = runs_dir / rid
            r_dir.mkdir(parents=True, exist_ok=True)
            write(
                r_dir / "state.json",
                {
                    "id": rid,
                    "status": "completed",
                    "startedAt": f"2026-09-0{i}T10:00:00Z",
                    "finishedAt": f"2026-09-0{i}T10:05:00Z",
                },
            )

        res = self.w.prune(keep=3)
        self.assertEqual(res["prunedCount"], 4)
        self.assertEqual(res["retainedCount"], 3)
        self.assertEqual(sorted(res["deletedRunIds"]), ["run-001", "run-002", "run-003", "run-004"])

        # Remaining runs should be run-005, run-006, run-007
        remaining = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
        self.assertEqual(remaining, ["run-005", "run-006", "run-007"])

    def test_prune_negative_keep_raises_problem(self):
        with self.assertRaises(Problem):
            self.w.prune(keep=-1)

    def test_content_addressing_on_write(self):
        out_dir = self.home_dir / "runs" / "sample-run"
        out_dir.mkdir(parents=True, exist_ok=True)
        report_data = {
            "schemaVersion": "1.0",
            "summary": {"installed": 10, "attentionRequired": 2},
            "extensions": [{"name": "token", "status": "update_available"}],
        }
        report_file = write_compatibility_report(out_dir, report_data, home=self.home_dir)
        self.assertTrue(report_file.is_file())
        saved = read(report_file)
        self.assertIn("digest", saved)
        d = saved["digest"]

        # Canonical file should exist in store/compatibility/<digest>.json
        canonical = self.home_dir / "store" / "compatibility" / f"{d}.json"
        self.assertTrue(canonical.is_file())
        self.assertEqual(read(canonical), saved)

        # On the same filesystem, both files share the same inode
        self.assertEqual(report_file.stat().st_ino, canonical.stat().st_ino)

    def test_deduplicate_compatibility_reports(self):
        runs_dir = self.home_dir / "runs"
        report_data = {"schemaVersion": "1.0", "extensions": [{"name": "admin_toolbar"}]}
        d = digest({k: v for k, v in report_data.items() if k != "digest"})
        report_data["digest"] = d

        # Write separate duplicate files across 3 runs without hardlinks
        for i in range(1, 4):
            r_dir = runs_dir / f"run-{i}"
            r_dir.mkdir(parents=True, exist_ok=True)
            target = r_dir / "compatibility-report.json"
            write(target, report_data)

        # Run deduplication
        res = self.w.deduplicate_compatibility_reports()
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["deduplicated"], 3)

        canonical = self.home_dir / "store" / "compatibility" / f"{d}.json"
        self.assertTrue(canonical.is_file())

        # All 3 run reports should now share the canonical file inode
        canonical_ino = canonical.stat().st_ino
        for i in range(1, 4):
            r_file = runs_dir / f"run-{i}" / "compatibility-report.json"
            self.assertEqual(r_file.stat().st_ino, canonical_ino)

    def test_cli_prune_command(self):
        runs_dir = self.home_dir / "runs"
        for i in range(1, 6):
            r_dir = runs_dir / f"run-{i}"
            r_dir.mkdir(parents=True, exist_ok=True)
            write(
                r_dir / "state.json",
                {"id": f"run-{i}", "status": "completed", "startedAt": f"2026-09-0{i}T10:00:00Z"},
            )

        cmd = [
            sys.executable,
            "-m",
            "d11.cli",
            "--home",
            str(self.home_dir),
            "prune",
            "--keep",
            "2",
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, f"prune failed: {proc.stderr}")
        self.assertIn("Pruned 3 old run(s); retained 2.", proc.stdout)

        remaining = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
        self.assertEqual(remaining, ["run-4", "run-5"])


if __name__ == "__main__":
    unittest.main()
