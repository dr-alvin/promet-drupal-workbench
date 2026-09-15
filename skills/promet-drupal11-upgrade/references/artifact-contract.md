# Artifact Contract 3.0

## Layout

```text
artifacts/drupal11/
├── assessment.json
├── phase-status.json
├── phase-report.md
├── client-summary.md
├── developer-report.md
├── combined-report.md
├── client-summary.docx
├── developer-report.docx
├── combined-upgrade-packet.docx
├── report-index.json
├── 00-baseline/{infrastructure-report.json,log-review.json,database-health.json}
├── 01-upgrade-control/{approval-decision.json,approved-step-manifest.json,compatibility-matrix.json,affected-custom-extensions.csv,patch-register.json,backup-and-rollback-plan.md,risk-acceptances.json,executive-summary.md,release-recommendation.md}
├── 01-upgrade-control/{context.json,module-readiness.csv,composer-resolution.json,deployment-sequence.json}
├── 02-static-analysis/{phpstan-drupal-report.json,drupal-rector-report.json}
├── 03-content-audit/post-upgrade-content-diff.json
├── 04-qa-uat/{qa-test-catalog.json,qa-test-results.json,smoke-test-report.json,phpunit-report.json,uat-plan.md,uat-scenarios.csv,uat-results.json,uat-approval.json}
└── 05-release/{deployment-rehearsal.json,rollback-rehearsal.json,monitoring-plan.md,final-signoff.json}
```

All JSON objects use `schemaVersion: "3.0"`. Arrays have deterministic ordering. Evidence paths are relative and must resolve inside the packet. Empty strings and placeholders (`none`, `n/a`, `tbd`, `todo`, `unknown`) are invalid in required fields.

The four discovery artifacts are derived, optional additions to the schema-3.0 packet. When present, `context.json` records discovered wrapper and project roots, `module-readiness.csv` records package/module disposition, `composer-resolution.json` records dependency and patch evidence, and `deployment-sequence.json` records detected command ordering and module-removal safety. They must not contain secrets or unrestricted Production URLs. Their evidence remains advisory until reviewed into packet findings.

`assessment.json` requires `run`, `releaseGate`, `counts`, `approval`, `releaseCandidateId`, `artifacts`, and `findings`. Counts contain `green`, `amber`, `red`, and `unknown` and match finding statuses. Every artifact entry contains `path` and SHA-256.

Findings and phases may include audience fields: `clientSummary`, `technicalSummary`, `businessImpact`, `requiredDecision`, `recommendedAction`, `ownerRole`, `estimateBand`, `dependencyIds`, `acceptanceCriteria`, `evidenceQuality`, `clientPriority`, and `technicalPriority`. When present, validation requires both audience explanations. Reports must be generated from the packet and must not introduce independent findings.

`report-index.json` records each report path, audience, source release candidate ID, source assessment SHA-256, generation timestamp, report SHA-256, status, and gate. Markdown and DOCX reports must share the same gate, finding counts, release candidate ID, and source assessment hash. Report generation may omit DOCX only when the document runtime is unavailable; it must still produce Markdown and JSON.

`phase-status.json` contains exactly six ordered phase objects using `lifecycle-workflow.md`. `phase-report.md` is generator-owned and derived from those objects.

The approval decision contains status, requested/approved scopes, approver/timestamp, conditions, and related manifest. The manifest contains environment, release ID, approver, backup evidence, rollback plan, and allowlisted argv-array steps. It may target only `local|dev|multidev|test`. Shell metacharacters and Production execution steps are invalid.

Amber risk acceptances contain finding ID, approver, rationale, acceptedAt, reviewOrExpiryAt, owner, and evidence. Executive and release Markdown must report the same gate and finding counts as `assessment.json`. Run `validate_upgrade_artifacts.py` for normative checks.
