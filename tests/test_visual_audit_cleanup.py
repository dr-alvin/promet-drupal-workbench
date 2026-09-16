"""Timed-out visual audits must not leave one-off containers running."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import visual_audit  # noqa: E402


class _Completed:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


class RemoveProjectContainersTests(unittest.TestCase):
    def test_removes_every_container_of_the_project_and_reports_survivors(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ["docker", "ps"]:
                # first listing: two containers; after rm: none
                return _Completed("abc123\ndef456\n" if len([c for c in calls if c[:2] == ["docker", "ps"]]) == 1 else "")
            return _Completed()

        with patch("visual_audit.subprocess.run", side_effect=fake_run):
            survivors = visual_audit._remove_project_containers("d11-zero-copy-proj", env={})
        self.assertEqual(survivors, [])
        rm = next(c for c in calls if c[:3] == ["docker", "rm", "-f"])
        self.assertEqual(rm[3:], ["abc123", "def456"])
        for c in calls:
            if c[:2] == ["docker", "ps"]:
                self.assertIn("label=com.docker.compose.project=d11-zero-copy-proj", c)

    def test_nothing_to_remove_is_quiet(self):
        with patch("visual_audit.subprocess.run", return_value=_Completed("")) as run:
            self.assertEqual(visual_audit._remove_project_containers("p", env={}), [])
        self.assertEqual(run.call_count, 1)  # no rm when nothing is listed

    def test_survivors_are_returned(self):
        with patch("visual_audit.subprocess.run", return_value=_Completed("zzz\n")):
            self.assertEqual(visual_audit._remove_project_containers("p", env={}), ["zzz"])


if __name__ == "__main__":
    unittest.main()
