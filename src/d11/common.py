"""Shared evidence, configuration and subprocess boundaries."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

for _site_dir in (ROOT / ".venv").glob("lib/python*/site-packages"):
    if _site_dir.is_dir() and str(_site_dir) not in sys.path:
        sys.path.insert(0, str(_site_dir))

NONPROD = {"local", "dev", "test", "multidev"}


def get_d11_home() -> Path:
    """Return the active D11 home directory, honoring $D11_HOME or ~/.d11."""
    if "D11_HOME" in os.environ and os.environ["D11_HOME"]:
        return Path(os.environ["D11_HOME"]).resolve()
    return (Path.home() / ".d11").resolve()


def set_d11_home(path: str | Path) -> Path:
    """Set the D11 home directory and export $D11_HOME."""
    p = Path(path).resolve()
    os.environ["D11_HOME"] = str(p)
    global D11_HOME
    D11_HOME = p
    return p


D11_HOME = get_d11_home()


class Problem(Exception):
    def __init__(self, message, code=3):
        super().__init__(message)
        self.code = code


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


_HASH_INDEX: dict[str, tuple[int, int, str]] = {}


def file_hash(path):
    p = Path(path)
    try:
        st = p.stat()
        key = str(p.resolve())
        cached = _HASH_INDEX.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        _HASH_INDEX[key] = (st.st_mtime_ns, st.st_size, h)
        return h
    except OSError:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Problem(f"Cannot read JSON {path}: {e}", 64)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(f.name, path)


def yaml_read(path):
    import yaml

    try:
        value = yaml.safe_load(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, yaml.YAMLError) as e:
        raise Problem(f"Invalid YAML evidence {path}: {e}", 2)


def schema(value, name):
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(read(ROOT / "config/schemas" / f"{name}.json")).iter_errors(value),
        key=lambda e: str(e.path),
    )
    if errors:
        raise Problem("; ".join(f"{list(e.path)}: {e.message}" for e in errors), 64)


def load_config(path):
    path = Path(path).resolve()
    cfg = read(path)
    schema(cfg, "project")
    for estimate in cfg.get("estimates", []):
        if estimate["highHours"] < estimate["lowHours"]:
            raise Problem("Estimate upper bound must be >= lower bound", 64)
    from .budget import budget_model

    budget_model(cfg)
    cfg["_config"] = str(path)
    cfg["_root"] = str((path.parent / cfg["repository"]).resolve())
    if not Path(cfg["_root"]).is_dir():
        raise Problem("Repository directory does not exist", 64)
    return cfg


def redact(text):
    text = str(text)
    for key, value in os.environ.items():
        if re.search(r"PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|COOKIE", key) and len(value) >= 4:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie)\s*[:=]\s*([^\s,;]+)",
        r"\1=[REDACTED]",
        text,
    )
    return text


def safe_output(text):
    # Parse before redaction so quotes/braces and legitimate identifier maps survive.
    try:
        return json.dumps(redact_tree(json.loads(text)))
    except ValueError:
        return redact(text)


def command(argv, cwd, timeout=120, input_text=None, env=None):
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(x, str) and x and "\x00" not in x for x in argv)
    ):
        raise Problem("Commands must be nonempty argv arrays", 64)
    record = {"argv": [redact(x) for x in argv], "cwd": str(cwd), "startedAt": now()}
    started = time.monotonic()
    process = None
    control = os.environ.get("D11_STOP_FILE")
    events = os.environ.get("D11_COMMAND_EVENTS")

    def event(data):
        if events:
            with open(events, "a") as stream:
                stream.write(json.dumps(redact_tree(data)) + "\n")

    event({"type": "command_started", **record})
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            stdin=subprocess.PIPE if input_text is not None else None,
            env=env,
        )
        first = True
        while True:
            if control and Path(control).exists():
                raise KeyboardInterrupt()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = process.communicate(
                    input=input_text if first else None, timeout=min(0.25, remaining)
                )
                break
            except subprocess.TimeoutExpired:
                first = False
        record.update(
            exitCode=process.returncode,
            stdout=safe_output(stdout),
            stderr=safe_output(stderr),
            status="passed" if process.returncode == 0 else "tool_failure",
        )
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as e:
        if process is not None:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            record.update(
                stdout=safe_output(stdout),
                stderr=safe_output(stderr),
                processExitCode=process.returncode,
            )
        record.update(
            exitCode=None,
            status="tool_failure",
            error="Interrupted" if isinstance(e, KeyboardInterrupt) else "Command timed out",
        )
    except OSError as e:
        record.update(
            exitCode=None,
            status="tool_failure",
            stderr=redact(str(e)),
            preparation="Install/configure the selected project command through an approved d11 prepare plan; do not install during assessment.",
        )
    record["finishedAt"] = now()
    record["elapsedSeconds"] = round(time.monotonic() - started, 6)
    record["executionStatus"] = record["status"]
    record["failureCategory"] = None if record["status"] == "passed" else "execution_failure"
    event({"type": "command_finished", **record})
    return record


def require_nonprod(cfg):
    env = cfg["environment"]
    if env["kind"] not in NONPROD or not env.get("authorized", False):
        raise Problem(
            "Explicitly authorized Local/Dev/Test/Multidev environment required; Production is handoff-only"
        )


def relative(root, value):
    if value is None or value == "":
        return Path(root).resolve()
    p = (Path(root) / value).resolve()
    return p


def state_hashes(root, exclude=()):
    """Hash all relevant source state, including untracked files; never read private files."""
    from .ignores import (
        STATE_HASHES_IGNORED_DIRS,
        STATE_HASHES_IGNORED_EXTENSIONS,
        STATE_HASHES_IGNORED_FILES,
    )

    root = Path(root)
    excluded = [Path(p).resolve() for p in exclude]
    result = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in STATE_HASHES_IGNORED_DIRS
            and not Path(base, d).is_symlink()
            and not any(Path(base, d).resolve() == p for p in excluded)
        )
        for name in sorted(names):
            p = Path(base, name)
            if (
                p.is_symlink()
                or name in STATE_HASHES_IGNORED_FILES
                or name.endswith(STATE_HASHES_IGNORED_EXTENSIONS)
            ):
                continue
            # Settings files are hashed, never copied to evidence.
            result[str(p.relative_to(root))] = file_hash(p)
    return result


def redact_tree(value, parent=None):
    if isinstance(value, dict):
        identifiers = parent in ("module", "theme", "modules", "themes", "extensions")
        return {
            k: "[REDACTED]"
            if not identifiers
            and (k.lower() != "token" or not isinstance(v, dict))
            and re.fullmatch(
                r"(?i)(?:[a-z]+[_-])?(password|passwd|secret|token|access[_-]?token|refresh[_-]?token|api[_-]?key|authorization|cookie)",
                k,
            )
            else redact_tree(v, k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_tree(x, parent) for x in value]
    if isinstance(value, str):
        return redact(value)
    return value
