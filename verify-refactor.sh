#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${D11_PYTHON:-$TOOL_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

BASELINE_DIR="$TOOL_ROOT/baselines/golden-run-pre-refactor"
TARGET_RUN="${1:-}"

if [[ -z "$TARGET_RUN" ]]; then
  # Check if a specific target run is passed or default to the baseline directory to verify normalizer integrity
  TARGET_RUN="$BASELINE_DIR"
fi

if [[ ! -d "$BASELINE_DIR" ]]; then
  echo "Error: Golden baseline directory not found at $BASELINE_DIR" >&2
  exit 1
fi

if [[ ! -d "$TARGET_RUN" ]]; then
  echo "Error: Target run directory not found at $TARGET_RUN" >&2
  exit 1
fi

echo "=== Verifying Refactor against Golden Baseline ==="
echo "  • Baseline: $BASELINE_DIR"
echo "  • Target:   $TARGET_RUN"

"$PYTHON" "$TOOL_ROOT/scripts/normalize_output.py" --diff "$BASELINE_DIR" "$TARGET_RUN"
