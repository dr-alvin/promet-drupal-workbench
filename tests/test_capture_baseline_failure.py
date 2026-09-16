"""A failed baseline capture must leave an explicit checkpoint, not a permanent "capturing"."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.common import read, write  # noqa: E402
from d11.two_gate import capture_run_baseline  # noqa: E402


class _Workflow:
    def __init__(self, out, fail):
        self.out = out
        self.fail = fail
        self.events = []

    def run(self, rid):
        return self.out, read(self.out / "state.json")

    def project(self, pid):
        return self.out.parent / "project", {"site": {"uri": "http://127.0.0.1"}, "roots": {"drupal": "web"}}

    def event(self, out, kind, **data):
        self.events.append((kind, data))

    def capture(self, p, cfg, out, mode):
        if self.fail:
            raise RuntimeError("Visual audit container timed out after 300 seconds")
        (out / "visual").mkdir(exist_ok=True)
        (out / "visual/home.png").write_bytes(b"png")

    def report(self, rid):
        pass


class CaptureRunBaselineTests(unittest.TestCase):
    def _out(self, tmp):
        out = Path(tmp) / "run"
        out.mkdir()
        write(out / "state.json", {"project": "proj", "checkpoint": "report_and_decisions", "status": "completed"})
        write(out / "gate.json", {"baseline": {"passed": False, "error": "pending"}})
        return out

    def test_failure_records_baseline_failed_and_reraises(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._out(tmp)
            w = _Workflow(out, fail=True)
            with self.assertRaises(RuntimeError):
                capture_run_baseline(w, "run-1")
            state = read(out / "state.json")
            self.assertEqual(state["checkpoint"], "baseline_failed")
            self.assertIn("timed out", state["baselineError"])
            self.assertEqual([k for k, _ in w.events], ["checkpoint", "checkpoint"])
            self.assertEqual(w.events[-1][1]["checkpoint"], "baseline_failed")
            self.assertIn("timed out", w.events[-1][1]["error"])
            # The gate is not marked passed on failure.
            self.assertFalse(read(out / "gate.json")["baseline"]["passed"])

    def test_success_clears_previous_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._out(tmp)
            state = read(out / "state.json")
            state.update(checkpoint="baseline_failed", baselineError="old")
            write(out / "state.json", state)
            result = capture_run_baseline(_Workflow(out, fail=False), "run-1")
            self.assertTrue(result["baselineCaptured"])
            state = read(out / "state.json")
            self.assertEqual(state["checkpoint"], "baseline_captured")
            self.assertNotIn("baselineError", state)
            self.assertTrue(read(out / "gate.json")["baseline"]["passed"])


if __name__ == "__main__":
    unittest.main()
