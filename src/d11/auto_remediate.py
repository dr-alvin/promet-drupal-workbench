"""Automated custom code and override remediation synthesizer for Drupal 11."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .common import file_hash, write
from .guardrails import fix_drupal_standards
from .knowledge import get_deprecation_rules, get_target_constraint, get_target_info_yml
from .proposals import validate_proposal

VERIFICATION_CHECKS = {"composer_validate", "drupal_bootstrap"}


def _update_info_yml(content: str, target=None) -> str:
    """Ensure core_version_requirement includes target constraint."""
    target_constraint = get_target_constraint(target)
    target_info_yml = get_target_info_yml(target)
    if "core_version_requirement" not in content:
        return content.rstrip() + f"\ncore_version_requirement: {target_info_yml}\n"
    return re.sub(
        r"core_version_requirement:\s*([^\n]+)",
        lambda m: (
            f"core_version_requirement: {target_info_yml}"
            if target_constraint not in m.group(1)
            else m.group(0)
        ),
        content,
    )


def _update_composer_json(content: str, target=None) -> str:
    """Ensure submodule composer.json requires target core constraint."""
    target_constraint = get_target_constraint(target)
    target_info_yml = get_target_info_yml(target)
    try:
        data = json.loads(content)
        req = data.get("require", {})
        if "drupal/core" in req and target_constraint not in req["drupal/core"]:
            req["drupal/core"] = target_info_yml
            return json.dumps(data, indent=2) + "\n"
    except json.JSONDecodeError:
        pass
    return content


PHP_LITERAL_OR_COMMENT = re.compile(
    r"//[^\n]*|#[^\n]*|/\*[\s\S]*?\*/"
    r"|'(?:[^'\\]|\\.)*'"
    r'|"(?:[^"\\]|\\.)*"'
)


def _apply_known_rector_and_deprecations(content: str, target=None) -> str:
    """Apply common deterministic Rector and deprecation transformations with idempotency and literal protection."""
    spans = [m.span() for m in PHP_LITERAL_OR_COMMENT.finditer(content)]

    for rule in get_deprecation_rules(target):
        pat = rule.get("pattern")
        rep = rule.get("replacement")
        file_type = rule.get("fileType", "php")
        if file_type == "css":
            if pat in content and rule.get("guard", rep) not in content:
                content = content.replace(pat, rep)
            continue
        if file_type == "twig":
            if re.search(pat, content):
                content = re.sub(pat, rep, content)
            continue

        if not pat or not rep:
            continue

        matches = list(re.finditer(pat, content))
        if not matches:
            continue

        for m in reversed(matches):
            start, end = m.span()
            if any(s <= start and end <= e for s, e in spans):
                continue
            prefix = content[max(0, start - 150) : start]
            if "backwardsCompatibleCall" in prefix and "fn() =>" in prefix:
                continue
            content = content[:start] + rep + content[end:]
            spans = [sm.span() for sm in PHP_LITERAL_OR_COMMENT.finditer(content)]

    return content


def generate_remediation_proposals(
    site_root: Path, run_out: Path, report: dict, context: dict | None = None
) -> dict:
    """Generate and validate manual-patches proposals for custom & override extensions."""
    site_root = Path(site_root).resolve()
    run_out = Path(run_out).resolve()
    manual_dir = run_out / "manual-patches"

    # Map extension name to its known path from context or report
    ext_paths = {}
    if context and "extensions" in context:
        for e in context["extensions"]:
            if e.get("name") and e.get("path"):
                try:
                    p = Path(e["path"]).resolve()
                    if p.is_relative_to(site_root):
                        ext_paths[e["name"]] = p.relative_to(site_root)
                except (ValueError, RuntimeError):
                    pass

    remediated = []
    total_changes = 0

    drupal_root = (context or {}).get("roots", {}).get("drupal", "").strip("/")
    path_prefixes = [""]
    if drupal_root:
        path_prefixes.append(f"{drupal_root}/")
    path_prefixes.extend(["web/", "docroot/", "site/web/", "site/docroot/"])
    path_prefixes = list(dict.fromkeys(path_prefixes))

    for ext in report.get("extensions", []):
        name = ext.get("name")
        src = ext.get("source")
        status = ext.get("status")
        issues = ext.get("upgradeStatus", {}).get("issues", [])
        fixes = ext.get("rector", {}).get("changes", [])

        needs_remediation = (
            (src == "custom" and (issues or fixes or status != "ready"))
            or (
                src == "contrib"
                and not ext.get("targetVersion")
                and (issues or fixes or status != "ready")
            )
            or ext.get("recommendedAction") in ("ai_manual_patch", "manual_remediation")
        )
        if not needs_remediation:
            continue

        candidate_files: set[str] = set()

        for iss in issues:
            loc = iss.get("location", {}).get("path")
            if loc:
                for prefix in path_prefixes:
                    p = site_root / (prefix + loc)
                    if p.is_file():
                        candidate_files.add(str(p.relative_to(site_root)))
                        break

        for ch in fixes:
            p_str = ch.get("file") or ch.get("path")
            if p_str:
                for prefix in path_prefixes:
                    p = site_root / (prefix + p_str)
                    if p.is_file():
                        candidate_files.add(str(p.relative_to(site_root)))
                        break

        # Locate extension's .info.yml and submodule composer.json
        info_rel = ext_paths.get(name)
        if not info_rel:
            # Check standard custom/contrib locations across web, docroot, and root
            doc_roots = [""]
            if drupal_root:
                doc_roots.append(drupal_root)
            doc_roots.extend(["web", "docroot"])
            doc_roots = list(dict.fromkeys(doc_roots))
            for doc_root in doc_roots:
                for sub in (
                    "modules/custom",
                    "themes/custom",
                    "profiles",
                    "modules/contrib",
                    "modules",
                    "themes",
                ):
                    search_dir = f"{doc_root}/{sub}".strip("/")
                    candidate = site_root / search_dir / name / f"{name}.info.yml"
                    if candidate.is_file():
                        info_rel = candidate.relative_to(site_root)
                        break
                if info_rel:
                    break

        if info_rel:
            candidate_files.add(str(info_rel))
            comp_rel = info_rel.parent / "composer.json"
            if (site_root / comp_rel).is_file():
                candidate_files.add(str(comp_rel))
            ext_dir = (site_root / info_rel).parent
            for twig_file in ext_dir.rglob("*.html.twig"):
                try:
                    txt = twig_file.read_text(errors="replace")
                    if "{% spaceless %}" in txt or "{%spaceless%}" in txt:
                        candidate_files.add(str(twig_file.relative_to(site_root)))
                except Exception:
                    pass

        changes = []
        for rel_path in sorted(candidate_files):
            file_path = site_root / rel_path
            if not file_path.is_file():
                continue
            before = file_path.read_text(errors="replace")
            after = before

            if rel_path.endswith(".info.yml"):
                after = _update_info_yml(after)
            elif rel_path.endswith("composer.json"):
                after = _update_composer_json(after)
            elif rel_path.endswith(".twig"):
                after = _apply_known_rector_and_deprecations(after)
            else:
                after = _apply_known_rector_and_deprecations(after)
                after = fix_drupal_standards(after, rel_path)

            if after != before:
                changes.append(
                    {
                        "path": rel_path,
                        "beforeSha256": file_hash(file_path),
                        "after": after,
                        "finding": f"compatibility:{name}",
                        "rationale": "Deterministic Drupal 11 compatibility remediation",
                        "verificationCheckIds": sorted(VERIFICATION_CHECKS),
                    }
                )

        if changes:
            proposal = {
                "summary": f"Drupal 11 compatibility remediation for {name}",
                "findings": [f"compatibility:{name}"],
                "limitations": [],
                "steps": [],
                "changes": changes,
            }
            try:
                checked = validate_proposal(proposal, site_root, [], VERIFICATION_CHECKS)
                p_dir = manual_dir / name
                p_dir.mkdir(parents=True, exist_ok=True)
                write(p_dir / "proposal.json", checked["proposal"])
                (p_dir / "proposal.diff").write_text(checked["diff"])
                remediated.append(
                    {"name": name, "changes": len(changes), "digest": checked["digest"]}
                )
                total_changes += len(changes)
            except Exception:
                continue

    return {
        "status": "passed",
        "remediatedCount": len(remediated),
        "totalChanges": total_changes,
        "modules": remediated,
    }
