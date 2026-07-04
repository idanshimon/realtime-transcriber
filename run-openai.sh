#!/usr/bin/env bash
set -euo pipefail

# Default `rtt` backend: Azure OpenAI gpt-4o-transcribe-diarize (chunked).
# English-first, native speaker diarization, ~1/3 the cost of classic Speech.
# Runs on the DEDICATED resource rtt-transcribe-* (eastus2) via RTT_OPENAI_ENDPOINT.
# For the classic Conversation Transcriber, use `rttold` (run-azure.sh).

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/run.sh" --backend openai "$@"
