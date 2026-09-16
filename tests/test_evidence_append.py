"""Evidence logs must stay intact under concurrent writers, and spans must time phases."""

import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from d11.common import append_jsonl, command, span  # noqa: E402


class AppendJsonlTests(unittest.TestCase):
    def test_large_records_from_many_threads_stay_intact(self):
        # 64 KB records are far past the 8 KB stdio buffer that used to split writes.
        payload = "x" * 65536
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            barrier = threading.Barrier(8)

            def writer(worker):
                barrier.wait()
                for i in range(40):
                    append_jsonl(path, {"worker": worker, "i": i, "payload": payload})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(writer, range(8)))
            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 8 * 40)
            seen = set()
            for line in lines:
                record = json.loads(line)  # raises if any line was interleaved
                self.assertEqual(len(record["payload"]), 65536)
                seen.add((record["worker"], record["i"]))
            self.assertEqual(len(seen), 8 * 40)

    def test_command_event_stream_survives_concurrent_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / "commands.jsonl"
            previous = os.environ.get("D11_COMMAND_EVENTS")
            os.environ["D11_COMMAND_EVENTS"] = str(events)
            try:
                argv = [sys.executable, "-c", "print('y' * 300000)"]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    records = list(pool.map(lambda _: command(argv, tmp, timeout=60), range(4)))
            finally:
                if previous is None:
                    os.environ.pop("D11_COMMAND_EVENTS", None)
                else:
                    os.environ["D11_COMMAND_EVENTS"] = previous
            self.assertTrue(all(r["status"] == "passed" for r in records))
            lines = events.read_text().splitlines()
            parsed = [json.loads(line) for line in lines]
            self.assertEqual(len(parsed), 8)  # 4 started + 4 finished
            finished = [p for p in parsed if p["type"] == "command_finished"]
            self.assertEqual(len(finished), 4)
            self.assertTrue(all(len(p["stdout"]) >= 300000 for p in finished))


class SpanTests(unittest.TestCase):
    def test_span_records_to_sink_and_emits(self):
        sink, emitted = [], []
        with span("copy", lambda **rec: emitted.append(rec), sink, files=3):
            pass
        self.assertEqual(len(sink), 1)
        record = sink[0]
        self.assertEqual(record["step"], "copy")
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["files"], 3)
        self.assertGreaterEqual(record["elapsedSeconds"], 0)
        self.assertIn("startedAt", record)
        self.assertIn("finishedAt", record)
        self.assertEqual(emitted, sink)

    def test_span_marks_failure_and_reraises(self):
        sink = []
        with self.assertRaises(RuntimeError):
            with span("rector", None, sink):
                raise RuntimeError("boom")
        self.assertEqual(sink[0]["status"], "failed")

    def test_emit_errors_never_break_the_work(self):
        def bad_emit(**rec):
            raise OSError("disk full")

        sink = []
        with span("teardown", bad_emit, sink):
            pass
        self.assertEqual(sink[0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
