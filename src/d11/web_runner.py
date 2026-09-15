"""Web-based Command Runner and Terminal Streamer for Drupal 11 Upgrade Workbench.

Enables executing `bin/d11 upgrade`, `bin/d11 handoff`, and other toolkit commands
directly from the web browser dashboard with real-time streaming output.
"""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from .common import ROOT, Problem, now

JOBS: dict[str, WebCommandJob] = {}
MAX_OUTPUT_LINES = 5000


class WebCommandJob:
    """Manages an active or completed CLI command process with line buffering and streaming."""

    def __init__(self, job_id: str, argv: list[str], cwd: Path | str, description: str = ""):
        self.id = job_id
        self.argv = argv
        self.cwd = str(Path(cwd).resolve())
        self.description = description
        self.status = "queued"  # queued, running, completed, failed, terminated
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.output_lines: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        self.process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self, env_overrides: dict[str, str] | None = None):
        """Start the process and launch the background stdout/stderr reader thread."""
        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "TERM": "xterm-256color",
                "FORCE_COLOR": "1",
            }
        )
        if env_overrides:
            env.update(env_overrides)

        self.started_at = now()
        self.status = "running"

        try:
            self.process = subprocess.Popen(
                self.argv,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            self.status = "failed"
            self.finished_at = now()
            self.exit_code = 1
            err_line = f"\033[1;31mFailed to start process: {exc}\033[0m\n"
            self.output_lines.append(err_line)
            self._queue.put(err_line)
            self._queue.put(None)
            return

        def _reader():
            try:
                for line in iter(self.process.stdout.readline, ""):
                    if not line:
                        break
                    if len(self.output_lines) < MAX_OUTPUT_LINES:
                        self.output_lines.append(line)
                    self._queue.put(line)
            except Exception as e:
                err_msg = f"\033[1;31mError reading output: {e}\033[0m\n"
                self.output_lines.append(err_msg)
                self._queue.put(err_msg)
            finally:
                if self.process:
                    self.process.stdout.close()
                    self.exit_code = self.process.wait()
                self.finished_at = now()
                self.status = "completed" if self.exit_code == 0 else "failed"
                self._queue.put(None)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()

    def send_input(self, text: str):
        """Send stdin input to the running command process."""
        if self.status != "running" or not self.process or not self.process.stdin:
            raise Problem("Command is not running; cannot accept input.")
        if not text.endswith("\n"):
            text += "\n"
        try:
            self.process.stdin.write(text)
            self.process.stdin.flush()
            echo_line = f"\033[1;30m> {text.strip()}\033[0m\n"
            self.output_lines.append(echo_line)
            self._queue.put(echo_line)
        except Exception as exc:
            raise Problem(f"Failed to send input: {exc}")

    def terminate(self):
        """Terminate the running process."""
        if self.process and self.status == "running":
            try:
                self.process.terminate()
                time.sleep(0.2)
                if self.process.poll() is None:
                    self.process.kill()
            except Exception:
                pass
            self.status = "terminated"
            self.finished_at = now()
            term_line = "\n\033[1;31m✖ Process terminated by user in browser.\033[0m\n"
            self.output_lines.append(term_line)
            self._queue.put(term_line)
            self._queue.put(None)

    def to_dict(self) -> dict:
        """Serialize job status."""
        return {
            "id": self.id,
            "argv": self.argv,
            "cwd": self.cwd,
            "description": self.description,
            "status": self.status,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "exitCode": self.exit_code,
            "lineCount": len(self.output_lines),
        }


def create_job(
    argv: list[str], cwd: Path | str | None = None, description: str = ""
) -> WebCommandJob:
    """Create and register a new command job."""
    job_id = f"cmd-{uuid.uuid4().hex[:10]}"
    resolved_cwd = cwd or ROOT
    job = WebCommandJob(job_id, argv, resolved_cwd, description)
    JOBS[job_id] = job
    return job


def get_job(job_id: str) -> WebCommandJob:
    """Retrieve job by ID."""
    if job_id not in JOBS:
        raise Problem(f"Unknown command job '{job_id}'", 404)
    return JOBS[job_id]


def get_active_job() -> WebCommandJob | None:
    """Return the most recently created active (queued or running) command job, if any."""
    for job in reversed(list(JOBS.values())):
        if job.status in ("queued", "running"):
            return job
    return None


async def stream_job_events(job: WebCommandJob) -> AsyncGenerator[str, None]:
    """Stream command output lines via Server-Sent Events (SSE)."""
    import json

    cursor = 0
    while True:
        # Drain all lines accumulated up to now
        while cursor < len(job.output_lines):
            line = job.output_lines[cursor]
            cursor += 1
            yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"

        if job.status not in ("queued", "running"):
            # One final drain in case the reader thread appended just before finishing
            while cursor < len(job.output_lines):
                line = job.output_lines[cursor]
                cursor += 1
                yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
            break

        yield ": heartbeat\n\n"
        await asyncio.sleep(0.15)

    # Final completion event
    yield f"data: {json.dumps({'type': 'finished', 'status': job.status, 'exitCode': job.exit_code})}\n\n"
