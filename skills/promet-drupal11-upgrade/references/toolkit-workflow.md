# Toolkit command and evidence workflow

Toolkit root is `../../..` relative to this reference directory. Resolve it from the repository package.

## Guided dashboard

1. Run `bin/d11 dashboard` and open the localhost URL printed by the launcher.
2. Add the local project root and URL. Add a sitemap URL or explicit routes only when needed.
3. Inspect the detected DDEV or Docksal source. **Prepare isolated copy** authorizes the read-only local database export and creates separate toolkit-managed code, database, files, credentials and runtime identity.
4. Complete the named privacy and isolation review for the copied content.
5. **Run audit** refreshes runtime evidence, runs compatibility tools, selects routes, captures the baseline, resolves dependencies, records patch candidates and emits `gate.json`.
6. Review the browser reports and exact Gate 1 package. Resolve No-Go blockers. Conditional Go requires listed risk acceptance.
7. **Approve exact batch and upgrade** records the bound approval and starts the guarded workflow. The executor recomputes all approval and batch hashes, creates the coordinated recovery checkpoint, and applies only the reviewed operations.
8. At Gate 2, review before/after and functional evidence. Accept the automated result, keep the copy for diagnosis or restore the recorded checkpoint.

The dashboard exposes six report views plus Markdown, DOCX and browser printing. Automated passing status applies only to configured scenarios. Named business UAT, representative recovery/deployment rehearsal and production approval remain separate.

## Equivalent CLI

```bash
# 1. Intake & prepare managed copy
bin/d11 workflow setup-create --source /absolute/project --source-url https://project.ddev.site --route /
bin/d11 workflow setup-inspect --project PROJECT_ID
bin/d11 workflow setup-start --project PROJECT_ID --export-local

# 2. Pre-flight check for uninstalled conflicting packages
bin/d11 workflow inspect-obsolete --project PROJECT_ID

# 3. Run guided audit & baseline capture
bin/d11 workflow audit --project PROJECT_ID
bin/d11 workflow runs

# 4. Auto-remediate custom code and resolve all extension compatibility decisions
bin/d11 workflow auto-decide --run AUDIT_RUN_ID
```

After reviewing the exact completed audit and Gate 1 metrics (`approvalEligible: True`):

```bash
# 5. Approve exact batch and run upgrade
bin/d11 workflow approve-upgrade --run AUDIT_RUN_ID --reviewer "Reviewer" --privacy-reviewed --accept-risks --approval-digest EXACT_DIGEST
bin/d11 workflow rollback --run UPGRADE_RUN_ID
```

Add `--accept-risks` only for a reviewed Conditional Go. The `auto-decide` command resolves all contrib and custom extension decisions automatically from Composer solver results, merge-request patches, and validated custom remediation proposals. Low-level `discover`, `assess`, `plan`, `prepare`, `execute`, `verify`, `auto-remediate` and `visual-audit` commands remain available for diagnostics and automation compatibility. Their evidence and approval contracts are unchanged.

No operation creates Git metadata, stages, commits, pushes or targets Production. A failed or partial run remains blocked until its recorded state is reconciled.
