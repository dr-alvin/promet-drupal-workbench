"""Reuse of pre-upgrade visual baselines across runs.

Capturing the baseline is the single most expensive phase of a visual audit, and
it is repeated in full on every run even when nothing that affects rendering has
changed. This module keys a stored baseline on everything that can change what a
capture looks like, and replays it when — and only when — every one of those
inputs still matches.

Safety properties, in order of importance:

* **Fails closed.** Every stored image is re-hashed on restore. A single missing
  or altered byte discards the whole entry and the caller re-captures.
* **Content is not fingerprinted.** Editing a node changes the page without
  changing any tracked input. That makes a reused baseline *stale*, which shows
  up as a visual difference — noisy, but it surfaces rather than hides. A short
  TTL bounds the window, and `refresh` forces a fresh capture.
* **Reuse is recorded.** Restores are stamped into the manifest so a report can
  state that the baseline was replayed and when it was originally captured.
"""

from __future__ import annotations

import base64
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from .common import command, digest, file_hash, read, write

# A baseline older than this is re-captured even on a full fingerprint match,
# bounding how long untracked content drift can sit in a replayed baseline.
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_KEEP = 3
MANIFEST = "cache-manifest.json"


def git_head(source_path: Optional[Path | str]) -> str:
    """Current commit of the project under test, or '' when unavailable."""
    if not source_path:
        return ""
    src = Path(source_path)
    if not (src / ".git").exists():
        return ""
    try:
        rec = command(["git", "rev-parse", "HEAD"], src, 15)
    except Exception:
        return ""
    if rec.get("exitCode") != 0:
        return ""
    return (rec.get("stdout") or "").strip()


def working_tree_dirty(source_path: Optional[Path | str]) -> bool:
    """Whether the project has uncommitted changes.

    A dirty tree means the commit hash no longer identifies what is deployed, so
    the baseline cannot be safely keyed on it.
    """
    if not source_path:
        return False
    src = Path(source_path)
    if not (src / ".git").exists():
        return False
    try:
        rec = command(["git", "status", "--porcelain"], src, 20)
    except Exception:
        return True
    if rec.get("exitCode") != 0:
        return True
    return bool((rec.get("stdout") or "").strip())


def fingerprint(
    image_tag: str,
    config_path: Path | str,
    source_path: Optional[Path | str] = None,
    reference_url: str = "",
) -> str:
    """Digest every tracked input that can change what a baseline looks like.

    `image_tag` already hashes the whole visual-audit tree (engine scripts,
    backstop config, run/scenarios logic), so any toolkit change invalidates the
    cache without restating each file here. The visual config covers routes,
    viewports, thresholds and masks. composer.lock covers the installed module
    set, and the git commit covers project code.
    """
    src = Path(source_path) if source_path else None
    lock = src / "composer.lock" if src else None
    return digest(
        {
            "image": image_tag,
            "config": file_hash(Path(config_path)),
            "referenceUrl": reference_url,
            "codeRevision": git_head(src),
            "composerLock": file_hash(lock) if lock and lock.is_file() else "",
        }
    )


def _entry(project_home: Path | str, key: str) -> Path:
    return Path(project_home) / "baseline-cache" / key


def store(project_home: Path | str, key: str, work: Path | str) -> Optional[Path]:
    """Persist a verified baseline so a later run can replay it."""
    work = Path(work)
    settings = work / "capture-settings.json"
    bitmaps = work / "bitmaps_reference"
    if not settings.is_file() or not bitmaps.is_dir():
        return None
    data = read(settings)
    # Only a baseline the capture proved reproducible is worth keeping.
    if not data.get("stable") or not data.get("referenceFiles"):
        return None

    entry = _entry(project_home, key)
    if entry.exists():
        shutil.rmtree(entry, ignore_errors=True)
    entry.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bitmaps, entry / "bitmaps_reference")
    shutil.copy2(settings, entry / "capture-settings.json")
    write(
        entry / MANIFEST,
        {
            "key": key,
            "capturedAt": time.time(),
            "identity": data.get("identity"),
            "imageCount": len(data.get("referenceFiles") or []),
            "restores": 0,
        },
    )
    prune(project_home)
    return entry


def _verify(entry: Path) -> bool:
    """Re-hash every stored image against the manifest recorded at capture time."""
    settings = entry / "capture-settings.json"
    if not settings.is_file():
        return False
    try:
        data = read(settings)
    except Exception:
        return False
    files = data.get("referenceFiles") or []
    if not files:
        return False
    for item in files:
        target = entry / item.get("path", "")
        if not target.is_file() or _image_hash(target) != item.get("hash"):
            return False
    return True


def _image_hash(path: Path) -> str:
    """Reproduce run.js's manifest hash exactly.

    run.js computes sha256(JSON.stringify(base64(bytes))). Python's digest() uses
    json.dumps on the same string, which produces byte-identical input, so the
    two agree and a baseline verified here will also satisfy run.js.
    """
    return digest(base64.b64encode(path.read_bytes()).decode("ascii"))


def restore(
    project_home: Path | str,
    key: str,
    work: Path | str,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Replay a stored baseline into `work`. Returns None when unusable."""
    entry = _entry(project_home, key)
    manifest_path = entry / MANIFEST
    if not entry.is_dir() or not manifest_path.is_file():
        return None
    try:
        manifest = read(manifest_path)
    except Exception:
        return None
    age = time.time() - float(manifest.get("capturedAt") or 0)
    if ttl_seconds and age > ttl_seconds:
        return None
    if not _verify(entry):
        # Corrupt or tampered entry: discard rather than risk a bad baseline.
        shutil.rmtree(entry, ignore_errors=True)
        return None

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    target_bitmaps = work / "bitmaps_reference"
    if target_bitmaps.exists():
        shutil.rmtree(target_bitmaps, ignore_errors=True)
    shutil.copytree(entry / "bitmaps_reference", target_bitmaps)
    shutil.copy2(entry / "capture-settings.json", work / "capture-settings.json")

    manifest["restores"] = int(manifest.get("restores") or 0) + 1
    manifest["lastRestoredAt"] = time.time()
    write(manifest_path, manifest)
    return {
        "reused": True,
        "key": key,
        "capturedAt": manifest.get("capturedAt"),
        "ageSeconds": round(age, 3),
        "restores": manifest["restores"],
        "imageCount": manifest.get("imageCount"),
    }


def prune(project_home: Path | str, keep: int = DEFAULT_KEEP) -> list[str]:
    """Drop all but the newest `keep` entries. Full-page bitmaps are large."""
    root = Path(project_home) / "baseline-cache"
    if not root.is_dir():
        return []
    entries = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest = child / MANIFEST
        try:
            captured = float(read(manifest).get("capturedAt") or 0) if manifest.is_file() else 0.0
        except Exception:
            captured = 0.0
        entries.append((captured, child))
    entries.sort(key=lambda x: x[0], reverse=True)
    removed = []
    for _, child in entries[max(0, keep):]:
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    return removed


def clear(project_home: Path | str) -> None:
    shutil.rmtree(Path(project_home) / "baseline-cache", ignore_errors=True)
