# Promet Drupal Workbench — CLI Commands & AI Prompts Guide

This comprehensive reference catalog provides all command-line subcommands, workflow actions, zero-copy safety rules, and copy-paste-ready AI agent prompts for developers using the **Promet Drupal Workbench** (`d11-upgrade-tools`).

---

## 1. Quick Start & CLI Subcommands

All commands can be run directly via `./bin/d11` from the toolkit root or symlinked into your `$PATH`.

### Dashboard & Verification
| Command | Description | Safety Level |
| :--- | :--- | :--- |
| `./bin/d11 dashboard --port 8765 --no-open` | Start the Promet Drupal Workbench local web GUI on port 8765. | 🟢 Safe (Read-only UI) |
| `./bin/d11 doctor` | Verify local development prerequisites (Docker, Composer, PHP, Drush). | 🟢 Safe (Read-only) |
| `./bin/d11 providers` | List and test connectivity for all configured AI providers (Gemini, Claude, OpenAI, Ollama, Codex). | 🟢 Safe (Read-only) |

---

### Project Registration & End-to-End Upgrade Rehearsal
| Command | Description | Details & Flags |
| :--- | :--- | :--- |
| `./bin/d11 init <PATH> -y --url <URL> --project <NAME>` | Register a Drupal 10 project and generate project configuration. | `-y`: Non-interactive<br>`--url`: Local dev URL (e.g. `http://my-site.docksal.site`)<br>`--project`: Unique project ID |
| `./bin/d11 audit <PATH>` | Run non-destructive compatibility audit and page baseline capture. | Runs in disposable MariaDB container. Live database and source code are **untouched**. |
| `./bin/d11 upgrade <PATH> -y` | Execute autonomous end-to-end Drupal 11 upgrade rehearsal. | Takes `pre-upgrade.sql.gz` and Git snapshot, uninstalls obsolete modules, runs Composer upgrade, executes schema updates, and runs visual regression test. |

---

### Granular Workflow Commands (`./bin/d11 workflow ...`)

Use the `workflow` runner to trigger specific stages of the Two-Gate pipeline:

```bash
# 1. Trigger non-destructive audit scan
./bin/d11 workflow scan --project <PROJECT_NAME>

# 2. Auto-resolve extension decisions (aligns 100% clean extensions to keep, resolves upstream D11 versions)
./bin/d11 workflow auto-decide --run <RUN_ID>

# 3. Generate automated Drupal Rector deprecation fixes for custom modules & themes
./bin/d11 workflow auto-remediate --run <RUN_ID>

# 4. Gate 1 pre-upgrade safety approval (validates 0 blockers and digest confirmation)
./bin/d11 workflow approve-upgrade --run <RUN_ID> --accept-risks

# 5. List all workflow runs for a project
./bin/d11 workflow runs --project <PROJECT_NAME>

# 6. Post-Upgrade Extension Transition Report (JSON format)
./bin/d11 workflow modules --project <PROJECT_NAME>

# 7. Export Post-Upgrade Extension Transition Matrix to CSV file
./bin/d11 workflow modules --project <PROJECT_NAME> --csv > d10_d11_modules_report.csv

# 8. Instantaneous 1-Click Rollback (restores pre-upgrade.sql.gz and resets Git working tree)
./bin/d11 workflow rollback --project <PROJECT_NAME> --run <RUN_ID>
```

---

### Environment & Troubleshooting
```bash
# Release workflow execution lock if a previous process was interrupted
rm -f ~/.d11/workflow.lock

# Check running Docker containers for Docksal / DDEV / Lando
fin ps       # Docksal
ddev list    # DDEV
lando list   # Lando
docker ps    # Docker Compose

# Restart Dashboard process on port 8765
lsof -ti :8765 | xargs kill -9
./bin/d11 dashboard --port 8765 --no-open &
```

---

## 2. Sample AI Prompts for Developers & AI Agents

These copy-paste-ready prompts are tailored for pairing with AI assistants (such as **Google Antigravity `agy`**, **Claude Code**, **Cursor**, or **Codex**).

### Prompt 1: Full Autonomous Upgrade Rehearsal (Hands-Free)
```text
You are pair programming with me on conducting an automated Drupal 10 to Drupal 11 upgrade rehearsal.
We are using the Promet Drupal Workbench located at <WORKBENCH_PATH> (e.g. /path/to/promet-drupal-workbench).

Please execute the following:
1. Verify the project runtime and doctor checks:
   <WORKBENCH_PATH>/bin/d11 doctor
2. Register this project if not already present:
   <WORKBENCH_PATH>/bin/d11 init . -y --url <INSERT_LOCAL_URL> --project <INSERT_PROJECT_NAME>
3. Run the end-to-end autonomous upgrade rehearsal:
   <WORKBENCH_PATH>/bin/d11 upgrade . -y
4. When finished, summarize:
   - Contrib extensions upgraded vs obsolete modules uninstalled
   - Custom code remediations applied
   - Schema update and cache rebuild results
   - Visual regression diff results across captured routes
```

---

### Prompt 2: Non-Destructive Audit & Compatibility Triage
```text
Perform a non-destructive Drupal 11 readiness audit on this project using the Promet Drupal Workbench (<WORKBENCH_PATH>).

Execute:
<WORKBENCH_PATH>/bin/d11 audit .

Important constraints:
- Do not modify any codebase files or touch the host database. All audit scans must run inside the disposable MariaDB container.
- Review the Gate 1 proposal:
  1. Identify any obsolete modules that will be removed (e.g. advagg).
  2. Identify custom code in modules/custom and themes/custom with deprecations.
  3. Verify that no hard blockers exist before pre-upgrade approval.
Present the findings in a structured table.
```

---

### Prompt 3: Custom Code & Theme Deprecation Remediation (Rector + AI)
```text
We need to remediate custom Drupal 10 deprecations for Drupal 11 compatibility using the Promet Drupal Workbench.

Target: web/modules/custom and web/themes/custom
1. Run automated Rector remediation:
   <WORKBENCH_PATH>/bin/d11 workflow auto-remediate --run <RUN_ID>
2. Inspect the generated diffs for:
   - Deprecated \Drupal::service() calls
   - Removed core procedural functions (e.g. file_create_url -> \Drupal::service('file_url_generator'))
   - Twig 3 syntax and Hook attribute updates
3. Verify that all custom extensions now pass PHPStan level 2 with 0 errors.
```

---

### Prompt 4: Post-Upgrade Extension Transition Report & Client Sign-Off
```text
Generate the post-upgrade module transition report comparing Drupal 10 pre-upgrade versions against Drupal 11 post-upgrade status for project <PROJECT_NAME>.

Run:
<WORKBENCH_PATH>/bin/d11 workflow modules --project <PROJECT_NAME> --csv > d10_d11_module_report.csv

Please analyze the CSV and provide an executive summary highlighting:
1. Removed / Uninstalled: Obsolete modules cleanly removed (e.g. advagg).
2. Updated / Upgraded: Contrib modules updated to D11 releases with versions.
3. Patched / Remediated: Custom modules fixed with Rector or patches.
4. Remained Same: Clean extensions kept on their existing version.
5. Uninstalled Packages: Packages on disk not enabled in the database.
```

---

### Prompt 5: Visual Regression Review & Route Verification
```text
Review the Gate 2 Visual Regression findings for project <PROJECT_NAME>.
Using the Promet Drupal Workbench dashboard at http://localhost:8765/#regression:
1. Compare the Baseline (Drupal 10 reference capture) against Post-Upgrade (Drupal 11 live verification).
2. Check captured routes (Homepage, News, Contact, Admin) for visual drift, missing assets, CSS aggregation issues, or HTTP status variances.
3. If visual variance is detected, determine if it is expected content change or an actual rendering defect.
```

---

### Prompt 6: Instant 1-Click Rollback to Clean D10 State
```text
Please rollback the current Drupal 11 upgrade rehearsal to restore the site to its exact pre-upgrade Drupal 10 state.

Using the Promet Drupal Workbench:
1. Locate the latest run ID:
   <WORKBENCH_PATH>/bin/d11 workflow runs --project <PROJECT_NAME>
2. Execute the rollback:
   <WORKBENCH_PATH>/bin/d11 workflow rollback --project <PROJECT_NAME> --run <RUN_ID>
3. Verify:
   - Database restored from pre-upgrade.sql.gz
   - Git working tree reset to pre-upgrade commit (git status clean)
   - Composer dependencies restored
   - Local site responds with HTTP 200
```

---

### Prompt 7: Skill & Agent Orchestration (`drupal-11-upgrade-readiness`)
```text
Activate the `drupal-11-upgrade-readiness` skill and review AGENTS.md rules.
Coordinate with the Promet Drupal Workbench to orchestrate an upgrade rehearsal for this repository.
Follow the strict two-gate lifecycle:
- Gate 1: Non-destructive scan in disposable container, 0 unresolved extensions, SHA-256 evidence validation.
- Gate 2: Atomic recovery checkpoint, mutation, Drush updatedb, and visual diff verification.
Never embed upgrade tools or vendor scripts into the client project repository. Keep all mutations isolated to the dedicated zero-copy upgrade branch.
```

---

## 3. Two-Gate Architecture & Safety Invariants

| Safety Invariant | Rule & Implementation |
| :--- | :--- |
| **Centralized & Non-Invasive** | The Workbench operates externally. Never copy or commit upgrade tooling files into client Drupal project repositories. |
| **Disposable Container Scans** | Drush, Upgrade Status, and Drupal Rector execute inside an isolated MariaDB container (`db:3306`). Live client databases are **never** modified during audit. |
| **Atomic Recovery Checkpoint** | Prior to modifying a single line of code or running `composer update`, the engine dumps `pre-upgrade.sql.gz` and records the pre-upgrade Git HEAD commit. |
| **Smart Contrib Analysis** | Contrib extensions follow upstream Drupal.org packaging. Contrib PHPStan is skipped (`--ignore-contrib`) to speed up scans from 15 minutes to ~1 minute, focusing deep static analysis on **custom code**. |
| **Strict Keep Invariant** | An extension is strictly allowed `action: "keep"` **only** if it is 100% clean (`ready`, 0 deprecations, 0 PHPStan issues). Any extension with code findings must use `compatible_release` or remediation. |
| **1-Click Clean Rollback** | Rollback restores `pre-upgrade.sql.gz`, resets Git working tree to pre-upgrade HEAD, re-installs D10 vendor packages, and validates site health with HTTP 200. |
