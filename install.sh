#!/usr/bin/env bash
# RTT Universal Installer — auto-detects OS and runs the right setup wizard.
# Usage:  ./install.sh
#         curl -fsSL https://raw.githubusercontent.com/idanshimon/realtime-transcriber/master/install.sh | bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# ── Pretty output helpers ──────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_BLUE=$'\033[0;34m'; C_YELLOW=$'\033[0;33m'
  C_RED=$'\033[0;31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_BLUE=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_RESET=""
fi
say()  { printf "%s\n" "$*"; }
ok()   { printf "${C_GREEN}✅${C_RESET} %s\n" "$*"; }
info() { printf "${C_BLUE}→${C_RESET}  %s\n" "$*"; }
warn() { printf "${C_YELLOW}⚠${C_RESET}  %s\n" "$*"; }
err()  { printf "${C_RED}✖${C_RESET}  %s\n" "$*" >&2; }
step() { printf "\n${C_BOLD}▸ %s${C_RESET}\n" "$*"; }

banner() {
cat <<'EOF'

  ╭──────────────────────────────────────────╮
  │   RTT — Real-Time Transcriber Installer  │
  ╰──────────────────────────────────────────╯

EOF
}

banner

# ── Detect OS ──────────────────────────────────────────────────────────────
OS_KIND=""
case "$(uname -s)" in
  Darwin)  OS_KIND="macos" ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      OS_KIND="wsl"
    else
      OS_KIND="linux"
    fi
    ;;
  CYGWIN*|MINGW*|MSYS*) OS_KIND="windows" ;;
  *) OS_KIND="unknown" ;;
esac

info "Detected OS: ${C_BOLD}${OS_KIND}${C_RESET}"

# ── Dispatch ────────────────────────────────────────────────────────────────
case "$OS_KIND" in
  macos)
    exec bash "$REPO_DIR/install/macos.sh" "$@"
    ;;
  linux)
    exec bash "$REPO_DIR/install/linux.sh" "$@"
    ;;
  wsl)
    err "WSL does not support live system audio capture."
    say ""
    say "You have two options:"
    say "  1. Use the ${C_BOLD}Teams browser extension${C_RESET} (zero audio config)"
    say "       → load \`teams-extension/\` as an unpacked Chrome extension"
    say "  2. Use ${C_BOLD}--input-file${C_RESET} mode for post-meeting transcription"
    say "       → ./run.sh --input-file recording.mp3"
    say ""
    exit 1
    ;;
  windows)
    err "Run the PowerShell installer instead:"
    say ""
    say "    ${C_BOLD}.\\install\\windows.ps1${C_RESET}"
    say ""
    exit 1
    ;;
  *)
    err "Unsupported OS. Open an issue with: uname -a"
    exit 1
    ;;
esac
