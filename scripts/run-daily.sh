#!/bin/sh
set -eu

REPOSITORY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$REPOSITORY_ROOT/.venv/bin/python"
PATH="$HOME/.local/bin:$PATH"
export PATH

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=$(command -v python3)
fi

cd "$REPOSITORY_ROOT"
exec "$PYTHON_BIN" -m jaguartv_postmatch.pipeline run --config "$REPOSITORY_ROOT/config/local.json"
