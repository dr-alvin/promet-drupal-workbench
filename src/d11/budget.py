"""Human effort accounting; elapsed machine time is never inferred as labor."""

from .common import Problem

ALLOCATION = {
    "discoveryBaseline": 3,
    "dependenciesPatches": 5,
    "customCode": 4,
    "coreConfiguration": 3,
    "verificationRecovery": 5,
}


def scaffold_delivery_budget(cfg):
    b = cfg.setdefault("deliveryBudget", {})
    b.setdefault("targetHours", 20)
    b.setdefault("checkpointHours", 3)
    b.setdefault("reportedHumanHours", 2.5)
    b.setdefault(
        "forecast",
        {
            "scope": "total",
            "lowHours": 8.0,
            "highHours": 16.0,
            "basis": "Default evidence-based forecast from baseline targets",
        },
    )
    return b


def budget_model(cfg):
    b = cfg.get("deliveryBudget", {})
    target, checkpoint = b.get("targetHours", 20), b.get("checkpointHours", 3)
    if target != 20 and not b.get("projectDecision", "").strip():
        raise Problem("Changing the 20-hour target requires deliveryBudget.projectDecision", 64)
    if checkpoint > target:
        raise Problem("Checkpoint must not exceed target", 64)
    effort = b.get("reportedHumanHours")
    forecast = b.get("forecast")
    if forecast and forecast["highHours"] < forecast["lowHours"]:
        raise Problem("Forecast upper bound must be >= lower bound", 64)
    remaining = None if effort is None else max(0, target - effort)
    forecast_total = (
        None
        if not forecast or (forecast.get("scope", "total") == "remaining" and effort is None)
        else {
            k: forecast[k] + (effort if forecast.get("scope") == "remaining" else 0)
            for k in ("lowHours", "highHours")
        }
    )
    over = bool(forecast and (forecast_total or forecast)["highHours"] > target)
    due = effort is not None and effort >= checkpoint
    status = (
        "decision_required"
        if over or (effort is not None and effort >= target)
        else "unknown"
        if not forecast
        else "within_target"
    )
    review = b.get("checkpointReview", {})
    checkpoint_status = (
        "decision_required"
        if status == "decision_required"
        else "review_required"
        if due and not review
        else "reviewed"
        if due
        else "not_reached"
        if effort is not None
        else "effort_unknown"
    )
    return {
        "targetHours": target,
        "checkpointHours": checkpoint,
        "allocationHours": ALLOCATION,
        "allocationBasis": "Default 20-hour allocation; replan allocations after a project budget decision.",
        "forecast": forecast,
        "totalForecastHours": forecast_total,
        "reportedHumanHours": effort,
        "remainingAllowanceHours": remaining,
        "forecastRemainingHours": None
        if not forecast or effort is None
        else {
            "low": max(0, forecast_total["lowHours"] - effort),
            "high": max(0, forecast_total["highHours"] - effort),
        },
        "feasibility": status,
        "checkpointStatus": checkpoint_status,
        "checkpointReview": review,
        "completedWork": b.get("completedWork", []),
        "unresolvedBlockers": b.get("unresolvedBlockers", []),
        "unattendedRuntimeHours": b.get("unattendedRuntimeHours"),
        "externalWaitingHours": b.get("externalWaitingHours"),
        "businessUatHours": b.get("businessUatHours"),
        "projectDecision": b.get("projectDecision"),
        "accounting": "Human supervision and technical QA count. Business UAT, unattended runtime, external waiting and toolkit maintenance are separate. Forecast scope is explicitly total or remaining; total is unknown when remaining-work forecasts have unreported actual effort.",
    }


def enforce_budget(cfg):
    model = budget_model(cfg)
    if model["reportedHumanHours"] is None or model["forecast"] is None:
        raise Problem(
            "Execution requires reported human effort and an evidence-based total forecast"
        )
    if model["checkpointStatus"] in ("decision_required", "review_required"):
        raise Problem(
            "Delivery checkpoint: pause for scope/budget decision or feasibility review; update the project budget and replan"
        )
    return model


def budget_markdown(m):
    f = m["forecast"]
    forecast = (
        f"{f['lowHours']}–{f['highHours']}h; {f['basis']}"
        if f
        else "unknown; evidence review required"
    )
    human = (
        "not reported" if m["reportedHumanHours"] is None else str(m["reportedHumanHours"]) + "h"
    )
    remaining = (
        "unknown until effort is reported"
        if m["remainingAllowanceHours"] is None
        else str(m["remainingAllowanceHours"]) + "h"
    )
    return "\n".join(
        [
            "## AI-assisted delivery budget",
            "",
            f"{'Toolkit default planning target' if m['targetHours'] == 20 else 'Project planning target'}: **{m['targetHours']} human engineering/technical QA hours**. Feasibility checkpoint: **{m['checkpointHours']}h**.",
            f"Forecast ({f.get('scope', 'total') if f else 'total'} human effort): **{forecast}**.",
            f"Reported human effort: {human}. Remaining allowance: {remaining}.",
            f"Feasibility: **{m['feasibility'].replace('_', ' ')}**. Checkpoint: **{m['checkpointStatus'].replace('_', ' ')}**.",
            "Default allocation: discovery/baseline 3h; dependencies/patches 5h; custom code 4h; core/configuration 3h; verification/recovery 5h.",
            "Completed work: " + ("; ".join(m["completedWork"]) or "not reported") + ".",
            "Unresolved blockers: "
            + (
                "; ".join(m["unresolvedBlockers"])
                or "see readiness evidence; no empty-list clearance implied"
            )
            + ".",
            m["accounting"],
            "Backups, code/configuration review, complete required tests, business UAT and recovery rehearsal remain mandatory. A budget is not a readiness waiver.",
            "",
        ]
    )
