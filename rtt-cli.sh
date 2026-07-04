#!/usr/bin/env bash
set -euo pipefail

# rtt-cli — interactive menu / wizard / chat front-end for RTT.
# Loads .env (endpoints + device defaults) then hands off to rtt_cli.py, which
# reads config_schema.py for every option. Passes all args through, so:
#   rtt-cli               # main menu
#   rtt-cli --wizard      # wizard
#   rtt-cli --chat        # chat mode
#   rtt-cli --profile rttheb --launch
#   rtt-cli --print-argv --profile rtt

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
  if command -v python3 >/dev/null 2>&1; then PY="python3"; else PY="python"; fi
fi

exec "$PY" rtt_cli.py "$@"
