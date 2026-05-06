# Changelog

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
