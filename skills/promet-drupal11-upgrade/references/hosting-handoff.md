# Hosting Rehearsal and Production Handoff

## Common requirements

Identify the actual provider and environment; do not infer it from aliases alone. Record immutable code release, matching database/files backups, build ownership, deployment entrypoint, update/config/cache sequence, maintenance behavior, monitoring sources, rollback/roll-forward criteria, owners, and communication window.

Rehearse the exact sequence in a representative lower environment. Critical commands must fail closed. Do not duplicate `drush deploy` with separate update/import/rebuild steps unless the repository explicitly documents that model.

Production artifacts are instructions and evidence only. The skill never executes Production mutation.

## Acquia

Verify application/environment aliases, Dev-to-Stage-to-Prod promotion, remote Drush ownership, build artifact behavior, Cloud Actions/hooks, backup IDs, database/files restore procedure, cache/CDN behavior, logs/monitoring, and release identifiers. For Site Factory add cohort, pause, retry, isolation, and fleet recovery controls. For Site Studio add package/component synchronization and visual/editorial checks.

## Pantheon

Verify Dev-to-Test-to-Live promotion, Multidev/Integrated Composer/upstream model, `pantheon.yml`, Terminus ownership, project-local Drush, backups, deploy hooks, cache/CDN behavior, New Relic/logs, and release identifiers. Use Test with representative Live data/files only when approved.

## Handoff gate

Require passed deployment rehearsal, passed restore rehearsal, approved UAT for the same release candidate, no Red/Unknown phase, accepted Amber risks, monitoring and decision owners, maintenance approval, and final sign-off.
