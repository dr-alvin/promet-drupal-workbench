# Approval and Documentation Gap Spec

Read this reference when the request asks for readiness documentation, a documentation gap analysis, an approval packet, go/no-go guidance, or approval-gated `prepare` or `execute` work.

## Documentation categories

Classify every gap under one category:

- `build-and-deploy`: build model, deployment wrappers, release promotion, platform hooks, and environment sequencing.
- `backup-and-rollback`: backup creation, restore ownership, rollback steps, roll-forward fallback, and file coverage.
- `environment-and-access`: environment identification, CLI access, repository access, secrets ownership, and operational permissions.
- `composer-and-runtime`: Composer workflow, lockfile policy, PHP/runtime constraints, services, patches, and platform requirements.
- `content-and-editorial-qa`: editorial regression scripts, workflow checks, moderation/access validation, representative content scenarios, and manual QA runbooks.
- `integrations-and-operations`: search, queues, cron, CDN, mail, APIs, third-party integrations, monitoring, and recovery ownership.

## Gap status

Use exactly one `gapStatus` per documentation finding:

- `missing`: required documentation or ownership is absent.
- `partial`: some guidance exists but it does not cover the required path.
- `stale`: the document exists but no longer matches the current project, platform, or upgrade path.
- `unverified`: the guidance exists but has not been confirmed against the current environment or release model.
- `not_applicable`: the category or finding does not apply to this project or mode.

## Risk and complexity

Keep release gates separate from the new scales:

- `riskLevel`: impact if the documentation gap stays unresolved.
- `complexity`: effort to close the documentation gap.

Use these defaults:

- `low` risk: inconvenience or follow-up work is likely, but safe execution and rollback remain intact.
- `medium` risk: execution, verification, or handoff may be delayed or error-prone without remediation.
- `high` risk: the gap can block safe execution, rollback, approval, or critical validation.

- `low` complexity: one owner can close the gap quickly with existing evidence.
- `medium` complexity: the gap needs cross-checking, multiple sources, or light rehearsal.
- `high` complexity: the gap needs environment access, rehearsal, coordination, or new operational discovery.

## Documentation gap register

Create `01-upgrade-control/documentation-gap-register.csv` with these columns:

- `id`
- `category`
- `summary`
- `affectedPhase`
- `gapStatus`
- `riskLevel`
- `complexity`
- `recommendation`
- `owner`
- `evidence`
- `releaseGateImpact`
- `closureCriteria`

Guidance:

- Use stable IDs such as `DG-001`.
- Keep `summary` short and operational.
- Use `releaseGateImpact` values `green|amber|red|unknown`.
- Use `owner` values like a role, team, or `unassigned` when no owner is known.
- Store artifact paths, ticket IDs, or redacted references in `evidence`.
- Use semicolon-separated values in `doc_gap_ids` columns when a work item points to multiple gaps.

Create `01-upgrade-control/documentation-gap-analysis.md` as the narrative summary. It must include:

- top blocking gaps
- unresolved medium/high-risk gaps
- unresolved medium/high-complexity gaps
- recommended sequencing
- ownership assumptions
- handoff impact

## Approval request packet

Create `01-upgrade-control/approval-request.md` as the human review document. Include:

- readiness summary
- current release gate
- unresolved blockers
- static-analysis summary
- configuration drift summary
- documentation-gap summary
- recommended approval scope
- requested commands or mutation gates
- rollback summary
- evidence links
- sign-off roles

Use this file to ask for approval. Do not treat it as the approval record itself.

When the user asks for a repository-ready, wiki-ready, or Markdown approval brief, use a strict Markdown information hierarchy with semantic headings. Prefer:

- `#` for the document title
- `##` for major decision sections
- `###` for named sub-sections such as affected custom modules or themes

Format module names, theme names, file names, and CLI commands as inline code.
Convert structured decision content into Markdown tables when appropriate, especially:

- project status and current recommendation
- risk summary
- decision options

When custom compatibility is part of the decision, include a named summary of the affected custom modules and themes, not only a generic risk statement. Source that summary from `01-upgrade-control/affected-custom-extensions.csv`.
When static-analysis or configuration-state findings are material, summarize them from:

- `02-static-analysis/phpstan-drupal-report.json`
- `02-static-analysis/drupal-rector-report.json`
- `01-upgrade-control/config-drift-report.json`

## Executive approval brief

When the audience is PM, leadership, procurement, or client stakeholders, also create:

- `01-upgrade-control/executive-summary.md`
- `Drupal-11-Upgrade-Approval-Brief.docx`
- `Drupal-11-Upgrade-Approval-Brief.md` when the requested output is repository-ready, Confluence-ready, engineering-wiki-ready, or explicitly Markdown

These files must stay aligned with the technical packet and use plain business language. Include:

- current recommendation
- current release gate
- top blockers and risks in plain language
- what can be approved now
- what should not be approved yet
- evidence still required to reduce risk
- rollback and recovery posture
- likely scope or schedule uncertainty if blockers remain open

For the Markdown brief:

- present the recommended decision as a Markdown blockquote
- present risk levels in tables with bold emphasis such as `**High**` and `**Medium**`
- keep numbered and bulleted lists precise for limitations, affected extensions, and next steps
- avoid conversational framing, filler, or unsupported conclusions

When custom modules or themes are materially affected, include the named high-risk set in business language so PM/client readers can see which site-specific features are most likely to need validation or remediation.
When static-analysis or configuration drift is a principal blocker, explain it in business language instead of raw tool output. Summarize the likely remediation implication rather than listing stack traces or internal-only details.

## Approval decision record

Create `01-upgrade-control/approval-decision.json` as the machine-readable gate:

```json
{
  "status": "pending",
  "requestedScopes": ["prepare"],
  "approvedScopes": [],
  "approvedBy": null,
  "approvedAt": null,
  "conditions": [],
  "relatedStepManifest": "01-upgrade-control/approved-step-manifest.json"
}
```

Allowed `status` values:

- `not_requested`
- `pending`
- `approved`
- `approved_with_conditions`
- `denied`
- `not_applicable`

## Approval scopes

Use these normalized scopes:

- `prepare`
- `execute:dependency_update`
- `execute:database_update`
- `execute:configuration_import`
- `execute:cache_rebuild`
- `execute:search_index_queue`
- `execute:lower_environment_deployment`

`prepare` or `execute` may proceed only when:

- `approval-decision.json` exists
- `status` is `approved` or `approved_with_conditions`
- the requested scope is present in `approvedScopes`
- `relatedStepManifest` matches `approved-step-manifest.json`
- no unresolved Red blocker remains
- required machine-readable evidence exists for any cited blocker areas, especially static analysis, compatibility matrix, config drift, and post-upgrade diff state

If a new undocumented step appears during execution, record a new or updated documentation gap, stop the run, and refresh approval before continuing.
