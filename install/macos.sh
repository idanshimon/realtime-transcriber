#!/usr/bin/env bash
# RTT macOS installer — installs BlackHole, creates Multi-Output Device,
# sets it as default output, configures .env, and smoke-tests the audio chain.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_DIR/install/lib/common.sh"

AGGREGATE_NAME="${RTT_AGGREGATE_NAME:-RTT Multi-Output}"
AGGREGATE_UID="com.rtt.multi-output"

# ── Step 1: Python ─────────────────────────────────────────────────────────
step "1/6  Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 is required."
  if command -v brew >/dev/null 2>&1; then
    if ask_yn "Install Python via Homebrew?" Y; then
      brew install python@3.12
    else
      exit 1
    fi
  else
    say "Install Homebrew first: https://brew.sh"
    exit 1
  fi
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "Python $PYV"

# ── Step 2: Homebrew (needed for BlackHole + SwitchAudioSource) ────────────
step "2/6  Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  warn "Homebrew not found."
  if ask_yn "Install Homebrew now?" Y; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH for this session
    if [[ -d /opt/homebrew/bin ]]; then export PATH="/opt/homebrew/bin:$PATH"; fi
    if [[ -d /usr/local/bin ]]; then export PATH="/usr/local/bin:$PATH"; fi
  else
    err "Homebrew is required to install BlackHole on macOS."
    exit 1
  fi
fi
ok "Homebrew at $(command -v brew)"

# ── Step 3: BlackHole ──────────────────────────────────────────────────────
step "3/6  Checking BlackHole audio driver"
if system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole"; then
  ok "BlackHole already installed"
else
  warn "BlackHole not found."
  say "BlackHole is a free virtual audio driver that lets RTT capture system audio."
  if ask_yn "Install BlackHole 2ch via Homebrew?" Y; then
    info "Installing… (you may be prompted for your password)"
    brew install --cask blackhole-2ch
    ok "BlackHole installed"
    warn "macOS may require a reboot or logout for the audio driver to activate."
    if ! system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole"; then
      err "BlackHole isn't visible to CoreAudio yet. Please reboot and re-run ./install.sh"
      exit 1
    fi
  else
    err "Cannot continue without BlackHole."
    exit 1
  fi
fi

# ── Step 4: SwitchAudioSource (for default-output flipping) ────────────────
if ! command -v SwitchAudioSource >/dev/null 2>&1; then
  info "Installing SwitchAudioSource (for managing default audio device)…"
  brew install switchaudio-osx >/dev/null 2>&1 || warn "SwitchAudioSource install failed (non-fatal)"
fi

# ── Step 5: Multi-Output Device (the unblock) ──────────────────────────────
step "4/6  Multi-Output Device"
SWIFT_HELPER="$REPO_DIR/install/lib/create_multi_output.swift"
if ! command -v swift >/dev/null 2>&1; then
  err "Swift CLI not found. Install Xcode Command Line Tools: xcode-select --install"
  exit 1
fi

info "Creating/verifying \"$AGGREGATE_NAME\"…"
SWIFT_OUT=$(swift "$SWIFT_HELPER" --name "$AGGREGATE_NAME" 2>&1) || {
  err "Failed to create Multi-Output Device:"
  echo "$SWIFT_OUT" >&2
  exit 1
}
echo "$SWIFT_OUT" | sed 's/^/   /'
ok "Multi-Output Device ready (uid=$AGGREGATE_UID)"

# ── Step 6: Set Multi-Output as default output ─────────────────────────────
if command -v SwitchAudioSource >/dev/null 2>&1; then
  CURRENT_OUT=$(SwitchAudioSource -c -t output 2>/dev/null || echo "")
  if [[ "$CURRENT_OUT" != "$AGGREGATE_NAME" ]]; then
    if ask_yn "Set \"$AGGREGATE_NAME\" as your default audio output? (recommended)" Y; then
      SwitchAudioSource -t output -s "$AGGREGATE_NAME" >/dev/null 2>&1 \
        && ok "Default output set to \"$AGGREGATE_NAME\"" \
        || warn "Could not switch default output; do it manually in System Settings → Sound → Output"
    else
      warn "Skipped. Remember to switch output to \"$AGGREGATE_NAME\" before transcribing."
    fi
  else
    ok "Default output already \"$AGGREGATE_NAME\""
  fi
else
  warn "SwitchAudioSource unavailable — set \"$AGGREGATE_NAME\" as default output manually."
fi

# ── Step 7: Python venv + deps ─────────────────────────────────────────────
step "5/6  Python virtual environment & dependencies"
setup_venv "$REPO_DIR"

# ── Step 8: .env (audio + optional Azure) ──────────────────────────────────
step "6/6  Configuration & smoke test"
env_set "RTT_INPUT_DEVICE" "BlackHole"
env_set "RTT_INCLUDE_MIC" "1"
ok ".env: RTT_INPUT_DEVICE=BlackHole, RTT_INCLUDE_MIC=1 (mic mixed into transcript)"

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
if ask_yn "Run audio smoke test now? (plays a 2-second tone)" Y; then
  if run_smoke_test "$REPO_DIR" "BlackHole"; then
    :
  else
    warn "Smoke test failed — audio routing may need manual adjustment."
    say "Common fixes:"
    say "  • Open System Settings → Sound → Output → choose \"$AGGREGATE_NAME\""
    say "  • Make sure system volume is not muted"
    say "  • Run \`./install.sh\` again after rebooting if BlackHole was just installed"
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────
say ""
ok "${C_BOLD}RTT installed!${C_RESET}"
say ""
say "Try it out:"
say "  ${C_BOLD}./run.sh${C_RESET}                          # local Whisper, default device"
say "  ${C_BOLD}./run-azure.sh${C_RESET}                    # Azure Speech (configure .env first)"
say "  ${C_BOLD}./run.sh --input-file file.mp3${C_RESET}    # transcribe a recording"
say "  ${C_BOLD}./run.sh --list-devices${C_RESET}           # show all input devices"
say ""
say "Tip: add ${C_BOLD}alias rtt=\"$REPO_DIR/run.sh\"${C_RESET} to your shell profile."
say ""
