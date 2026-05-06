#!/usr/bin/env bash
set -euo pipefail

# Hebrew + English mixed auto-detect wrapper.
# Uses Azure Continuous language ID mode — re-detects language per segment,
# not just at the start. Best for Hebrew/English code-switching mid-sentence.
# Loads .env (if present) and uses the repo-local .venv.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/run.sh" --backend azure --azure-speaker-labels --azure-languages "en-US,he-IL" "$@"
