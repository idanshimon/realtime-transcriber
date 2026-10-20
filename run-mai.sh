#!/usr/bin/env bash
set -euo pipefail

# `mai` backend: Azure Speech MAI-Transcribe-2 (chunked batch) with speaker
# diarization. LLM-enhanced transcription on the SAME Speech resource as the
# llmspeech backend (rttspeech1763582903), reached via the fast-transcription
# REST API with definition enhancedMode{model:"MAI-Transcribe-2"} +
# diarization{enabled:true} (no maxSpeakers — MAI rejects it).
#
# This is an EVALUATION alias — it does NOT replace the default `rtt`
# (run-openai.sh). The parent repoints aliases only after live verification.
# For the classic Conversation Transcriber, use `rttold` (run-azure.sh).

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/run.sh" --backend mai "$@"
