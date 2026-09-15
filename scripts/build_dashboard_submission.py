"""Build an honest fixture demonstration packet; never manufacture real-site evidence."""

from pathlib import Path
import shutil
from d11lib.workflow import Workflow
from d11lib.common import read, write, file_hash, now

w = Workflow()
out = w.home / "submission"
out.mkdir(exist_ok=True)
runs = w.runs()
models = [
    read(w.home / "runs" / s["id"] / "evidence.json")
    for s in runs
    if (w.home / "runs" / s["id"] / "evidence.json").exists()
]
# Select consecutive same-project assessments with equal static cache keys automatically.
comparisons = []
for first, second in zip(
    sorted([s for s in runs if s["action"] == "assess"], key=lambda x: x["startedAt"]),
    sorted([s for s in runs if s["action"] == "assess"], key=lambda x: x["startedAt"])[1:],
):
    a = w.home / "runs" / first["id"] / "result/context.json"
    b = w.home / "runs" / second["id"] / "result/context.json"
    if first["project"] == second["project"] and a.is_file() and b.is_file():
        aa, bb = read(a), read(b)
        if aa.get("metrics", {}).get("cacheKey") == bb.get("metrics", {}).get("cacheKey"):
            comparisons.append(
                {
                    "first": first["id"],
                    "second": second["id"],
                    "firstMetrics": aa["metrics"],
                    "secondMetrics": bb["metrics"],
                    "equivalentChecks": read(a.parent / "result.json")["checks"]
                    == read(b.parent / "result.json")["checks"],
                }
            )
model = {
    "generatedAt": now(),
    "status": "BLOCKED_REAL_SITE_DEMONSTRATION",
    "built": [
        "Local FastAPI browser dashboard and shared CLI workflow service",
        "Reviewed snapshot intake with preserved hashes and two synthetic project profiles",
        "Structured local Codex proposals with registered check/step restrictions",
        "Exact batch approval, preparation gates, stop/reconciliation and guarded executor integration",
        "Effort ledger, run events, registered downloads, normalized Markdown and optional Word export",
    ],
    "blockers": [
        "No reviewed sanitized Prometweb code/database/files bundle has been supplied.",
        "Real runtime provisioning and isolation inspection have not been performed.",
    ],
    "remainingImplementation": [
        "Automated runtime provisioning and disposable Composer solver workspaces",
        "Evidence-triggered scanner-collision and breadcrumb recipes validated against real source",
        "Integrated real-site content/reference verification and complete scenario coverage",
        "One-click Word generation and release-readiness aggregation from named UAT/recovery evidence",
    ],
    "remainingValidation": [
        "Real-site AI proposal and approved remediation batch",
        "Full Drupal 10 to 11 run, business UAT and representative recovery rehearsal",
        "Second real run from the same sanitized starting snapshot",
        "Second operator walkthrough and recorded real-site demonstration",
    ],
    "measurements": comparisons,
    "runs": models,
    "humanEffort": "Unknown for implementation and unattended fixture runs; no operator ledger was supplied. Toolkit maintenance is separate from project delivery.",
    "submissionLimit": "This is a fixture demonstration and implementation status packet, not a completed contest submission or client upgrade approval.",
}
write(out / "submission.json", model)
lines = [
    "# Drupal upgrade dashboard implementation status",
    "",
    model["submissionLimit"],
    "",
    "Status: " + model["status"],
    "",
    "## What was built",
] + ["- " + x for x in model["built"]]
lines += [
    "",
    "## How another operator runs it",
    "",
    "Follow docs/dashboard.md. Run bin/d11 dashboard from the toolkit and open the localhost URL printed by the launcher. Choose demo-alpha or demo-bravo for a fixture assessment. A real copy requires reviewed intake and runtime isolation evidence.",
    "",
    "## Blockers",
] + ["- " + x for x in model["blockers"]]
lines += ["", "## Remaining implementation"] + ["- " + x for x in model["remainingImplementation"]]
lines += ["", "## Remaining validation"] + ["- " + x for x in model["remainingValidation"]]
lines += [
    "",
    "## Measured fixture runs",
    "",
    model["humanEffort"],
    "",
    "| Run | Project | Action | Outcome | Elapsed seconds |",
    "|---|---|---|---|---|",
]
for s in runs:
    lines.append(
        f"| {s['id']} | {s['project']} | {s['action']} | {s['status']} | {s.get('elapsedSeconds', 'unknown')} |"
    )
lines += [
    "",
    "Repeated static findings and cache metrics are in submission.json. These tiny fixture timings do not predict client upgrade duration.",
    "",
    "No client upgrade, production operation, staging, commit or push was performed. Source snapshots are preserved; a real-site source-before/after proof remains pending the real demonstration.",
]
(out / "README.md").write_text("\n".join(lines) + "\n")
shutil.copyfile("docs/dashboard.md", out / "runbook.md")
for name, src in [
    ("python-tests.log", "/tmp/d11-tests.log"),
    ("browser-tests.log", "/tmp/d11-js-tests.log"),
]:
    if Path(src).is_file():
        shutil.copyfile(src, out / name)
write(
    out / "hashes.json",
    {p.name: file_hash(p) for p in out.iterdir() if p.is_file() and p.name != "hashes.json"},
)
print(out / "README.md")
