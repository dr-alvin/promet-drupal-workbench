"""Golden-baseline normalizer: the refactor safety net must not flag its own noise."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import normalize_output  # noqa: E402


class NormalizeOutputTests(unittest.TestCase):
    def test_both_compose_project_generations_normalize_identically(self):
        old = normalize_output.normalize_string("docker compose -p d11-analysis-5cadfe8ebd69 up")
        new = normalize_output.normalize_string("docker compose -p d11-stack-prometwe-b7ec063294 up")
        self.assertEqual(old, new)
        self.assertIn("<COMPOSE_PROJECT>", new)

    def test_other_developers_home_directories_normalize_identically(self):
        mine = normalize_output.normalize_string("/Users/alice.dev/.d11/runs/abc/site")
        theirs = normalize_output.normalize_string("/Users/bob/.d11/runs/abc/site")
        linux = normalize_output.normalize_string("/home/ci/.d11/runs/abc/site")
        self.assertEqual(mine, theirs)
        self.assertEqual(mine, linux)
        self.assertIn("<USER_HOME>/.d11/runs/abc/site", mine)
        # Shared, non-home locations are left alone.
        self.assertEqual(normalize_output.normalize_string("/opt/app/x"), "/opt/app/x")

    def test_rector_config_filename_is_not_mistaken_for_a_project(self):
        text = normalize_output.normalize_string("--config=d11-analysis-rector.php")
        self.assertEqual(text, "--config=d11-analysis-rector.php")

    def test_diff_ignores_compose_project_name_only_differences(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, target = Path(tmp) / "base", Path(tmp) / "target"
            for root, project in ((base, "d11-analysis-5cadfe8ebd69"), (target, "d11-stack-prometwe-b7ec063294")):
                root.mkdir()
                (root / "audit-tools.json").write_text(json.dumps({
                    "mode": "disposable",
                    "checks": [{"id": "drupal_rector", "command": {"argv": ["docker", "compose", "-p", project, "exec"]}}],
                }))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                deltas = normalize_output.diff_directories(base, target)
            self.assertEqual(deltas, 0, out.getvalue())

    def test_diff_still_reports_real_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, target = Path(tmp) / "base", Path(tmp) / "target"
            for root, count in ((base, 0), (target, 3)):
                root.mkdir()
                (root / "audit-tools.json").write_text(json.dumps({"checks": [{"id": "drupal_rector", "findingCount": count}]}))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                deltas = normalize_output.diff_directories(base, target)
            self.assertEqual(deltas, 1)


if __name__ == "__main__":
    unittest.main()
