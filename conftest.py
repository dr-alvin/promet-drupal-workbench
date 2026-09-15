import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for sub in ("src", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import importlib
import importlib.abc
import importlib.machinery


class AliasLoader(importlib.abc.Loader):
    def __init__(self, target_module):
        self.target = target_module

    def create_module(self, spec):
        return self.target

    def exec_module(self, module):
        pass


class D11LibAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "d11lib" or fullname.startswith("d11lib."):
            real_name = "d11" + fullname[len("d11lib") :]
            mod = importlib.import_module(real_name)
            return importlib.machinery.ModuleSpec(fullname, AliasLoader(mod))
        return None


sys.meta_path.insert(0, D11LibAliasFinder())

try:
    import d11

    sys.modules["d11lib"] = d11
except ImportError:
    pass
