"""Post-upgrade health checks that ask Drupal itself, not just Composer.

Composer and Drupal do not share a dependency graph. Composer resolves
`composer.json` constraints; Drupal resolves `dependencies:` declared in each
extension's `.info.yml`, which Composer never sees. An upgrade can therefore
finish with Composer reporting complete success while Drupal considers the site
broken — and nothing in the pipeline notices, because nothing asks Drupal.

Two checks close that gap:

`requirements_findings` runs Drupal's own requirements report — the same data
behind /admin/reports/status — and treats any error as a Gate 2 failure. It is
deliberately generic: it inherits every check Drupal and its contrib modules
implement, rather than reimplementing a subset.

`source_install_findings` catches a specific, silent corruption. When a dist
download fails, Composer falls back to cloning from git and still exits 0. Git
checkouts lack the `version:`/`project:`/`datestamp:` footer that drupal.org's
packaging script appends, and Drupal reads a module's version from that key.
With it missing, *every* version-constrained dependency on that module reports
as unresolved even though the correct version is installed. The install looks
clean from Composer's side and is broken from Drupal's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .common import command

# Composer 2 emits these when a dist download fails and it falls back to git.
# `Cloning` in an install line is the definitive marker: however the fallback
# was reached, the result is a source checkout without packaging metadata.
_DIST_FAILURE = re.compile(r"Failed to download\s+(\S+?)\s+from dist", re.IGNORECASE)
_SOURCE_CLONE = re.compile(
    r"(?:Installing|Downloading|Updating)\s+(\S+?)\s+\([^)]*\)\s*:\s*Cloning", re.IGNORECASE
)
_PACKAGING_MARKER = "Information added by Drupal.org packaging script"

# Drupal's REQUIREMENT_ERROR. Warnings are recorded but never block.
SEVERITY_ERROR = 2


def composer_source_fallbacks(text: str) -> list[str]:
    """Package names Composer installed from git source instead of dist."""
    if not text:
        return []
    found = {m.group(1) for m in _DIST_FAILURE.finditer(text)}
    found |= {m.group(1) for m in _SOURCE_CLONE.finditer(text)}
    return sorted(n for n in found if n and "/" in n)


def source_install_findings(
    source_dir: Path | str, drupal_root: str = "web"
) -> list[dict[str, Any]]:
    """Find installed extensions whose packaging metadata is missing.

    A `.git` directory is suggestive; the absent packaging footer is what
    actually breaks Drupal, so that is what is reported.
    """
    root = Path(source_dir) / (drupal_root or "web")
    findings: list[dict[str, Any]] = []
    for kind in ("modules", "themes", "profiles"):
        base = root / kind / "contrib"
        if not base.is_dir():
            continue
        for extension in sorted(base.iterdir()):
            if not extension.is_dir():
                continue
            info = extension / f"{extension.name}.info.yml"
            if not info.is_file():
                continue
            try:
                content = info.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _PACKAGING_MARKER in content or re.search(r"^version:", content, re.MULTILINE):
                continue
            findings.append(
                {
                    "extension": extension.name,
                    "type": kind,
                    "path": str(info.relative_to(source_dir)),
                    "fromGitSource": (extension / ".git").exists(),
                    "reason": (
                        "No version in .info.yml. Drupal cannot satisfy any "
                        "version-constrained dependency on this extension."
                    ),
                }
            )
    return findings


def parse_requirements(raw: str) -> list[dict[str, Any]]:
    """Normalise `drush core:requirements --format=json` output.

    Drush returns a keyed object or a bare list depending on version and
    severity filter, so both shapes are accepted.
    """
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    items = list(data.values()) if isinstance(data, dict) else data
    return [x for x in items if isinstance(x, dict)]


def _severity_value(entry: dict[str, Any]) -> Optional[int]:
    """Drush reports severity as an int or a label depending on version."""
    raw = entry.get("severity")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip().lower()
    return {"error": 2, "warning": 1, "ok": 0, "info": -1}.get(text)


def requirement_errors(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries Drupal classifies as errors.

    Entries whose severity cannot be interpreted are excluded: the caller asked
    Drush to filter to errors already, and inventing a severity here would
    manufacture blockers from unparsed output.
    """
    out = []
    for entry in entries:
        if _severity_value(entry) == SEVERITY_ERROR:
            out.append(
                {
                    "title": str(entry.get("title") or "").strip(),
                    "value": str(entry.get("value") or "").strip(),
                    "description": re.sub(
                        r"<[^>]+>", "", str(entry.get("description") or "")
                    ).strip(),
                }
            )
    return out


def check(
    cfg: dict[str, Any],
    source_dir: Path | str,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run Drupal's requirements report and inspect packaging metadata.

    Returns evidence with a `status` of passed, failed or unknown. `unknown`
    means the report could not be collected; the caller decides whether that is
    acceptable, since a missing report is absence of evidence rather than
    evidence of health.
    """
    from .source_runtime import get_runtime_prefixes

    source_dir = Path(source_dir)
    wrapper = cfg.get("runtime", {}).get("wrapper") or cfg.get("wrapper") or "fin"
    drupal_root = cfg.get("roots", {}).get("drupal", "web")
    site_uri = cfg.get("site", {}).get("uri", "")

    result: dict[str, Any] = {
        "schemaVersion": "1.0",
        "status": "unknown",
        "errors": [],
        "sourceInstalls": source_install_findings(source_dir, drupal_root),
        "blockers": [],
        "warnings": [],
    }

    try:
        drush_prefix, _ = get_runtime_prefixes(wrapper, source_dir)
        argv = drush_prefix + ["core:requirements", "--format=json", "--severity=2"]
        if site_uri:
            argv.append("--uri=" + site_uri)
        rec = command(argv, source_dir, timeout)
    except Exception as exc:  # pragma: no cover - defensive
        result["message"] = f"Could not run Drupal requirements report: {exc}"
        return result

    result["command"] = {"argv": rec.get("argv"), "exitCode": rec.get("exitCode")}
    if rec.get("exitCode") != 0:
        result["message"] = (
            "Drupal requirements report did not run; site health is unverified. "
            + (rec.get("stderr") or rec.get("stdout") or "").strip()[-300:]
        )
        return result

    result["errors"] = requirement_errors(parse_requirements(rec.get("stdout", "")))

    for err in result["errors"]:
        label = err["title"] or "Requirement"
        detail = err["value"] or err["description"]
        result["blockers"].append(f"{label}: {detail}"[:300])

    # Missing packaging metadata is a latent hazard, not a present defect: it
    # only breaks the site once some extension declares a version-constrained
    # dependency on the affected one. Drupal's requirements report is the
    # authority on whether that has happened, and it is already consulted above.
    # Blocking on the metadata alone would fail Gate 2 on sites Drupal considers
    # healthy, so it is reported as a warning the operator can act on.
    if result["sourceInstalls"]:
        names = ", ".join(sorted(x["extension"] for x in result["sourceInstalls"]))
        packages = " ".join("drupal/" + x["extension"] for x in result["sourceInstalls"])
        result["warnings"].append(
            f"Missing packaging metadata (no version in .info.yml) for: {names}. "
            "Drupal cannot verify version-constrained dependencies on these, so a "
            "future upgrade of any dependent will fail. Repair with: "
            f"composer reinstall --prefer-dist {packages}"
        )

    result["status"] = "failed" if result["blockers"] else "passed"
    return result
