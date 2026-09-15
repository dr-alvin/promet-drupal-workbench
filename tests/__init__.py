import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_src = str(_ROOT / "src")
_tests = str(_ROOT / "tests")

if _src in sys.path:
    sys.path.remove(_src)
sys.path.insert(0, _src)

if _tests not in sys.path:
    sys.path.insert(0, _tests)
