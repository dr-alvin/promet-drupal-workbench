"""Bounded Drupal.org GitLab fallback discovery for unresolved contrib packages."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import command, get_d11_home, write

CORE = {
    "drupal/core",
    "drupal/core-recommended",
    "drupal/core-composer-scaffold",
    "drupal/core-project-message",
}
PRE_RELEASE = re.compile(r"(?:dev|alpha|beta|rc)", re.I)


def is_d11_compatible(compat):
    if not compat:
        return False
    if re.search(r"<\s*1[2-9]\b", compat):
        return True
    if re.search(r"(?:\^|~|>=|\b)11(?:\.x|\b)", compat):
        parts = [p.strip() for p in re.split(r"[,|]+", compat)]
        for p in parts:
            if re.search(r"(?:<|<=)\s*11(?:\.0(?:\.0)?)?(?!\.\d|\d)", p):
                continue
            if re.search(r"(?:\^|~|>=|\b)11(?:\.x|\b)", p):
                return True
    return False


def _composer_version(version):
    """Translate legacy Drupal.org 8.x-1.x labels to Composer's package version."""
    match = re.match(r"^\d+\.x-(\d+\..+)$", version or "")
    if match:
        return match.group(1)
    if version and not re.match(r"^\d+\.x-", version):
        return version
    return version


def _release_candidates(machine, opener):
    url = (
        "https://updates.drupal.org/release-history/"
        + urllib.parse.quote(machine, safe="")
        + "/current"
    )
    import sys, os
    is_test = "unittest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    cache_dir = get_d11_home() / "cache" / "release-history"
    if not is_test:
        cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{machine}.xml"
    content = None
    if not is_test and cache_file.is_file():
        try:
            if (time.time() - cache_file.stat().st_mtime) < 7200:
                content = cache_file.read_bytes()
        except OSError:
            pass

    if content is None:
        try:
            with opener(
                urllib.request.Request(url, headers={"User-Agent": "promet-d11-upgrade-workbench/1"}),
                timeout=15,
            ) as response:
                content = response.read(2000000)
            if not is_test:
                try:
                    cache_file.write_bytes(content)
                except OSError:
                    pass
        except Exception as exc:
            if not is_test and cache_file.is_file():
                content = cache_file.read_bytes()
            else:
                raise exc

    document = ET.fromstring(content)
    values = []
    for release in document.findall("./releases/release"):
        version = (release.findtext("version") or "").strip()
        compatibility = (release.findtext("core_compatibility") or "").strip()
        if (
            not version
            or release.findtext("status") != "published"
            or not is_d11_compatible(compatibility)
        ):
            continue
        stability = "prerelease" if PRE_RELEASE.search(version) else "stable"
        values.append(
            {
                "version": _composer_version(version),
                "drupalOrgVersion": version,
                "stability": stability,
                "coreCompatibility": compatibility,
                "source": url,
            }
        )
    # Drupal.org returns newest releases first. Preserve that order while
    # preferring a stable option for the default decision.
    stable = [x for x in values if x["stability"] == "stable"][:5]
    prerelease = [x for x in values if x["stability"] == "prerelease"][:5]
    return stable + prerelease


def _blockers(solver):
    records = [solver.get("whyNot", {}), solver.get("command", {})]
    text = "\n".join(
        record.get("stdout", "") + "\n" + record.get("stderr", "") for record in records
    ).lower()
    return sorted(
        {name for name in re.findall(r"\bdrupal/[a-z0-9_-]+\b", text) if name not in CORE}
    )[:12]


def _extension_root(context, package):
    paths = []
    for extension in (context or {}).get("extensions", []):
        if extension.get("package") == package:
            path = Path(extension.get("path", ""))
            if path.is_file():
                paths.append(path.parent)
    return min(paths, key=lambda p: len(p.parts)) if paths else None


def discover(solver, out, packages=None, opener=None, context=None, patch_packages=None):
    """Find review candidates; never applies a remote patch or marks it approved."""
    opener = opener or urllib.request.urlopen
    root = Path(out) / "patches"
    root.mkdir(exist_ok=True)
    requested = sorted(set((packages or []) + _blockers(solver)))
    omitted = max(0, len(requested) - 200)
    packages = requested[:200]
    patch_targets = set(
        (patch_packages if patch_packages is not None else packages) + _blockers(solver)
    )
    result = {
        "status": "not_needed" if solver.get("status") == "passed" and not packages else "searched",
        "packages": [],
        "limitations": [],
        "omittedPackages": omitted,
    }
    if omitted:
        result["limitations"].append(
            f"{omitted} contrib packages exceeded the 200-package release-discovery safety bound."
        )

    def inspect(package):
        machine = package.split("/", 1)[1]
        encoded = urllib.parse.quote("project/" + machine, safe="")
        url = (
            "https://git.drupalcode.org/api/v4/projects/"
            + encoded
            + "/merge_requests?state=opened&search=Drupal%2011&per_page=5"
        )
        item = {
            "package": package,
            "stableResolution": "unavailable",
            "prereleaseResolution": "unavailable",
            "releaseCandidates": [],
            "query": url if package in patch_targets else None,
            "candidates": [],
        }
        try:
            item["releaseCandidates"] = _release_candidates(machine, opener)
            stable = next(
                (x for x in item["releaseCandidates"] if x["stability"] == "stable"), None
            )
            prerelease = next(
                (x for x in item["releaseCandidates"] if x["stability"] == "prerelease"), None
            )
            item["stableResolution"] = stable["version"] if stable else "unavailable"
            item["prereleaseResolution"] = prerelease["version"] if prerelease else "unavailable"
            item["releaseLookup"] = "ok"
        except Exception as exc:
            item["releaseLookup"] = "failed"
            item["releaseError"] = type(exc).__name__ + ": " + str(exc)[:300]
        try:
            if package not in patch_targets:
                return item
            with opener(
                urllib.request.Request(
                    url, headers={"User-Agent": "promet-d11-upgrade-workbench/1"}
                ),
                timeout=15,
            ) as response:
                data = json.loads(response.read(1000000))
            for mr in data if isinstance(data, list) else []:
                web = mr.get("web_url", "")
                sha = mr.get("sha") or mr.get("diff_refs", {}).get("head_sha")
                if (
                    not web.startswith(
                        "https://git.drupalcode.org/project/" + machine + "/-/merge_requests/"
                    )
                    or not sha
                ):
                    continue
                name = machine + "-mr-" + str(mr.get("iid")) + "-" + sha[:12] + ".patch"
                path = root / name
                if not path.is_file():
                    diff_url = web + ".diff"
                    with opener(
                        urllib.request.Request(
                            diff_url, headers={"User-Agent": "promet-d11-upgrade-workbench/1"}
                        ),
                        timeout=15,
                    ) as response:
                        content = response.read(5000000)
                    path.write_bytes(content)
                else:
                    content = path.read_bytes()
                extension_root = _extension_root(context, package)
                applicability = "not_verified"
                apply_record = None
                if extension_root:
                    apply_record = command(
                        ["patch", "--dry-run", "--batch", "-p1", "-i", str(path)],
                        extension_root,
                        120,
                    )
                    applicability = "passed" if apply_record.get("exitCode") == 0 else "failed"
                # Only advertise checks that the approved executor actually runs.
                # Upgrade Status evidence is captured during the disposable audit;
                # post-upgrade route/visual checks run in Gate 2.
                regression = ["composer_validate", "drupal_bootstrap"]
                patch_text = content.decode(errors="replace")
                changed_files = len(re.findall(r"^diff --git ", patch_text, re.M))
                runtime_change = bool(
                    re.search(
                        r"^\+\+\+ b/(?:src|modules|themes|[^/]+\.module|[^/]+\.php)",
                        patch_text,
                        re.M,
                    )
                )
                pipeline = (mr.get("head_pipeline") or {}).get("status")
                risk_reasons = []
                if runtime_change:
                    risk_reasons.append("changes runtime code")
                if changed_files > 5:
                    risk_reasons.append("changes more than five files")
                if mr.get("work_in_progress"):
                    risk_reasons.append("merge request is marked work in progress")
                if mr.get("blocking_discussions_resolved") is not True:
                    risk_reasons.append("review discussions are not verified resolved")
                if pipeline != "success":
                    risk_reasons.append("successful upstream test pipeline is not verified")
                item["candidates"].append(
                    {
                        "id": machine + "-mr-" + str(mr.get("iid")) + "-" + sha[:12],
                        "title": mr.get("title"),
                        "source": web,
                        "status": mr.get("state"),
                        "commit": sha,
                        "patch": str(path.relative_to(out)),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "applicability": applicability,
                        "applicabilityCommand": apply_record,
                        "changedFiles": changed_files,
                        "runtimeCodeChange": runtime_change,
                        "reviewStatus": "gate_1_review_required",
                        "upstreamReviewEvidence": {
                            "workInProgress": mr.get("work_in_progress"),
                            "blockingDiscussionsResolved": mr.get("blocking_discussions_resolved"),
                            "mergeStatus": mr.get("detailed_merge_status")
                            or mr.get("merge_status"),
                        },
                        "testEvidence": {
                            "upstreamPipeline": pipeline,
                            "requiredLocalChecks": regression,
                        },
                        "riskClassification": "critical" if risk_reasons else "high",
                        "riskReasons": risk_reasons,
                        "regressionChecks": regression,
                        "approvalEligible": mr.get("state") == "opened"
                        and applicability == "passed",
                    }
                )
        except Exception as exc:
            item["patchError"] = type(exc).__name__ + ": " + str(exc)[:300]
        return item

    with ThreadPoolExecutor(max_workers=12) as pool:
        result["packages"] = list(pool.map(inspect, packages))
    if solver.get("status") != "passed" and not result["packages"]:
        result["limitations"].append(
            "Composer output did not identify a bounded contrib package for automatic Drupal.org MR discovery."
        )
    if any(x["candidates"] for x in result["packages"]):
        result["status"] = "candidates_found"
    elif any(x["releaseCandidates"] for x in result["packages"]):
        result["status"] = "releases_found"
    write(Path(out) / "patch-candidates.json", result)
    return result
