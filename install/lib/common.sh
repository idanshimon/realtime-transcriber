# Shared helpers for RTT installers. Source from install/<os>.sh.
# shellcheck shell=bash

# Pretty output (works with or without TTY)
if [[ -t 1 ]]; then
  C_GREEN=$'\033[0;32m'; C_BLUE=$'\033[0;34m'; C_YELLOW=$'\033[0;33m'
  C_RED=$'\033[0;31m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_GREEN=""; C_BLUE=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_DIM=""; C_RESET=""
fi

say()  { printf "%s\n" "$*"; }
ok()   { printf "${C_GREEN}✅${C_RESET} %s\n" "$*"; }
info() { printf "${C_BLUE}→${C_RESET}  %s\n" "$*"; }
warn() { printf "${C_YELLOW}⚠${C_RESET}  %s\n" "$*"; }
err()  { printf "${C_RED}✖${C_RESET}  %s\n" "$*" >&2; }
step() { printf "\n${C_BOLD}▸ %s${C_RESET}\n" "$*"; }
dim()  { printf "${C_DIM}%s${C_RESET}\n" "$*"; }

# Yes/No prompt with default. Usage: ask_yn "Question?" Y  → returns 0 for yes
ask_yn() {
  local q="$1" def="${2:-N}" reply prompt def_upper reply_upper
  def_upper=$(printf '%s' "$def" | tr '[:lower:]' '[:upper:]')
  if [[ "$def_upper" == "Y" ]]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
  printf "${C_BOLD}?${C_RESET} %s %s " "$q" "$prompt"
  read -r reply || reply=""
  reply="${reply:-$def}"
  reply_upper=$(printf '%s' "$reply" | tr '[:lower:]' '[:upper:]')
  [[ "$reply_upper" == "Y" || "$reply_upper" == "YES" ]]
}

# Persist a KEY=VALUE line into .env (replaces if exists)
env_set() {
  local key="$1" val="$2" file="${3:-$REPO_DIR/.env}"
  touch "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    # macOS sed needs '' after -i; GNU sed does not. Try both.
    if sed --version >/dev/null 2>&1; then
      sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
      sed -i '' "s|^${key}=.*|${key}=${val}|" "$file"
    fi
  else
    printf "%s=%s\n" "$key" "$val" >> "$file"
  fi
}

# Ensure venv + deps. Idempotent.
setup_venv() {
  local repo="$1"
  cd "$repo"
  if [[ ! -d .venv ]]; then
    info "Creating .venv (one-time)…"
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  if [[ ! -f .venv/.deps-installed ]] || [[ requirements.txt -nt .venv/.deps-installed ]]; then
    info "Installing Python dependencies (this can take a minute)…"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    touch .venv/.deps-installed
    ok "Dependencies installed"
  else
    ok "Dependencies already installed"
  fi
}

# Run smoke test: play a tone and verify capture
run_smoke_test() {
  local repo="$1" device="$2"
  cd "$repo"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python3 install/lib/smoke_test.py --device "$device"
}
