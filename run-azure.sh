#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper for your common Azure run command.
# Loads .env (if present) and uses the repo-local .venv.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/run.sh" --backend azure --azure-speaker-labels "$@"
