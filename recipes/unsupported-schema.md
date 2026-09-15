# Unsupported module schema

Classification: **contributed-module-specific**

## Symptom

Installed schema is older than the oldest supported update path.

## Applicability

An installed module’s recorded schema is outside the supported hooks for the selected release.

## Read-only detection

Compare installed schema, source update/post-update hooks and documented intermediate release paths.

## Evidence required

Installed schema evidence, module release notes, available hooks and backup references.

## Remediation options

Prefer supported intermediate releases and complete their updates. Module-specific uninstall/reinstall is a separate reviewed alternative.

## Data or behavior implications

Uninstall can delete content, configuration or state; configuration import cannot generally restore that data.

## Verification

Confirm updates complete, module behavior and semantic content/configuration parity.

## Recovery

Restore matching code, database and files from the pre-update checkpoint.

## Do not apply

Never automatically reset schema records or uninstall/reinstall because Composer succeeds.
