# Changelog

## 2026-07-04
- **Two new transcription backends** for the newer Azure speech models (chunked HTTP, `chunked_backends.py`):
  - **`--backend openai`** → Azure OpenAI `gpt-4o-transcribe-diarize`. Better WER, native diarization, ~1/3 the per-hour cost of classic Speech. New default for the `rtt` alias. Runs on a dedicated `rtt-transcribe-*` resource (eastus2).
  - **`--backend llmspeech`** → Azure Speech "LLM Speech" `enhancedMode`. LLM-enhanced quality, multilingual auto-detect (Hebrew/English code-switching), diarization. New default for the `rttheb` alias. Runs on the existing Speech resource.
- Both use a **micro-batch** design: audio is buffered into `--chunk-seconds` windows (default 15s) and POSTed per chunk. Trades true real-time latency for model quality. Includes a silence gate (skips quiet chunks) and per-chunk failure isolation (a dropped chunk never kills the run).
- Auth reuses the AAD `token_provider` discipline (fresh bearer token on demand, 30-min cache); API-key auth also supported.
- **Alias changes:** `rtt`→`run-openai.sh`, `rttheb`→`run-llmspeech.sh`. Classic Speech SDK backends preserved as **`rttold`** (`run-azure.sh`) and **`rtthebold`** (`run-azure-heb.sh`) for fallback + A/B baseline.
- New CLI options: `--chunk-seconds`, `--openai-endpoint`, `--openai-deployment`, `--llmspeech-endpoint`, `--llmspeech-locales`. New env vars: `RTT_OPENAI_ENDPOINT`, `RTT_LLMSPEECH_ENDPOINT`.
- Unit tests: `tests/test_chunked_backends.py` (20 tests, no network).
- API contract note: the OpenAI audio endpoint needs stable data-plane api-version `2024-10-21` (Speech-style `2025-10-15` 404s). Diarize returns letter labels (`Speaker A`), LLM Speech returns numeric (`Speaker 1`).

## 2026-05-06
- **Cross-platform installer** (`install.sh` / `install\windows.ps1`) — zero-friction onboarding:
  - macOS: auto-installs BlackHole via Homebrew, programmatically creates a `RTT Multi-Output` aggregate device (Swift + CoreAudio), sets it as default output, runs a tone-based audio smoke test.
  - Linux: detects PulseAudio/PipeWire monitor source.
  - Windows: configures WASAPI loopback (built-in, no drivers). *Beta — not yet validated on a live Windows machine.*
- **`--include-mic`** flag and `RTT_INCLUDE_MIC=1` env var — mixes the system microphone into the captured stream so the user's own voice appears in the transcript. Companion flags `--mic-device` and `--mic-gain`.
- **`--loopback`** flag and `RTT_USE_LOOPBACK=1` — enables WASAPI loopback capture on Windows.
- Added `MixedDeviceCapture` class: dual-stream capture (system + mic) with bounded ring buffers and real-time mixing.
- Sanitized public-repo references; added `.stubs/`, `.vfsmeta/`, `devices.txt`, `AGENTS.md` to `.gitignore`.

## 2026-03-12
- Fixed Azure AD token authentication: tokens are now formatted as `aad#<resource-id>#<token>` as required by the Speech SDK.
- Added `AZURE_SPEECH_RESOURCE_ID` env var and `--azure-resource-id` CLI flag (required for Azure AD auth).
- Updated `.env.example` with Azure AD auth instructions.
- Expanded Troubleshooting section with common 401 causes (stale keys, missing resource ID, RBAC roles).

## 2026-01-18
- Updated terminal hotkeys for clipboard workflow:
  - `Ctrl+S` copies full transcript so far and sets the delta baseline.
  - `Ctrl+E` copies transcript delta since the last `Ctrl+S`.
- Added `Ctrl+P` to pause/resume transcribing.
- Removed the mistaken VS Code-extension approach; hotkeys are handled in-terminal.
- Added `run.sh` and `run-azure.sh` helpers to simplify startup and auto-load `.env`.
