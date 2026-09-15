# QA and UAT Specification

## QA catalog

Create `04-qa-uat/qa-test-catalog.json` with deterministic scenario IDs. Each scenario records area, title, owner, criticality, environment, preconditions, steps, expected result, evidence path, automation type, and applicable custom/contrib extensions. Cover applicable runtime, routes, authentication, forms, Views, search, APIs, cron, queues, files/media, caching/CDN, mail, editor, workflows, translations, accessibility, responsive/visual behavior, performance, and third-party integrations.

Create `qa-test-results.json` with the exact release ID, scenario result (`passed|failed|blocked|skipped`), evidence, defect IDs, and execution timestamp. A failed critical scenario is Red. A blocked or skipped critical scenario is Unknown.

Use the toolkit visual runner or configured read-only project HTTP checks for authorized lower-environment routes, and `scripts/inherited/aggregate_phpunit_results.py` for existing JUnit XML. These checks do not authorize an environment; saving browser interactions require explicit disposable-fixture authorization.

## UAT

Create `uat-plan.md`, `uat-scenarios.csv`, `uat-results.json`, and `uat-approval.json`. The CSV columns are `id,title,business_owner,criticality,preconditions,steps,expected_result,evidence`.

An approved UAT record requires a named business approver, ISO-8601 approval timestamp, environment, immutable tested release ID, matching release-candidate ID, `buildMatchesReleaseCandidate: true`, non-empty accepted scenario IDs, explicit defect disposition, and an unresolved-defect list.

Allowed UAT status values are `not_started`, `in_progress`, `approved`, and `rejected`. Missing or in-progress UAT is Unknown. Rejected UAT or a release-ID mismatch is Red. Unresolved defects require severity, owner, rationale, target date, and explicit acceptance; release-blocking defects prohibit approval.
