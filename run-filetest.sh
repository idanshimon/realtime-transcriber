#!/usr/bin/env bash
set -euo pipefail

# File-mode test wrapper for transcribe.py.
#
# WHY THIS EXISTS: `.env` sets capture vars (RTT_INPUT_DEVICE, RTT_INCLUDE_MIC,
# RTT_MIX_MIC, RTT_MIC_DEVICE, RTT_USE_LOOPBACK) for LIVE meeting capture. In
# file mode those collide with --input-file and trip transcribe.py's guard:
#   "Use either --input-file or --input-device, not both."
# The old workaround was to hand-edit/comment `.env` before a file test and
# restore it after — error-prone (easy to leave `.env` half-masked and break
# the next real capture, which is exactly what happened 2026-07-06). This
# wrapper sources `.env` for CREDENTIALS/ENDPOINTS but UNSETS the capture vars
# for this invocation only, leaving `.env` on disk untouched.
#
# USAGE:
#   ./run-filetest.sh --backend openai --input-file test.mp3 --max-seconds 25
#   ./run-filetest.sh --backend llmspeech --input-file test.mp3
#   ./run-filetest.sh --input-file test.mp3           # defaults to local whisper
#
# File-mode auto-exits at end-of-file (bound with --max-seconds), so no timeout
# wrapper is needed. `.env` is NEVER modified.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Strip every capture-related var so file mode can't collide with a device.
# (RTT_AAD_SUBSCRIPTION, RTT_OPENAI_ENDPOINT, RTT_LLMSPEECH_ENDPOINT, and all
#  AZURE_* creds are intentionally preserved — file mode still needs auth.)
unset RTT_INPUT_DEVICE RTT_INCLUDE_MIC RTT_MIX_MIC RTT_MIC_DEVICE RTT_USE_LOOPBACK RTT_MIC_GAIN

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

# Guard: require --input-file, since this wrapper is ONLY for file testing.
case " $* " in
  *" --input-file "*) : ;;
  *)
    echo "run-filetest.sh is for FILE testing — pass --input-file <path>." >&2
    echo "For live capture use: rtt / run-openai.sh / run.sh" >&2
    exit 2
    ;;
esac

exec "$PY" transcribe.py "$@"
