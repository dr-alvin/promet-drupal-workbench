# Six-Phase Lifecycle Workflow

## Phase record contract

Each phase record contains `id`, `name`, `goal`, `entryCriteria`, `tasks`, `evidence`, `owner`, `risk`, `estimateBand`, `dependencies`, `acceptanceCriteria`, `testingInstructions`, `approvalState`, `exitCriteria`, `stopConditions`, and `status`. Arrays must be non-empty. Text cannot be empty, `none`, `n/a`, `tbd`, `todo`, `unknown`, or placeholder prose. Evidence paths must be relative to the artifact root.

Allowed status values are `green`, `amber`, `red`, and `unknown`. Phase order is fixed and a later phase cannot be Green while an earlier phase is Red or Unknown. Amber requires a risk acceptance record.

## 1. Audit and infrastructure

Verify Drupal 10.3.0 minimum source and the selected target release’s documented PHP, Composer, Drush and database requirements, wrapper, hosting, environment identity, deployment ownership, logs, status report, pending updates, configuration drift, database health, and backup/restore ownership. Capture pre-existing failures separately. Stop for unknown environment identity, unsupported platform, failed bootstrap, unresolved security/status errors, or absent recovery ownership.

## 2. Dependencies and patches

Capture Composer validation, direct requirements, `why-not` results, abandoned packages, removed-core-extension decisions, contrib compatibility, and every patch's upstream status, owner, regression scope, and removal trigger. Prefer stable compatible releases, then approved replacements/removals. Allow patches or prereleases only as explicitly accepted Amber risks. Never delete the lockfile or reset constraints indiscriminately.

## 3. Custom code remediation

List each custom extension. Run Drupal 11-targeted PHPStan/Upgrade Status and Rector dry-run assistance. Review Symfony 7, Twig 3, CKEditor 5, SDC, recipe, plugin, service, event, access, form, widget, formatter, and integration paths. Change `core_version_requirement` only after code review and regression evidence. Stop for missing providers, fatal paths, unreviewed automation, or unsupported critical business behavior.

## 4. Core and configuration alignment

Require approved non-production steps and matching code/database/files recovery evidence. Resolve the selected Drupal 11 release and its compatible Drush version without deleting `composer.lock`; run the repository's single deployment path; stop on any critical failure. Reconcile configuration UUID, field storage, revisions, semantic content counts, references, files, aliases, workflows, and access. Production mutation is prohibited.

## 5. QA and mandatory UAT

Run automated, functional, editorial, access, integration, visual, accessibility, performance, log, and security smoke coverage appropriate to the site. Read `qa-uat-spec.md`. Critical failures are Red. Skipped critical coverage is Unknown. UAT must approve the exact release candidate before Phase 6 can become Green.

## 6. Rehearsal and Production handoff

Rehearse deployment and restoration in a representative lower environment. Capture immutable release ID, database/files backup IDs, command/task sequence, durations, monitoring, decision owners, rollback/roll-forward criteria, and validation results. Produce handoff instructions only; do not execute Production. Green requires passed rehearsal, passed restore, approved UAT, defect disposition, and final sign-off.

## Aggregate gate

Use precedence `red > unknown > amber > green`. Green requires every phase Green. Amber requires every non-Green phase to be Amber with valid acceptance. Any Red or Unknown blocks progression.
