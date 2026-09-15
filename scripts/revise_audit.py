#!/usr/bin/env python3
"""Publish a separate audience revision without modifying historical evidence."""

import sys
from pathlib import Path

# Add project root and src to path for module resolution
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from d11.audit_report import build, document, main, validate

if __name__ == "__main__":
    sys.exit(main())
