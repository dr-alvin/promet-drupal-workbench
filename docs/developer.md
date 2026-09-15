# Developer and deployment workflow

## Overview

Developers can audit, rehearse, and verify upgrades through two primary interfaces:
- **Drupal Upgrade Workbench (GUI)**: Run `bin/d11 dashboard` for interactive project intake, disposable scanner audits, compatibility matrix review, 1-click upgrade rehearsals, visual regression diffs, and instant rollback. See the [Drupal Upgrade Workbench Operator Guide](dashboard.md).
- **CLI Automation Runner**: Run `bin/d11 init`, `bin/d11 upgrade`, and `bin/d11 workflow rollback` for non-interactive scripting or CI pipelines. See the [Quick Start Guide](quick-start.md).

---

## Establish the baseline

Copy the example configuration into your project's tooling location and replace its values. Set authorization only for the environment you are permitted to inspect. Resolve the selected wrapper and explicit site URI. Multiple wrappers on PATH never determine the selection. Local PHP/Composer/Drush must have explicit command arrays; nested Composer roots are supported.

Run static discovery, then runtime discovery/assessment. Inspect context.json, active versus exported configuration, removed-core-extension dependencies and the observed deployment source lines. A dynamic PHP config path or conditional CI workflow may require explicit configuration/review.

Record a tested code/database/files recovery procedure with an owner and externally stored backup references. Record installed extensions and active config. Configure public and authenticated browser scenarios and establish a stable reference before code changes. Preserve existing dirty work and evidence.

## Readiness and remediation

Review the target release's current Drupal documentation, Composer metadata, actual PHP extensions and runtime/database requirements. The source minimum is Drupal 10.3.0; apply the organization's supported patch policy separately. Complete earlier update hooks and review removed core extensions before core replacement.

Use installed `drush help upgrade_status:analyze` to verify command support before configuring its check. The structured format is `--format=codeclimate`; use explicit `--all`. A nonempty valid issue array is findings even when the scanner exits nonzero; malformed output or empty output with failure is tooling failure. Missing scanners are preparation work, not successful assessments.

Prepare approved development tooling in an isolated directory where site dependency conflicts warrant it. Preserve existing Rector settings; choose source/target-appropriate rules; dry-run first, review the diff, then approve applying transformations. Run relevant static analysis and project tests. Review upstream fixes and patch applicability instead of forcing forks or broad metadata changes.

## Exact execution sequence

Populate `steps` with reviewed argv arrays and working directories from the actual project. Include a dependency-resolution dry run, approved Composer changes, lock/platform validation, a clean installation from the resulting lock in an isolated build, and the documented database/config/deploy/asset sequence. Do not infer deploy order from a regex or duplicate `drush deploy` component operations.

Before config import, inspect `config-diff.json` against active/config-export snapshots. Field/storage removal, modules/themes, roles/permissions, disabled Views/URL patterns, splits and UUID differences require review. Export into a temporary evidence directory rather than overwriting the project's sync directory during assessment.

Generate the plan and read the unsigned approval template. Review the exact commands, environment/site, requirements and baseline hashes, recovery references, proposed changes, and estimate/risks. Save the completed approval separately. `--auto-approve` is optional and has no effect on the approval requirement.

`prepare` executes preparation-tagged steps only. `execute --resume` uses the recorded checkpoint for remaining steps. After a failed or interrupted mutation, inspect logs and state before producing a new plan; the toolkit does not automatically retry, rollback or Git-reset the project.

## Verification and handoff

Configure and run Drupal bootstrap, pending updates, required config state, static/project tests, asset completeness, clean-build install and logging checks. Logging evidence must cover the execution window and identify the backend (database, files or hosting service), including sites without dblog. Compare against baseline failures. Add applicable accessibility and integration checks.

Use visual-audit test for before/after public/CMS checks. Confirm semantic content, revisions, storage and file references using the inherited content comparator with project-generated metadata, excluding private content itself. Reconcile all results into the lifecycle packet.

A production handoff requires the exact release/build identity, reviewed deployment order, durations, matching backups, named decision/recovery owners, monitoring window, rollback/roll-forward criteria, a representative deployment and restore rehearsal, and named business UAT. The toolkit does not execute Production or assume a hosting provider's packaging strategy.
