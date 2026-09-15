"""Run registered patch verification checks in their configured managed directories."""

import argparse, json, sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
while str(_SCRIPTS) in sys.path:
    sys.path.remove(str(_SCRIPTS))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from d11lib.common import read
from d11lib.intake import safe_path
from d11lib.execution import run_check

p = argparse.ArgumentParser()
p.add_argument("config")
p.add_argument("ids", nargs="+")
a = p.parse_args()
cfg = read(a.config)
checks = {c["id"]: c for c in cfg["checks"]}
for cid in a.ids:
    c = checks[cid]
    r = run_check(c, safe_path(cfg["_root"], c["cwd"]))
    print(json.dumps(r))
    if r["status"] != "passed":
        raise SystemExit(2)
