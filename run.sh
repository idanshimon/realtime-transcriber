#!/usr/bin/env bash
set -euo pipefail

# Run transcribe.py using the repo-local virtualenv if present.
# Also loads environment variables from .env (if the file exists).

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PY="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    echo "No python executable found. Create a venv with: python3 -m venv .venv" >&2
    exit 1
  fi
fi

exec "$PY" transcribe.py "$@"
