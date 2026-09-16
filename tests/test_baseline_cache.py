import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from d11lib.baseline_cache import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    MANIFEST,
    clear,
    fingerprint,
    prune,
    restore,
    store,
)
from d11lib.common import digest  # noqa: E402


def image_hash(path: Path) -> str:
    """The hash format run.js writes into the baseline manifest."""
    return digest(base64.b64encode(path.read_bytes()).decode("ascii"))


def make_work(root: Path, stable=True, images=("a.png", "b.png")) -> Path:
    """Build a directory shaped like a completed reference capture."""
    work = root / "visual-work"
    bitmaps = work / "bitmaps_reference"
    bitmaps.mkdir(parents=True, exist_ok=True)
    reference_files = []
    for i, name in enumerate(images):
        f = bitmaps / name
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64)
        reference_files.append(
            {"path": f"bitmaps_reference/{name}", "hash": image_hash(f)}
        )
    (work / "capture-settings.json").write_text(
        json.dumps({"identity": "id-1", "stable": stable, "referenceFiles": reference_files})
    )
    return work


class BaselineCacheTests(unittest.TestCase):
    def test_roundtrip_restores_images_and_settings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            work = make_work(root)
            self.assertIsNotNone(store(home, "key1", work))

            target = root / "fresh-run"
            result = restore(home, "key1", target)
            self.assertIsNotNone(result)
            self.assertTrue(result["reused"])
            self.assertEqual(result["imageCount"], 2)
            self.assertTrue((target / "capture-settings.json").is_file())
            self.assertEqual(len(list((target / "bitmaps_reference").glob("*.png"))), 2)

            # Restored images must still satisfy the manifest run.js validates against.
            settings = json.loads((target / "capture-settings.json").read_text())
            for item in settings["referenceFiles"]:
                self.assertEqual(image_hash(target / item["path"]), item["hash"])

    def test_unstable_baseline_is_never_cached(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            work = make_work(root, stable=False)
            self.assertIsNone(store(home, "key1", work))
            self.assertIsNone(restore(home, "key1", root / "fresh"))

    def test_tampered_image_discards_the_entry_and_forces_recapture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            store(home, "key1", make_work(root))
            entry = home / "baseline-cache" / "key1"
            victim = next((entry / "bitmaps_reference").glob("*.png"))
            victim.write_bytes(victim.read_bytes() + b"tampered")

            self.assertIsNone(restore(home, "key1", root / "fresh"))
            # Fails closed: the poisoned entry is removed, not merely skipped.
            self.assertFalse(entry.exists())

    def test_missing_image_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            store(home, "key1", make_work(root))
            entry = home / "baseline-cache" / "key1"
            next((entry / "bitmaps_reference").glob("*.png")).unlink()
            self.assertIsNone(restore(home, "key1", root / "fresh"))

    def test_expired_entry_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            store(home, "key1", make_work(root))
            manifest_path = home / "baseline-cache" / "key1" / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            manifest["capturedAt"] = time.time() - (DEFAULT_TTL_SECONDS + 60)
            manifest_path.write_text(json.dumps(manifest))
            self.assertIsNone(restore(home, "key1", root / "fresh"))
            # A stale entry is refused but kept; it may still be valid under a
            # longer TTL, and deleting it would discard usable evidence.
            self.assertTrue(manifest_path.is_file())
            self.assertIsNotNone(restore(home, "key1", root / "fresh2", ttl_seconds=0))

    def test_restore_count_is_recorded_for_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            store(home, "key1", make_work(root))
            self.assertEqual(restore(home, "key1", root / "r1")["restores"], 1)
            self.assertEqual(restore(home, "key1", root / "r2")["restores"], 2)

    def test_prune_keeps_only_the_newest_entries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            for i in range(5):
                store(home, f"key{i}", make_work(root / f"w{i}"))
                manifest_path = home / "baseline-cache" / f"key{i}" / MANIFEST
                manifest = json.loads(manifest_path.read_text())
                manifest["capturedAt"] = 1000 + i
                manifest_path.write_text(json.dumps(manifest))
            prune(home, keep=2)
            remaining = sorted(x.name for x in (home / "baseline-cache").iterdir())
            self.assertEqual(remaining, ["key3", "key4"])

    def test_fingerprint_tracks_every_declared_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "visual.json"
            cfg.write_text(json.dumps({"scenarios": [{"id": "a"}]}))
            base = fingerprint("img:1", cfg)
            self.assertEqual(base, fingerprint("img:1", cfg))
            # Toolkit change (engine scripts, backstop config) must invalidate.
            self.assertNotEqual(base, fingerprint("img:2", cfg))
            # Scenario/route/threshold/mask change must invalidate.
            cfg.write_text(json.dumps({"scenarios": [{"id": "a"}, {"id": "b"}]}))
            self.assertNotEqual(base, fingerprint("img:1", cfg))

    def test_clear_removes_the_whole_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "project"
            store(home, "key1", make_work(root))
            clear(home)
            self.assertFalse((home / "baseline-cache").exists())
            self.assertIsNone(restore(home, "key1", root / "fresh"))


if __name__ == "__main__":
    unittest.main()
