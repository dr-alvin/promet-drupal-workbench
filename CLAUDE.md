# CLAUDE.md — Developer & AI Agent Guidelines

This file provides architecture context, development invariants, and operational commands for developers and AI agents (Claude Code, Cursor, Antigravity, Codex) working on or interacting with the **Drupal 11 Upgrade Toolkit**.

---

## 1. Repository Architecture

```
d11-upgrade-tools/
├── bin/
│   └── d11                    # Shell wrapper; auto-provisions .venv and launches CLI/dashboard
├── src/d11/                   # Core Python engine
│   ├── audit_tools.py         # Disposable MariaDB container for Upgrade Status & Rector analysis
│   ├── auto_decide.py         # Automated compatibility decision engine
│   ├── auto_remediate.py      # Refactoring proposal generator for custom code & info.yml
│   ├── common.py              # Subprocess execution (argv-only), sys.path injection, hashing
│   ├── compatibility.py       # Decision validation, Drupal.org candidate matching, risk rules
│   ├── dashboard.py           # FastAPI web backend for local dashboard
│   ├── discovery.py           # Runtime & project metadata detection (Docksal, DDEV, Lando)
│   ├── execution.py           # Step-by-step upgrade execution, mutation plan & verification
│   ├── scenario_setup.py      # Network resolution and scenario setup
│   ├── two_gate.py            # Gate 1 & Gate 2 orchestration, risk scoring, rollback triggers
│   └── workflow.py            # High-level service facade and worker process launcher
├── web/dashboard/             # Drupal Upgrade Workbench UI (Single-Page Application)
│   ├── index.html             # Workbench SPA layout (Apple-inspired design system)
│   ├── app.js                 # Pipeline orchestration, stage management, API client
│   ├── compatibility-view.js  # Remediation dropdowns, filtering, draft normalization
│   └── style.css              # Responsive dark/light theme stylesheet
├── tests/                     # Pytest suite
└── requirements.txt           # Python dependencies
```

---

## 2. Core Invariants & Safety Rules

### Invariant 1: Non-Destructive Pre-Upgrade Scans
- Scans must **never** modify the user's live database or working tree.
- Drush `upgrade_status` and `drupal_rector` execute inside an isolated MariaDB container using database caching (`_configure_analysis_settings`).
- All mutation steps are gated on explicit operator approval (Gate 1).

### Invariant 2: Compatibility Decision Logic (`compatibility.py`, `auto_decide.py`)
- An extension may **only** be marked with `"action": "keep"` if:
  `row.status === "ready" and issueCount == 0 and fixableCount == 0`
- Any extension with deprecations or code findings must be assigned:
  - `"action": "compatible_release"` (with candidate version and `acceptRisk: True`), or
  - `"action": "available_patch"`, or
  - `"action": "manual_remediation"` / `"ai_manual_patch"`.
- Assigning `keep` to a module with deprecations triggers hard blockers (`Keep is allowed only when completed evidence establishes Drupal 11 compatibility` and `Code findings require remediation before Keep`).

### Invariant 3: Argv-Only Subprocess Execution (`common.py`)
- Every subprocess invocation must be passed as an explicit list of strings (`list[str]`).
- Never concatenate uncontrolled strings into shell commands or use `shell=True`.

### Invariant 4: Centralized & Non-Invasive Workbench
- The **Drupal Upgrade Workbench** is a centralized, standalone application.
- **Never embed, copy, or synchronize the toolkit into client Drupal repositories.** Target projects are audited, upgraded, and verified externally through their filesystem path and local URL, keeping client repositories completely clean.

### Invariant 5: Smart Static Analysis (Skip Contrib PHPStan, Focus on Custom & Remediations)
- Contrib extensions follow upstream Drupal community packaging standards, and their Drupal 11 compatibility is established directly via Composer solver and Drupal.org release feeds.
- Deep static analysis (PHPStan / Upgrade Status and Drupal Rector) strictly targets:
  1. **Custom code** (`web/modules/custom`, `web/themes/custom`) where proprietary deprecations live.
  2. **Remediations & Patches** (`ai_manual_patch`, `manual_remediation`, Drupal.org MR patches) where code modifications are introduced.
- Contrib modules are bypassed in PHPStan using `--ignore-contrib` by default, cutting audit times from 14+ minutes down to ~1 minute without sacrificing safety.

---

## 3. Standard Development & Verification Commands

### One-Time Environment Setup
```bash
cd /path/to/promet-drupal-workbench
./bin/d11 setup      # Auto-creates .venv and installs requirements
./bin/d11 doctor     # Verifies Docker, Git, Python environment health
```

### Run Unit Tests
```bash
cd /path/to/promet-drupal-workbench
./.venv/bin/pytest tests -q
# or
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests
```

### Start Dashboard Server
```bash
./bin/d11 dashboard --port 8765 --no-open
```

### Run Full Upgrade Rehearsal (CLI)
```bash
./bin/d11 upgrade /path/to/drupal-project -y
```

### Trigger Rollback (CLI)
```bash
PROJECT="my-drupal-project"
RUN_ID=$(./bin/d11 workflow runs --project "$PROJECT" | jq -r '.[0].id')
./bin/d11 workflow rollback --project "$PROJECT" --run "$RUN_ID"
```

---

## 4. Key API Endpoints (`dashboard.py`)

- `GET /api/projects`: List registered Drupal projects.
- `POST /api/setup/quick-add`: Register a new project and trigger audit scan.
- `GET /api/runs/{rid}/compatibility`: Get live compatibility report and evidence digest.
- `POST /api/runs/{rid}/compatibility-decisions`: Save manual or bulk remediation choices.
- `POST /api/runs/{rid}/one-click-upgrade`: Auto-resolve decisions, approve Gate 1, and launch rehearsal.
- `POST /api/runs/{rid}/rollback`: Restore pre-upgrade database backup and Git branch.

---

## 5. Troubleshooting Reference

- **Lock contention**: Delete `~/.d11/workflow.lock` if an interrupted process left a stale lock.
- **58 unresolved decisions**: Caused if the UI attempts to set `action: "keep"` on non-clean modules. Use `isCleanExtension` in frontend JS and ensure decisions are aligned to `compatible_release`.
- **Missing Python modules**: `common.py` automatically injects the toolkit `.venv` site-packages into `sys.path`.
