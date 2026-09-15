# Drupal 11 Upgrade Toolkit — Agent Prompt Reference

Use this reference prompt when delegating autonomous Drupal 10-to-11 assessment and remediation tasks to AI coding assistants.

---

```text
You are assisting with a Drupal 10-to-11 upgrade using the Drupal 11 Upgrade Toolkit and Drupal Upgrade Workbench.
Target repository: /path/to/<project>
Toolkit root: /path/to/promet-drupal-workbench

Ground rules:
1. Never mutate the client repository directly during audits. Scans run in an isolated disposable MariaDB container.
2. The visual interface is the Drupal Upgrade Workbench:
   bin/d11 dashboard --port 8765
3. To run end-to-end upgrade rehearsals via CLI:
   bin/d11 init /path/to/<project> -y --url <url> --project <name>
   bin/d11 upgrade /path/to/<project> -y
4. Rollback safely at any time:
   bin/d11 workflow rollback --project <name> --run <run-id>
5. Decision safety invariant: Only assign 'keep' if an extension is 100% clean (0 deprecations, 0 PHPStan issues).
   All extensions with deprecations must be assigned 'compatible_release' or remediation.
6. Validate code quality, security, and standards:
   bin/d11 guardrails /path/to/<project>
7. Inspect the resulting evidence and reports under ~/.d11/runs/<project-id>/<run-id>/
   before proposing any Git handoff or human review.
```

---

## Configuration Reference

A minimal, valid `project.json` for the toolkit:

```json
{
  "schemaVersion": "1.0",
  "repository": ".",
  "environment": {
    "id": "my-project-local",
    "kind": "local",
    "authorized": true
  },
  "site": {
    "uri": "https://my-project.ddev.site"
  },
  "roots": {
    "composer": ".",
    "drupal": "web",
    "custom": [
      "web/modules/custom",
      "web/themes/custom"
    ],
    "config": "config/sync"
  },
  "runtime": {
    "wrapper": "ddev"
  },
  "identity": {
    "argv": ["ddev", "exec", "php", "-r", "echo \"ok\";"],
    "expected": "ok"
  },
  "deliveryBudget": {
    "targetHours": 20,
    "checkpointHours": 3
  }
}
```
