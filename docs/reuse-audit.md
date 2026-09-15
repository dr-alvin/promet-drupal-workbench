# Reference-skill reuse audit

Source: the user-provided `drupal-11-upgrade-readiness` / `drupal-11-upgrade-lifecycle` skill, inspected on 2026-09-08. No global files were modified. This repository is self-contained and does not read that personal path at runtime.

## Inherited

The local skill retains the six-phase audit → dependencies → custom code → core/configuration → QA/UAT → rehearsal/handoff process, estimate bands, client approval scopes, red/unknown/amber/green gates, release-bound UAT and matching code/database/files recovery. Relevant references are copied locally.

The packet generator, audience report generator, validator, content comparator and JUnit aggregator are vendored under `scripts/inherited/`. Their original content hashes and local modifications are recorded in `reference-provenance.json`. Native execution data uses schema 1.0; reviewed lifecycle packets preserve schema 3.0. The full packet is an explicit review step, not an automatic promotion of discovery evidence to approval.

## Replaced discovery behavior

| Reference behavior | Toolkit behavior |
|---|---|
| First wrapper on PATH wins | Repository markers or explicit configuration select wrapper; ambiguity stays unknown |
| First recursive composer.json/lock independently | Drupal Composer candidate selection; lock paired with Composer root; ambiguous candidates block |
| Drupal root assumes web | Explicit roots, scaffold metadata, nested docroot and detectable layout |
| Hardcoded core.extension.yml locations | Explicit/inferred config sync and separate selected-site active evidence |
| Package name equals module name | Extension info inventory plus Composer installed-path mapping; multiple modules/package |
| Regex guesses Drupal 11 compatibility | Preserve constraints and dependency graph; actual Composer/scanner evidence required |
| Operations sorted by a predefined list | Source-line observations including .ci; conditional ordering requires review |
| Missing evidence can appear disabled/green | Installed and compatibility remain null/unknown without evidence |

## Local packet fixes

- The original generator could fabricate passing infrastructure, static-analysis, content and smoke results from a green phase status. Local defaults are unknown/not-run, and green phases require explicit evidence.
- The original generator rejected `unknown` as placeholder text even in status fields. Status enums now explicitly allow unknown while required prose still rejects placeholders.
- Gate calculation considers both phases and findings and refuses contradictory supplied gates.
- The original client summary always said execution could not begin even for green input. Local text follows the recorded gate without granting Production authorization.
- Historical target PHP/Drush/database assumptions in the lifecycle reference are replaced with selected-release verification.

No patch is installed globally. The repository-local corrected copies and provenance are the reviewable changes; global synchronization, if desired later, is a separate task.

## Deliberate limits

The inherited tools generate documentation and validate contracts; they cannot prove that a claimed backup or manually supplied UAT approval is genuine. Native execution additionally binds exact steps, input hashes and runtime identity. Reviewers remain responsible for project configuration, trusted scripts, estimate basis, backup validity and business authorization.
