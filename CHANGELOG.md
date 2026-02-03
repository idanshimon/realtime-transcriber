# Changelog

## 2026-01-18
- Updated terminal hotkeys for clipboard workflow:
  - `Ctrl+S` copies full transcript so far and sets the delta baseline.
  - `Ctrl+E` copies transcript delta since the last `Ctrl+S`.
- Added `Ctrl+P` to pause/resume transcribing.
- Removed the mistaken VS Code-extension approach; hotkeys are handled in-terminal.
- Added `run.sh` and `run-azure.sh` helpers to simplify startup and auto-load `.env`.
