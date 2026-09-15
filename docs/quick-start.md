# Drupal 11 Upgrade Toolkit — Quick Start Guide

Welcome to the **Drupal 11 Upgrade Toolkit**. This guide is designed to get any developer up and running with automated, deterministic Drupal 10 → Drupal 11 upgrade rehearsals in **5 minutes**.

---

## 1. Prerequisites

Before running the toolkit, ensure you have:
1. A Drupal 10 project managed with Composer.
2. A running local development environment:
   - **Docksal**: `fin up`
   - **DDEV**: `ddev start`
   - **Lando**: `lando start`
   - **Docker Compose**: `docker compose up -d`
3. Your local site is reachable in a browser (e.g., `http://my-site.docksal.site`).
4. Docker Desktop is running.

Check your environment prerequisites anytime with:
```bash
bin/d11 doctor
```

---

## 2. Option A: The Drupal Upgrade Workbench (Recommended UI)

The **Drupal Upgrade Workbench** is the main, centralized single-page visual application for managing upgrades across all your Drupal sites.

> [!TIP]
> **Zero Repository Pollution**: You do **not** need to install, commit, or copy any upgrade tools into your Drupal projects. The Drupal Upgrade Workbench runs centrally from its own directory and connects to any number of Drupal projects via their local directory paths.

### Step 1: Start the Dashboard
```bash
cd /path/to/promet-drupal-workbench
./bin/d11 dashboard --port 8765
```
Open **[http://localhost:8765](http://localhost:8765)** in your browser.

### Step 2: Add Your Project
1. Click **+ Add Project** in the upper-right corner.
2. Provide:
   - **Project Name**: e.g., `my-drupal-site`
   - **Source Directory**: Absolute path to your local Drupal repository, e.g., `/path/to/my-drupal-site`
   - **Site URL**: Local dev URL, e.g., `http://my-drupal-site.docksal.site`
3. Click **Add project → Scan**.

### Step 3: Review the Gate 1 Audit
- The toolkit runs Upgrade Status and Drupal Rector inside an isolated, disposable MariaDB container (`db:3306`). **Your live database and source code are never touched during the audit.**
- The **Gate 1 Upgrade Proposal** displays compatibility statuses for all contrib and custom extensions:
  - **`compatible_release`**: Drupal.org has a D11-compatible release.
  - **`keep`**: 100% clean extensions (0 deprecations, 0 PHPStan issues).
  - **`remove`**: Obsolete modules superseded by Drupal 11 core (e.g., `advagg`, `advagg_mod`).
  - **`manual_remediation`**: Rector-generated refactorings for custom code.

### Step 4: 1-Click Upgrade Rehearsal
1. Click **Start 1-Click Upgrade →**.
2. Review the planned actions in the confirmation modal and click **Confirm & Start Upgrade →**.
3. The engine autonomously:
   - Dumps a pre-upgrade database backup (`pre-upgrade.sql.gz`) and records a zero-copy Git checkpoint.
   - Uninstalls obsolete performance modules.
   - Applies custom-code refactorings.
   - Runs `composer update` to upgrade core to Drupal 11.4.x and Drush to 13.x.
   - Runs `drush updatedb --yes` and `drush cache:rebuild`.
   - Captures Gate 2 visual regression screenshots across baseline routes.

### Step 5: Verify or Roll Back
- Inspect the visual diffs and site status at **Gate 2**.
- **To Roll Back**: Click **Rollback to Pre-Upgrade State**. The site is instantly restored to its exact Drupal 10 database and Git state.

---

## 3. Option B: CLI Automation Runner

For headless CI/CD pipelines or developers who prefer the terminal:

### Step 1: Initialize Project Configuration
```bash
cd /path/to/your-drupal-project

<WORKBENCH_PATH>/bin/d11 init . -y \
  --url http://my-site.docksal.site \
  --project my-drupal-site
```

### Step 2: Run End-to-End Upgrade Rehearsal
```bash
<WORKBENCH_PATH>/bin/d11 upgrade . -y
```
To test without mutating files or running Composer:
```bash
<WORKBENCH_PATH>/bin/d11 upgrade . --dry-run
```

### Step 3: Roll Back
```bash
# Get the run ID of the rehearsal
RUN_ID=$(<WORKBENCH_PATH>/bin/d11 workflow runs --project my-drupal-site | jq -r '.[0].id')

# Execute rollback
<WORKBENCH_PATH>/bin/d11 workflow rollback --project my-drupal-site --run "$RUN_ID"
```

### Step 4: Git Handoff (When Ready)
After completing a verified rehearsal and deciding to commit:
```bash
<WORKBENCH_PATH>/bin/d11 handoff /path/to/your-drupal-project \
  ~/.d11/runs/my-drupal-site/<run-id> \
  --branch upgrade/drupal-11
```

---

## 4. Understanding Compatibility Decision Rules

The toolkit enforces strict mathematical safety rules for extension compatibility:

| Action | When it is applied | Safety Invariant |
| :--- | :--- | :--- |
| **`compatible_release`** | An extension has a published Drupal 11 compatible release on Drupal.org. | Upgrades extension via Composer. |
| **`keep`** | An extension is already installed and has **0 deprecations and 0 code findings**. | **Invariant**: Cannot be applied if any deprecations exist. Attempting to keep a deprecated extension triggers hard blockers. |
| **`remove`** | Modules obsolete in D11 core (e.g. `advagg`, `quickedit`) or uninstalled contrib. | Automatically uninstalled cleanly prior to Composer upgrade. |
| **`manual_remediation` / `ai_manual_patch`** | Custom modules or themes needing code adjustments. | Applied as deterministic diffs with SHA-256 verification. |

---

## 5. Troubleshooting & FAQ

### 1. "Only one workflow may run at a time"
If a previous command was forcefully stopped (e.g. Ctrl+C), clear the stale lock:
```bash
rm -f ~/.d11/workflow.lock
```

### 2. "58 extension compatibility decisions remain unresolved"
This error occurs if the browser has cached older decision logic that attempted to mark deprecated modules with `keep`.
- **Solution**: Hard-refresh your browser (**`Cmd + Shift + R`** on macOS or **`Ctrl + F5`** on Linux/Windows).
- The auto-decide engine will automatically classify compatible versions as `compatible_release` instead of `keep`.

### 3. "Source runtime is stopped or ambiguous"
The toolkit requires exactly one running container matching your project root.
- Run `fin ps`, `ddev list`, or `docker ps` to verify running containers.
- Stop any other projects that share container names or directories.
- Restart your local environment: `fin up` or `ddev start`.

### 4. "Port 8765 is already in use"
Find and stop the existing dashboard process:
```bash
lsof -i :8765
kill <PID>

# Restart dashboard
./bin/d11 dashboard --port 8765 --no-open
```
