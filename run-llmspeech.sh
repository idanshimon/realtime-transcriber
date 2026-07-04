#!/usr/bin/env bash
set -euo pipefail

# Default `rttheb` backend: Azure Speech "LLM Speech" enhancedMode (chunked).
# Multilingual auto-detect biased to Hebrew+English, LLM-enhanced quality,
# native diarization. Runs on the classic Speech resource via RTT_LLMSPEECH_ENDPOINT.
# For the classic continuous-LID Conversation Transcriber, use `rtthebold`
# (run-azure-heb.sh) — that stays the true-realtime Hebrew fallback.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/run.sh" --backend llmspeech --llmspeech-locales "en-US,he-IL" "$@"
