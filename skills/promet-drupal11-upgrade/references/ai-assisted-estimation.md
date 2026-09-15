# AI-assisted level of effort

Default to delivery using the repository-local toolkit, AI-assisted repository analysis and code changes, applicable automated remediation, and repeatable browser/static/project tests. Do not silently estimate every investigation, code edit, screenshot comparison or report as manual work.

Estimate remaining work from current evidence. Already-completed discovery and reusable reports are not charged again. Identify each work package's deliverable, known scope, AI/tool contribution, human review and decision work, automation setup, verification/rework, prerequisites and confidence.

Report separately:
- Human engineering and technical QA person-hours: investigation, tool setup/supervision, code review, decisions, corrections and verification. Include time actually spent supervising tools, but not unattended runtime as human labor.
- Business/independent acceptance effort: participant hours and ownership, separate from engineering support already counted.
- Unattended tool runtime and elapsed delivery time: show unknown where unmeasured; account for dependencies and waiting. Concurrent work does not automatically reduce total person-hours.
- Explicit exclusions and escalation triggers: major replacements, data recovery, extensive scanner findings, provider/access delays and scope additions need re-estimation.

Do not use a universal AI speedup, arbitrary percentage discount, or unchanged per-item manual rates. Batch discovery/scanning and report generation where tooling supports it; retain item-level review for patches, dependency decisions and data/behavior changes. State what must still be implemented or repaired in the automation itself.

With incomplete scans, label downstream ranges low-confidence planning allowances tied to bounded assumptions, not validated quotes or measured productivity. A preparation timebox is a budget for reducing uncertainty, not a promise that all blockers can be fixed within it. Re-estimate after the timebox using actual human time, unattended runtime, completed deliverables and remaining findings.

AI assistance does not waive backups, code/config review, functional testing, business UAT or representative recovery rehearsal. Preserve readiness/approval gates independently of the estimate.

When revising a published estimate, keep the prior packet as a historical snapshot or regenerate all affected audience files and hashes consistently. A separately versioned estimate addendum must clearly identify what it supersedes and which original findings remain authoritative.

## Default 20 hour delivery target

Every project defaults to 20 human engineering/technical QA hours with a 3-hour feasibility checkpoint. Allocate 3h discovery/baseline, 5h dependencies/patches, 4h custom code, 3h core/configuration, and 5h verification/recovery. This allocation is a target, not evidence that the project fits. Toolkit maintenance is separate.

Use `deliveryBudget` in the project JSON as the accounting source. Keep target, forecast (explicitly total or remaining), reported actual effort, and remaining allowance separate. Unknown actuals remain unknown. Include human tool supervision and technical QA. Track unattended runtime, external waiting and business UAT independently. A changed target requires a recorded explicit project decision; do not infer approval from a forecast.

At 3 human hours, record completed work, unresolved blockers, forecast, feasibility and reviewer evidence. Pause delivery for a scope/budget decision when the remaining required work cannot reasonably fit, including when the upper forecast already exceeds the target. Do not clip estimates, conceal uncertainty or skip release requirements. Scope reductions must preserve required testing, UAT, backups and recovery. Execution needs reported effort and forecast; checkpoint review and any budget decision must be reflected in a newly reviewed plan.

Use [the unified prompt](../../../docs/unified-prompt.md) to start a configuration-driven read-only audit. Regenerate new audience views from one normalized revision model and preserve historical reports. A toolkit diagnostic fix does not clear a client's findings or establish a valid visual baseline; capture behavior changes require a fresh output directory and baseline.

In audience reports, present the default allowance as the toolkit planning target. Do not attribute it to the requesting user or describe it as a user-imposed maximum. Keep actual forecasts, assumptions and feasibility visible.
