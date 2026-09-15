#!/usr/bin/env python3
"""Thin executable wrapper for d11 CLI."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
if (ROOT / "src").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from d11.cli import main

if __name__ == "__main__":
    sys.exit(main())
