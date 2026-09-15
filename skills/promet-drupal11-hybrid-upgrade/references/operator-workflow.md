# Operator workflow

## 1-Command CLI Upgrade (Recommended)

From the toolkit root, run:

```sh
bin/d11 upgrade /absolute/path/to/drupal
```

This single command:
1. Autodetects DDEV/Docksal runtime and site URL.
2. Runs pre-flight check for uninstalled packages conflicting with Drupal 11.
3. Prepares an isolated, Git-free copy, captures baseline routes and screenshots, and scans compatibility.
4. Auto-remediates custom code and auto-decides contrib package releases/patches.
5. Displays the upgrade plan summary card and prompts for confirmation.
6. Executes Composer upgrade, runs database updates, rebuilds cache, and validates Gate 2 routes.

*Options:*
- `--dry-run`: Generate plan without modifying the copy.
- `-y`: Run non-interactively.
- `--dashboard`: Launch the dashboard after completion.

## Dashboard (1-Click Experience)

From the toolkit root, run `bin/d11 dashboard`. Open the localhost URL printed by the launcher.

1. **Add Project**: Enter the project root (site URL and wrapper are auto-detected). Click **Add project → Scan**.
2. **Auto-Resolve**: In **Report and choose actions**, click **"✨ Auto-Resolve Everything"** to auto-remediate custom code and select compatible packages in 1 click.
3. **Proceed**: Review the clean summary card, confirm managed-copy safety, and click **"Proceed with managed upgrade"**.
4. **Verify**: Review the post-upgrade browser comparison, accept the automated result, or roll back if needed.

## Step-by-Step CLI (Low-Level)

```sh
# 1. Register project
bin/d11 workflow setup-create --source /absolute/project --source-url https://project.ddev.site --route /

# 2. Check for uninstalled conflicting packages
bin/d11 workflow inspect-obsolete --project PROJECT_ID

# 3. Execute guided audit and baseline capture
bin/d11 workflow scan --project PROJECT_ID

# 4. Automatically remediate custom code and resolve all compatibility decisions
bin/d11 workflow auto-decide --run RUN_ID

# 5. Review Gate 1 state and approve
bin/d11 workflow approve-upgrade \
  --run RUN_ID \
  --reviewer "Your Name" \
  --privacy-reviewed \
  --accept-risks \
  --approval-digest APPROVAL_DIGEST
```

The `auto-decide` command synthesizes compatible releases from the Composer solver, selects approved merge-request patches, and generates hash-pinned remediation proposals for custom code, instantly clearing Gate 1 blockers. After reviewing `compatibility-decisions.json`, `gate.json`, and `plan.json`, approve with the exact digest through `bin/d11 workflow approve-upgrade`. The approval writes `approved-batch.json`, which is the provider-neutral contract for the exact extension actions and ordered steps. Use `bin/d11 workflow rollback --run UPGRADE_RUN_ID` for a preserved candidate.

Stop the dashboard with Ctrl+C. Use its **Request stop** action for an active workflow. Never use `docker compose down -v` on a managed project because it deletes its isolated database volume.
