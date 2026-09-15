---
name: promet-drupal11-upgrade
description: Audit and plan Composer-managed Drupal 10-to-11 upgrades with client estimates and approval packets, execute approved non-production steps, compare before/after browser evidence, and prepare QA, UAT and deployment/recovery handoff using the repository-local Promet toolkit.
---

# Promet Drupal 11 Upgrade

Inherit the reference lifecycle's six phases, client approval, estimate ranges, evidence gates, mandatory UAT and recovery handoff. Use the toolkit two directories above this skill; never require a personal installation path. Read [toolkit-workflow.md](references/toolkit-workflow.md) for executable commands and [artifact-contract.md](references/artifact-contract.md) when creating the inherited schema-3.0 approval packet.

## Begin with evidence

Inspect the selected repository and project configuration. Run `bin/d11 discover --config PROJECT --output RUN` first; runtime collection requires `--runtime` and an explicitly authorized non-production environment and site URI. Static evidence is reused only when source/configuration/toolkit inputs match; `--refresh` bypasses reuse. Runtime identity and mutable Drupal/database/configuration evidence must always be refreshed. A blocked result can still contain useful static evidence. Fix ambiguous roots, wrappers or site selection before runtime commands. Read the actual project wrapper/configuration and deployment entrypoints, including `.ci/`; PATH is not authority.

Distinguish verified facts, inferred findings and unknowns. Active state, exported config and files on disk are different evidence. A Composer package can supply multiple extensions. Do not interpret missing runtime/scanner evidence as disabled, compatible or passed. Runtime PHP/Composer/Drush and database versions must come from the selected wrapper, not an unrelated host installation.

## Lifecycle and client decisions

Follow [lifecycle-workflow.md](references/lifecycle-workflow.md): audit/infrastructure, dependencies/patches, custom code, core/configuration, QA/UAT, rehearsal/handoff. The execution plan's stages A–F implement these phases; they do not replace the client-facing lifecycle.

For every phase, record scope, goal, entry/exit criteria, owner, risk, dependencies, estimate range and basis, testing, approval and stop conditions. Default LOE to AI-assisted delivery using this toolkit: a 20-hour human engineering/technical QA target and 3-hour feasibility checkpoint, with honest forecasts and separately reported actual effort. Pause delivery for a scope/budget decision if required work cannot fit. Read [ai-assisted-estimation.md](references/ai-assisted-estimation.md) before estimating: separate human effort, unattended runtime, business acceptance and elapsed schedule; account for automation setup/review and do not apply a blanket AI discount. Use one authoritative finding set for client and developer reports. Never invent effort estimates or an approval to fill missing evidence. Explain business effects plainly. Generate the schema-3.0 packet and audience views through `scripts/inherited/`; validate them before client delivery. See [approval-and-docs-gap-spec.md](references/approval-and-docs-gap-spec.md) for required planning evidence.

Verify the explicit target release's current requirements against authoritative Drupal release documentation. Drupal 10.3.0 is the documented in-place minimum; a newer supported source patch is a separate organizational policy. Do not use historical PHP/Drush/database floors in inherited examples as a permanent target policy.

Select applicable recipes from the toolkit `recipes/` directory; each has evidence prerequisites and exclusions. Review transformations, replacements, patches and configuration changes before adding them to an execution plan. Rector starts in dry-run mode and must preserve existing configuration. Upgrade Status must match the installed CLI; its structured scan output is Code Climate, not assumed `--format=json`.

## Execute only an approved plan

`d11 plan` emits commands, input hashes, environment/site, recovery references, proposed changes, blockers and an unsigned approval template. The developer completes a separate approval file matching that plan. `--auto-approve` only consumes an existing approval; it grants no extra scope.

Before any mutation require code/database/files backup references, recovery owner and procedure, baseline evidence, source readiness, reviewed target requirements, and command pre/postconditions. Prefer isolated development tools. Preserve the site's dependency strategy and lockfile. Dry-run dependency resolution, validate lock/platform requirements, then test clean installation in an isolated build. Follow the reviewed deployment sequence; do not duplicate `drush deploy` components.

Stop for stale inputs, wrong environment, unknown prerequisites, unavailable backup, failure, interrupted mutation or destructive/unreviewed changes. Never retry a failed/partially completed mutation blindly. Reconcile state and produce a new reviewed plan. Recovery after hooks requires matching code, database and files; never silently restore or discard changes.

Production is handoff-only. Do not stage, commit, push, open PRs or send external messages automatically. Do not modify global skills or upgrade a client site incidentally during toolkit development.

## Before/after testing and release handoff

Use `bin/visual-audit reference` before mutation, including repeated unchanged-site captures, and `test` afterward. Test expected URLs/content and authentication before accepting screenshots. Keep role secrets in explicitly forwarded environment variables. Default to non-saving CMS interactions; writes require an authorized disposable environment and cleanup. A failed or missing capture is not a visual pass.

Use [qa-uat-spec.md](references/qa-uat-spec.md) to build project-specific public/CMS, access, editor, media, Views, display-mode, forms/search, integration and applicable accessibility coverage. QA instructions are browser steps; commands belong in developer/deployment instructions. Review new PHP/Drupal/browser/network errors for the run window using the configured logging backend, not just the latest five watchdog entries.

Report “Passed the configured scenarios and thresholds.” Automated passing results do not establish complete upgrade or production readiness. Require named business UAT for the exact release plus representative deployment and restore rehearsal. Read [hosting-handoff.md](references/hosting-handoff.md) for applicable Acquia/Pantheon handoff evidence; do not claim those integrations were tested without actual runs.

## Local dashboard and isolated demonstrations

Use `bin/d11 upgrade /path/to/project` for the unified 1-command CLI runner, or `bin/d11 dashboard` for the visual workbench described in `docs/dashboard.md` at the toolkit root. The normal operator flow has two gates: **Add project → Run audit → Auto-resolve (or auto-decide & remediate) → Approve upgrade → Review results or roll back**. The only normal inputs are the running local project root and URL, plus an optional sitemap URL or explicit routes. Detect DDEV or Docksal and create a Git-free toolkit-managed destination. Never mutate the selected source project. A named privacy/isolation review is still required after automatic sanitization.

The guided audit must refresh mutable runtime evidence, execute verified Upgrade Status and Rector dry runs, discover at most 100 same-host routes, capture a stable baseline, resolve the target in a disposable Composer workspace, and produce one risk decision with exact changes and recovery references. Prefer stable compatible contrib releases. Use `bin/d11 workflow auto-decide --run RUN_ID` to automatically map stable Composer solver targets, select approved merge-request patches, and generate hash-pinned remediation proposals for custom extensions, establishing Gate 1 approval eligibility without manual decision authoring. Missing compatibility or critical custom-code evidence is a No-Go.

Gate 1 approval must bind the named reviewer, privacy review, project fingerprint, gate digest, exact target, plan, proposal, dependency result, route/baseline digest, recovery inputs and allowed scopes. Recompute these bindings immediately before mutation. No-Go cannot be approved; Conditional Go requires recorded risk acceptance.

Before the approved batch runs, create and verify a coordinated code, database, public-files and private-files checkpoint. Automatically restore it after Composer, patching, Rector, database, bootstrap or required smoke-check failure, then verify restored runtime identity, Drupal bootstrap and critical routes. Preserve an upgraded candidate on browser/visual failure so Gate 2 can choose diagnosis or rollback. A failed rollback is critical.

AI proposals are untrusted data: validate their schema, source hashes, scope and registered verification checks. Review concrete diffs and commands, bind approval to the exact plan and artifacts, then use the existing guarded executor. Preparation may use reviewed baseline-failure evidence without clean target scans; it still requires isolation, recovery and an exclusively preparation-marked batch. Core-upgrade gates remain unchanged. Interrupted mutations require reconciliation and a new plan.

Keep actual contest measurements separate from planning allowances. Fixture runs, AI narrative, missing effort logs and incomplete QA must never become claims of real-site completion, zero human effort or release readiness.
