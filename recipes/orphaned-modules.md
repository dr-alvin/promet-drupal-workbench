# Missing or orphaned module records

Classification: **contributed-module-specific**

## Symptom

Extension reports show missing code or residual records.

## Applicability

Evidence distinguishes installed missing code, disk-only extensions, residual schema and dependency-retained packages.

## Read-only detection

Compare active core.extension, extension discovery, schema records, Composer installed paths and reverse dependencies.

## Evidence required

Runtime state and package-to-multiple-module mapping; missing runtime remains unknown.

## Remediation options

Restore compatible required code; remove unused packages only after dependency/configuration review; investigate residual records separately.

## Data or behavior implications

Package removal can affect several enabled modules and their configuration.

## Verification

Bootstrap, configuration dependency validation, update status and affected module workflows.

## Recovery

Restore matching package lock/code and data if any uninstall occurred.

## Do not apply

Do not call every red entry harmless, treat files as installed, or delete schema records automatically.
