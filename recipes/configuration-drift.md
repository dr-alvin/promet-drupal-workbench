# Configuration drift

Classification: **project-specific**

## Symptom

Upgrade export/import proposes unexpected behavior or data changes.

## Applicability

Active/exported differences include consequential or unrelated changes.

## Read-only detection

Compare configuration semantically; flag field/storage deletion, extension removal, roles, disabled Views/URL patterns, splits and UUID.

## Evidence required

Before/after config hashes, provenance, active site identity and reviewed intended changes.

## Remediation options

Separate intended upgrade changes from unrelated drift; reconcile in the correct environment and review before import.

## Data or behavior implications

Field/storage or extension deletion may destroy data; access and URL changes affect users.

## Verification

Required config state, content semantics, permissions, routes and affected CMS workflows.

## Recovery

Restore matching config/code/database/files according to whether destructive imports or hooks occurred.

## Do not apply

Do not accept all exported changes, bypass UUID mismatches or import another environment’s configuration.
