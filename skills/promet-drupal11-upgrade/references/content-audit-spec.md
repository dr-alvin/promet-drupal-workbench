# Content Model and Editorial Integrity Audit

This is a read-only integrity audit for an in-place Drupal upgrade. Content remains in the existing database. Do not treat this as content migration and do not mutate entities during assessment or verification.

## Required finding metadata

Every finding includes:

```json
{
  "baselineState": "existing|new|unknown",
  "introducedByUpgrade": false,
  "postUpgradeChanged": false,
  "classification": "expected_upgrade_change|pre_existing_issue|introduced_regression|approved_configuration_change|unknown",
  "severity": "green|amber|red|unknown",
  "evidence": []
}
```

## Content model inventory

For each node bundle, inventory machine name/label, descriptions, revision and translation support, workflow, fields, storage types, cardinality, required state, translatability, defaults, allowed values, entity-reference targets/bundles/handlers, form modes, view modes, widget IDs/settings, formatter IDs/settings, configuration dependencies, third-party settings, Layout Builder, Paragraphs, Field Group, Media, Scheduler, Metatag, Site Studio, and detected custom form alters/widgets/formatters/validation/submit handlers.

Flag missing field types, widgets, formatters, selection handlers, validation constraints, and providers as Red when the affected form/display cannot operate.

## Content state baseline

Record aggregate counts per applicable bundle:

- Total, published, unpublished
- Moderation states
- Pending non-default revisions
- Translations by language and entities with translations
- Missing required-field values
- Broken entity references
- Missing files/media
- Missing expected URL aliases
- Scheduled publication/unpublication where supported

For moderated content, track separately:

- Publication status of the default revision
- Moderation state of the latest revision
- Presence of a newer pending revision

Also capture machine-readable baseline markers for post-upgrade comparison:

- configuration UUID
- entity revision identifiers for representative records
- field storage signatures for critical/custom fields

Use these markers to support `scripts/inherited/compare_content_audit.py` checks for configuration UUID preservation, entity revision drift, and field storage drift.

Do not store titles, body text, field values, personal information, file contents, credentials, or secrets.

## Field usage

Produce `field-usage.csv` with bundle, field, type, required state, applicable entity count, populated count, empty count, population percentage, widget provider, formatter provider, associated module, compatibility finding, and QA risk.

Use it to identify critical, unused, legacy, custom, module-dependent, and complex fields.

## Editorial regression plan

Generate scenarios, without executing mutations, for applicable bundles/form modes:

- Add/edit form loads
- Required validation/default values/conditional fields
- AJAX widgets
- Multi-value add/remove/reorder
- Entity-reference autocomplete
- Media selection
- Paragraph add/edit/reorder/remove
- CKEditor 5 and embedded media
- Preview and draft save
- Publish/unpublish
- Revisions and revision logs
- Moderation transitions
- Translation
- Custom validation and submit handlers

Record missing editorial QA runbooks, workflow test scripts, or permission-validation checklists in `01-upgrade-control/documentation-gap-register.csv` with `riskLevel`, `complexity`, and `recommendation`. Do not classify those gaps as content defects unless an actual content or access failure is observed.

## Workflow and access

Inventory workflows, states, published-state configuration, transitions, bundles, roles, default state, revision requirements, scheduler/publishing integrations, and custom subscribers/hooks. Generate a role-transition matrix for representative roles: Anonymous, Authenticated, Content author, Editor, Publisher, and Site administrator.

Flag states without outgoing transitions, transitions no role can execute, unexpected publish permissions, missing workflow assignments, incorrect published-state mappings, and custom transition logic without coverage.

Test the access contract across canonical routes, Views, search/indexes, JSON:API, REST, GraphQL, sitemaps, RSS/feeds, menus, autocomplete, related blocks, CDN/reverse proxy, and headless frontends. An anonymously accessible unpublished entity/API result is Red.

## Views and sampling

Audit bundle/status/moderation filters, access controls, exposed forms, sorting, pagination, contextual filters, relationships, handlers, custom plugins, exports, administrative views, removed fields, removed modules, and entity-access bypasses.

Rank bundles by fields, custom widgets/formatters, references, media/files, Paragraphs/nesting, Layout Builder/Site Studio, revisions, moderation, translations, form alters, scheduling, integrations, business criticality, and record count. Select representative IDs/structural characteristics only: oldest/newest, published/unpublished, each moderation state, pending revision, translated, media-heavy, deeply nested, highly populated, and records with missing optional values or public references.

## Semantic comparison

Compare content-type/field/display/workflow/permission inventories and aggregate counts for bundles, publication, moderation, translations, pending revisions, references, files, aliases, and relevant Views/API results. Do not compare only raw table rows. Classify differences as expected upgrade change, pre-existing issue, introduced regression, approved configuration change, or unknown.

Require explicit drift review for:

- configuration UUID preservation
- entity revision drift
- field storage definition drift

Treat unexpected UUID changes as a configuration-state risk. Treat unexpected entity revision or field storage drift as at least Amber until the upgrade team proves the change is expected and safe.

## Release gates

- Red: fatal form, missing required widget/formatter, content loss, unreconciled semantic counts, unexpected publication access change, failed critical workflow, corrupted references, inaccessible files/media, or critical bundle unusable.
- Amber: incomplete coverage, manual custom widget, unchanged pre-existing issue, visual regression dependency, or unavailable representative production data.
- Unknown: unavailable database, roles, files, environment, integration, or required evidence.
- Green: semantic state reconciles, forms/workflows/access pass, and no Red finding exists.
