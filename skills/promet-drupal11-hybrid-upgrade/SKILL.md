---
name: promet-drupal11-hybrid-upgrade
description: Run the Promet four-step Drupal 10-to-11 workbench for managed local copies, including scanning, module/theme decisions, AI-assisted patch proposals, exact approval, verification, and rollback. Use when auditing or upgrading a Composer-managed Drupal 10 project through this repository's dashboard or CLI.
---

# Promet Drupal 11 Hybrid Upgrade

Use the toolkit at the repository root as the execution and evidence authority. Start with [operator-workflow.md](references/operator-workflow.md). Read [compatibility-decisions.md](references/compatibility-decisions.md) before recommending a release, patch, AI change, replacement, or removal. Read [provider-contract.md](references/provider-contract.md) before invoking an AI CLI.

## Route the request

- **Unified 1-command upgrade (Preferred)**: Run `bin/d11 upgrade /path/to/drupal` (with optional `--dry-run` or `-y`). It automatically detects runtime/URL, checks obsolete packages, scans, auto-remediates custom code, resolves contrib decisions, prints the plan summary, prompts for confirmation, and executes the upgrade on an isolated copy.
- **1-click dashboard (Visual)**: Launch `bin/d11 dashboard`. Add project path (URL and wrapper are auto-detected), click Scan, click **"✨ Auto-Resolve Everything"** to remediate custom modules and clear Gate 1, then click **"Proceed with managed upgrade"**.
- For step-by-step CLI workflow:
  1. `bin/d11 workflow setup-create --source /path/to/drupal --source-url https://project.ddev.site`
  2. `bin/d11 workflow inspect-obsolete --project PROJECT_ID` (pre-flight package check)
  3. `bin/d11 workflow scan --project PROJECT_ID`
  4. `bin/d11 workflow auto-decide --run RUN_ID` (auto-remediates custom code and resolves all extension decisions)
  5. `bin/d11 workflow approve-upgrade --run RUN_ID --reviewer "NAME" --privacy-reviewed --accept-risks --approval-digest DIGEST`
- For a failed command or health check, use recorded automatic rollback evidence. For a preserved visual failure, ask for the dashboard disposition and use its rollback action when selected.

## Evidence rules

Use `reviewDisposition` to focus operator attention on required decisions; read the complete contrib/custom inventory for client documentation. `automatically_planned` choices still belong to the exact approved batch. Preserve the manifest's automatic/operator origin. Shared verification gaps remain blockers even when no extension needs an individual decision. Reports before schema 1.2 require a fresh scan for automatic planning. Never infer compatibility from an extension being hidden in the decision view.

Treat toolkit evidence as authoritative. Keep command success, scanner findings, compatibility conclusions, missing evidence, and site failures distinct. Never convert missing or malformed output into compatibility. AI narrative is a proposal and never a test result.

Limit the upgrade assessment to selected-page behavior, contributed extensions, custom modules/themes/profiles, and the minimum platform/dependency/recovery prerequisites required to judge Drupal 11 feasibility. Do not treat uploaded-file inventory or content classification as a separate audit or estimate category. Report a broken visible asset only when it affects a selected page.

Upgrade Status and Drupal Rector run only in the disposable Drupal 10 analysis copy. The original local project and managed upgrade candidate must remain unchanged during analysis. Prefer compatible stable releases. Require explicit risk acceptance for prereleases. Accept a Drupal.org patch only when its issue or MR, status, exact commit, local SHA-256, applicability result, and regression checks are recorded.

AI may propose only registered file changes and verification IDs against sanitized source. Validate paths, source hashes, schema, scope, patch content, and deterministic checks before exposing approval. Never let provider output supply shell commands or approval. Missing or contradictory decisions must stop execution and name the affected extensions.

## Safety boundary

Work only on toolkit-managed local copies. Preserve coordinated code, database, public-files, and private-files recovery evidence. Stop for stale fingerprints, changed patch hashes, unresolved reverse dependencies, populated-content uncertainty, destructive configuration loss, unsupported runtime identity, incomplete baseline, or failed rollback.

Never operate on Production. Never stage, commit, push, create Git state, or modify the selected source project. Automated verification remains separate from business UAT, recovery rehearsal, and production release readiness.
