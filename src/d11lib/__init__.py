"""Backward-compatibility alias for d11lib -> d11."""

import sys
from pathlib import Path

_src = str(Path(__file__).resolve().parent.parent)
if _src in sys.path:
    sys.path.remove(_src)
sys.path.insert(0, _src)

import importlib

import d11

sys.modules["d11lib"] = d11


class _AliasFinder:
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("d11lib."):
            real_name = "d11." + fullname[len("d11lib.") :]
            real_mod = importlib.import_module(real_name)
            sys.modules[fullname] = real_mod
            return getattr(real_mod, "__spec__", None)
        return None


import pkgutil

if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

# Pre-alias all modules in d11 as d11lib.<name>
for _, modname, _ in pkgutil.iter_modules(d11.__path__):
    if modname in ("setup_worker", "workflow_worker"):
        continue
    try:
        mod = importlib.import_module(f"d11.{modname}")
        sys.modules[f"d11lib.{modname}"] = mod
        setattr(d11, modname, mod)
    except Exception:
        pass

from d11 import *


