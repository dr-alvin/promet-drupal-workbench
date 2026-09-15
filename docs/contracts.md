# Contracts and execution boundaries

## Project configuration

`config/schemas/project.json` is the native schema (1.0). Paths in `roots` and step/check working directories are repository-relative. `repository`, `requirementsEvidence`, `baselineEvidence`, visual configuration/output and config comparison directories are relative to the project configuration file. CLI `--output` is relative to the caller. Commands are argv arrays; no shell expansion or `eval` is performed.

Wrapper commands and explicitly configured checks/scripts are trusted project code. Read them before authorizing runtime use. The toolkit cannot prove that a program named `drush` or an arbitrary project script is read-only. Built-in discovery invokes inspection commands only; checks must be inspection/tests and must not install tools or mutate site data. Optional verification fixture mutation belongs in the disposable visual scenario contract.

Environment identity is an explicit command whose output must match `identity.expected`. Choose a project/site-specific identity, not simply `hostname` or a generic “local” label. Environment configuration is an operator assertion, not a production-detection security boundary against a malicious operator.

`target` is an explicit Drupal 11 version range chosen for the project. `requirementsEvidence` points to reviewed JSON containing `target`, authoritative `sources`, `verifiedAt`, `readinessPassed`, `earlierDatabaseUpdatesComplete` and `removedCoreExtensionsReviewed`. Record actual runtime/tool requirements and findings alongside those fields. Do not mark these true without evidence.

`recovery` contains matching `code`, `database`, `files` references plus `owner`, `procedure` and `verifiedAt`. References identify externally stored backups; the toolkit does not copy or independently restore them. The reviewer must verify availability and scope before approving.

`baselineEvidence` points to JSON containing matching `environment` and `site`, `codeRevision`, installed-extension and active-configuration evidence references, `publicScenarios`, `authenticatedScenarios`, and `captureSettings` with its SHA-256 in `captureSettingsHash`. `captureSettings` is relative to that baseline JSON and must identify stable visual reference evidence. Store baselines outside source outputs so later verification does not replace them.

## Plan and approval

`plan.json` records sources, target, roots, identity, input hashes, exact argv/cwd steps, dependencies, proposed changes, recovery, requirements/baseline hashes and blockers. The approval file contains the exact `planId`, environment/site, approver/time, complete step list and `recoveryDigest`. This is a reviewed local approval record, not a cryptographic signature or remote authorization service.

Each mutating step needs observable preconditions and postconditions. Conditions have argv, optional timeout and optional exact `stdoutEquals`. Steps wrapping a project deployment script must set `importsConfig:true` when that script imports configuration. Direct drush deploy/cim/config:import commands are detected automatically. Such plans require `configComparison` snapshots, whose hashes and differences are bound to approval; destructive removals/UUID mismatches block unattended execution. Step IDs are unique and dependencies refer to earlier steps. Stages are ordered A-baseline through F-verification. `preparation: true` selects steps for `prepare`.

The executor is intentionally conservative: destructive removals, new dependency workarounds, shell entrypoints and Git mutations cannot be included in unattended execution. Handle consequential changes through a separate reviewed procedure, then establish a new plan. Do not embed secrets in argv; use the existing runtime's credential mechanism.

Source hashes include untracked/dirty files but exclude dependencies, file stores, environment secret files, caches and outputs. `composer.lock` and source configuration remain relevant inputs. Input hashing is not a backup, and it does not detect arbitrary remote database changes; environment probes and per-step preconditions must cover those.

A lock is keyed by canonical project root and environment ID. A started/failed mutation cannot be retried through `--resume`. A completed checkpoint can resume only when hashes and prerequisites still match. `prepare` and subsequent `execute --resume` share the same output directory.

## Results

Toolkit exit codes: 0 required configured checks pass; 1 findings/mismatches; 2 execution/tool failure; 3 blocked/missing authorization or evidence; 64 invalid usage. Underlying command statuses are recorded separately. Static discovery commonly returns 3 because runtime/compatibility evidence is unknown; its inventory and reports are still useful.

Use `d11 report --package` for an allowlisted archive of native reports; failed verification/execution also attempts packaging without replacing the original failure code.

Native results and inherited schema-3.0 lifecycle packets are distinct. The packet's six phase records include goals, owners, risks, estimate bands, dependencies, approval and entry/exit/stop conditions. Green evidence must be supplied explicitly. Packet validation blocks missing evidence, unapproved UAT and mismatched release IDs. A valid visual report is only one input to these gates.

`client-report.md` contains the audit decision and engineer-supplied estimate ranges; `developer-report.md` contains findings and recovery guidance; `qa-report.md` uses browser instructions. For the full inherited six-phase/client/developer/combined packet, run the inherited generator and validator with reviewed input as described in the skill.
