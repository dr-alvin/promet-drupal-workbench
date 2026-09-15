"""Configurable heuristic estimation weights and delivery forecast calculations."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .common import ROOT, Problem, schema

DEFAULT_ESTIMATION_PATH = ROOT / "config" / "estimation.json"
_ESTIMATION_CACHE: dict[str, dict[str, Any]] = {}


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overrides dictionary into base dictionary."""
    merged = copy.deepcopy(base)
    for k, v in overrides.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def load_estimation_weights(
    profile_or_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load estimation weights from default config, profile, or custom path with optional overrides."""
    if profile_or_path is None:
        source_path = DEFAULT_ESTIMATION_PATH
    else:
        p = Path(profile_or_path)
        if p.is_file():
            source_path = p
        elif (ROOT / "config" / "estimation" / f"{profile_or_path}.json").is_file():
            source_path = ROOT / "config" / "estimation" / f"{profile_or_path}.json"
        elif (ROOT / "config" / f"estimation-{profile_or_path}.json").is_file():
            source_path = ROOT / "config" / f"estimation-{profile_or_path}.json"
        else:
            raise Problem(f"Unknown estimation profile or path '{profile_or_path}'")

    cache_key = str(source_path.resolve())
    if cache_key not in _ESTIMATION_CACHE:
        try:
            base_data = json.loads(source_path.read_text(encoding="utf-8"))
            schema(base_data, "estimation")
            _ESTIMATION_CACHE[cache_key] = base_data
        except Exception as e:
            raise Problem(f"Failed to load estimation weights from {source_path}: {e}") from e

    weights = copy.deepcopy(_ESTIMATION_CACHE[cache_key])
    if overrides:
        weights = _deep_merge(weights, overrides)
        schema(weights, "estimation")

    return weights


def get_estimation_weights(
    config: dict[str, Any] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Resolve estimation weights for a project configuration with profile and per-project overrides."""
    est_profile = profile
    overrides = None
    if config:
        cfg_est = config.get("estimation")
        if isinstance(cfg_est, str) and not est_profile:
            est_profile = cfg_est
        elif isinstance(cfg_est, dict):
            overrides = cfg_est
        if not est_profile and config.get("estimationProfile"):
            est_profile = config.get("estimationProfile")

    return load_estimation_weights(est_profile, overrides)


def delivery_forecast(
    compatibility: dict[str, Any],
    solver: dict[str, Any],
    baseline_ok: bool,
    weights: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Produce a transparent AI-assisted human-effort forecast from audit evidence."""
    summary = compatibility.get("summary", {})
    if compatibility.get("sharedBlockers") or summary.get("workCategories", {}).get(
        "Unknown or blocked"
    ):
        return None

    w = weights or get_estimation_weights(config)
    f_defs = w["factors"]

    if compatibility.get("schemaVersion") == "1.2":
        rows = compatibility.get("extensions", [])

        def count(actions):
            return len(
                {
                    row.get("package") or row["name"]
                    for row in rows
                    if row.get("selectedAction") in actions
                }
            )

        factors = {
            f_defs.get("routinePackageUpdate", {}).get("label", "routine package updates"): (
                count({"compatible_release"}),
                f_defs.get("routinePackageUpdate", {}).get("low", 0.25),
                f_defs.get("routinePackageUpdate", {}).get("high", 0.75),
            ),
            f_defs.get("reviewedPatch", {}).get("label", "reviewed patch packages"): (
                count({"available_patch"}),
                f_defs.get("reviewedPatch", {}).get("low", 0.75),
                f_defs.get("reviewedPatch", {}).get("high", 1.5),
            ),
            f_defs.get("manualOrAiRemediation", {}).get("label", "manual/AI remediations"): (
                count({"ai_manual_patch", "manual_remediation"}),
                f_defs.get("manualOrAiRemediation", {}).get("low", 1.5),
                f_defs.get("manualOrAiRemediation", {}).get("high", 3.0),
            ),
            f_defs.get("removalPlan", {}).get("label", "removal plans"): (
                count({"remove"}),
                f_defs.get("removalPlan", {}).get("low", 1.0),
                f_defs.get("removalPlan", {}).get("high", 3.0),
            ),
        }
    else:
        factors = {
            "compatible release updates": (
                summary.get("update_available", 0),
                f_defs.get("routinePackageUpdate", {}).get("low", 0.25),
                f_defs.get("routinePackageUpdate", {}).get("high", 0.75),
            ),
            "vetted patch candidates": (
                summary.get("patch_available", 0),
                f_defs.get("reviewedPatch", {}).get("low", 0.75),
                f_defs.get("reviewedPatch", {}).get("high", 1.5),
            ),
            "manual/AI code remediations": (
                summary.get("manual_remediation", 0),
                f_defs.get("manualOrAiRemediation", {}).get("low", 1.5),
                f_defs.get("manualOrAiRemediation", {}).get("high", 3.0),
            ),
            "unknown or blocked extensions": (
                summary.get("unknown", 0) + summary.get("blocked", 0),
                f_defs.get("removalPlan", {}).get("low", 1.0),
                f_defs.get("removalPlan", {}).get("high", 3.0),
            ),
        }

    low = float(w.get("base", {}).get("low", 8.0))
    high = float(w.get("base", {}).get("high", 12.0))

    assumptions = list(
        w.get(
            "assumptions",
            [
                "Includes selected-page baseline/comparison, contrib/custom compatibility work, core dependency execution, technical QA and rollback handoff.",
                "Excludes business UAT, external waiting, toolkit maintenance and uploaded-file inventory/content classification.",
            ],
        )
    )

    for label, (cnt, lo, hi) in factors.items():
        if cnt:
            low += cnt * lo
            high += cnt * hi
            assumptions.append(
                f"{cnt} {label} at {lo:g}–{hi:g} human hours each pending exact remediation evidence."
            )

    adj = w.get("adjustments", {})
    if solver.get("status") != "passed":
        s_adj = adj.get("solverUnresolved", {})
        low += s_adj.get("low", 2.0)
        high += s_adj.get("high", 4.0)
        assumptions.append(
            s_adj.get(
                "description",
                "Composer resolution is unresolved: add 2–4 hours until the conflict set is bounded.",
            )
        )

    if not baseline_ok:
        b_adj = adj.get("baselineFailed", {})
        low += b_adj.get("low", 1.0)
        high += b_adj.get("high", 2.0)
        assumptions.append(
            b_adj.get(
                "description",
                "Page baseline failed: add 1–2 hours for route/readiness correction.",
            )
        )

    def round_half(value: float) -> float:
        return round(value * 2) / 2

    return {
        "scope": "total",
        "lowHours": round_half(low),
        "highHours": round_half(high),
        "confidence": "Provisional; validate remediation and QA assumptions",
        "constraints": [
            "Human effort range is not a calendar delivery commitment.",
            "Scheduling depends on reviewer availability, technical verification and separate business UAT.",
        ],
        "basis": "AI-assisted forecast derived from audited page coverage, extension decisions, custom-code remediation evidence, Composer resolution and verification requirements.",
        "assumptions": assumptions,
    }
