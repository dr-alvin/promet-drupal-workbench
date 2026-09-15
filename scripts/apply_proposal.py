#!/usr/bin/env python3
import argparse, hashlib, sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
while str(_SCRIPTS) in sys.path:
    sys.path.remove(str(_SCRIPTS))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from d11lib.common import read
from d11lib.intake import safe_path
from d11lib.proposals import apply_changes

p = argparse.ArgumentParser()
p.add_argument("mode", choices=["check", "apply", "verify"])
p.add_argument("root")
p.add_argument("proposal")
a = p.parse_args()
proposal = read(a.proposal)
if a.mode == "verify":
    for c in proposal["changes"]:
        assert safe_path(a.root, c["path"]).read_text() == c["after"], "Patch verification failed"
else:
    apply_changes(a.root, proposal, a.mode == "check")
    if a.mode == "apply":
        import subprocess
        root_path = Path(a.root).resolve()
        git_dirs = list(root_path.glob("web/**/.git")) + list(root_path.glob("vendor/**/.git"))
        for git_dir in git_dirs:
            if git_dir.parent == root_path:
                continue
            repo_dir = git_dir.parent
            try:
                subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_dir, capture_output=True)
                subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, capture_output=True)
            except Exception:
                pass
