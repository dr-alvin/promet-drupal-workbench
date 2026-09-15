"""Creates synthetic intake bundles only; contains no client data."""

import argparse
from d11lib.workflow import Workflow
from d11lib.intake import inventory, CONTROL_KEYS
from d11lib.common import write, now

p = argparse.ArgumentParser()
p.add_argument("--id", default="demo-alpha")
a = p.parse_args()
w = Workflow()
from d11lib.intake import safe_path

bundle = safe_path(w.home / "inbox", a.id)
if bundle.exists():
    raise SystemExit("Fixture already exists; choose a new ID")
for name in ("code", "database", "files"):
    (bundle / name).mkdir(parents=True)
write(
    bundle / "code/composer.json",
    {"name": "fixture/" + a.id, "require": {"drupal/core-recommended": "^10.3"}},
)
(bundle / "code/web/modules/custom").mkdir(parents=True)
(bundle / "database/fixture.sql").write_text(
    "-- Synthetic placeholder; not an importable Drupal database.\n"
)
(bundle / "files/fixture.txt").write_text("Synthetic fixture asset.\n")
write(
    bundle / "manifest.json",
    {
        "fixture": True,
        "reviewer": "synthetic-fixture-generator",
        "reviewedAt": now(),
        "controls": {k: True for k in CONTROL_KEYS},
        "hashes": {k: inventory(bundle / k) for k in ("code", "database", "files")},
    },
)
cfg = {
    "schemaVersion": "1.0",
    "repository": "site",
    "environment": {"id": "d11-" + a.id, "kind": "local", "authorized": True},
    "site": {"uri": "https://d11-" + a.id + ".docksal.site:4433"},
    "roots": {"composer": ".", "drupal": "web", "custom": ["web/modules/custom"]},
    "runtime": {"wrapper": "fin"},
    "steps": [],
    "checks": [],
    "estimates": [],
}
write(bundle / "project.json", cfg)
print(w.register(a.id, a.id, cfg))
