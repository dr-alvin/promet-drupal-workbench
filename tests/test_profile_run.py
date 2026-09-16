"""profile_run attributes wall time without double-counting nested commands."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import profile_run  # noqa: E402


def _cmd(argv, start, end):
    return {
        "type": "command_finished",
        "argv": argv,
        "startedAt": f"2026-09-16T10:00:{start:02d}+00:00",
        "finishedAt": f"2026-09-16T10:00:{end:02d}+00:00",
        "elapsedSeconds": end - start,
    }


class ProfileRunTests(unittest.TestCase):
    def test_nested_commands_count_once_and_spans_are_summed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "state.json").write_text(json.dumps({
                "status": "completed", "scanMode": "fast", "elapsedSeconds": 60.0,
            }))
            # A 30s parent (assess) with two probes nested inside it, plus a 10s sibling.
            commands = [
                _cmd(["/x/bin/d11", "assess"], 0, 30),
                _cmd(["fin", "drush", "core:status"], 5, 10),
                _cmd(["fin", "drush", "pm:list"], 12, 20),
                _cmd(["docker", "compose", "down"], 40, 50),
            ]
            (run / "commands.jsonl").write_text("\n".join(json.dumps(c) for c in commands) + "\n")
            events = [
                {"type": "checkpoint", "at": "2026-09-16T10:00:00+00:00", "checkpoint": "assessing"},
                {"type": "span", "step": "copy_analysis", "status": "passed", "elapsedSeconds": 7.5},
                {"type": "span", "step": "rector", "status": "passed", "elapsedSeconds": 20.0},
                {"type": "span", "step": "rector", "status": "failed", "elapsedSeconds": 1.0},
                {"type": "checkpoint", "at": "2026-09-16T10:00:45+00:00", "checkpoint": "resolving"},
            ]
            (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

            result = profile_run.profile(run)
            self.assertEqual(result["commandCount"], 4)
            self.assertEqual(result["topLevelCommandCount"], 2)
            self.assertEqual(result["commandSeconds"], 40.0)  # 30 + 10, probes not double-counted
            self.assertEqual(result["unattributedSeconds"], 20.0)
            self.assertEqual(result["spans"]["rector"], {"count": 2, "seconds": 21.0, "failed": 1})
            self.assertEqual(result["spans"]["copy_analysis"]["seconds"], 7.5)
            self.assertEqual(result["checkpointSpans"], [{"checkpoint": "assessing", "seconds": 45.0}])
            self.assertEqual(result["malformedCommandLines"], 0)

    def test_malformed_lines_are_counted_and_fail_the_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "state.json").write_text(json.dumps({"elapsedSeconds": 1.0}))
            (run / "commands.jsonl").write_text('{"type":"command_finished","argv":["a"],"startedAt":"2026-09-16T10:00:00+00:00","finishedAt":"2026-09-16T10:00:01+00:00","elapsedSeconds":1}\n{"type":"command_fini{"broken\n')
            self.assertEqual(profile_run.profile(run)["malformedCommandLines"], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(profile_run.main([str(run)]), 1)


if __name__ == "__main__":
    unittest.main()
