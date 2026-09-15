# Compatibility decisions

Use exactly one action for each module or theme. The dashboard stores the normalized map in `compatibility-decisions.json`, keyed by extension machine name.

## Keep

Use **Keep — already compatible** only when completed scanner and dependency evidence establishes Drupal 11 compatibility. The toolkit may preselect this low-risk action.

## Compatible release

Prefer a stable release that the disposable Composer solver resolves with the exact Drupal 11 core target. A prerelease requires explicit risk acceptance and the same verification as a patch. A declared core constraint without completed scanner and dependency evidence is insufficient.

The toolkit may preselect a verified stable compatible release. Saving the choice reruns Composer resolution before it becomes approval eligible.

## Verified Drupal.org patch

Use only an open, reviewed Drupal.org issue or merge request. Store the patch locally, bind its exact commit and SHA-256, verify applicability against the installed extension source, and register regression checks. Treat broad runtime-code patches, weak review status, or weak tests as High or Critical risk. A changed remote patch becomes a new candidate and invalidates approval.

## AI manual patch

Give the provider only sanitized scanner evidence and the affected source. Provider output must match the proposal schema, use project-relative permitted paths, retain source hashes, select registered verification checks, and contain no shell authority. Review the diff. The deterministic executor applies it only after Gate 1 approval.

## Manual remediation

Use for an exact human-authored or deterministic Rector diff that already has a proposal digest and registered checks. A note without an exact validated diff is not executable and remains blocked.

## Remove or uninstall

Require reverse dependency, active/exported configuration, field-provider, populated-content, and uninstall-order evidence. An enabled module may be removed only when the generated removal evidence shows no unresolved dependents, configuration migration, or populated data risk. Theme removal additionally requires an exact default/admin theme replacement plan. Missing evidence remains No-Go.

## Defer

Defer preserves the finding and keeps Gate 1 blocked. Use it when no supported resolution is ready or the business decision belongs outside the current batch.

Any choice change regenerates `compatibility-report.json`, `compatibility-decisions.json`, `plan.json`, `batch-config.json`, and the Gate approval digest. Changed source, dependency state, patch hashes, routes, runtime identity, or decisions makes an earlier approval stale.
