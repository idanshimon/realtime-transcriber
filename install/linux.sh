#!/usr/bin/env bash
# RTT Linux installer — uses PulseAudio/PipeWire monitor source for system audio capture.
# No drivers needed — leverages the OS-built-in monitor of the default output sink.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/install/lib/common.sh"

# ── Python ─────────────────────────────────────────────────────────────────
step "1/4  Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 is required (3.10+)."
  say "On Debian/Ubuntu:  sudo apt install -y python3 python3-venv python3-pip"
  say "On Fedora:         sudo dnf install -y python3 python3-virtualenv"
  say "On Arch:           sudo pacman -S python python-virtualenv"
  exit 1
fi
ok "Python $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

# ── PortAudio (sounddevice native dep) ────────────────────────────────────
step "2/4  Checking PortAudio"
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  warn "libportaudio not found."
  if command -v apt-get >/dev/null 2>&1; then
    say "Run: ${C_BOLD}sudo apt install -y libportaudio2 portaudio19-dev${C_RESET}"
  elif command -v dnf >/dev/null 2>&1; then
    say "Run: ${C_BOLD}sudo dnf install -y portaudio portaudio-devel${C_RESET}"
  elif command -v pacman >/dev/null 2>&1; then
    say "Run: ${C_BOLD}sudo pacman -S portaudio${C_RESET}"
  fi
  exit 1
fi
ok "PortAudio installed"

# ── Detect monitor source ─────────────────────────────────────────────────
step "3/4  Detecting system audio monitor (PulseAudio/PipeWire)"
MONITOR_SRC=""
if command -v pactl >/dev/null 2>&1; then
  DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || echo "")
  if [[ -n "$DEFAULT_SINK" ]]; then
    MONITOR_SRC="${DEFAULT_SINK}.monitor"
    ok "Monitor source: $MONITOR_SRC"
  else
    warn "Could not detect default sink. You'll need to set RTT_INPUT_DEVICE manually."
  fi
else
  warn "pactl not found (PulseAudio/PipeWire CLI). System audio capture may not work."
  warn "RTT will fall back to mic-only capture."
fi

# ── Python venv + deps ─────────────────────────────────────────────────────
step "4/4  Python virtual environment & dependencies"
setup_venv "$REPO_DIR"

# ── .env ──────────────────────────────────────────────────────────────────
if [[ -n "$MONITOR_SRC" ]]; then
  env_set "RTT_INPUT_DEVICE" "Monitor"
  ok ".env: RTT_INPUT_DEVICE=Monitor (matches $MONITOR_SRC)"
fi

if [[ ! -f "$REPO_DIR/.env" ]] || ! grep -q "AZURE_SPEECH_REGION" "$REPO_DIR/.env"; then
  cat <<EOF >> "$REPO_DIR/.env"
# Azure Speech (optional — only needed for --backend azure)
# AZURE_SPEECH_REGION=eastus
# AZURE_SPEECH_KEY=
# AZURE_SPEECH_RESOURCE_ID=
EOF
fi

# ── Smoke test ─────────────────────────────────────────────────────────────
say ""
if [[ -n "$MONITOR_SRC" ]] && ask_yn "Run audio smoke test now? (plays a 2-second tone)" Y; then
  run_smoke_test "$REPO_DIR" "Monitor" || warn "Smoke test failed — verify your default sink and try again."
fi

say ""
ok "${C_BOLD}RTT installed!${C_RESET}"
say ""
say "Try it out:"
say "  ${C_BOLD}./run.sh${C_RESET}"
say "  ${C_BOLD}./run.sh --list-devices${C_RESET}"
say ""
