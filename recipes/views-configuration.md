# Outdated Views configuration

Classification: **contributed-module-specific**

## Symptom

Views reports missing handlers, invalid tables or relationships.

## Applicability

The installed module version changed handlers or data definitions used by a configured view.

## Read-only detection

Inspect affected view configuration, handler availability, relationships and entity/field definitions.

## Evidence required

Exact view/display IDs, missing handler diagnostics and intended entity semantics.

## Remediation options

Update the affected view using supported handlers and relationships; export only reviewed changes.

## Data or behavior implications

An apparently similar table may change entity, revision, language or access semantics.

## Verification

Browser results, filters, sorting, relationships, access and relevant displays match expectations.

## Recovery

Restore the reviewed view configuration and compatible module/data state.

## Do not apply

Never use global string replacement or disable unrelated Views to hide errors.
