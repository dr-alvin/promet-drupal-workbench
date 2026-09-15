# Validation record — 2026-09-08

## Actually executed

- 29 Python fixture/lifecycle checks passed. Coverage: discovery layouts, nested Composer/docroot, repository-first wrapper selection with multiple installed wrappers, multisite URI requirement, absent runtime evidence, package/multiple-module mapping, .ci observations, configuration deletion/removal/UUID risks, dirty/stale inputs, matching approvals, missing backups/baselines, process timeout, failed mutations stopping dependents, invalid resume, locks, and unchanged Git index/history.
- Inherited lifecycle tests: explicit evidence required for green phases, unknown evidence preserved, packet/report gate consistency, wrong-release UAT blocked, and existing packet preservation.
- Schema-3.0 synthetic packet generation, three Markdown audience reports and validator: passed with zero warnings. No actual client approval was generated.
- Skill creator quick validator: passed for `skills/promet-drupal11-upgrade`.
- Four container Node tests: scenario validation/selection/authorization and sitemap namespace/index/gzip/deduplication/query/asset/empty/DTD/traversal cases.
- Fourteen browser integration checks: unchanged reference/test; deliberate color mismatch; login redirect; HTTP 500; broken image; authenticated editor dialog baseline/test and cleanup; missing required element/incomplete capture; corrupted baseline rejection. All produced the expected success/failure result. Existing baseline replacement was rejected and failed tests created archives without losing their exit code.
- Browser runner executed from `/tmp`, verifying path anchoring. Role fixture credentials were forwarded by Compose; explicit reference/test URL arguments overrode intentionally invalid configured URLs. Container output used host UID/GID 501:20 and a writable temporary browser profile. Sandboxed Chromium worked as that non-root user; root launch was separately observed to fail as expected.

The reproducible fixture suite and local evidence are in `tests/` and ignored `artifacts/integration/summary.json`. Fixture screenshots and logs contain only synthetic local pages.

## Tested environment and pins

- macOS ARM64 host; Python 3.9.6; Docker Desktop 4.37.2 / Engine 27.4.0; Compose 2.31.0.
- BackstopJS 6.3.25 image index digest: `sha256:020d8f17eaa1cd3f2165f3caa6b3f9c56c5311c2cdedc4897b8cf0484b07040f`.
- Inspected manifest contains Linux ARM64 and AMD64 images; actual execution used ARM64 only.
- Actual bundled versions: Node 20.17.0, Playwright 1.47.0, Chromium 129.0.6668.29. These are reproducibility pins, not claims that they are the newest browser releases.
- Image entrypoint is `backstop`. Helper invocations explicitly use `node`. Local Dockerfile applies the source-checked TLS verification correction documented in visual-testing.md.
- `fast-xml-parser` 5.11.1 and transitive helper dependencies are locked. `npm ci` audited these helper dependencies with zero reported vulnerabilities at installation. This is not a security audit of every dependency in the base image.

## Not yet integration-validated

- An actual Drupal 10-to-11 upgrade, real Composer resolution/install, Rector/PHPStan/Upgrade Status execution, database hooks, config import, or real Drupal CMS selectors.
- Actual DDEV, Docksal, Lando, Acquia, Pantheon or other hosting/runtime adapters. Wrapper command composition is implemented; fixture coverage does not prove provider integration.
- Linux host mount ownership/routing, AMD64 browser capture, Windows/WSL, or native Windows.
- Local TLS exception behavior against a live HTTPS fixture, shared project Docker-network routing, or actual private-file storage protection. Strict TLS is enforced in the patched engine; platform-specific routing/certificate validation still needs testing.
- Project-specific accessibility, external integrations, content migrations, UAT, production deployment or recovery rehearsal. These require configured checks and separately authorized environments.
- DOCX render/visual validation; Markdown and JSON reports are the tested formats.

## Operational limits

Static PHP settings expressions and arbitrary deployment scripts cannot be safely evaluated during discovery. Configure ambiguous paths and review ordering. Composer constraints are retained without pretending a substring match is compatibility; collect actual solver/scanner evidence. Backups, approval identities, estimates and supplied readiness evidence are reviewable operator assertions, not independently attested facts.

The executor runs reviewed project commands and records their outcomes; it cannot prove that arbitrary trusted scripts respect their declared scope. Scenario reviewers must mark saving interactions as mutations. Interrupted/failed mutation runs require manual reconciliation and a new plan. Production execution and automatic Git staging/commit/push are excluded.

## Delivery revision validation

The final implementation passed 39 Python tests, all container helper/image tests (Node reports 11 including the containing test), and 18 browser workflow checks against a synthetic local site. Added coverage includes budget defaults and checkpoint review, larger and remaining-work forecasts, unknown actuals, Docksal PHP argv, `token` identifier preservation and credential redaction, verified empty update status, malformed output, scanner results versus execution failure, static cache invalidation and runtime refresh, the two-probe concurrency cap, container build fingerprints/reuse, hidden responsive HTTP-failed images, lazy loading, explicit hidden-image interactions, failed login and critical-then-full coverage. Existing lifecycle/approval/backup/UAT gates remain tested.

The final discovery fixture measured 0.177019s fresh and 0.164134s cached with equivalent static findings. Both performed 11 subprocesses because mutable runtime and Git evidence were refreshed. The two execution failures in this synthetic fixture are expected Git probes against an unversioned temporary directory. These small fixture timings establish instrumentation and correctness, not a promised client speedup. Machine timings and logs are retained in `artifacts/benchmarks/discovery.json`, `artifacts/integration/summary.json` and the integration logs.

Prometweb delivery revision 02 has four Markdown and four DOCX audience reports generated from one normalized model. All eight report hashes validate. All nine rendered Word pages were inspected. The original assessment findings, RED gate and historical report hashes are unchanged. Project phase forecasts sum to the carried-forward 38–76h remaining-work allowance, distinct from the 20h target. Human actuals remain unreported. No client runtime scans, upgrades, production changes, staging, commits or pushes were performed for this toolkit implementation.

## Dashboard implementation validation

The dashboard extension passed 59 Python tests and 11 container-based Node/Chromium tests. The browser interface was opened and visually inspected, including project selection, common configuration fields, run state, artifacts, approvals and effort controls. The fixture Word summary was rendered and its single page inspected. Tests cover reviewed intake hashes, component/path symlinks, unknown artifacts, local host and same-origin action checks, malformed API requests, concurrent starts, interrupted-worker reconciliation, stale proposals/approvals/batch artifacts, missing Codex, preparation evidence without clean target scans, preparation/core mode separation, changed supporting evidence, explicit effort timers and unattended interval arithmetic.

A real local Codex invocation against synthetic source returned structured JSON without tool calls. It declined changes because the fixture had no registered verification checks; the workflow correctly recorded a blocker. An initial restricted-process attempt could not initialize Codex state and remains diagnostic evidence. Real client-source proposals and remediation have not been validated.

Repeated demo-bravo static assessments reused the same cache key and produced equivalent checks. Full run timings, command timings and reused evidence are retained under artifacts/workbench/runs and summarized in artifacts/workbench/submission. These are fixture measurements, not a measured real Drupal upgrade. The submission status explicitly lists missing snapshot/provisioning, remaining toolkit automation, real QA/recovery and second-operator validation. No real-site completion is claimed.
