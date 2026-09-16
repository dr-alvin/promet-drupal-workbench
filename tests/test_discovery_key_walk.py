"""discovery_key's metadata hashing must match the former rglob passes exactly."""

import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.common import file_hash  # noqa: E402
from d11.discovery import _drush_probe_workers, _metadata_hashes  # noqa: E402


def _rglob_reference(directory):
    result = {}
    for pattern in ("*.info.yml", "installed.json"):
        for p in Path(directory).rglob(pattern):
            result[str(p)] = file_hash(p)
    return result


class MetadataHashesTests(unittest.TestCase):
    def test_single_walk_matches_rglob_reference_including_symlinked_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vendor"
            (root / "composer").mkdir(parents=True)
            (root / "composer/installed.json").write_text("{}")
            (root / "drupal/foo").mkdir(parents=True)
            (root / "drupal/foo/foo.info.yml").write_text("name: Foo")
            (root / "drupal/foo/README.md").write_text("not metadata")
            (root / "drupal/foo/tests").mkdir()
            (root / "drupal/foo/tests/bar.info.yml").write_text("name: Bar")
            (root / "noise").mkdir()
            (root / "noise/installed.json.bak").write_text("x")
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "linked.info.yml").write_text("name: Linked")
            os.symlink(outside, root / "linked", target_is_directory=True)

            hashes = _metadata_hashes(root)
            self.assertEqual(hashes, _rglob_reference(root))
            # rglob does not descend into symlinked directories on this interpreter; neither may we,
            # or every existing discovery cache key would change.
            self.assertEqual(len(hashes), 3)
            self.assertFalse(any("linked" in key for key in hashes))

    def test_missing_directory_yields_nothing(self):
        self.assertEqual(_metadata_hashes(Path("/nonexistent/path/for/test")), {})


class ProbeWorkersTests(unittest.TestCase):
    def test_default_is_serial_and_values_are_clamped(self):
        cases = {None: 1, "1": 1, "3": 3, "12": 4, "0": 1, "-2": 1, "many": 1}
        for value, expected in cases.items():
            env = dict(os.environ)
            env.pop("D11_DRUSH_PROBE_WORKERS", None)
            if value is not None:
                env["D11_DRUSH_PROBE_WORKERS"] = value
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(_drush_probe_workers(), expected, value)


if __name__ == "__main__":
    unittest.main()
